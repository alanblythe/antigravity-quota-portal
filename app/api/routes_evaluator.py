"""Evaluation and publish trigger API endpoints."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_actor, get_audit_emitter, get_db, get_evaluator, get_wtu_client
from app.config import settings
from app.core.timezone_engine import get_current_week_window
from app.db.models import AuditEvent, EvaluationResult, KPIStats, ProjectWTUPoolStatus, UserStatus

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evaluator", tags=["Evaluator"])


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@router.post("/publish", response_model=EvaluationResult)
def publish_and_sync(request: Request = None, evaluator=Depends(get_evaluator)):
    """Execute immediate full evaluation & publish sync."""
    actor = get_actor(request, default="admin_ui")
    result = evaluator.run_evaluation(triggered_by=actor, is_publish=True)
    if not result.success:
        raise HTTPException(status_code=502, detail=result.error or "Evaluation failed")
    if getattr(result, "failed_swaps_count", 0) > 0 and not result.error:
        result.error = f"{result.failed_swaps_count} Cloud Identity group swap(s) failed"
    try:
        from app.main import schedule_project_wtu_reset_triggers

        schedule_project_wtu_reset_triggers()
    except Exception as e:
        logger.warning(f"Failed to reschedule WTU reset triggers after publish: {e}")
    return result


@router.post("/run", response_model=EvaluationResult)
def run_evaluation_manual(
    request: Request = None,
    evaluator=Depends(get_evaluator),
    db=Depends(get_db),
    audit_emitter=Depends(get_audit_emitter),
):
    """Run an ad-hoc evaluation without updating the published timestamp."""
    actor = get_actor(request, default="admin_manual_run")
    result = evaluator.run_evaluation(triggered_by=actor, is_publish=False)
    if not result.success:
        raise HTTPException(status_code=502, detail=result.error or "Evaluation failed")
    if getattr(result, "failed_swaps_count", 0) > 0 and not result.error:
        result.error = f"{result.failed_swaps_count} Cloud Identity group swap(s) failed"

    recent_events = db.list_audit_events(limit=5)
    already_logged = any(
        e.action == "EVALUATION_RUN" and e.timestamp >= result.evaluated_at
        for e in recent_events
    )
    if not already_logged:
        audit_event = AuditEvent(
            event_id=f"evt-eval-{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now(UTC),
            action="EVALUATION_RUN",
            triggered_by=actor,
            details={
                "evaluated_users": getattr(result, "evaluated_users_count", result.users_evaluated),
                "users_evaluated": result.users_evaluated,
                "swaps": result.group_swaps_count,
                "failed_swaps": getattr(result, "failed_swaps_count", 0),
            },
        )
        db.add_audit_event(audit_event)
        if audit_emitter is not None and getattr(db, "audit_emitter", None) is not audit_emitter:
            audit_emitter.emit(audit_event)

    try:
        from app.main import schedule_project_wtu_reset_triggers

        schedule_project_wtu_reset_triggers()
    except Exception as e:
        logger.warning(f"Failed to reschedule WTU reset triggers after manual run: {e}")
    return result


@router.get("/kpis", response_model=KPIStats)
def get_kpi_stats(db=Depends(get_db), wtu_client=Depends(get_wtu_client)):
    users = db.list_users()
    config = db.get_config()
    week_id, start_utc, end_utc, now_utc = get_current_week_window(config.timezone)
    monitored_projects = config.monitored_projects or settings.monitored_project_ids
    primary_project = monitored_projects[0] if monitored_projects else settings.PROJECT_ID
    all_known_projects = list(
        dict.fromkeys(list(monitored_projects) + [u.project_id for u in users if u.project_id])
    ) or [primary_project]

    active_count = sum(1 for u in users if u.status == UserStatus.ACTIVE and not u.is_exempt)
    throttled_count = sum(1 for u in users if u.status == UserStatus.AUTO_DISABLED)
    locked_count = sum(1 for u in users if u.status == UserStatus.MANUALLY_DISABLED)
    exempt_count = sum(1 for u in users if u.is_exempt and u.status != UserStatus.MANUALLY_DISABLED)

    total_tokens = sum(u.current_week.total_tokens for u in users)
    total_gross = sum(u.current_week.gross_spend_usd for u in users)
    total_credits = sum(u.current_week.quota_credits_usd for u in users)
    total_net = sum(u.current_week.net_billable_cost_usd for u in users)

    # Team weekly budget progress
    team_budget = config.weekly_team_budget_usd if config.weekly_team_budget_usd > 0 else total_credits
    budget_util_pct = round((total_gross / team_budget) * 100.0, 1) if team_budget > 0 else 0.0

    # Cloud Monitoring WTU telemetry across monitored and user-assigned projects
    if hasattr(wtu_client, "fetch_multi_project_wtu_status"):
        project_wtu_map = wtu_client.fetch_multi_project_wtu_status(
            project_ids=all_known_projects,
            preferred_tier=config.wtu_tier_metric,
            configured_wtu_limit=int(config.weekly_wtu_quota or 0),
        )
    else:
        user_emails = [u.email for u in users]
        try:
            single_status = wtu_client.fetch_wtu_status(
                user_emails=user_emails,
                preferred_tier=config.wtu_tier_metric,
                configured_wtu_limit=int(config.weekly_wtu_quota or 0),
                project_id=primary_project,
            )
        except TypeError:
            single_status = wtu_client.fetch_wtu_status(
                preferred_tier=config.wtu_tier_metric,
                configured_wtu_limit=int(config.weekly_wtu_quota or 0),
                project_id=primary_project,
            )
        project_wtu_map = {primary_project: single_status}

    primary_wtu = project_wtu_map.get(primary_project) or next(iter(project_wtu_map.values()))

    wtu_users_dict = {}
    project_pools: list[ProjectWTUPoolStatus] = []

    for pid, p_wtu in project_wtu_map.items():
        for u_key, u_snap in (p_wtu.per_user_wtu or {}).items():
            entry = {
                "project_id": pid,
                "net_usage": u_snap.wtu_consumed,
                "limit": u_snap.wtu_limit,
                "utilization_pct": u_snap.wtu_utilization_pct,
                "window_start_utc": u_snap.cycle_start_utc.isoformat() if u_snap.cycle_start_utc else None,
                "window_reset_utc": u_snap.cycle_reset_utc.isoformat() if u_snap.cycle_reset_utc else None,
            }
            wtu_users_dict[f"{pid}:{u_key}"] = entry
            if u_key not in wtu_users_dict:
                wtu_users_dict[u_key] = entry

        p_users = [u for u in users if (u.project_id or primary_project) == pid]
        p_active = sum(1 for u in p_users if u.status == UserStatus.ACTIVE and not u.is_exempt)
        p_throttled = sum(1 for u in p_users if u.status == UserStatus.AUTO_DISABLED)
        p_spend = round(sum(u.current_week.gross_spend_usd for u in p_users), 2)

        if p_wtu and p_wtu.active and p_wtu.active_cycle_start_utc:
            p_start_utc = _ensure_utc(p_wtu.active_cycle_start_utc)
            p_reset_utc = _ensure_utc(p_wtu.active_cycle_reset_utc) or (
                p_start_utc + timedelta(days=7)
            )
            while now_utc >= p_reset_utc:
                p_start_utc = p_reset_utc
                p_reset_utc = p_start_utc + timedelta(days=7)
        else:
            p_start_utc = start_utc
            p_reset_utc = end_utc
            candidate_users = p_users or (users if pid == primary_project else [])
            for u in candidate_users:
                u_rst = _ensure_utc(u.current_week.wtu_window_reset_at)
                if u_rst is not None and u_rst.microsecond != 999999:
                    u_st = _ensure_utc(u.current_week.wtu_window_start_at) or (
                        u_rst - timedelta(days=7)
                    )
                    while now_utc >= u_rst:
                        u_st = u_rst
                        u_rst = u_st + timedelta(days=7)
                    p_start_utc = u_st
                    p_reset_utc = u_rst
                    break

        p_rem_sec = max(0.0, (p_reset_utc - now_utc).total_seconds())
        p_days = int(p_rem_sec // 86400)
        p_hours = int((p_rem_sec % 86400) // 3600)

        project_pools.append(
            ProjectWTUPoolStatus(
                project_id=pid,
                tier_metric=p_wtu.tier_metric,
                license_tier=p_wtu.license_tier,
                active=p_wtu.active,
                wtu_consumed=p_wtu.total_active_wtu_consumed,
                wtu_limit=p_wtu.wtu_quota_limit,
                wtu_utilization_pct=p_wtu.wtu_utilization_pct,
                cycle_start_utc=p_start_utc,
                cycle_reset_utc=p_reset_utc,
                days_until_reset=p_days,
                hours_until_reset=p_hours,
                active_developers=p_active,
                throttled_developers=p_throttled,
                cycle_spend_usd=p_spend,
            )
        )

    # Align primary weekly reset countdown with the active WTU 7-day cycle reset when available
    primary_pool = next(
        (p for p in project_pools if p.project_id == primary_project),
        project_pools[0] if project_pools else None,
    )
    effective_start_utc = (
        primary_pool.cycle_start_utc
        if (primary_pool and primary_pool.cycle_start_utc)
        else start_utc
    )
    effective_reset_utc = (
        primary_pool.cycle_reset_utc
        if (primary_pool and primary_pool.cycle_reset_utc)
        else end_utc
    )
    remaining_seconds = max(0.0, (effective_reset_utc - now_utc).total_seconds())
    days_until_reset = int(remaining_seconds // 86400)
    hours_until_reset = int((remaining_seconds % 86400) // 3600)

    return KPIStats(
        active_developers=active_count,
        auto_throttled_developers=throttled_count,
        manually_locked_developers=locked_count,
        exempt_developers=exempt_count,
        total_tokens_this_week=total_tokens,
        total_gross_usage_usd=round(total_gross, 2),
        total_credits_allocated_usd=round(total_credits, 2),
        total_net_overages_usd=round(total_net, 2),
        week_id=week_id,
        week_start_utc=effective_start_utc,
        week_reset_utc=effective_reset_utc,
        days_until_reset=days_until_reset,
        hours_until_reset=hours_until_reset,
        weekly_team_budget_usd=round(team_budget, 2),
        weekly_budget_utilization_pct=budget_util_pct,
        wtu_pool_net_usage=primary_wtu.total_active_wtu_consumed if primary_wtu.active else None,
        wtu_pool_limit=primary_wtu.wtu_quota_limit if primary_wtu.active else None,
        wtu_pool_utilization_pct=primary_wtu.wtu_utilization_pct if primary_wtu.active else None,
        wtu_window_start_utc=(
            effective_start_utc
            if primary_wtu.active
            else _ensure_utc(primary_wtu.active_cycle_start_utc)
        ),
        wtu_window_reset_utc=(
            effective_reset_utc
            if primary_wtu.active
            else _ensure_utc(primary_wtu.active_cycle_reset_utc)
        ),
        wtu_user_usage=wtu_users_dict,
        monitored_projects=all_known_projects,
        project_pools=project_pools,
    )

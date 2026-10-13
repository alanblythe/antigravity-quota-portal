"""Application configuration and model pricing API endpoints."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import (
    get_actor,
    get_audit_emitter,
    get_bq,
    get_db,
    get_evaluator,
    get_identity,
    get_wtu_client,
)
from app.config import settings
from app.core.cost_model import calculate_total_usage_costs
from app.core.state_machine import evaluate_user_state_and_group
from app.db.models import (
    AppConfig,
    AuditEvent,
    ConfigUpdateRequest,
    ModelPricing,
    UserStatus,
    load_default_model_pricing,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/config", tags=["Config"])


def _fetch_group_members_or_502(identity: Any, group_email: str) -> set[str]:
    """Query Cloud Identity group members or raise 502 on upstream failure."""
    try:
        try:
            return identity.get_group_members(group_email, raise_on_error=True)
        except TypeError:
            return identity.get_group_members(group_email)
    except Exception as e:
        logger.warning(f"Failed to query Cloud Identity group memberships for {group_email}: {e}")
        raise HTTPException(
            status_code=502,
            detail="Failed to query Cloud Identity group memberships",
        ) from e


def _reconcile_user_group_safe(
    identity: Any, email: str, target_group: str, current_config: AppConfig
) -> bool:
    """Reconcile a user's Cloud Identity group membership, returning False on failure."""
    try:
        try:
            res = identity.reconcile_user_group(
                email,
                target_group,
                enabled_group=current_config.enabled_group,
                disabled_group=current_config.disabled_group,
            )
        except TypeError:
            res = identity.reconcile_user_group(email, target_group)
        return False if res is False else True
    except Exception as e:
        logger.warning(f"Failed to reconcile Cloud Identity group for {email}: {e}")
        return False


def _recalculate_all_users_spend(
    db: Any,
    current_config: AppConfig,
    identity: Any = None,
    audit_emitter: Any = None,
    actor: str = "admin_ui",
) -> None:
    """Recalculate all users' current-week spend, state, and group membership using current_config.models."""
    now = datetime.now(UTC)
    enabled_members: set[str] | None = None
    disabled_members: set[str] | None = None
    if identity is not None:
        enabled_members = _fetch_group_members_or_502(identity, current_config.enabled_group)
        disabled_members = _fetch_group_members_or_502(identity, current_config.disabled_group)

    updated_users = []
    pending_audit_events: list[AuditEvent] = []
    failed_swaps = 0
    for u in db.list_users():
        eff_q = (
            u.custom_quota_usd
            if u.has_custom_quota and u.custom_quota_usd is not None
            else current_config.default_quota_usd
        )
        eff_o = (
            u.custom_overage_usd
            if u.has_custom_quota and u.custom_overage_usd is not None
            else current_config.default_overage_usd
        )
        tokens_by_model = u.current_week.tokens_by_model or {}
        granular_by_model: dict[str, dict[str, int]] = {}
        for m_name, details in (u.current_week.token_details_by_model or {}).items():
            granular_by_model[m_name] = {
                "prompt_tokens": int(getattr(details, "prompt_tokens", 0) or 0),
                "candidates_tokens": int(getattr(details, "candidates_tokens", 0) or 0),
                "cached_tokens": int(getattr(details, "cached_tokens", 0) or 0),
                "thoughts_tokens": int(getattr(details, "thoughts_tokens", 0) or 0),
                "prompt_tokens_gt_200k": int(getattr(details, "prompt_tokens_gt_200k", 0) or 0),
                "candidates_tokens_gt_200k": int(
                    getattr(details, "candidates_tokens_gt_200k", 0) or 0
                ),
                "cached_tokens_gt_200k": int(getattr(details, "cached_tokens_gt_200k", 0) or 0),
                "thoughts_tokens_gt_200k": int(getattr(details, "thoughts_tokens_gt_200k", 0) or 0),
                "fallback_tokens": int(getattr(details, "fallback_tokens", 0) or 0),
            }
        (
            gross_spend,
            remaining_credit,
            net_billable,
            spend_breakdown,
            spend_by_model,
            utilization_pct,
            _,
        ) = calculate_total_usage_costs(
            tokens_by_model,
            current_config.models,
            eff_q,
            eff_o,
            granular_by_model=granular_by_model or None,
        )
        u.current_week.quota_credits_usd = eff_q
        u.current_week.overage_buffer_usd = eff_o
        u.current_week.spend_by_model = spend_by_model
        u.current_week.spend_breakdown = spend_breakdown
        u.current_week.gross_spend_usd = gross_spend
        u.current_week.remaining_credit_usd = remaining_credit
        u.current_week.net_billable_cost_usd = net_billable
        u.current_week.credit_utilization_percentage = utilization_pct

        norm_email = u.email.lower().strip()
        in_enabled = (
            norm_email in enabled_members
            if enabled_members is not None
            else (u.status == UserStatus.ACTIVE)
        )
        in_disabled = (
            norm_email in disabled_members
            if disabled_members is not None
            else (u.status != UserStatus.ACTIVE)
        )
        transition = evaluate_user_state_and_group(
            current_status=u.status,
            current_in_enabled_group=in_enabled,
            is_exempt=u.is_exempt,
            gross_spend_usd=gross_spend,
            quota_credits_usd=eff_q,
            overage_buffer_usd=eff_o,
            is_weekly_reset=False,
            current_in_disabled_group=in_disabled,
        )
        swap_succeeded = True
        if (transition.state_changed or transition.group_changed) and identity is not None:
            if transition.group_changed:
                swap_succeeded = _reconcile_user_group_safe(
                    identity, u.email, transition.target_group, current_config
                )
                if swap_succeeded:
                    target_group_email = (
                        current_config.enabled_group
                        if transition.target_group == "ENABLED"
                        else current_config.disabled_group
                    )
                    pending_audit_events.append(
                        AuditEvent(
                            event_id=f"evt-swap-{uuid.uuid4().hex[:8]}",
                            timestamp=now,
                            action="GROUP_SWAP",
                            triggered_by=actor,
                            target_user=u.email,
                            details={
                                "target_group_email": target_group_email,
                                "direction": transition.target_group,
                                "reason": transition.reason,
                            },
                        )
                    )
                else:
                    failed_swaps += 1
        if u.status != transition.new_status and swap_succeeded:
            old_status = u.status
            u.status = transition.new_status
            pending_audit_events.append(
                AuditEvent(
                    event_id=f"evt-stat-{uuid.uuid4().hex[:8]}",
                    timestamp=now,
                    action="STATUS_CHANGE",
                    triggered_by=actor,
                    target_user=u.email,
                    details={
                        "from_status": old_status.value,
                        "to_status": transition.new_status.value,
                        "reason": transition.reason,
                        "gross_spend": gross_spend,
                        "quota": eff_q,
                    },
                )
            )
        u.updated_at = now
        updated_users.append(u)
    if updated_users:
        db.save_users(updated_users)
    for evt in pending_audit_events:
        db.add_audit_event(evt)
        if audit_emitter is not None and getattr(db, "audit_emitter", None) is not audit_emitter:
            audit_emitter.emit(evt)
    if failed_swaps > 0:
        raise HTTPException(
            status_code=502,
            detail=f"{failed_swaps} Cloud Identity group swap(s) failed",
        )


@router.get("", response_model=AppConfig)
def get_config(db=Depends(get_db)):
    return db.get_config()


@router.put("", response_model=AppConfig)
def update_config(
    payload: ConfigUpdateRequest,
    request: Request = None,
    db=Depends(get_db),
    identity=Depends(get_identity),
    evaluator=Depends(get_evaluator),
):
    lock = getattr(evaluator, "_eval_lock", None) or get_evaluator()._eval_lock
    with lock:
        current = db.get_config()
        update_data = payload.model_dump(exclude_unset=True)
        clean_updates = {k: v for k, v in update_data.items() if v is not None}

        if "timezone" in clean_updates:
            tz_name = str(clean_updates["timezone"]).strip()
            try:
                ZoneInfo(tz_name)
            except (ZoneInfoNotFoundError, KeyError, ValueError) as e:
                raise HTTPException(status_code=422, detail=f"Invalid timezone: {tz_name}") from e
            clean_updates["timezone"] = tz_name

        for key, value in clean_updates.items():
            setattr(current, key, value)

        if payload.enabled_group is not None or payload.disabled_group is not None:
            en_grp = (current.enabled_group or "").strip()
            dis_grp = (current.disabled_group or "").strip()
            if "@" not in en_grp or "@" not in dis_grp:
                raise HTTPException(
                    status_code=422,
                    detail="enabled_group and disabled_group must be valid email addresses containing '@'",
                )
            if en_grp.lower() == dis_grp.lower():
                raise HTTPException(
                    status_code=422,
                    detail="enabled_group and disabled_group must be distinct Cloud Identity groups",
                )
            current.enabled_group = en_grp
            current.disabled_group = dis_grp

        if "enabled_group" in clean_updates and hasattr(identity, "enabled_group_email"):
            identity.enabled_group_email = current.enabled_group
        if "disabled_group" in clean_updates and hasattr(identity, "disabled_group_email"):
            identity.disabled_group_email = current.disabled_group

        if "monitored_projects" in clean_updates and current.monitored_projects:
            wtu_client = get_wtu_client()
            wtu_client.project_id = current.monitored_projects[0]
            bq = get_bq()
            bq.default_workload_project_id = current.monitored_projects[0]

        updated = db.update_config(current)
        now_utc = datetime.now(UTC)
        actor = get_actor(request)

        if "wtu_tier_metric" in clean_updates:
            new_tier = (
                "ENTERPRISE_STANDARD"
                if "standard" in (current.wtu_tier_metric or "").lower()
                else "ENTERPRISE_PLUS"
            )
            primary_proj = (
                current.monitored_projects[0]
                if current.monitored_projects
                else settings.PROJECT_ID
            )
            tier_updated_users = []
            for u in db.list_users():
                if (u.project_id or primary_proj) == primary_proj and u.license_tier != new_tier:
                    u.license_tier = new_tier
                    u.updated_at = now_utc
                    tier_updated_users.append(u)
            if tier_updated_users:
                db.save_users(tier_updated_users)

        quota_changed = (
            "default_quota_usd" in clean_updates or "default_overage_usd" in clean_updates
        )
        groups_changed = (
            "enabled_group" in clean_updates or "disabled_group" in clean_updates
        )
        failed_swaps = 0
        if quota_changed or groups_changed:
            enabled_members = _fetch_group_members_or_502(identity, current.enabled_group)
            disabled_members = _fetch_group_members_or_502(identity, current.disabled_group)

            users_to_save = []
            pending_audit_events: list[AuditEvent] = []
            for u in db.list_users():
                should_recalc_quota = quota_changed and not u.has_custom_quota
                if not should_recalc_quota and not groups_changed:
                    continue

                eff_q = (
                    u.custom_quota_usd
                    if u.has_custom_quota and u.custom_quota_usd is not None
                    else current.default_quota_usd
                )
                eff_o = (
                    u.custom_overage_usd
                    if u.has_custom_quota and u.custom_overage_usd is not None
                    else current.default_overage_usd
                )
                gross = u.current_week.gross_spend_usd

                if should_recalc_quota:
                    u.current_week.quota_credits_usd = eff_q
                    u.current_week.overage_buffer_usd = eff_o
                    u.current_week.remaining_credit_usd = round(max(0.0, eff_q - gross), 2)
                    u.current_week.net_billable_cost_usd = round(max(0.0, gross - eff_q), 2)
                    if eff_q > 0:
                        u.current_week.credit_utilization_percentage = round(
                            (gross / eff_q) * 100.0, 2
                        )
                    else:
                        u.current_week.credit_utilization_percentage = (
                            100.0 if gross > 0 else 0.0
                        )

                norm_email = u.email.lower().strip()
                transition = evaluate_user_state_and_group(
                    current_status=u.status,
                    current_in_enabled_group=norm_email in enabled_members,
                    is_exempt=u.is_exempt,
                    gross_spend_usd=gross,
                    quota_credits_usd=eff_q,
                    overage_buffer_usd=eff_o,
                    is_weekly_reset=False,
                    current_in_disabled_group=norm_email in disabled_members,
                )
                swap_succeeded = True
                if transition.group_changed:
                    swap_succeeded = _reconcile_user_group_safe(
                        identity, u.email, transition.target_group, current
                    )
                    if swap_succeeded:
                        target_group_email = (
                            current.enabled_group
                            if transition.target_group == "ENABLED"
                            else current.disabled_group
                        )
                        pending_audit_events.append(
                            AuditEvent(
                                event_id=f"evt-swap-{uuid.uuid4().hex[:8]}",
                                timestamp=now_utc,
                                action="GROUP_SWAP",
                                triggered_by=actor,
                                target_user=u.email,
                                details={
                                    "target_group_email": target_group_email,
                                    "direction": transition.target_group,
                                    "reason": transition.reason,
                                },
                            )
                        )
                    else:
                        failed_swaps += 1
                status_changed = False
                if u.status != transition.new_status and swap_succeeded:
                    old_status = u.status
                    u.status = transition.new_status
                    status_changed = True
                    pending_audit_events.append(
                        AuditEvent(
                            event_id=f"evt-stat-{uuid.uuid4().hex[:8]}",
                            timestamp=now_utc,
                            action="STATUS_CHANGE",
                            triggered_by=actor,
                            target_user=u.email,
                            details={
                                "from_status": old_status.value,
                                "to_status": transition.new_status.value,
                                "reason": transition.reason,
                                "gross_spend": gross,
                                "quota": eff_q,
                            },
                        )
                    )
                if should_recalc_quota or status_changed or (transition.group_changed and swap_succeeded):
                    u.updated_at = now_utc
                    users_to_save.append(u)
            if users_to_save:
                db.save_users(users_to_save)
            for evt in pending_audit_events:
                db.add_audit_event(evt)

        try:
            from app.main import schedule_project_wtu_reset_triggers

            schedule_project_wtu_reset_triggers()
        except Exception as e:
            logger.warning(f"Failed to reschedule WTU reset triggers after config update: {e}")
        db.add_audit_event(
            AuditEvent(
                event_id=f"evt-cfg-{uuid.uuid4().hex[:8]}",
                timestamp=now_utc,
                action="CONFIG_UPDATE",
                triggered_by=actor,
                details=clean_updates,
            )
        )
        if failed_swaps > 0:
            raise HTTPException(
                status_code=502,
                detail=f"{failed_swaps} Cloud Identity group swap(s) failed",
            )
        return updated


@router.get("/pricing", response_model=dict[str, ModelPricing])
def get_pricing_matrix(db=Depends(get_db)):
    config = db.get_config()
    return config.models


@router.put("/pricing", response_model=dict[str, ModelPricing])
def update_pricing_matrix(
    matrix: dict[str, ModelPricing],
    request: Request = None,
    db=Depends(get_db),
    identity=Depends(get_identity),
    audit_emitter=Depends(get_audit_emitter),
    evaluator=Depends(get_evaluator),
):
    lock = getattr(evaluator, "_eval_lock", None) or get_evaluator()._eval_lock
    with lock:
        actor = get_actor(request)
        config = db.get_config()
        config.models = matrix
        db.update_config(config)
        _recalculate_all_users_spend(
            db, config, identity=identity, audit_emitter=audit_emitter, actor=actor
        )

        db.add_audit_event(
            AuditEvent(
                event_id=f"evt-price-{uuid.uuid4().hex[:8]}",
                timestamp=datetime.now(UTC),
                action="PRICING_MATRIX_UPDATE",
                triggered_by=actor,
                details={"updated_models": list(matrix.keys())},
            )
        )
        return config.models


@router.post("/pricing/reset", response_model=dict[str, ModelPricing])
def reset_pricing_matrix(
    request: Request = None,
    db=Depends(get_db),
    identity=Depends(get_identity),
    audit_emitter=Depends(get_audit_emitter),
    evaluator=Depends(get_evaluator),
):
    """Reload model pricing matrix from the external config/model_pricing.json file."""
    lock = getattr(evaluator, "_eval_lock", None) or get_evaluator()._eval_lock
    with lock:
        actor = get_actor(request)
        default_matrix = load_default_model_pricing()
        config = db.get_config()
        config.models = default_matrix
        db.update_config(config)
        _recalculate_all_users_spend(
            db, config, identity=identity, audit_emitter=audit_emitter, actor=actor
        )

        db.add_audit_event(
            AuditEvent(
                event_id=f"evt-price-rst-{uuid.uuid4().hex[:8]}",
                timestamp=datetime.now(UTC),
                action="PRICING_MATRIX_RESET",
                triggered_by=actor,
                details={"reloaded_models": list(default_matrix.keys())},
            )
        )
        return config.models

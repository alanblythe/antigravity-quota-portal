"""Evaluation engine for weekly quota reconciliation and dual-group governance."""

import hashlib
import logging
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from app.bq.wtu_client import derive_license_tier
from app.config import settings
from app.core.cost_model import calculate_total_usage_costs
from app.core.state_machine import evaluate_user_state_and_group
from app.core.timezone_engine import get_current_week_window, is_new_week
from app.db.models import (
    AppConfig,
    AuditEvent,
    CurrentWeekUsage,
    EvaluationResult,
    ModelTokenUsage,
    User,
    UserStatus,
    WeeklySnapshot,
)

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime | None) -> datetime | None:
    """Normalize a datetime to timezone-aware UTC (or return None)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _deterministic_event_id(prefix: str, *parts: Any) -> str:
    """Generate a deterministic audit event_id from stable event attributes."""
    raw = ":".join(str(p) for p in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"evt-{prefix}-{digest}"


def _accumulate_records(rec_entries: list[tuple[str | None, Any, datetime | None]]) -> dict[str, Any]:
    """Aggregate token counts, granular sub-counts, and project attribution for a slice of BQ records."""
    proj_stats: dict[str, tuple[int, datetime]] = {}
    tokens_by_model: dict[str, int] = {}
    granular_by_model: dict[str, dict[str, int]] = {}
    token_details_by_model: dict[str, ModelTokenUsage] = {}
    total_requests = 0
    total_tokens = 0
    last_active: datetime | None = None

    for rec_project, record, rec_last_active in rec_entries:
        prev_tokens = tokens_by_model.get(record.model_name, 0)
        tokens_by_model[record.model_name] = prev_tokens + record.token_count

        prev_gran = granular_by_model.get(
            record.model_name,
            {
                "prompt_tokens": 0,
                "candidates_tokens": 0,
                "cached_tokens": 0,
                "thoughts_tokens": 0,
                "prompt_tokens_gt_200k": 0,
                "candidates_tokens_gt_200k": 0,
                "cached_tokens_gt_200k": 0,
                "thoughts_tokens_gt_200k": 0,
                "fallback_tokens": 0,
            },
        )
        p_tok = getattr(record, "prompt_tokens", 0) or 0
        c_tok = getattr(record, "candidates_tokens", 0) or 0
        ca_tok = getattr(record, "cached_tokens", 0) or 0
        th_tok = getattr(record, "thoughts_tokens", 0) or 0
        p_gt_tok = getattr(record, "prompt_tokens_gt_200k", 0) or 0
        c_gt_tok = getattr(record, "candidates_tokens_gt_200k", 0) or 0
        ca_gt_tok = getattr(record, "cached_tokens_gt_200k", 0) or 0
        th_gt_tok = getattr(record, "thoughts_tokens_gt_200k", 0) or 0
        raw_fb = getattr(record, "fallback_tokens", None)
        if raw_fb is not None and raw_fb > 0:
            fb_tok = int(raw_fb)
        elif (p_tok + c_tok + ca_tok + th_tok) == 0:
            fb_tok = record.token_count
        elif raw_fb is None:
            fb_tok = max(0, record.token_count - (max(p_tok, ca_tok) + c_tok + th_tok))
        else:
            fb_tok = 0

        updated_gran = {
            "prompt_tokens": prev_gran["prompt_tokens"] + p_tok,
            "candidates_tokens": prev_gran["candidates_tokens"] + c_tok,
            "cached_tokens": prev_gran["cached_tokens"] + ca_tok,
            "thoughts_tokens": prev_gran["thoughts_tokens"] + th_tok,
            "prompt_tokens_gt_200k": prev_gran["prompt_tokens_gt_200k"] + p_gt_tok,
            "candidates_tokens_gt_200k": prev_gran["candidates_tokens_gt_200k"] + c_gt_tok,
            "cached_tokens_gt_200k": prev_gran["cached_tokens_gt_200k"] + ca_gt_tok,
            "thoughts_tokens_gt_200k": prev_gran["thoughts_tokens_gt_200k"] + th_gt_tok,
            "fallback_tokens": prev_gran["fallback_tokens"] + fb_tok,
        }
        granular_by_model[record.model_name] = updated_gran
        token_details_by_model[record.model_name] = ModelTokenUsage(
            total_tokens=prev_tokens + record.token_count,
            prompt_tokens=updated_gran["prompt_tokens"],
            candidates_tokens=updated_gran["candidates_tokens"],
            cached_tokens=updated_gran["cached_tokens"],
            thoughts_tokens=updated_gran["thoughts_tokens"],
            prompt_tokens_gt_200k=updated_gran["prompt_tokens_gt_200k"],
            candidates_tokens_gt_200k=updated_gran["candidates_tokens_gt_200k"],
            cached_tokens_gt_200k=updated_gran["cached_tokens_gt_200k"],
            thoughts_tokens_gt_200k=updated_gran["thoughts_tokens_gt_200k"],
            fallback_tokens=updated_gran["fallback_tokens"],
        )

        total_tokens += record.token_count
        total_requests += record.request_count
        if rec_last_active and (last_active is None or rec_last_active > last_active):
            last_active = rec_last_active

        if rec_project:
            prev_p_tok, prev_p_ts = proj_stats.get(rec_project, (0, datetime.min.replace(tzinfo=UTC)))
            new_p_ts = max(prev_p_ts, rec_last_active) if rec_last_active else prev_p_ts
            proj_stats[rec_project] = (prev_p_tok + record.token_count, new_p_ts)

    return {
        "proj_stats": proj_stats,
        "tokens_by_model": tokens_by_model,
        "granular_by_model": granular_by_model,
        "token_details_by_model": token_details_by_model,
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "last_active": last_active,
    }


class QuotaEvaluator:
    """Core evaluation engine that reconciles BigQuery usage against Firestore quotas."""

    def __init__(self, db_client: Any, bq_client: Any, identity_client: Any, wtu_client: Any = None):
        self.db = db_client
        self.bq = bq_client
        self.identity = identity_client
        self.wtu = wtu_client
        self._eval_lock = threading.RLock()

    def run_evaluation(
        self,
        triggered_by: str = "SYSTEM_WORKER",
        is_publish: bool = False,
    ) -> EvaluationResult:
        """Execute a full quota reconciliation run across all users."""
        with self._eval_lock:
            return self._run_evaluation_locked(triggered_by=triggered_by, is_publish=is_publish)

    def _run_evaluation_locked(
        self,
        triggered_by: str = "SYSTEM_WORKER",
        is_publish: bool = False,
    ) -> EvaluationResult:
        eval_time = _ensure_utc(datetime.now(UTC))
        assert eval_time is not None
        logger.info(f"Starting quota evaluation run triggered by '{triggered_by}' (is_publish={is_publish})")

        try:
            config: AppConfig = self.db.get_config()
            week_id, start_utc, end_utc, now_utc = get_current_week_window(config.timezone, eval_time)
            monitored_projects = config.monitored_projects or settings.monitored_project_ids
            primary_project = monitored_projects[0] if monitored_projects else settings.PROJECT_ID

            # 0. Fetch existing users and Cloud Monitoring WTU cycle windows per monitored workload project
            existing_users_list = self.db.list_users()
            users_map: dict[str, User] = {u.email.lower().strip(): u for u in existing_users_list}

            user_projects = [u.project_id for u in existing_users_list if u.project_id]
            all_known_projects = list(dict.fromkeys([*monitored_projects, *user_projects])) or [primary_project]

            project_wtu_map: dict[str, Any] = {}
            if self.wtu is not None:
                try:
                    if hasattr(self.wtu, "fetch_multi_project_wtu_status"):
                        project_wtu_map = self.wtu.fetch_multi_project_wtu_status(
                            project_ids=all_known_projects,
                            preferred_tier=config.wtu_tier_metric,
                            configured_wtu_limit=int(config.weekly_wtu_quota or 0),
                        )
                    else:
                        single_wtu = self.wtu.fetch_wtu_status(
                            preferred_tier=config.wtu_tier_metric,
                            configured_wtu_limit=int(config.weekly_wtu_quota or 0),
                        )
                        project_wtu_map = {pid: single_wtu for pid in all_known_projects}
                except Exception as wtu_err:
                    logger.debug(f"Optional WTU status fetch skipped during evaluation: {wtu_err}")

            # Compute effective 7-day cycle window (start_utc, reset_utc) per project
            project_cycle_map: dict[str, tuple[datetime, datetime]] = {}
            other_active_starts_by_proj: dict[str, set[datetime]] = {
                pid: {
                    norm_start
                    for other_pid, p_wtu in project_wtu_map.items()
                    if other_pid != pid
                    and p_wtu
                    and getattr(p_wtu, "active", False)
                    and (norm_start := _ensure_utc(getattr(p_wtu, "active_cycle_start_utc", None))) is not None
                }
                for pid in all_known_projects
            }
            for pid in all_known_projects:
                p_wtu = project_wtu_map.get(pid)
                if (
                    p_wtu
                    and getattr(p_wtu, "active", False)
                    and getattr(p_wtu, "active_cycle_start_utc", None) is not None
                ):
                    c_start = _ensure_utc(p_wtu.active_cycle_start_utc)
                    assert c_start is not None
                    c_reset = _ensure_utc(getattr(p_wtu, "active_cycle_reset_utc", None)) or (
                        c_start + timedelta(days=7)
                    )
                    while eval_time >= c_reset:
                        c_start = c_reset
                        c_reset = c_start + timedelta(days=7)
                    project_cycle_map[pid] = (c_start, c_reset)
                else:
                    # Fallback to persisted WTU window on users in this project (advancing if expired)
                    persisted_windows: list[tuple[datetime, datetime]] = []
                    for u in existing_users_list:
                        if (u.project_id or primary_project) != pid:
                            continue
                        u_start = _ensure_utc(u.current_week.wtu_window_start_at)
                        u_reset = _ensure_utc(u.current_week.wtu_window_reset_at)
                        if (
                            u_start is not None
                            and u_reset is not None
                            and u_reset.microsecond != 999999
                            and u_reset > u_start
                            and u_start not in other_active_starts_by_proj.get(pid, set())
                        ):
                            persisted_windows.append((u_start, u_reset))
                    if persisted_windows:
                        c_start, c_reset = max(persisted_windows, key=lambda pair: pair[1])
                        if eval_time >= c_reset:
                            if c_reset.microsecond == 999999:
                                _, c_start, c_reset, _ = get_current_week_window(config.timezone, eval_time)
                            else:
                                while eval_time >= c_reset:
                                    c_start = c_reset
                                    c_reset = c_start + timedelta(days=7)
                        project_cycle_map[pid] = (c_start, c_reset)
                    else:
                        project_cycle_map[pid] = (start_utc, end_utc)

            project_start_map: dict[str, datetime] = {
                pid: c_start for pid, (c_start, _) in project_cycle_map.items()
            }
            earliest_needed_start_map: dict[str, datetime] = dict(project_start_map)
            for u in existing_users_list:
                u_proj = u.project_id or primary_project
                prev_u_start = _ensure_utc(u.current_week.wtu_window_start_at)
                prev_u_reset = _ensure_utc(u.current_week.wtu_window_reset_at)
                c_start_for_u = project_start_map.get(u_proj, start_utc)
                if prev_u_start is not None and (
                    (prev_u_reset is not None and eval_time >= prev_u_reset)
                    or c_start_for_u > prev_u_start
                ):
                    earliest_needed_start_map[u_proj] = min(
                        earliest_needed_start_map.get(u_proj, prev_u_start), prev_u_start
                    )
            query_start_utc = (
                min(earliest_needed_start_map.values()) if earliest_needed_start_map else start_utc
            )

            # 1. Fetch BigQuery usage for the active (and any closing rollover) WTU cycle window(s)
            threshold_tokens = next(
                (m.context_tier_threshold_tokens for m in config.models.values() if m.context_tier_threshold_tokens),
                200000,
            )
            try:
                usage_records = self.bq.fetch_weekly_usage(
                    query_start_utc,
                    now_utc,
                    project_start_map=earliest_needed_start_map,
                    default_workload_project_id=primary_project,
                    context_tier_threshold_tokens=threshold_tokens,
                )
            except TypeError:
                try:
                    usage_records = self.bq.fetch_weekly_usage(
                        query_start_utc, now_utc, project_start_map=earliest_needed_start_map
                    )
                except TypeError:
                    usage_records = self.bq.fetch_weekly_usage(query_start_utc, now_utc)

            # Group usage records by user email across all projects (respecting per-project cycle start timestamps)
            user_records_by_email: dict[str, list[Any]] = {}
            for record in usage_records:
                email = record.user_email.lower().strip()
                rec_project = getattr(record, "project_id", None)
                known_u = users_map.get(email)
                eff_rec_proj = rec_project or (
                    known_u.project_id if known_u and known_u.project_id else primary_project
                )
                proj_min_start = _ensure_utc(earliest_needed_start_map.get(eff_rec_proj))
                rec_last_active = _ensure_utc(getattr(record, "last_active", None))
                if proj_min_start and rec_last_active and rec_last_active < proj_min_start:
                    continue
                user_records_by_email.setdefault(email, []).append((rec_project, record, rec_last_active))

            user_bq_data: dict[str, dict[str, Any]] = {}
            user_closing_bq_data: dict[str, dict[str, Any]] = {}
            for email, rec_entries in user_records_by_email.items():
                known_u = users_map.get(email)
                prev_u_start = _ensure_utc(known_u.current_week.wtu_window_start_at) if known_u else None
                current_entries: list[tuple[str | None, Any, datetime | None]] = []
                closing_entries: list[tuple[str | None, Any, datetime | None]] = []

                for rec_project, record, rec_last_active in rec_entries:
                    eff_rec_proj = rec_project or (
                        known_u.project_id if known_u and known_u.project_id else primary_project
                    )
                    proj_c_start = _ensure_utc(project_start_map.get(eff_rec_proj))
                    if (
                        proj_c_start is not None
                        and rec_last_active is not None
                        and rec_last_active < proj_c_start
                    ):
                        if prev_u_start is not None and rec_last_active >= prev_u_start:
                            closing_entries.append((rec_project, record, rec_last_active))
                        continue
                    current_entries.append((rec_project, record, rec_last_active))

                if closing_entries:
                    user_closing_bq_data[email] = _accumulate_records(closing_entries)

                if not current_entries:
                    continue

                acc = _accumulate_records(current_entries)
                proj_stats = acc["proj_stats"]
                if known_u and known_u.project_id and known_u.project_id in proj_stats:
                    primary_bq_proj: str | None = known_u.project_id
                elif proj_stats:
                    primary_bq_proj = max(proj_stats.items(), key=lambda kv: (kv[1][0], kv[1][1]))[0]
                else:
                    primary_bq_proj = None

                user_bq_data[email] = {
                    "project_id": primary_bq_proj,
                    "tokens_by_model": acc["tokens_by_model"],
                    "granular_by_model": acc["granular_by_model"],
                    "token_details_by_model": acc["token_details_by_model"],
                    "total_requests": acc["total_requests"],
                    "total_tokens": acc["total_tokens"],
                    "last_active": acc["last_active"],
                }

            # 2. Discover new users from BigQuery logs (Synthetic Discovery)
            pending_audit_events: list[AuditEvent] = []
            pending_snapshots: list[tuple[str, WeeklySnapshot]] = []

            for email in user_bq_data:
                if email.endswith(".gserviceaccount.com"):
                    continue
                if email not in users_map:
                    disc_project = user_bq_data[email].get("project_id") or primary_project
                    disc_wtu = project_wtu_map.get(disc_project)
                    disc_tier = (
                        getattr(disc_wtu, "license_tier", None)
                        or derive_license_tier(config.wtu_tier_metric, disc_project)
                    )
                    disc_start, disc_reset = project_cycle_map.get(disc_project, (start_utc, end_utc))
                    new_user = User(
                        email=email,
                        status=UserStatus.ACTIVE,
                        is_exempt=False,
                        has_custom_quota=False,
                        custom_quota_usd=None,
                        custom_overage_usd=None,
                        project_id=disc_project,
                        license_tier=disc_tier,
                        current_week=CurrentWeekUsage(
                            week_id=week_id,
                            quota_credits_usd=config.default_quota_usd,
                            overage_buffer_usd=config.default_overage_usd,
                            wtu_window_start_at=disc_start,
                            wtu_window_reset_at=disc_reset,
                        ),
                        created_at=eval_time,
                        updated_at=eval_time,
                    )
                    users_map[email] = new_user
                    # Buffer discovery audit event until user batch save succeeds
                    pending_audit_events.append(
                        AuditEvent(
                            event_id=_deterministic_event_id(
                                "disc", "USER_DISCOVERED", email, week_id, disc_project
                            ),
                            timestamp=eval_time,
                            action="USER_DISCOVERED",
                            triggered_by=triggered_by,
                            target_user=email,
                            details={
                                "week_id": week_id,
                                "project_id": disc_project,
                                "license_tier": disc_tier,
                                "default_quota": config.default_quota_usd,
                            },
                        )
                    )

            # 4. Fetch current group memberships for accuracy across both dual groups
            try:
                enabled_members = self.identity.get_group_members(config.enabled_group, raise_on_error=True)
                disabled_members = self.identity.get_group_members(config.disabled_group, raise_on_error=True)
            except TypeError:
                enabled_members = self.identity.get_group_members(config.enabled_group)
                disabled_members = self.identity.get_group_members(config.disabled_group)

            changes_summary: list[dict[str, Any]] = []
            group_swaps_count = 0
            failed_swaps_count = 0
            updated_users_to_save: list[User] = []

            for email, user in users_map.items():
                old_project = user.project_id
                prev_week_copy = user.current_week.model_copy(deep=True)
                prev_wtu_start = _ensure_utc(prev_week_copy.wtu_window_start_at)
                prev_wtu_reset = _ensure_utc(prev_week_copy.wtu_window_reset_at)
                prev_week_id = prev_week_copy.week_id

                bq_user_entry = user_bq_data.get(email)
                bq_proj = bq_user_entry.get("project_id") if bq_user_entry else None
                effective_project = bq_proj or user.project_id or primary_project
                user.project_id = effective_project
                proj_wtu = project_wtu_map.get(effective_project)
                active_proj_tier = (
                    getattr(proj_wtu, "license_tier", None)
                    if (proj_wtu and getattr(proj_wtu, "active", False))
                    else None
                )
                user.license_tier = (
                    active_proj_tier
                    or user.license_tier
                    or derive_license_tier(config.wtu_tier_metric, effective_project)
                )
                eff_cycle_start, eff_cycle_reset = project_cycle_map.get(
                    effective_project, (start_utc, end_utc)
                )
                eff_cycle_start = _ensure_utc(eff_cycle_start) or start_utc
                eff_cycle_reset = _ensure_utc(eff_cycle_reset) or end_utc

                # Check for Weekly Reset (phase-locked to the developer's project WTU 7-day cycle reset)
                weekly_reset_applied = False
                wtu_cycle_active = bool(
                    proj_wtu and getattr(proj_wtu, "active", False) and proj_wtu.active_cycle_start_utc
                )
                if prev_wtu_reset is not None and eval_time >= prev_wtu_reset:
                    weekly_reset_applied = True
                elif (
                    wtu_cycle_active
                    and prev_wtu_start is not None
                    and old_project in (None, effective_project)
                    and eff_cycle_start > prev_wtu_start
                ):
                    weekly_reset_applied = True
                elif prev_wtu_start is None and is_new_week(prev_week_id, week_id):
                    weekly_reset_applied = True

                # Determine effective quota and overage
                quota_credits = (
                    user.custom_quota_usd
                    if user.has_custom_quota and user.custom_quota_usd is not None
                    else config.default_quota_usd
                )
                overage_buffer = (
                    user.custom_overage_usd
                    if user.has_custom_quota and user.custom_overage_usd is not None
                    else config.default_overage_usd
                )

                closing_bq = user_closing_bq_data.get(email)
                if (
                    weekly_reset_applied
                    and closing_bq
                    and closing_bq["total_tokens"] > prev_week_copy.total_tokens
                ):
                    prev_quota = prev_week_copy.quota_credits_usd or quota_credits
                    prev_overage = prev_week_copy.overage_buffer_usd or overage_buffer
                    (
                        c_gross,
                        c_rem,
                        c_net,
                        c_breakdown,
                        c_spend_by_model,
                        c_util,
                        _,
                    ) = calculate_total_usage_costs(
                        closing_bq["tokens_by_model"],
                        config.models,
                        prev_quota,
                        prev_overage,
                        granular_by_model=closing_bq.get("granular_by_model"),
                    )
                    prev_week_copy.total_tokens = closing_bq["total_tokens"]
                    prev_week_copy.total_requests = closing_bq["total_requests"]
                    prev_week_copy.tokens_by_model = closing_bq["tokens_by_model"]
                    prev_week_copy.token_details_by_model = closing_bq.get("token_details_by_model", {})
                    prev_week_copy.gross_spend_usd = c_gross
                    prev_week_copy.remaining_credit_usd = c_rem
                    prev_week_copy.net_billable_cost_usd = c_net
                    prev_week_copy.spend_breakdown = c_breakdown
                    prev_week_copy.spend_by_model = c_spend_by_model
                    prev_week_copy.credit_utilization_percentage = c_util

                pending_user_snapshot: WeeklySnapshot | None = None
                if weekly_reset_applied and (
                    prev_week_copy.total_tokens > 0 or prev_week_copy.gross_spend_usd > 0
                ):
                    pending_user_snapshot = WeeklySnapshot(
                        week_id=prev_week_copy.week_id or week_id,
                        final_tokens=prev_week_copy.total_tokens,
                        final_gross_cost_usd=prev_week_copy.gross_spend_usd,
                        final_quota_credits_usd=prev_week_copy.quota_credits_usd,
                        final_net_cost_usd=prev_week_copy.net_billable_cost_usd,
                        final_status=user.status.value,
                        snapshot_timestamp=eval_time,
                    )

                # Get usage data
                bq_data = user_bq_data.get(
                    email,
                    {
                        "tokens_by_model": {},
                        "granular_by_model": {},
                        "token_details_by_model": {},
                        "total_requests": 0,
                        "total_tokens": 0,
                        "last_active": user.current_week.last_active,
                    },
                )

                # Calculate costs & credit utilization
                (
                    gross_spend,
                    remaining_credit,
                    net_billable,
                    spend_breakdown,
                    spend_by_model,
                    utilization_pct,
                    _,
                ) = calculate_total_usage_costs(
                    bq_data["tokens_by_model"],
                    config.models,
                    quota_credits,
                    overage_buffer,
                    granular_by_model=bq_data.get("granular_by_model"),
                )

                # Update current week usage object
                user.current_week.week_id = week_id
                user.current_week.total_tokens = bq_data["total_tokens"]
                user.current_week.total_requests = bq_data["total_requests"]
                user.current_week.gross_spend_usd = gross_spend
                user.current_week.quota_credits_usd = quota_credits
                user.current_week.remaining_credit_usd = remaining_credit
                user.current_week.net_billable_cost_usd = net_billable
                user.current_week.overage_buffer_usd = overage_buffer
                user.current_week.spend_breakdown = spend_breakdown
                user.current_week.tokens_by_model = bq_data["tokens_by_model"]
                user.current_week.token_details_by_model = bq_data.get("token_details_by_model", {})
                user.current_week.spend_by_model = spend_by_model
                user.current_week.credit_utilization_percentage = utilization_pct
                if bq_data.get("last_active"):
                    user.current_week.last_active = bq_data["last_active"]

                # Stamp project WTU cycle start/reset onto user's current_week consistently
                user.current_week.wtu_window_start_at = eff_cycle_start
                user.current_week.wtu_window_reset_at = eff_cycle_reset

                # Populate per-user WTU metrics when LocalizedUser (ppu) telemetry matches exact email.
                proj_user_wtu = (
                    proj_wtu.per_user_wtu
                    if (proj_wtu and getattr(proj_wtu, "per_user_wtu", None))
                    else {}
                )
                u_wtu = proj_user_wtu.get(email)
                if u_wtu is not None:
                    user.current_week.wtu_net_usage = float(u_wtu.wtu_consumed)
                    user.current_week.wtu_limit = float(u_wtu.wtu_limit)
                    user.current_week.wtu_utilization_pct = float(u_wtu.wtu_utilization_pct)
                else:
                    user.current_week.wtu_net_usage = None
                    user.current_week.wtu_limit = None
                    user.current_week.wtu_utilization_pct = None

                # Evaluate state and group
                current_in_enabled = email in enabled_members
                current_in_disabled = email in disabled_members
                transition = evaluate_user_state_and_group(
                    current_status=user.status,
                    current_in_enabled_group=current_in_enabled,
                    is_exempt=user.is_exempt,
                    gross_spend_usd=gross_spend,
                    quota_credits_usd=quota_credits,
                    overage_buffer_usd=overage_buffer,
                    is_weekly_reset=weekly_reset_applied,
                    current_in_disabled_group=current_in_disabled,
                )

                swap_succeeded = True
                cycle_key = eff_cycle_start.isoformat()
                if transition.group_changed:
                    # Move user in Cloud Identity (passing dynamic group emails from config)
                    try:
                        swap_result = self.identity.reconcile_user_group(
                            email,
                            transition.target_group,
                            enabled_group=config.enabled_group,
                            disabled_group=config.disabled_group,
                        )
                    except TypeError:
                        swap_result = self.identity.reconcile_user_group(email, transition.target_group)
                    swap_succeeded = False if swap_result is False else True
                    if swap_succeeded:
                        group_swaps_count += 1
                        target_email = (
                            config.enabled_group if transition.target_group == "ENABLED" else config.disabled_group
                        )
                        swap_evt = AuditEvent(
                            event_id=_deterministic_event_id(
                                "swap",
                                "GROUP_SWAP",
                                email,
                                cycle_key,
                                transition.target_group,
                                transition.reason,
                            ),
                            timestamp=eval_time,
                            action="GROUP_SWAP",
                            triggered_by=triggered_by,
                            target_user=email,
                            details={
                                "target_group_email": target_email,
                                "direction": transition.target_group,
                                "reason": transition.reason,
                            },
                        )
                        self.db.add_audit_event(swap_evt)
                        changes_summary.append(
                            {
                                "user": email,
                                "action": f"MOVED_TO_{transition.target_group}",
                                "reason": transition.reason,
                                "status": transition.new_status.value,
                            }
                        )
                    else:
                        failed_swaps_count += 1
                        if weekly_reset_applied:
                            user.current_week = prev_week_copy
                        else:
                            user.current_week.wtu_window_start_at = prev_wtu_start
                            user.current_week.wtu_window_reset_at = prev_wtu_reset
                            user.current_week.week_id = prev_week_id
                        logger.error(
                            f"Cloud Identity group swap failed for {email} -> {transition.target_group}; "
                            "skipping status commit so next evaluation run retries."
                        )

                if swap_succeeded and pending_user_snapshot is not None:
                    pending_snapshots.append((user.email, pending_user_snapshot))

                status_changed = user.status != transition.new_status
                if status_changed and swap_succeeded:
                    old_status = user.status
                    user.status = transition.new_status
                    pending_audit_events.append(
                        AuditEvent(
                            event_id=_deterministic_event_id(
                                "stat",
                                "STATUS_CHANGE",
                                email,
                                cycle_key,
                                old_status.value,
                                transition.new_status.value,
                            ),
                            timestamp=eval_time,
                            action="STATUS_CHANGE",
                            triggered_by=triggered_by,
                            target_user=email,
                            details={
                                "from_status": old_status.value,
                                "to_status": transition.new_status.value,
                                "reason": transition.reason,
                                "gross_spend": gross_spend,
                                "quota": quota_credits,
                            },
                        )
                    )

                updated_users_to_save.append(user)

            # Batch save all updated users first
            self.db.save_users(updated_users_to_save)

            # Persist buffered snapshots and audit events only after user batch save succeeds
            if pending_snapshots:
                if hasattr(self.db, "save_weekly_snapshots_batch"):
                    self.db.save_weekly_snapshots_batch(pending_snapshots)
                else:
                    for snap_email, snapshot in pending_snapshots:
                        self.db.save_weekly_snapshot(snap_email, snapshot)
            if pending_audit_events:
                if hasattr(self.db, "add_audit_events_batch"):
                    self.db.add_audit_events_batch(pending_audit_events)
                else:
                    for audit_evt in pending_audit_events:
                        self.db.add_audit_event(audit_evt)

            # Update last publish timestamp if publish was requested and all group swaps succeeded
            if is_publish:
                if failed_swaps_count == 0:
                    latest_cfg = self.db.get_config()
                    latest_cfg.last_publish_timestamp = eval_time
                    self.db.update_config(latest_cfg)
                self.db.add_audit_event(
                    AuditEvent(
                        event_id=_deterministic_event_id(
                            "pub", "PUBLISH_TRIGGERED", triggered_by, eval_time.isoformat()
                        ),
                        timestamp=eval_time,
                        action="PUBLISH_TRIGGERED",
                        triggered_by=triggered_by,
                        details={
                            "users_evaluated": len(updated_users_to_save),
                            "group_swaps": group_swaps_count,
                            "failed_swaps": failed_swaps_count,
                        },
                    )
                )

            logger.info(
                f"Evaluation completed: {len(updated_users_to_save)} users evaluated, "
                f"{group_swaps_count} group swaps executed."
            )

            eval_kwargs: dict[str, Any] = {
                "evaluated_at": eval_time,
                "week_id": week_id,
                "users_evaluated": len(updated_users_to_save),
                "group_swaps_count": group_swaps_count,
                "changes": changes_summary,
                "success": True,
                "error": (
                    f"{failed_swaps_count} Cloud Identity group swap(s) failed"
                    if failed_swaps_count > 0
                    else None
                ),
            }
            if "failed_swaps_count" in getattr(EvaluationResult, "model_fields", {}):
                eval_kwargs["failed_swaps_count"] = failed_swaps_count
            return EvaluationResult(**eval_kwargs)

        except Exception as e:
            logger.error(f"Quota evaluation failed: {e}", exc_info=True)
            return EvaluationResult(
                evaluated_at=eval_time,
                week_id="",
                users_evaluated=0,
                group_swaps_count=0,
                success=False,
                error=str(e),
            )

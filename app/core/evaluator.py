"""Evaluation engine for weekly quota reconciliation and dual-group governance."""

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.cost_model import calculate_total_usage_costs
from app.core.state_machine import evaluate_user_state_and_group
from app.core.timezone_engine import get_current_week_window, is_new_week
from app.db.models import (
    AppConfig,
    AuditEvent,
    CurrentWeekUsage,
    EvaluationResult,
    User,
    UserStatus,
    WeeklySnapshot,
)

logger = logging.getLogger(__name__)


class QuotaEvaluator:
    """Core evaluation engine that reconciles BigQuery usage against Firestore quotas."""

    def __init__(self, db_client: Any, bq_client: Any, identity_client: Any):
        self.db = db_client
        self.bq = bq_client
        self.identity = identity_client

    def run_evaluation(
        self,
        triggered_by: str = "SYSTEM_WORKER",
        is_publish: bool = False,
    ) -> EvaluationResult:
        """Execute a full quota reconciliation run across all users."""
        eval_time = datetime.now(UTC)
        logger.info(f"Starting quota evaluation run triggered by '{triggered_by}' (is_publish={is_publish})")

        try:
            config: AppConfig = self.db.get_config()
            week_id, start_utc, end_utc, now_utc = get_current_week_window(config.timezone, eval_time)

            # 1. Fetch BigQuery usage for the current weekly window
            usage_records = self.bq.fetch_weekly_usage(start_utc, now_utc)

            # Group usage records by user email
            user_bq_data: dict[str, dict[str, Any]] = {}
            for record in usage_records:
                email = record.user_email.lower().strip()
                if email not in user_bq_data:
                    user_bq_data[email] = {
                        "tokens_by_model": {},
                        "total_requests": 0,
                        "total_tokens": 0,
                        "last_active": None,
                    }
                user_bq_data[email]["tokens_by_model"][record.model_name] = record.token_count
                user_bq_data[email]["total_tokens"] += record.token_count
                user_bq_data[email]["total_requests"] += record.request_count
                if record.last_active:
                    curr_last = user_bq_data[email]["last_active"]
                    if curr_last is None or record.last_active > curr_last:
                        user_bq_data[email]["last_active"] = record.last_active

            # 2. Fetch all known users from Database
            existing_users_list = self.db.list_users()
            users_map: dict[str, User] = {u.email.lower().strip(): u for u in existing_users_list}

            # 3. Discover new users from BigQuery logs (Synthetic Discovery)
            for email in user_bq_data:
                if email not in users_map:
                    new_user = User(
                        email=email,
                        status=UserStatus.ACTIVE,
                        is_exempt=False,
                        has_custom_quota=False,
                        custom_quota_usd=None,
                        custom_overage_usd=None,
                        current_week=CurrentWeekUsage(
                            week_id=week_id,
                            quota_credits_usd=config.default_quota_usd,
                            overage_buffer_usd=config.default_overage_usd,
                        ),
                        created_at=eval_time,
                        updated_at=eval_time,
                    )
                    users_map[email] = new_user
                    # Log discovery audit event
                    self.db.add_audit_event(
                        AuditEvent(
                            event_id=f"evt-disc-{uuid.uuid4().hex[:8]}",
                            timestamp=eval_time,
                            action="USER_DISCOVERED",
                            triggered_by=triggered_by,
                            target_user=email,
                            details={"week_id": week_id, "default_quota": config.default_quota_usd},
                        )
                    )

            # 4. Fetch current group memberships for accuracy
            enabled_members = self.identity.get_group_members(config.enabled_group)

            changes_summary: list[dict[str, Any]] = []
            group_swaps_count = 0
            updated_users_to_save: list[User] = []

            for email, user in users_map.items():
                # Check for Weekly Reset (Monday 00:00 boundary crossover)
                weekly_reset_applied = False
                if is_new_week(user.current_week.week_id, week_id):
                    # Snapshot previous week
                    if user.current_week.week_id:
                        snapshot = WeeklySnapshot(
                            week_id=user.current_week.week_id,
                            final_tokens=user.current_week.total_tokens,
                            final_gross_cost_usd=user.current_week.gross_spend_usd,
                            final_quota_credits_usd=user.current_week.quota_credits_usd,
                            final_net_cost_usd=user.current_week.net_billable_cost_usd,
                            final_status=user.status.value,
                            snapshot_timestamp=eval_time,
                        )
                        self.db.save_weekly_snapshot(user.email, snapshot)

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

                # Get usage data
                bq_data = user_bq_data.get(
                    email,
                    {
                        "tokens_by_model": {},
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
                user.current_week.spend_by_model = spend_by_model
                user.current_week.credit_utilization_percentage = utilization_pct
                if bq_data.get("last_active"):
                    user.current_week.last_active = bq_data["last_active"]

                # Evaluate state and group
                current_in_enabled = email in enabled_members
                transition = evaluate_user_state_and_group(
                    current_status=user.status,
                    current_in_enabled_group=current_in_enabled,
                    is_exempt=user.is_exempt,
                    gross_spend_usd=gross_spend,
                    quota_credits_usd=quota_credits,
                    overage_buffer_usd=overage_buffer,
                    is_weekly_reset=weekly_reset_applied,
                )

                status_changed = user.status != transition.new_status
                if status_changed:
                    old_status = user.status
                    user.status = transition.new_status
                    self.db.add_audit_event(
                        AuditEvent(
                            event_id=f"evt-stat-{uuid.uuid4().hex[:8]}",
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

                if transition.group_changed:
                    # Move user in Cloud Identity
                    self.identity.reconcile_user_group(email, transition.target_group)
                    group_swaps_count += 1
                    target_email = (
                        config.enabled_group if transition.target_group == "ENABLED" else config.disabled_group
                    )
                    self.db.add_audit_event(
                        AuditEvent(
                            event_id=f"evt-swap-{uuid.uuid4().hex[:8]}",
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
                    )
                    changes_summary.append(
                        {
                            "user": email,
                            "action": f"MOVED_TO_{transition.target_group}",
                            "reason": transition.reason,
                            "status": transition.new_status.value,
                        }
                    )

                updated_users_to_save.append(user)

            # Batch save all updated users
            self.db.save_users(updated_users_to_save)

            # Update last publish timestamp if publish was requested
            if is_publish:
                config.last_publish_timestamp = eval_time
                self.db.update_config(config)
                self.db.add_audit_event(
                    AuditEvent(
                        event_id=f"evt-pub-{uuid.uuid4().hex[:8]}",
                        timestamp=eval_time,
                        action="PUBLISH_TRIGGERED",
                        triggered_by=triggered_by,
                        details={
                            "users_evaluated": len(updated_users_to_save),
                            "group_swaps": group_swaps_count,
                        },
                    )
                )

            logger.info(
                f"Evaluation completed: {len(updated_users_to_save)} users evaluated, "
                f"{group_swaps_count} group swaps executed."
            )

            return EvaluationResult(
                evaluated_at=eval_time,
                week_id=week_id,
                users_evaluated=len(updated_users_to_save),
                group_swaps_count=group_swaps_count,
                changes=changes_summary,
                success=True,
            )

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

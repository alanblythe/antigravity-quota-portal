"""User management API endpoints."""

import logging
import re
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_actor, get_db, get_evaluator, get_identity, get_wtu_client
from app.bq.wtu_client import derive_license_tier
from app.config import settings
from app.core.state_machine import evaluate_user_state_and_group
from app.core.timezone_engine import get_current_week_window
from app.db.models import (
    AuditEvent,
    CurrentWeekUsage,
    User,
    UserCreateRequest,
    UserStatus,
    UserUpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/users", tags=["Users"])


def _get_actor(request: Request = None) -> str:
    return get_actor(request, default="admin_ui")


def _reconcile_group(identity, email: str, target_group: str, config) -> bool:
    try:
        try:
            res = identity.reconcile_user_group(
                email,
                target_group,
                enabled_group=config.enabled_group,
                disabled_group=config.disabled_group,
            )
        except TypeError:
            res = identity.reconcile_user_group(email, target_group)
        return False if res is False else True
    except Exception as e:
        logger.error(f"Unexpected error reconciling group for {email}: {e}")
        return False


def _get_group_members(identity, group_email: str) -> set[str]:
    try:
        return identity.get_group_members(group_email, raise_on_error=True)
    except TypeError:
        return identity.get_group_members(group_email)


@router.get("", response_model=list[User])
def list_users(
    search: str | None = Query(None, description="Search by email"),
    status: str | None = Query(None, description="Filter by status (ACTIVE, AUTO_DISABLED, MANUALLY_DISABLED)"),
    project_id: str | None = Query(None, description="Filter by licensed workload project ID"),
    exempt_only: bool | None = Query(None, description="Filter only exempt users"),
    sort_by: str = Query(
        "gross_spend",
        description="Sort field: email, tokens, gross_spend, remaining_credit, utilization",
    ),
    sort_order: str = Query("desc", description="Sort order (asc, desc)"),
    db=Depends(get_db),
):
    users = db.list_users()

    # Filtering
    if search:
        s = search.lower().strip()
        users = [u for u in users if s in u.email.lower()]

    if status:
        st = status.upper().strip()
        users = [u for u in users if u.status.value == st]

    if project_id:
        pid = project_id.strip()
        config = db.get_config()
        monitored = config.monitored_projects or settings.monitored_project_ids
        default_proj = monitored[0] if monitored else settings.PROJECT_ID
        users = [u for u in users if (u.project_id or default_proj) == pid]

    if exempt_only is not None:
        users = [u for u in users if u.is_exempt == exempt_only]

    # Sorting
    reverse = sort_order.lower() == "desc"
    if sort_by == "gross_spend":
        users.sort(key=lambda u: u.current_week.gross_spend_usd, reverse=reverse)
    elif sort_by == "tokens":
        users.sort(key=lambda u: u.current_week.total_tokens, reverse=reverse)
    elif sort_by == "email":
        users.sort(key=lambda u: u.email.lower(), reverse=reverse)
    elif sort_by == "utilization":
        users.sort(key=lambda u: u.current_week.credit_utilization_percentage, reverse=reverse)
    elif sort_by == "remaining_credit":
        users.sort(key=lambda u: u.current_week.remaining_credit_usd, reverse=reverse)

    return users


@router.get("/{email}", response_model=User)
def get_user(email: str, db=Depends(get_db)):
    email = email.lower().strip()
    user = db.get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {email} not found")
    return user


@router.post("", response_model=User, status_code=201)
def create_user(
    payload: UserCreateRequest,
    request: Request = None,
    db=Depends(get_db),
    identity=Depends(get_identity),
    wtu_client=Depends(get_wtu_client),
    evaluator=Depends(get_evaluator),
):
    lock = getattr(evaluator, "_eval_lock", None) or get_evaluator()._eval_lock
    with lock:
        email = payload.email.lower().strip()
        if not email or not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
            raise HTTPException(status_code=422, detail="Invalid email address")

        existing = db.get_user(email)
        if existing:
            raise HTTPException(status_code=400, detail=f"User {email} already exists")

        config = db.get_config()
        now = datetime.now(UTC)
        week_id, start_utc, end_utc, _ = get_current_week_window(config.timezone, now)
        monitored_projects = config.monitored_projects or settings.monitored_project_ids
        default_project = monitored_projects[0] if monitored_projects else settings.PROJECT_ID
        assigned_project = (payload.project_id or "").strip() or default_project

        wtu_start = start_utc
        wtu_reset = end_utc
        detected_tier = payload.license_tier or derive_license_tier(
            config.wtu_tier_metric, assigned_project
        )
        try:
            wtu_st = wtu_client.fetch_wtu_status(
                preferred_tier=config.wtu_tier_metric,
                configured_wtu_limit=int(config.weekly_wtu_quota or 0),
                project_id=assigned_project,
            )
            if wtu_st.active and wtu_st.active_cycle_reset_utc:
                wtu_start = wtu_st.active_cycle_start_utc
                wtu_reset = wtu_st.active_cycle_reset_utc
            if not payload.license_tier and wtu_st.license_tier:
                detected_tier = wtu_st.license_tier
        except Exception as e:
            logger.debug(f"Could not fetch WTU status during create_user for {assigned_project}: {e}")

        quota = (
            payload.custom_quota_usd
            if payload.has_custom_quota and payload.custom_quota_usd is not None
            else config.default_quota_usd
        )
        overage = (
            payload.custom_overage_usd
            if payload.has_custom_quota and payload.custom_overage_usd is not None
            else config.default_overage_usd
        )

        new_user = User(
            email=email,
            status=UserStatus.ACTIVE,
            is_exempt=payload.is_exempt,
            has_custom_quota=payload.has_custom_quota,
            custom_quota_usd=payload.custom_quota_usd if payload.has_custom_quota else None,
            custom_overage_usd=payload.custom_overage_usd if payload.has_custom_quota else None,
            project_id=assigned_project,
            license_tier=detected_tier,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                quota_credits_usd=quota,
                remaining_credit_usd=quota,
                overage_buffer_usd=overage,
                wtu_window_start_at=wtu_start,
                wtu_window_reset_at=wtu_reset,
            ),
            created_at=now,
            updated_at=now,
        )

        if not _reconcile_group(identity, new_user.email, "ENABLED", config):
            raise HTTPException(
                status_code=502,
                detail="Failed to reconcile Cloud Identity group membership",
            )
        db.save_user(new_user)

        if assigned_project:
            try:
                from app.main import schedule_project_wtu_reset_triggers

                schedule_project_wtu_reset_triggers()
            except Exception as e:
                logger.warning(f"Failed to reschedule WTU reset triggers after create_user: {e}")

        db.add_audit_event(
            AuditEvent(
                event_id=f"evt-create-{uuid.uuid4().hex[:8]}",
                timestamp=now,
                action="USER_PREPROVISION",
                triggered_by=_get_actor(request),
                target_user=new_user.email,
                details=payload.model_dump(),
            )
        )

        return new_user


@router.patch("/{email}", response_model=User)
def update_user(
    email: str,
    payload: UserUpdateRequest,
    request: Request = None,
    db=Depends(get_db),
    identity=Depends(get_identity),
    wtu_client=Depends(get_wtu_client),
    evaluator=Depends(get_evaluator),
):
    lock = getattr(evaluator, "_eval_lock", None) or get_evaluator()._eval_lock
    with lock:
        email = email.lower().strip()
        user = db.get_user(email)
        if not user:
            raise HTTPException(status_code=404, detail=f"User {email} not found")

        if payload.status == UserStatus.AUTO_DISABLED:
            raise HTTPException(
                status_code=422,
                detail="Cannot manually set status to AUTO_DISABLED; use MANUALLY_DISABLED or ACTIVE",
            )

        config = db.get_config()
        now = datetime.now(UTC)
        actor = _get_actor(request)

        # Track updates for audit
        update_details = {}
        project_changed = False

        if payload.is_exempt is not None and payload.is_exempt != user.is_exempt:
            update_details["is_exempt"] = {"from": user.is_exempt, "to": payload.is_exempt}
            user.is_exempt = payload.is_exempt

        if payload.has_custom_quota is not None and payload.has_custom_quota != user.has_custom_quota:
            update_details["has_custom_quota"] = payload.has_custom_quota
            user.has_custom_quota = payload.has_custom_quota

        if not user.has_custom_quota:
            if user.custom_quota_usd is not None:
                update_details["custom_quota_usd"] = None
            if user.custom_overage_usd is not None:
                update_details["custom_overage_usd"] = None
            user.custom_quota_usd = None
            user.custom_overage_usd = None
        else:
            if (
                payload.custom_quota_usd is not None
                and payload.custom_quota_usd != user.custom_quota_usd
            ):
                update_details["custom_quota_usd"] = payload.custom_quota_usd
                user.custom_quota_usd = payload.custom_quota_usd

            if (
                payload.custom_overage_usd is not None
                and payload.custom_overage_usd != user.custom_overage_usd
            ):
                update_details["custom_overage_usd"] = payload.custom_overage_usd
                user.custom_overage_usd = payload.custom_overage_usd

        if "project_id" in payload.model_fields_set:
            monitored = config.monitored_projects or settings.monitored_project_ids
            default_proj = monitored[0] if monitored else settings.PROJECT_ID
            new_proj = (
                payload.project_id.strip()
                if payload.project_id and payload.project_id.strip()
                else default_proj
            )
            if new_proj and new_proj != user.project_id:
                project_changed = True
                update_details["project_id"] = {"from": user.project_id, "to": new_proj}
                user.project_id = new_proj
                _, fallback_start, fallback_end, _ = get_current_week_window(config.timezone, now)
                wtu_start = fallback_start
                wtu_reset = fallback_end
                u_tier = derive_license_tier(config.wtu_tier_metric, new_proj)
                try:
                    wtu_status = wtu_client.fetch_wtu_status(
                        preferred_tier=config.wtu_tier_metric,
                        configured_wtu_limit=int(config.weekly_wtu_quota or 0),
                        project_id=new_proj,
                    )
                    if wtu_status.active and wtu_status.active_cycle_reset_utc:
                        wtu_start = wtu_status.active_cycle_start_utc or fallback_start
                        wtu_reset = wtu_status.active_cycle_reset_utc
                    wtu_metric = (
                        getattr(wtu_status, "wtu_tier_metric", None)
                        or getattr(wtu_status, "tier_metric", None)
                        or ""
                    )
                    if wtu_metric:
                        u_tier = (
                            "ENTERPRISE_STANDARD"
                            if "standard" in wtu_metric.lower()
                            else derive_license_tier(wtu_metric, new_proj)
                        )
                    elif getattr(wtu_status, "license_tier", None):
                        u_tier = wtu_status.license_tier
                except Exception as e:
                    logger.debug(f"Could not fetch WTU status during update_user for {new_proj}: {e}")
                user.current_week.wtu_window_start_at = wtu_start
                user.current_week.wtu_window_reset_at = wtu_reset
                if payload.license_tier is None:
                    if user.license_tier != u_tier:
                        update_details["license_tier"] = {"from": user.license_tier, "to": u_tier}
                    user.license_tier = u_tier

        if payload.license_tier is not None and payload.license_tier != user.license_tier:
            update_details["license_tier"] = {"from": user.license_tier, "to": payload.license_tier}
            user.license_tier = payload.license_tier

        # Recalculate credit and net spend based on new quota
        effective_quota = (
            user.custom_quota_usd
            if user.has_custom_quota and user.custom_quota_usd is not None
            else config.default_quota_usd
        )
        effective_overage = (
            user.custom_overage_usd
            if user.has_custom_quota and user.custom_overage_usd is not None
            else config.default_overage_usd
        )

        user.current_week.quota_credits_usd = effective_quota
        user.current_week.overage_buffer_usd = effective_overage
        user.current_week.remaining_credit_usd = max(
            0.0, round(effective_quota - user.current_week.gross_spend_usd, 2)
        )
        user.current_week.net_billable_cost_usd = max(
            0.0, round(user.current_week.gross_spend_usd - effective_quota, 2)
        )
        if effective_quota > 0:
            user.current_week.credit_utilization_percentage = round(
                (user.current_week.gross_spend_usd / effective_quota) * 100.0, 2
            )
        else:
            user.current_week.credit_utilization_percentage = (
                100.0 if user.current_week.gross_spend_usd > 0 else 0.0
            )

        # Evaluate state & reconcile group
        if (
            payload.status == UserStatus.MANUALLY_DISABLED
            and user.status != UserStatus.MANUALLY_DISABLED
        ):
            old_status = user.status
            if not _reconcile_group(identity, user.email, "DISABLED", config):
                raise HTTPException(
                    status_code=502,
                    detail="Failed to reconcile Cloud Identity group membership",
                )
            user.status = UserStatus.MANUALLY_DISABLED
            update_details["status"] = {"from": old_status.value, "to": user.status.value}
        else:
            was_manually_disabled = (
                user.status == UserStatus.MANUALLY_DISABLED and payload.status == UserStatus.ACTIVE
            )
            eval_base_status = UserStatus.ACTIVE if was_manually_disabled else user.status
            try:
                enabled_members = _get_group_members(identity, config.enabled_group)
                disabled_members = _get_group_members(identity, config.disabled_group)
            except Exception as e:
                raise HTTPException(
                    status_code=502,
                    detail="Failed to reconcile Cloud Identity group membership",
                ) from e

            norm_email = user.email.lower().strip()
            transition = evaluate_user_state_and_group(
                current_status=eval_base_status,
                current_in_enabled_group=norm_email in enabled_members,
                is_exempt=user.is_exempt,
                gross_spend_usd=user.current_week.gross_spend_usd,
                quota_credits_usd=effective_quota,
                overage_buffer_usd=effective_overage,
                is_weekly_reset=False,
                current_in_disabled_group=norm_email in disabled_members,
            )
            if transition.group_changed:
                if not _reconcile_group(identity, user.email, transition.target_group, config):
                    raise HTTPException(
                        status_code=502,
                        detail="Failed to reconcile Cloud Identity group membership",
                    )
            if user.status != transition.new_status:
                update_details["status"] = {
                    "from": user.status.value,
                    "to": transition.new_status.value,
                }
                user.status = transition.new_status

        user.updated_at = now
        db.save_user(user)

        if project_changed:
            try:
                from app.main import schedule_project_wtu_reset_triggers

                schedule_project_wtu_reset_triggers()
            except Exception as e:
                logger.warning(f"Failed to reschedule WTU reset triggers after update_user: {e}")

        if update_details:
            db.add_audit_event(
                AuditEvent(
                    event_id=f"evt-upd-{uuid.uuid4().hex[:8]}",
                    timestamp=now,
                    action="QUOTA_UPDATE",
                    triggered_by=actor,
                    target_user=user.email,
                    details=update_details,
                )
            )

        return user


@router.post("/{email}/lock", response_model=User)
def toggle_user_lock(
    email: str,
    request: Request = None,
    db=Depends(get_db),
    identity=Depends(get_identity),
    evaluator=Depends(get_evaluator),
):
    """Toggle manual lock state on user."""
    lock = getattr(evaluator, "_eval_lock", None) or get_evaluator()._eval_lock
    with lock:
        email = email.lower().strip()
        user = db.get_user(email)
        if not user:
            raise HTTPException(status_code=404, detail=f"User {email} not found")

        config = db.get_config()
        now = datetime.now(UTC)
        actor = _get_actor(request)

        if user.status == UserStatus.MANUALLY_DISABLED:
            # Unlock user -> evaluate against current spend/quota/exemption
            effective_quota = (
                user.custom_quota_usd
                if user.has_custom_quota and user.custom_quota_usd is not None
                else config.default_quota_usd
            )
            effective_overage = (
                user.custom_overage_usd
                if user.has_custom_quota and user.custom_overage_usd is not None
                else config.default_overage_usd
            )
            try:
                enabled_members = _get_group_members(identity, config.enabled_group)
                disabled_members = _get_group_members(identity, config.disabled_group)
            except Exception as e:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to query Cloud Identity group membership: {e}",
                ) from e
            norm_email = user.email.lower().strip()
            transition = evaluate_user_state_and_group(
                current_status=UserStatus.ACTIVE,
                current_in_enabled_group=norm_email in enabled_members,
                is_exempt=user.is_exempt,
                gross_spend_usd=user.current_week.gross_spend_usd,
                quota_credits_usd=effective_quota,
                overage_buffer_usd=effective_overage,
                is_weekly_reset=False,
                current_in_disabled_group=norm_email in disabled_members,
            )
            if not _reconcile_group(identity, user.email, transition.target_group, config):
                raise HTTPException(
                    status_code=502,
                    detail="Failed to reconcile Cloud Identity group membership",
                )
            user.status = transition.new_status
            action_name = "MANUAL_UNLOCK"
        else:
            # Lock user -> MANUALLY_DISABLED
            if not _reconcile_group(identity, user.email, "DISABLED", config):
                raise HTTPException(
                    status_code=502,
                    detail="Failed to reconcile Cloud Identity group membership",
                )
            user.status = UserStatus.MANUALLY_DISABLED
            action_name = "MANUAL_LOCK"

        user.updated_at = now
        db.save_user(user)
        db.add_audit_event(
            AuditEvent(
                event_id=f"evt-lock-{uuid.uuid4().hex[:8]}",
                timestamp=now,
                action=action_name,
                triggered_by=actor,
                target_user=user.email,
                details={"new_status": user.status.value},
            )
        )

        return user

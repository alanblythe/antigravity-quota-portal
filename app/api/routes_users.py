"""User management API endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_db, get_identity
from app.core.timezone_engine import get_current_week_window
from app.db.models import (
    AuditEvent,
    CurrentWeekUsage,
    User,
    UserCreateRequest,
    UserStatus,
    UserUpdateRequest,
)

router = APIRouter(prefix="/api/users", tags=["Users"])


@router.get("", response_model=list[User])
def list_users(
    search: str | None = Query(None, description="Search by email"),
    status: str | None = Query(None, description="Filter by status (ACTIVE, AUTO_DISABLED, MANUALLY_DISABLED)"),
    exempt_only: bool | None = Query(None, description="Filter only exempt users"),
    sort_by: str = Query("gross_spend", description="Field to sort by (gross_spend, tokens, email, utilization)"),
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
    user = db.get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {email} not found")
    return user


@router.post("", response_model=User, status_code=201)
def create_user(payload: UserCreateRequest, db=Depends(get_db), identity=Depends(get_identity)):
    existing = db.get_user(payload.email)
    if existing:
        raise HTTPException(status_code=400, detail=f"User {payload.email} already exists")

    config = db.get_config()
    now = datetime.now(UTC)
    week_id, _, _, _ = get_current_week_window(config.timezone, now)

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
        email=payload.email.lower().strip(),
        status=UserStatus.ACTIVE,
        is_exempt=payload.is_exempt,
        has_custom_quota=payload.has_custom_quota,
        custom_quota_usd=payload.custom_quota_usd if payload.has_custom_quota else None,
        custom_overage_usd=payload.custom_overage_usd if payload.has_custom_quota else None,
        current_week=CurrentWeekUsage(
            week_id=week_id,
            quota_credits_usd=quota,
            remaining_credit_usd=quota,
            overage_buffer_usd=overage,
        ),
        created_at=now,
        updated_at=now,
    )

    db.save_user(new_user)
    identity.reconcile_user_group(new_user.email, "ENABLED")

    db.add_audit_event(
        AuditEvent(
            event_id=f"evt-create-{uuid.uuid4().hex[:8]}",
            timestamp=now,
            action="USER_PREPROVISION",
            triggered_by="admin_ui",
            target_user=new_user.email,
            details=payload.model_dump(),
        )
    )

    return new_user


@router.patch("/{email}", response_model=User)
def update_user(
    email: str,
    payload: UserUpdateRequest,
    db=Depends(get_db),
    identity=Depends(get_identity),
):
    user = db.get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {email} not found")

    config = db.get_config()
    now = datetime.now(UTC)

    # Track updates for audit
    update_details = {}

    if payload.is_exempt is not None and payload.is_exempt != user.is_exempt:
        update_details["is_exempt"] = {"from": user.is_exempt, "to": payload.is_exempt}
        user.is_exempt = payload.is_exempt
        if user.is_exempt and user.status == UserStatus.AUTO_DISABLED:
            user.status = UserStatus.ACTIVE
            identity.reconcile_user_group(user.email, "ENABLED")

    if payload.status is not None and payload.status != user.status:
        update_details["status"] = {"from": user.status.value, "to": payload.status.value}
        user.status = payload.status
        if user.status == UserStatus.MANUALLY_DISABLED:
            identity.reconcile_user_group(user.email, "DISABLED")
        elif user.status == UserStatus.ACTIVE:
            identity.reconcile_user_group(user.email, "ENABLED")

    if payload.has_custom_quota is not None:
        user.has_custom_quota = payload.has_custom_quota
        update_details["has_custom_quota"] = payload.has_custom_quota

    if payload.custom_quota_usd is not None:
        user.custom_quota_usd = payload.custom_quota_usd
        update_details["custom_quota_usd"] = payload.custom_quota_usd

    if payload.custom_overage_usd is not None:
        user.custom_overage_usd = payload.custom_overage_usd
        update_details["custom_overage_usd"] = payload.custom_overage_usd

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
    user.current_week.remaining_credit_usd = max(0.0, round(effective_quota - user.current_week.gross_spend_usd, 2))
    user.current_week.net_billable_cost_usd = max(0.0, round(user.current_week.gross_spend_usd - effective_quota, 2))
    if effective_quota > 0:
        user.current_week.credit_utilization_percentage = round(
            (user.current_week.gross_spend_usd / effective_quota) * 100.0, 2
        )

    db.save_user(user)

    if update_details:
        db.add_audit_event(
            AuditEvent(
                event_id=f"evt-upd-{uuid.uuid4().hex[:8]}",
                timestamp=now,
                action="QUOTA_UPDATE",
                triggered_by="admin_ui",
                target_user=user.email,
                details=update_details,
            )
        )

    return user


@router.post("/{email}/lock", response_model=User)
def toggle_user_lock(email: str, db=Depends(get_db), identity=Depends(get_identity)):
    """Toggle manual lock state on user."""
    user = db.get_user(email)
    if not user:
        raise HTTPException(status_code=404, detail=f"User {email} not found")

    now = datetime.now(UTC)
    if user.status == UserStatus.MANUALLY_DISABLED:
        # Unlock user -> ACTIVE
        user.status = UserStatus.ACTIVE
        identity.reconcile_user_group(user.email, "ENABLED")
        action_name = "MANUAL_UNLOCK"
    else:
        # Lock user -> MANUALLY_DISABLED
        user.status = UserStatus.MANUALLY_DISABLED
        identity.reconcile_user_group(user.email, "DISABLED")
        action_name = "MANUAL_LOCK"

    db.save_user(user)
    db.add_audit_event(
        AuditEvent(
            event_id=f"evt-lock-{uuid.uuid4().hex[:8]}",
            timestamp=now,
            action=action_name,
            triggered_by="admin_ui",
            target_user=user.email,
            details={"new_status": user.status.value},
        )
    )

    return user

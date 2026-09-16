"""Audit trail event API endpoints."""

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_db
from app.db.models import AuditEvent

router = APIRouter(prefix="/api/audit", tags=["Audit"])


@router.get("", response_model=list[AuditEvent])
def list_audit_events(
    limit: int = Query(50, ge=1, le=500),
    action: str | None = Query(None, description="Filter by action type"),
    user: str | None = Query(None, description="Filter by target user email"),
    db=Depends(get_db),
):
    events = db.list_audit_events(limit=limit)

    if action:
        act = action.upper().strip()
        events = [e for e in events if e.action.upper() == act]

    if user:
        u = user.lower().strip()
        events = [e for e in events if e.target_user and u in e.target_user.lower()]

    return events

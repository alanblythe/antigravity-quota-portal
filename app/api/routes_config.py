"""Application configuration and model pricing API endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends

from app.api.deps import get_db
from app.db.models import (
    AppConfig,
    AuditEvent,
    ConfigUpdateRequest,
    ModelPricing,
)

router = APIRouter(prefix="/api/config", tags=["Config"])


@router.get("", response_model=AppConfig)
def get_config(db=Depends(get_db)):
    return db.get_config()


@router.put("", response_model=AppConfig)
def update_config(payload: ConfigUpdateRequest, db=Depends(get_db)):
    current = db.get_config()
    update_data = payload.model_dump(exclude_unset=True)

    for key, value in update_data.items():
        if value is not None:
            setattr(current, key, value)

    updated = db.update_config(current)
    db.add_audit_event(
        AuditEvent(
            event_id=f"evt-cfg-{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now(UTC),
            action="CONFIG_UPDATE",
            triggered_by="admin_ui",
            details=update_data,
        )
    )
    return updated


@router.get("/pricing", response_model=dict[str, ModelPricing])
def get_pricing_matrix(db=Depends(get_db)):
    config = db.get_config()
    return config.models


@router.put("/pricing", response_model=dict[str, ModelPricing])
def update_pricing_matrix(matrix: dict[str, ModelPricing], db=Depends(get_db)):
    config = db.get_config()
    config.models = matrix
    db.update_config(config)

    db.add_audit_event(
        AuditEvent(
            event_id=f"evt-price-{uuid.uuid4().hex[:8]}",
            timestamp=datetime.now(UTC),
            action="PRICING_MATRIX_UPDATE",
            triggered_by="admin_ui",
            details={"updated_models": list(matrix.keys())},
        )
    )
    return config.models

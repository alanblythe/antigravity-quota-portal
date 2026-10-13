"""Pydantic schemas and data models for Antigravity Quota Portal."""

import json
import logging
import math
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.config import settings

logger = logging.getLogger(__name__)


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    AUTO_DISABLED = "AUTO_DISABLED"
    MANUALLY_DISABLED = "MANUALLY_DISABLED"


class ModelPricing(BaseModel):
    display_name: str
    log_pattern: str
    input_price_per_million: float = Field(ge=0.0)
    output_price_per_million: float = Field(ge=0.0)
    cached_price_per_million: float = Field(ge=0.0)
    context_tier_threshold_tokens: int | None = Field(default=None, ge=0)
    input_price_gt_threshold_per_million: float | None = Field(default=None, ge=0.0)
    output_price_gt_threshold_per_million: float | None = Field(default=None, ge=0.0)
    cached_price_gt_threshold_per_million: float | None = Field(default=None, ge=0.0)
    estimated_input_ratio: float = Field(default=0.70, ge=0.0, le=1.0)
    estimated_output_ratio: float = Field(default=0.20, ge=0.0, le=1.0)
    estimated_cached_ratio: float = Field(default=0.10, ge=0.0, le=1.0)


_DEFAULT_FALLBACK_PRICING = ModelPricing(
    display_name="Default Model Fallback",
    log_pattern="%",
    input_price_per_million=0.75,
    output_price_per_million=3.75,
    cached_price_per_million=0.075,
    estimated_input_ratio=0.80,
    estimated_output_ratio=0.15,
    estimated_cached_ratio=0.05,
)


def load_default_model_pricing(config_path: str | Path | None = None) -> dict[str, ModelPricing]:
    """Load default model pricing catalog from external JSON configuration file."""
    resolved_path = Path(config_path or settings.MODEL_PRICING_CONFIG_PATH)
    if not resolved_path.is_absolute():
        repo_root = Path(__file__).resolve().parent.parent.parent
        candidate = repo_root / resolved_path
        if candidate.exists():
            resolved_path = candidate

    try:
        raw = json.loads(resolved_path.read_text(encoding="utf-8"))
        models_raw = raw.get("models", raw) if isinstance(raw, dict) else {}
        parsed: dict[str, ModelPricing] = {}
        if isinstance(models_raw, dict):
            for key, val in models_raw.items():
                if not isinstance(val, dict):
                    continue
                try:
                    parsed[key] = ModelPricing(**val)
                except Exception as item_err:
                    logger.warning(f"Skipping invalid model pricing entry '{key}' in {resolved_path}: {item_err}")
        if "default_fallback" not in parsed:
            parsed["default_fallback"] = _DEFAULT_FALLBACK_PRICING.model_copy(deep=True)
        return parsed
    except Exception as e:
        logger.error(f"Failed to load model pricing config from {resolved_path}: {e}")
        return {"default_fallback": _DEFAULT_FALLBACK_PRICING.model_copy(deep=True)}


DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = load_default_model_pricing()


def merge_model_pricing_with_defaults(
    custom_models: Mapping[str, Any] | None,
) -> dict[str, ModelPricing]:
    """Merge persisted or custom model pricing entries onto the default model pricing catalog."""
    models: dict[str, ModelPricing] = {
        k: v.model_copy(deep=True) for k, v in load_default_model_pricing().items()
    }
    if isinstance(custom_models, Mapping):
        for k, v in custom_models.items():
            try:
                v_dict = (
                    v.model_dump()
                    if isinstance(v, ModelPricing)
                    else (dict(v) if isinstance(v, Mapping) else None)
                )
                if v_dict is not None:
                    if k in models:
                        base_dump = models[k].model_dump()
                        overrides = {
                            mk: mv
                            for mk, mv in v_dict.items()
                            if mv is not None or base_dump.get(mk) is None
                        }
                        merged = {**base_dump, **overrides}
                        models[k] = ModelPricing(**merged)
                    else:
                        models[k] = ModelPricing(**v_dict)
            except Exception as e:
                logger.warning(f"Skipping invalid model pricing entry {k}: {e}")
    if not models:
        models = {k: v.model_copy(deep=True) for k, v in DEFAULT_MODEL_PRICING.items()}
    elif "default_fallback" not in models:
        models["default_fallback"] = _DEFAULT_FALLBACK_PRICING.model_copy(deep=True)
    return models


class ModelTokenUsage(BaseModel):
    total_tokens: int = 0
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    prompt_tokens_gt_200k: int = 0
    candidates_tokens_gt_200k: int = 0
    cached_tokens_gt_200k: int = 0
    thoughts_tokens_gt_200k: int = 0
    fallback_tokens: int = 0


class SpendBreakdown(BaseModel):
    input_spend_usd: float = 0.0
    output_spend_usd: float = 0.0
    cached_spend_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0


class CurrentWeekUsage(BaseModel):
    week_id: str = ""
    total_tokens: int = 0
    total_requests: int = 0
    gross_spend_usd: float = 0.0
    quota_credits_usd: float = 10.00
    remaining_credit_usd: float = 10.00
    net_billable_cost_usd: float = 0.0
    overage_buffer_usd: float = 2.00
    spend_breakdown: SpendBreakdown = Field(default_factory=SpendBreakdown)
    tokens_by_model: dict[str, int] = Field(default_factory=dict)
    token_details_by_model: dict[str, ModelTokenUsage] = Field(default_factory=dict)
    spend_by_model: dict[str, float] = Field(default_factory=dict)
    credit_utilization_percentage: float = 0.0
    last_active: datetime | None = None
    wtu_net_usage: float | None = None
    wtu_limit: float | None = None
    wtu_utilization_pct: float | None = None
    wtu_window_start_at: datetime | None = None
    wtu_window_reset_at: datetime | None = None


class ProjectWTUPoolStatus(BaseModel):
    project_id: str
    tier_metric: str = ""
    license_tier: str = "ENTERPRISE_PLUS"
    active: bool = False
    wtu_consumed: int = 0
    wtu_limit: int = 0
    wtu_utilization_pct: float = 0.0
    cycle_start_utc: datetime | None = None
    cycle_reset_utc: datetime | None = None
    days_until_reset: int = 0
    hours_until_reset: int = 0
    active_developers: int = 0
    throttled_developers: int = 0
    cycle_spend_usd: float = 0.0

    @field_validator("days_until_reset", "hours_until_reset", mode="before")
    @classmethod
    def _coerce_int_countdown(cls, v: Any) -> int:
        if v is None:
            return 0
        try:
            f = float(v)
            return max(0, int(f)) if math.isfinite(f) else 0
        except (ValueError, TypeError, OverflowError):
            return 0


class WeeklySnapshot(BaseModel):
    week_id: str
    final_tokens: int
    final_gross_cost_usd: float
    final_quota_credits_usd: float
    final_net_cost_usd: float
    final_status: str
    snapshot_timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class User(BaseModel):
    email: str
    status: UserStatus = UserStatus.ACTIVE
    is_exempt: bool = False
    has_custom_quota: bool = False
    custom_quota_usd: float | None = None
    custom_overage_usd: float | None = None
    project_id: str | None = None
    license_tier: str | None = None
    current_week: CurrentWeekUsage = Field(default_factory=lambda: CurrentWeekUsage(week_id=""))
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AppConfig(BaseModel):
    doc_id: str = "app_config"
    timezone: str = Field(default_factory=lambda: settings.APP_TIMEZONE)
    enabled_group: str = Field(default_factory=lambda: settings.ENABLED_GROUP_EMAIL)
    disabled_group: str = Field(default_factory=lambda: settings.DISABLED_GROUP_EMAIL)
    default_quota_usd: float = Field(default_factory=lambda: settings.DEFAULT_QUOTA_USD)
    default_overage_usd: float = Field(default_factory=lambda: settings.DEFAULT_OVERAGE_USD)
    weekly_team_budget_usd: float = 100.00
    weekly_wtu_quota: float = 0.0
    wtu_tier_metric: str = "agentic_wtu_tier_enterprise_plus_pp_weekly_regional"
    monitored_projects: list[str] = Field(default_factory=lambda: settings.monitored_project_ids)
    preset_quotas: list[float] = Field(
        default_factory=lambda: list(getattr(settings, "preset_quotas_list", settings.PRESET_QUOTAS))
    )
    webhook_alert_url: str = Field(default_factory=lambda: settings.WEBHOOK_ALERT_URL)
    last_publish_timestamp: datetime | None = None
    models: dict[str, ModelPricing] = Field(
        default_factory=lambda: {k: v.model_copy(deep=True) for k, v in DEFAULT_MODEL_PRICING.items()}
    )


class AuditEvent(BaseModel):
    event_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    action: str  # e.g., "GROUP_SWAP", "QUOTA_UPDATE", "PUBLISH_TRIGGERED", "STATUS_CHANGE", "USER_PREPROVISION"
    triggered_by: str  # e.g., "SYSTEM_WORKER", "admin@company.com"
    target_user: str | None = None
    details: dict = Field(default_factory=dict)


class KPIStats(BaseModel):
    active_developers: int = 0
    auto_throttled_developers: int = 0
    manually_locked_developers: int = 0
    exempt_developers: int = 0
    total_tokens_this_week: int = 0
    total_gross_usage_usd: float = 0.0
    total_credits_allocated_usd: float = 0.0
    total_net_overages_usd: float = 0.0
    week_id: str = ""
    week_start_utc: datetime | None = None
    week_reset_utc: datetime | None = None
    days_until_reset: int = 0
    hours_until_reset: int = 0
    weekly_team_budget_usd: float = 100.0
    weekly_budget_utilization_pct: float = 0.0
    wtu_pool_net_usage: float | None = None
    wtu_pool_limit: float | None = None
    wtu_pool_utilization_pct: float | None = None
    wtu_window_start_utc: datetime | None = None
    wtu_window_reset_utc: datetime | None = None
    wtu_user_usage: dict[str, dict] = Field(default_factory=dict)
    monitored_projects: list[str] = Field(default_factory=list)
    project_pools: list[ProjectWTUPoolStatus] = Field(default_factory=list)

    @field_validator("days_until_reset", "hours_until_reset", mode="before")
    @classmethod
    def _coerce_int_countdown(cls, v: Any) -> int:
        if v is None:
            return 0
        try:
            f = float(v)
            return max(0, int(f)) if math.isfinite(f) else 0
        except (ValueError, TypeError, OverflowError):
            return 0


class UserUpdateRequest(BaseModel):
    is_exempt: bool | None = None
    status: UserStatus | None = None
    has_custom_quota: bool | None = None
    custom_quota_usd: float | None = Field(default=None, ge=0.0)
    custom_overage_usd: float | None = Field(default=None, ge=0.0)
    project_id: str | None = None
    license_tier: str | None = None


class UserCreateRequest(BaseModel):
    email: str
    is_exempt: bool = False
    has_custom_quota: bool = False
    custom_quota_usd: float | None = Field(default=None, ge=0.0)
    custom_overage_usd: float | None = Field(default=None, ge=0.0)
    project_id: str | None = None
    license_tier: str | None = None


class ConfigUpdateRequest(BaseModel):
    timezone: str | None = None
    enabled_group: str | None = None
    disabled_group: str | None = None
    default_quota_usd: float | None = Field(default=None, ge=0.0)
    default_overage_usd: float | None = Field(default=None, ge=0.0)
    weekly_team_budget_usd: float | None = Field(default=None, ge=0.0)
    weekly_wtu_quota: float | None = Field(default=None, ge=0.0)
    wtu_tier_metric: str | None = None
    monitored_projects: list[str] | None = None
    preset_quotas: list[float] | None = None
    webhook_alert_url: str | None = None


class EvaluationResult(BaseModel):
    evaluated_at: datetime
    week_id: str
    users_evaluated: int
    group_swaps_count: int
    failed_swaps_count: int = 0
    changes: list[dict] = Field(default_factory=list)
    success: bool = True
    error: str | None = None

"""Pydantic schemas and data models for Antigravity Quota Portal."""

from datetime import UTC, datetime
from enum import Enum

from pydantic import BaseModel, Field


class UserStatus(str, Enum):
    ACTIVE = "ACTIVE"
    AUTO_DISABLED = "AUTO_DISABLED"
    MANUALLY_DISABLED = "MANUALLY_DISABLED"


class ModelPricing(BaseModel):
    display_name: str
    log_pattern: str
    input_price_per_million: float
    output_price_per_million: float
    cached_price_per_million: float
    estimated_input_ratio: float
    estimated_output_ratio: float
    estimated_cached_ratio: float


DEFAULT_MODEL_PRICING: dict[str, ModelPricing] = {
    "gemini-3-pro": ModelPricing(
        display_name="Gemini 3.1 Pro / 3.0 Pro",
        log_pattern="%3%pro%",
        input_price_per_million=1.25,
        output_price_per_million=5.00,
        cached_price_per_million=0.30,
        estimated_input_ratio=0.70,
        estimated_output_ratio=0.20,
        estimated_cached_ratio=0.10,
    ),
    "gemini-2.5-pro": ModelPricing(
        display_name="Gemini 2.5 Pro",
        log_pattern="%2.5%pro%",
        input_price_per_million=1.25,
        output_price_per_million=5.00,
        cached_price_per_million=0.30,
        estimated_input_ratio=0.70,
        estimated_output_ratio=0.20,
        estimated_cached_ratio=0.10,
    ),
    "gemini-3.5-flash": ModelPricing(
        display_name="Gemini 3.5 Flash",
        log_pattern="%3.5%flash%",
        input_price_per_million=0.15,
        output_price_per_million=0.60,
        cached_price_per_million=0.0375,
        estimated_input_ratio=0.75,
        estimated_output_ratio=0.20,
        estimated_cached_ratio=0.05,
    ),
    "gemini-3.6-flash": ModelPricing(
        display_name="Gemini 3.6 / 3.7 Flash",
        log_pattern="%3.6%flash%",
        input_price_per_million=0.075,
        output_price_per_million=0.30,
        cached_price_per_million=0.01875,
        estimated_input_ratio=0.80,
        estimated_output_ratio=0.15,
        estimated_cached_ratio=0.05,
    ),
    "gemini-2.5-flash": ModelPricing(
        display_name="Gemini 2.5 Flash",
        log_pattern="%2.5%flash%",
        input_price_per_million=0.075,
        output_price_per_million=0.30,
        cached_price_per_million=0.01875,
        estimated_input_ratio=0.80,
        estimated_output_ratio=0.15,
        estimated_cached_ratio=0.05,
    ),
    "default_fallback": ModelPricing(
        display_name="Default Model Fallback",
        log_pattern="default",
        input_price_per_million=0.15,
        output_price_per_million=0.60,
        cached_price_per_million=0.0375,
        estimated_input_ratio=0.75,
        estimated_output_ratio=0.20,
        estimated_cached_ratio=0.05,
    ),
}


class SpendBreakdown(BaseModel):
    input_spend_usd: float = 0.0
    output_spend_usd: float = 0.0
    cached_spend_usd: float = 0.0


class CurrentWeekUsage(BaseModel):
    week_id: str
    total_tokens: int = 0
    total_requests: int = 0
    gross_spend_usd: float = 0.0
    quota_credits_usd: float = 10.00
    remaining_credit_usd: float = 10.00
    net_billable_cost_usd: float = 0.0
    overage_buffer_usd: float = 2.00
    spend_breakdown: SpendBreakdown = Field(default_factory=SpendBreakdown)
    tokens_by_model: dict[str, int] = Field(default_factory=dict)
    spend_by_model: dict[str, float] = Field(default_factory=dict)
    credit_utilization_percentage: float = 0.0
    last_active: datetime | None = None


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
    current_week: CurrentWeekUsage
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class AppConfig(BaseModel):
    doc_id: str = "app_config"
    timezone: str = "America/Los_Angeles"
    enabled_group: str = "antigravity-enabled@example.com"
    disabled_group: str = "antigravity-disabled@example.com"
    default_quota_usd: float = 10.00
    default_overage_usd: float = 2.00
    preset_quotas: list[float] = Field(default_factory=lambda: [10.0, 15.0, 25.0, 50.0, 100.0])
    webhook_alert_url: str = ""
    last_publish_timestamp: datetime | None = None
    models: dict[str, ModelPricing] = Field(default_factory=lambda: DEFAULT_MODEL_PRICING)


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


class UserUpdateRequest(BaseModel):
    is_exempt: bool | None = None
    status: UserStatus | None = None
    has_custom_quota: bool | None = None
    custom_quota_usd: float | None = None
    custom_overage_usd: float | None = None


class UserCreateRequest(BaseModel):
    email: str
    is_exempt: bool = False
    has_custom_quota: bool = False
    custom_quota_usd: float | None = None
    custom_overage_usd: float | None = None


class ConfigUpdateRequest(BaseModel):
    timezone: str | None = None
    enabled_group: str | None = None
    disabled_group: str | None = None
    default_quota_usd: float | None = None
    default_overage_usd: float | None = None
    preset_quotas: list[float] | None = None
    webhook_alert_url: str | None = None


class EvaluationResult(BaseModel):
    evaluated_at: datetime
    week_id: str
    users_evaluated: int
    group_swaps_count: int
    changes: list[dict] = Field(default_factory=list)
    success: bool = True
    error: str | None = None

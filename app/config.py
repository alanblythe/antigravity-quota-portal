"""Configuration settings for Antigravity Quota Portal."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    PROJECT_ID: str = "local-project"
    WORKLOAD_PROJECT_ID: str = ""
    WORKLOAD_PROJECT_IDS: str = ""
    REGION: str = "us-east5"
    BIGQUERY_DATASET: str = "antigravity_inference_logs"
    ENABLED_GROUP_EMAIL: str = "antigravity-enabled@example.com"
    DISABLED_GROUP_EMAIL: str = "antigravity-disabled@example.com"
    APP_TIMEZONE: str = "America/Los_Angeles"
    DEFAULT_QUOTA_USD: float = Field(default=10.00, ge=0.0)
    DEFAULT_OVERAGE_USD: float = Field(default=2.00, ge=0.0)
    PRESET_QUOTAS: list[float] = [10.0, 15.0, 25.0, 50.0, 100.0]
    WEBHOOK_ALERT_URL: str = ""
    USE_MOCK_SERVICES: bool = True
    ENABLE_GCP_AUDIT_LOGGING: bool = True
    GCP_AUDIT_LOG_NAME: str = "antigravity-quota-audit"
    SCHEDULER_INTERVAL_HOURS: int = Field(default=1, ge=1)
    MODEL_PRICING_CONFIG_PATH: str = "config/model_pricing.json"
    CORS_ORIGINS: str = "*"
    PORT: int = 8080
    HOST: str = "0.0.0.0"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()] or ["*"]

    @property
    def preset_quotas_list(self) -> list[float]:
        return list(self.PRESET_QUOTAS)

    @property
    def monitored_project_ids(self) -> list[str]:
        for raw in (self.WORKLOAD_PROJECT_IDS, self.WORKLOAD_PROJECT_ID):
            if raw:
                parsed = [p.strip() for p in raw.split(",") if p.strip()]
                if parsed:
                    return list(dict.fromkeys(parsed))
        return [self.PROJECT_ID.strip() or "local-project"]


settings = Settings()

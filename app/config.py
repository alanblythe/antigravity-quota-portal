"""Configuration settings for Antigravity Quota Portal."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    PROJECT_ID: str = "local-project"
    REGION: str = "us-central1"
    BIGQUERY_DATASET: str = "antigravity_inference_logs"
    ENABLED_GROUP_EMAIL: str = "antigravity-enabled@example.com"
    DISABLED_GROUP_EMAIL: str = "antigravity-disabled@example.com"
    APP_TIMEZONE: str = "America/Los_Angeles"
    DEFAULT_QUOTA_USD: float = 10.00
    DEFAULT_OVERAGE_USD: float = 2.00
    PRESET_QUOTAS: list[float] = [10.0, 15.0, 25.0, 50.0, 100.0]
    WEBHOOK_ALERT_URL: str = ""
    USE_MOCK_SERVICES: bool = True
    ENABLE_GCP_AUDIT_LOGGING: bool = True
    GCP_AUDIT_LOG_NAME: str = "antigravity-quota-audit"
    SCHEDULER_INTERVAL_HOURS: int = 1
    PORT: int = 8080
    HOST: str = "0.0.0.0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


settings = Settings()

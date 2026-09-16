"""Mock BigQuery client generating synthetic usage logs for local sandbox testing."""

from datetime import UTC, datetime, timedelta

from app.bq.bq_client import UserUsageRecord


class MockBigQueryUsageClient:
    """Mock BigQuery client returning synthetic inference usage records."""

    def __init__(self):
        self._custom_records: list[UserUsageRecord] | None = None

    def set_custom_records(self, records: list[UserUsageRecord]):
        self._custom_records = records

    def reset(self):
        self._custom_records = None

    def fetch_weekly_usage(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[UserUsageRecord]:
        if self._custom_records is not None:
            return self._custom_records

        now = datetime.now(UTC)
        return [
            UserUsageRecord(
                user_email="alice.chen@example.com",
                model_name="gemini-3-pro",
                request_count=200,
                token_count=2500000,
                last_active=now - timedelta(minutes=15),
            ),
            UserUsageRecord(
                user_email="alice.chen@example.com",
                model_name="gemini-3.6-flash",
                request_count=120,
                token_count=1500000,
                last_active=now - timedelta(minutes=15),
            ),
            UserUsageRecord(
                user_email="bob.martin@example.com",
                model_name="gemini-3-pro",
                request_count=210,
                token_count=3800000,
                last_active=now - timedelta(minutes=4),
            ),
            UserUsageRecord(
                user_email="charlie.davis@example.com",
                model_name="gemini-3-pro",
                request_count=380,
                token_count=5500000,
                last_active=now - timedelta(hours=2),
            ),
            UserUsageRecord(
                user_email="charlie.davis@example.com",
                model_name="gemini-2.5-pro",
                request_count=100,
                token_count=1000000,
                last_active=now - timedelta(hours=2),
            ),
            UserUsageRecord(
                user_email="dana.scully@example.com",
                model_name="gemini-3-pro",
                request_count=950,
                token_count=12000000,
                last_active=now - timedelta(minutes=45),
            ),
            UserUsageRecord(
                user_email="evan.wright@example.com",
                model_name="gemini-3-pro",
                request_count=12,
                token_count=120000,
                last_active=now - timedelta(days=3),
            ),
        ]

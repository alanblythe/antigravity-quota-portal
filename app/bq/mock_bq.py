"""Mock BigQuery client generating synthetic usage logs for local sandbox testing."""

from datetime import UTC, datetime, timedelta

from app.bq.bq_client import BigQueryUsageClient, UserUsageRecord
from app.config import settings


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class MockBigQueryUsageClient:
    """Mock BigQuery client returning synthetic inference usage records."""

    def __init__(self, default_workload_project_id: str | None = None):
        self._custom_records: list[UserUsageRecord] | None = None
        self.default_workload_project_id = default_workload_project_id or (
            settings.monitored_project_ids[0] if settings.monitored_project_ids else settings.PROJECT_ID
        )

    def set_custom_records(self, records: list[UserUsageRecord]):
        self._custom_records = [r.model_copy(deep=True) for r in records]

    def reset(self):
        self._custom_records = None

    def fetch_weekly_usage(
        self,
        start_utc: datetime | None,
        end_utc: datetime | None,
        project_start_map: dict[str, datetime] | None = None,
        default_workload_project_id: str | None = None,
        context_tier_threshold_tokens: int = 200000,
    ) -> list[UserUsageRecord]:
        start_utc = _ensure_utc(start_utc) if start_utc is not None else None
        end_utc = _ensure_utc(end_utc) if end_utc is not None else None
        norm_project_start_map = (
            {pid: _ensure_utc(dt) for pid, dt in project_start_map.items()}
            if project_start_map
            else None
        )
        now = end_utc if end_utc is not None else datetime.now(UTC)
        evan_last_active = (
            max(start_utc + timedelta(hours=1), now - timedelta(hours=12))
            if start_utc is not None
            else (now - timedelta(hours=12))
        )
        if end_utc is not None and evan_last_active > end_utc:
            evan_last_active = end_utc

        source_records = (
            self._custom_records
            if self._custom_records is not None
            else [
                UserUsageRecord(
                    user_email="alice.chen@example.com",
                    project_id="your-workload-project",
                    model_name="gemini-3-pro",
                    request_count=200,
                    token_count=2500000,
                    prompt_tokens=2000000,
                    candidates_tokens=400000,
                    cached_tokens=250000,
                    thoughts_tokens=100000,
                    last_active=now - timedelta(minutes=15),
                ),
                UserUsageRecord(
                    user_email="alice.chen@example.com",
                    project_id="your-workload-project",
                    model_name="gemini-3.6-flash",
                    request_count=120,
                    token_count=1500000,
                    prompt_tokens=1275000,
                    candidates_tokens=225000,
                    cached_tokens=75000,
                    thoughts_tokens=0,
                    last_active=now - timedelta(minutes=15),
                ),
                UserUsageRecord(
                    user_email="bob.martin@example.com",
                    project_id="your-workload-project",
                    model_name="gemini-3-pro",
                    request_count=150,
                    token_count=2000000,
                    prompt_tokens=1500000,
                    candidates_tokens=300000,
                    cached_tokens=200000,
                    thoughts_tokens=50000,
                    last_active=now - timedelta(minutes=4),
                ),
                UserUsageRecord(
                    user_email="bob.martin@example.com",
                    project_id="your-workload-project",
                    model_name="gemini-3.8-flash",
                    request_count=60,
                    token_count=1800000,
                    prompt_tokens=1200000,
                    candidates_tokens=200000,
                    cached_tokens=400000,
                    thoughts_tokens=88000,
                    last_active=now - timedelta(minutes=4),
                ),
                UserUsageRecord(
                    user_email="charlie.davis@example.com",
                    project_id="your-workload-project-std",
                    model_name="gemini-3-pro",
                    request_count=380,
                    token_count=5500000,
                    prompt_tokens=4400000,
                    candidates_tokens=850000,
                    cached_tokens=550000,
                    thoughts_tokens=250000,
                    last_active=now - timedelta(hours=2),
                ),
                UserUsageRecord(
                    user_email="charlie.davis@example.com",
                    project_id="your-workload-project-std",
                    model_name="gemini-2.5-pro",
                    request_count=100,
                    token_count=1000000,
                    prompt_tokens=800000,
                    candidates_tokens=200000,
                    cached_tokens=100000,
                    thoughts_tokens=0,
                    last_active=now - timedelta(hours=2),
                ),
                UserUsageRecord(
                    user_email="dana.scully@example.com",
                    project_id="your-workload-project",
                    model_name="gemini-3-pro",
                    request_count=950,
                    token_count=12000000,
                    prompt_tokens=9600000,
                    candidates_tokens=1800000,
                    cached_tokens=1200000,
                    thoughts_tokens=600000,
                    last_active=now - timedelta(minutes=45),
                ),
                UserUsageRecord(
                    user_email="evan.wright@example.com",
                    project_id="your-workload-project-std",
                    model_name="gemini-3-pro",
                    request_count=12,
                    token_count=120000,
                    prompt_tokens=96000,
                    candidates_tokens=24000,
                    cached_tokens=12000,
                    thoughts_tokens=0,
                    last_active=evan_last_active,
                ),
            ]
        )

        filtered: list[UserUsageRecord] = []
        for rec in source_records:
            norm_email = rec.user_email.lower().strip()
            if not norm_email or norm_email.endswith(".gserviceaccount.com"):
                continue
            eff_proj = rec.project_id or default_workload_project_id or self.default_workload_project_id
            eff_start = (
                norm_project_start_map.get(eff_proj, start_utc)
                if (norm_project_start_map and eff_proj)
                else start_utc
            )
            rec_last_active = _ensure_utc(rec.last_active) if rec.last_active is not None else None
            if rec_last_active is not None:
                if eff_start is not None and rec_last_active < eff_start:
                    continue
                if end_utc is not None and rec_last_active > end_utc:
                    continue
            copied = rec.model_copy(deep=True)
            copied.user_email = norm_email
            copied.project_id = eff_proj
            copied.last_active = rec_last_active
            has_granular = (
                copied.prompt_tokens
                + copied.candidates_tokens
                + copied.cached_tokens
                + copied.thoughts_tokens
            ) > 0
            if not has_granular and copied.fallback_tokens == 0 and copied.token_count > 0:
                copied.fallback_tokens = copied.token_count
            filtered.append(copied)

        return BigQueryUsageClient._merge_records(filtered)

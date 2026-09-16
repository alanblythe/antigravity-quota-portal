"""In-memory mock database for testing and local development sandbox."""

import threading
from datetime import UTC, datetime, timedelta

from app.audit.emitter import BaseAuditEmitter
from app.db.models import (
    AppConfig,
    AuditEvent,
    CurrentWeekUsage,
    SpendBreakdown,
    User,
    UserStatus,
    WeeklySnapshot,
)


class MockDatabase:
    """Thread-safe in-memory database simulating Cloud Firestore."""

    def __init__(self, audit_emitter: BaseAuditEmitter | None = None):
        self._lock = threading.RLock()
        self.audit_emitter = audit_emitter
        self._config: AppConfig = AppConfig()
        self._users: dict[str, User] = {}
        self._audit_events: list[AuditEvent] = []
        self._snapshots: dict[str, list[WeeklySnapshot]] = {}
        self._init_mock_data()

    def _init_mock_data(self):
        """Seed realistic demo users for the sandbox environment."""
        now = datetime.now(UTC)
        iso_year, iso_week, _ = now.isocalendar()
        week_id = f"{iso_year}-W{iso_week:02d}"

        # 1. Alice - Active heavy user under quota
        alice_tokens = {"gemini-3-pro": 2500000, "gemini-3.6-flash": 1500000}
        self._users["alice.chen@example.com"] = User(
            email="alice.chen@example.com",
            status=UserStatus.ACTIVE,
            is_exempt=False,
            has_custom_quota=True,
            custom_quota_usd=25.00,
            custom_overage_usd=5.00,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                total_tokens=4000000,
                total_requests=320,
                gross_spend_usd=6.15,
                quota_credits_usd=25.00,
                remaining_credit_usd=18.85,
                net_billable_cost_usd=0.00,
                overage_buffer_usd=5.00,
                spend_breakdown=SpendBreakdown(
                    input_spend_usd=4.20,
                    output_spend_usd=1.65,
                    cached_spend_usd=0.30,
                ),
                tokens_by_model=alice_tokens,
                spend_by_model={"gemini-3-pro": 5.75, "gemini-3.6-flash": 0.40},
                credit_utilization_percentage=24.6,
                last_active=now - timedelta(minutes=15),
            ),
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(minutes=15),
        )

        # 2. Bob - Warning pace (near 85% quota)
        bob_tokens = {"gemini-3-pro": 3800000}
        self._users["bob.martin@example.com"] = User(
            email="bob.martin@example.com",
            status=UserStatus.ACTIVE,
            is_exempt=False,
            has_custom_quota=False,
            custom_quota_usd=None,
            custom_overage_usd=None,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                total_tokens=3800000,
                total_requests=210,
                gross_spend_usd=8.74,
                quota_credits_usd=10.00,
                remaining_credit_usd=1.26,
                net_billable_cost_usd=0.00,
                overage_buffer_usd=2.00,
                spend_breakdown=SpendBreakdown(
                    input_spend_usd=6.12,
                    output_spend_usd=2.28,
                    cached_spend_usd=0.34,
                ),
                tokens_by_model=bob_tokens,
                spend_by_model={"gemini-3-pro": 8.74},
                credit_utilization_percentage=87.4,
                last_active=now - timedelta(minutes=4),
            ),
            created_at=now - timedelta(days=45),
            updated_at=now - timedelta(minutes=4),
        )

        # 3. Charlie - Auto Throttled (Breached Quota + Overage)
        charlie_tokens = {"gemini-3-pro": 5500000, "gemini-2.5-pro": 1000000}
        self._users["charlie.davis@example.com"] = User(
            email="charlie.davis@example.com",
            status=UserStatus.AUTO_DISABLED,
            is_exempt=False,
            has_custom_quota=False,
            custom_quota_usd=None,
            custom_overage_usd=None,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                total_tokens=6500000,
                total_requests=480,
                gross_spend_usd=14.95,
                quota_credits_usd=10.00,
                remaining_credit_usd=0.00,
                net_billable_cost_usd=4.95,
                overage_buffer_usd=2.00,
                spend_breakdown=SpendBreakdown(
                    input_spend_usd=10.45,
                    output_spend_usd=3.90,
                    cached_spend_usd=0.60,
                ),
                tokens_by_model=charlie_tokens,
                spend_by_model={"gemini-3-pro": 12.65, "gemini-2.5-pro": 2.30},
                credit_utilization_percentage=149.5,
                last_active=now - timedelta(hours=2),
            ),
            created_at=now - timedelta(days=30),
            updated_at=now - timedelta(hours=2),
        )

        # 4. Dana - Exempt Tech Lead
        dana_tokens = {"gemini-3-pro": 12000000}
        self._users["dana.scully@example.com"] = User(
            email="dana.scully@example.com",
            status=UserStatus.ACTIVE,
            is_exempt=True,
            has_custom_quota=False,
            custom_quota_usd=None,
            custom_overage_usd=None,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                total_tokens=12000000,
                total_requests=950,
                gross_spend_usd=27.60,
                quota_credits_usd=10.00,
                remaining_credit_usd=0.00,
                net_billable_cost_usd=17.60,
                overage_buffer_usd=2.00,
                spend_breakdown=SpendBreakdown(
                    input_spend_usd=19.32,
                    output_spend_usd=7.20,
                    cached_spend_usd=1.08,
                ),
                tokens_by_model=dana_tokens,
                spend_by_model={"gemini-3-pro": 27.60},
                credit_utilization_percentage=276.0,
                last_active=now - timedelta(minutes=45),
            ),
            created_at=now - timedelta(days=90),
            updated_at=now - timedelta(minutes=45),
        )

        # 5. Evan - Manually Disabled by Security Admin
        self._users["evan.wright@example.com"] = User(
            email="evan.wright@example.com",
            status=UserStatus.MANUALLY_DISABLED,
            is_exempt=False,
            has_custom_quota=False,
            custom_quota_usd=None,
            custom_overage_usd=None,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                total_tokens=120000,
                total_requests=12,
                gross_spend_usd=0.28,
                quota_credits_usd=10.00,
                remaining_credit_usd=9.72,
                net_billable_cost_usd=0.00,
                overage_buffer_usd=2.00,
                spend_breakdown=SpendBreakdown(
                    input_spend_usd=0.19,
                    output_spend_usd=0.07,
                    cached_spend_usd=0.02,
                ),
                tokens_by_model={"gemini-3-pro": 120000},
                spend_by_model={"gemini-3-pro": 0.28},
                credit_utilization_percentage=2.8,
                last_active=now - timedelta(days=3),
            ),
            created_at=now - timedelta(days=120),
            updated_at=now - timedelta(days=1),
        )

        # Initial Audit Trail
        self._audit_events.append(
            AuditEvent(
                event_id="evt-seed-1",
                timestamp=now - timedelta(hours=2),
                action="GROUP_SWAP",
                triggered_by="SYSTEM_WORKER",
                target_user="charlie.davis@example.com",
                details={
                    "from_group": "antigravity-enabled@example.com",
                    "to_group": "antigravity-disabled@example.com",
                    "reason": "HARD_LIMIT_BREACHED: Spend $14.95 >= Limit $12.00",
                },
            )
        )
        self._audit_events.append(
            AuditEvent(
                event_id="evt-seed-2",
                timestamp=now - timedelta(days=1),
                action="MANUAL_LOCK",
                triggered_by="admin@example.com",
                target_user="evan.wright@example.com",
                details={"reason": "Security review pending"},
            )
        )

    def get_config(self) -> AppConfig:
        with self._lock:
            return self._config.model_copy(deep=True)

    def update_config(self, config: AppConfig) -> AppConfig:
        with self._lock:
            self._config = config.model_copy(deep=True)
            return self._config.model_copy(deep=True)

    def get_user(self, email: str) -> User | None:
        with self._lock:
            user = self._users.get(email.lower().strip())
            return user.model_copy(deep=True) if user else None

    def list_users(self) -> list[User]:
        with self._lock:
            return [user.model_copy(deep=True) for user in self._users.values()]

    def save_user(self, user: User) -> User:
        with self._lock:
            user.updated_at = datetime.now(UTC)
            self._users[user.email.lower().strip()] = user.model_copy(deep=True)
            return user.model_copy(deep=True)

    def save_users(self, users: list[User]) -> None:
        with self._lock:
            now = datetime.now(UTC)
            for user in users:
                user.updated_at = now
                self._users[user.email.lower().strip()] = user.model_copy(deep=True)

    def add_audit_event(self, event: AuditEvent) -> None:
        with self._lock:
            self._audit_events.insert(0, event.model_copy(deep=True))
            # Keep latest 500 events
            if len(self._audit_events) > 500:
                self._audit_events = self._audit_events[:500]
        if self.audit_emitter:
            self.audit_emitter.emit(event)

    def list_audit_events(self, limit: int = 100) -> list[AuditEvent]:
        with self._lock:
            return [evt.model_copy(deep=True) for evt in self._audit_events[:limit]]

    def save_weekly_snapshot(self, user_email: str, snapshot: WeeklySnapshot) -> None:
        with self._lock:
            key = user_email.lower().strip()
            if key not in self._snapshots:
                self._snapshots[key] = []
            self._snapshots[key].append(snapshot.model_copy(deep=True))

    def list_weekly_snapshots(self, user_email: str) -> list[WeeklySnapshot]:
        with self._lock:
            key = user_email.lower().strip()
            return [snap.model_copy(deep=True) for snap in self._snapshots.get(key, [])]

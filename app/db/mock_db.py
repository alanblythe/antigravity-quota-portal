"""In-memory mock database for testing and local development sandbox."""

import threading
from datetime import UTC, datetime, timedelta

from app.audit.emitter import BaseAuditEmitter
from app.core.timezone_engine import get_current_week_window
from app.db.models import (
    AppConfig,
    AuditEvent,
    CurrentWeekUsage,
    ModelTokenUsage,
    SpendBreakdown,
    User,
    UserStatus,
    WeeklySnapshot,
    merge_model_pricing_with_defaults,
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
        week_id, start_utc, _, _ = get_current_week_window(self._config.timezone, now)
        hour_anchor = now.replace(minute=0, second=0, microsecond=0)
        plus_wtu_start = hour_anchor - timedelta(hours=32)
        plus_wtu_reset = plus_wtu_start + timedelta(days=7)
        std_wtu_start = hour_anchor - timedelta(hours=8)
        std_wtu_reset = std_wtu_start + timedelta(days=7)
        evan_last_active = max(start_utc + timedelta(hours=1), now - timedelta(hours=12))

        # 1. Alice - Active heavy user under quota
        alice_tokens = {"gemini-3-pro": 2500000, "gemini-3.6-flash": 1500000}
        self._users["alice.chen@example.com"] = User(
            email="alice.chen@example.com",
            project_id="your-workload-project",
            license_tier="ENTERPRISE_PLUS",
            status=UserStatus.ACTIVE,
            is_exempt=False,
            has_custom_quota=True,
            custom_quota_usd=25.00,
            custom_overage_usd=5.00,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                total_tokens=4000000,
                total_requests=320,
                gross_spend_usd=11.30,
                quota_credits_usd=25.00,
                remaining_credit_usd=13.70,
                net_billable_cost_usd=0.00,
                overage_buffer_usd=5.00,
                spend_breakdown=SpendBreakdown(
                    input_spend_usd=4.40,
                    output_spend_usd=6.84,
                    cached_spend_usd=0.06,
                    input_tokens=2950000,
                    output_tokens=725000,
                    cached_tokens=325000,
                    thoughts_tokens=100000,
                ),
                tokens_by_model=alice_tokens,
                token_details_by_model={
                    "gemini-3-pro": ModelTokenUsage(
                        total_tokens=2500000,
                        prompt_tokens=2000000,
                        candidates_tokens=400000,
                        cached_tokens=250000,
                        thoughts_tokens=100000,
                    ),
                    "gemini-3.6-flash": ModelTokenUsage(
                        total_tokens=1500000,
                        prompt_tokens=1275000,
                        candidates_tokens=225000,
                        cached_tokens=75000,
                        thoughts_tokens=0,
                    ),
                },
                spend_by_model={"gemini-3-pro": 9.55, "gemini-3.6-flash": 1.7494},
                credit_utilization_percentage=45.2,
                last_active=now - timedelta(minutes=15),
                wtu_window_start_at=plus_wtu_start,
                wtu_window_reset_at=plus_wtu_reset,
            ),
            created_at=now - timedelta(days=60),
            updated_at=now - timedelta(minutes=15),
        )

        # 2. Bob - Warning pace (near 85% quota)
        bob_tokens = {"gemini-3-pro": 2000000, "gemini-3.8-flash": 1800000}
        self._users["bob.martin@example.com"] = User(
            email="bob.martin@example.com",
            project_id="your-workload-project",
            license_tier="ENTERPRISE_PLUS",
            status=UserStatus.ACTIVE,
            is_exempt=False,
            has_custom_quota=False,
            custom_quota_usd=None,
            custom_overage_usd=None,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                total_tokens=3800000,
                total_requests=210,
                gross_spend_usd=8.55,
                quota_credits_usd=10.00,
                remaining_credit_usd=1.45,
                net_billable_cost_usd=0.00,
                overage_buffer_usd=2.00,
                spend_breakdown=SpendBreakdown(
                    input_spend_usd=3.20,
                    output_spend_usd=5.28,
                    cached_spend_usd=0.07,
                ),
                tokens_by_model=bob_tokens,
                token_details_by_model={
                    "gemini-3-pro": ModelTokenUsage(
                        total_tokens=2000000,
                        prompt_tokens=1500000,
                        candidates_tokens=300000,
                        cached_tokens=200000,
                        thoughts_tokens=50000,
                    ),
                    "gemini-3.8-flash": ModelTokenUsage(
                        total_tokens=1800000,
                        prompt_tokens=1200000,
                        candidates_tokens=200000,
                        cached_tokens=400000,
                        thoughts_tokens=88000,
                    ),
                },
                spend_by_model={"gemini-3-pro": 6.84, "gemini-3.8-flash": 1.71},
                credit_utilization_percentage=85.5,
                last_active=now - timedelta(minutes=4),
                wtu_window_start_at=plus_wtu_start,
                wtu_window_reset_at=plus_wtu_reset,
            ),
            created_at=now - timedelta(days=45),
            updated_at=now - timedelta(minutes=4),
        )

        # 3. Charlie - Auto Throttled (Breached Quota + Overage)
        charlie_tokens = {"gemini-3-pro": 5500000, "gemini-2.5-pro": 1000000}
        self._users["charlie.davis@example.com"] = User(
            email="charlie.davis@example.com",
            project_id="your-workload-project-std",
            license_tier="ENTERPRISE_STANDARD",
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
                token_details_by_model={
                    "gemini-3-pro": ModelTokenUsage(
                        total_tokens=5500000,
                        prompt_tokens=4400000,
                        candidates_tokens=850000,
                        cached_tokens=550000,
                        thoughts_tokens=250000,
                    ),
                    "gemini-2.5-pro": ModelTokenUsage(
                        total_tokens=1000000,
                        prompt_tokens=800000,
                        candidates_tokens=200000,
                        cached_tokens=100000,
                        thoughts_tokens=0,
                    ),
                },
                spend_by_model={"gemini-3-pro": 12.65, "gemini-2.5-pro": 2.30},
                credit_utilization_percentage=149.5,
                last_active=now - timedelta(hours=2),
                wtu_window_start_at=std_wtu_start,
                wtu_window_reset_at=std_wtu_reset,
            ),
            created_at=now - timedelta(days=30),
            updated_at=now - timedelta(hours=2),
        )

        # 4. Dana - Exempt Tech Lead
        dana_tokens = {"gemini-3-pro": 12000000}
        self._users["dana.scully@example.com"] = User(
            email="dana.scully@example.com",
            project_id="your-workload-project",
            license_tier="ENTERPRISE_PLUS",
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
                token_details_by_model={
                    "gemini-3-pro": ModelTokenUsage(
                        total_tokens=12000000,
                        prompt_tokens=9600000,
                        candidates_tokens=1800000,
                        cached_tokens=1200000,
                        thoughts_tokens=600000,
                    ),
                },
                spend_by_model={"gemini-3-pro": 27.60},
                credit_utilization_percentage=276.0,
                last_active=now - timedelta(minutes=45),
                wtu_window_start_at=plus_wtu_start,
                wtu_window_reset_at=plus_wtu_reset,
            ),
            created_at=now - timedelta(days=90),
            updated_at=now - timedelta(minutes=45),
        )

        # 5. Evan - Manually Disabled by Security Admin
        self._users["evan.wright@example.com"] = User(
            email="evan.wright@example.com",
            project_id="your-workload-project-std",
            license_tier="ENTERPRISE_STANDARD",
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
                token_details_by_model={
                    "gemini-3-pro": ModelTokenUsage(
                        total_tokens=120000,
                        prompt_tokens=96000,
                        candidates_tokens=24000,
                        cached_tokens=12000,
                        thoughts_tokens=0,
                    ),
                },
                spend_by_model={"gemini-3-pro": 0.28},
                credit_utilization_percentage=2.8,
                last_active=evan_last_active,
                wtu_window_start_at=std_wtu_start,
                wtu_window_reset_at=std_wtu_reset,
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
            cfg = self._config.model_copy(deep=True)
            cfg.models = merge_model_pricing_with_defaults(cfg.models)
            return cfg

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
            self._audit_events = [e for e in self._audit_events if e.event_id != event.event_id]
            self._audit_events.insert(0, event.model_copy(deep=True))
            self._audit_events.sort(key=lambda e: e.timestamp, reverse=True)
            # Keep latest 500 events
            if len(self._audit_events) > 500:
                self._audit_events = self._audit_events[:500]
        if self.audit_emitter:
            self.audit_emitter.emit(event)

    def add_audit_events_batch(self, events: list[AuditEvent]) -> None:
        deduped_by_id: dict[str, AuditEvent] = {}
        for event in events:
            deduped_by_id[event.event_id] = event
        for event in deduped_by_id.values():
            self.add_audit_event(event)

    def list_audit_events(self, limit: int = 100) -> list[AuditEvent]:
        with self._lock:
            sorted_events = sorted(self._audit_events, key=lambda e: e.timestamp, reverse=True)
            return [evt.model_copy(deep=True) for evt in sorted_events[:limit]]

    @staticmethod
    def _snapshot_dedup_key(snap: WeeklySnapshot) -> tuple[str, int]:
        snap_ts = int(snap.snapshot_timestamp.timestamp()) if snap.snapshot_timestamp else 0
        return (snap.week_id, snap_ts)

    def save_weekly_snapshot(self, user_email: str, snapshot: WeeklySnapshot) -> None:
        with self._lock:
            key = user_email.lower().strip()
            if key not in self._snapshots:
                self._snapshots[key] = []
            snap_copy = snapshot.model_copy(deep=True)
            dedup_key = self._snapshot_dedup_key(snap_copy)
            self._snapshots[key] = [
                s for s in self._snapshots[key] if self._snapshot_dedup_key(s) != dedup_key
            ]
            self._snapshots[key].append(snap_copy)

    def save_weekly_snapshots_batch(self, snapshots: list[tuple[str, WeeklySnapshot]]) -> None:
        for user_email, snapshot in snapshots:
            self.save_weekly_snapshot(user_email, snapshot)

    def list_weekly_snapshots(self, user_email: str, limit: int = 52) -> list[WeeklySnapshot]:
        if limit <= 0:
            return []
        with self._lock:
            key = user_email.lower().strip()
            dedup_map: dict[tuple[str, int], WeeklySnapshot] = {}
            for snap in self._snapshots.get(key, []):
                dedup_map[self._snapshot_dedup_key(snap)] = snap.model_copy(deep=True)
            sorted_snaps = sorted(
                dedup_map.values(),
                key=lambda s: (
                    s.week_id,
                    s.snapshot_timestamp.timestamp() if s.snapshot_timestamp else 0.0,
                ),
                reverse=True,
            )
            return sorted_snaps[:limit]

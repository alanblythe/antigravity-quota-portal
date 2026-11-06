"""Google Cloud Firestore Native mode client."""

import logging
from datetime import UTC, datetime

from google.cloud import firestore

from app.audit.emitter import BaseAuditEmitter
from app.db.models import (
    AppConfig,
    AuditEvent,
    User,
    WeeklySnapshot,
    merge_model_pricing_with_defaults,
)

logger = logging.getLogger(__name__)


class FirestoreDatabase:
    """Production client for Google Cloud Firestore Native Database."""

    def __init__(
        self,
        project_id: str,
        database: str = "(default)",
        audit_emitter: BaseAuditEmitter | None = None,
    ):
        self.project_id = project_id
        self.audit_emitter = audit_emitter
        self.client = firestore.Client(project=project_id, database=database)
        self.config_collection = self.client.collection("config")
        self.users_collection = self.client.collection("users")
        self.audit_collection = self.client.collection("audit_events")

    def get_config(self) -> AppConfig:
        try:
            doc_ref = self.config_collection.document("app_config")
            doc = doc_ref.get()
            if doc.exists:
                data = doc.to_dict() or {}
                # Merge external config file defaults with Firestore overrides
                data["models"] = merge_model_pricing_with_defaults(data.get("models"))
                clean_data = {k: v for k, v in data.items() if v is not None or k == "last_publish_timestamp"}
                return AppConfig(**clean_data)
            else:
                # Initialize default config doc
                default_config = AppConfig()
                self.update_config(default_config)
                return default_config
        except Exception as e:
            logger.error(f"Error fetching Firestore app config: {e}", exc_info=True)
            raise

    def update_config(self, config: AppConfig) -> AppConfig:
        doc_ref = self.config_collection.document("app_config")
        doc_ref.set(config.model_dump(mode="json"))
        return config

    def get_user(self, email: str) -> User | None:
        key = email.lower().strip()
        doc = self.users_collection.document(key).get()
        if doc.exists:
            return User(**doc.to_dict())
        return None

    def list_users(self) -> list[User]:
        docs = self.users_collection.stream()
        users = []
        for doc in docs:
            try:
                users.append(User(**doc.to_dict()))
            except Exception as e:
                logger.error(f"Error deserializing user {doc.id}: {e}")
        return users

    def save_user(self, user: User) -> User:
        user.updated_at = datetime.now(UTC)
        key = user.email.lower().strip()
        self.users_collection.document(key).set(user.model_dump(mode="json"))
        return user

    def save_users(self, users: list[User]) -> None:
        if not users:
            return
        now = datetime.now(UTC)
        # Deduplicate by email (keeping the last occurrence)
        deduped_by_key: dict[str, User] = {}
        for user in users:
            key = user.email.lower().strip()
            deduped_by_key[key] = user
        unique_users = list(deduped_by_key.values())
        # Firestore batches support up to 500 operations per batch
        batch_size = 500
        for i in range(0, len(unique_users), batch_size):
            batch = self.client.batch()
            for user in unique_users[i : i + batch_size]:
                user.updated_at = now
                key = user.email.lower().strip()
                doc_ref = self.users_collection.document(key)
                batch.set(doc_ref, user.model_dump(mode="json"))
            batch.commit()

    def add_audit_event(self, event: AuditEvent) -> None:
        doc_ref = self.audit_collection.document(event.event_id)
        doc_ref.set(event.model_dump(mode="json"))
        if self.audit_emitter:
            self.audit_emitter.emit(event)

    def add_audit_events_batch(self, events: list[AuditEvent]) -> None:
        if not events:
            return
        deduped_by_id: dict[str, AuditEvent] = {}
        for event in events:
            deduped_by_id[event.event_id] = event
        unique_events = list(deduped_by_id.values())
        batch_size = 500
        for i in range(0, len(unique_events), batch_size):
            batch = self.client.batch()
            for event in unique_events[i : i + batch_size]:
                doc_ref = self.audit_collection.document(event.event_id)
                batch.set(doc_ref, event.model_dump(mode="json"))
            batch.commit()
        if self.audit_emitter:
            for event in unique_events:
                self.audit_emitter.emit(event)

    def list_audit_events(self, limit: int = 100) -> list[AuditEvent]:
        query = self.audit_collection.order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
        docs = query.stream()
        events = []
        for doc in docs:
            try:
                events.append(AuditEvent(**doc.to_dict()))
            except Exception as e:
                logger.error(f"Error deserializing audit event {doc.id}: {e}")
        return events

    def save_weekly_snapshot(self, user_email: str, snapshot: WeeklySnapshot) -> None:
        key = user_email.lower().strip()
        snap_ts = int(snapshot.snapshot_timestamp.timestamp()) if snapshot.snapshot_timestamp else 0
        doc_id = f"{snapshot.week_id}_{snap_ts}" if snap_ts else snapshot.week_id
        snap_ref = self.users_collection.document(key).collection("snapshots").document(doc_id)
        snap_ref.set(snapshot.model_dump(mode="json"))

    def save_weekly_snapshots_batch(self, snapshots: list[tuple[str, WeeklySnapshot]]) -> None:
        if not snapshots:
            return
        deduped_by_key: dict[tuple[str, str], WeeklySnapshot] = {}
        for user_email, snapshot in snapshots:
            key = user_email.lower().strip()
            snap_ts = int(snapshot.snapshot_timestamp.timestamp()) if snapshot.snapshot_timestamp else 0
            doc_id = f"{snapshot.week_id}_{snap_ts}" if snap_ts else snapshot.week_id
            deduped_by_key[(key, doc_id)] = snapshot
        unique_snapshots = list(deduped_by_key.items())
        batch_size = 500
        for i in range(0, len(unique_snapshots), batch_size):
            batch = self.client.batch()
            for (key, doc_id), snapshot in unique_snapshots[i : i + batch_size]:
                snap_ref = self.users_collection.document(key).collection("snapshots").document(doc_id)
                batch.set(snap_ref, snapshot.model_dump(mode="json"))
            batch.commit()

    def list_weekly_snapshots(self, user_email: str, limit: int = 52) -> list[WeeklySnapshot]:
        if limit <= 0:
            return []
        key = user_email.lower().strip()
        fetch_limit = max(limit * 5, 100)
        snaps = (
            self.users_collection.document(key)
            .collection("snapshots")
            .order_by("week_id", direction=firestore.Query.DESCENDING)
            .limit(fetch_limit)
            .stream()
        )
        results: list[WeeklySnapshot] = []
        for doc in snaps:
            if not doc.exists:
                continue
            try:
                results.append(WeeklySnapshot(**doc.to_dict()))
            except Exception as e:
                logger.error(f"Error deserializing weekly snapshot {doc.id} for {key}: {e}")
        results.sort(
            key=lambda s: (
                s.week_id,
                s.snapshot_timestamp.timestamp() if s.snapshot_timestamp else 0.0,
            ),
            reverse=True,
        )
        return results[:limit]

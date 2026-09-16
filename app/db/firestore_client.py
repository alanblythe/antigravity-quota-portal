"""Google Cloud Firestore Native mode client."""

import logging
from datetime import UTC, datetime

from google.cloud import firestore

from app.audit.emitter import BaseAuditEmitter
from app.db.models import (
    DEFAULT_MODEL_PRICING,
    AppConfig,
    AuditEvent,
    ModelPricing,
    User,
    WeeklySnapshot,
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
                # Parse models dict
                models_data = data.get("models", {})
                models = {}
                for k, v in models_data.items():
                    models[k] = ModelPricing(**v)
                if not models:
                    models = DEFAULT_MODEL_PRICING
                data["models"] = models
                return AppConfig(**data)
            else:
                # Initialize default config doc
                default_config = AppConfig()
                self.update_config(default_config)
                return default_config
        except Exception as e:
            logger.error(f"Error fetching Firestore app config: {e}", exc_info=True)
            return AppConfig()

    def update_config(self, config: AppConfig) -> AppConfig:
        doc_ref = self.config_collection.document("app_config")
        doc_ref.set(config.model_dump(mode="json"), merge=True)
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
        self.users_collection.document(key).set(user.model_dump(mode="json"), merge=True)
        return user

    def save_users(self, users: list[User]) -> None:
        batch = self.client.batch()
        now = datetime.now(UTC)
        for user in users:
            user.updated_at = now
            key = user.email.lower().strip()
            doc_ref = self.users_collection.document(key)
            batch.set(doc_ref, user.model_dump(mode="json"), merge=True)
        batch.commit()

    def add_audit_event(self, event: AuditEvent) -> None:
        doc_ref = self.audit_collection.document(event.event_id)
        doc_ref.set(event.model_dump(mode="json"))
        if self.audit_emitter:
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
        snap_ref = self.users_collection.document(key).collection("snapshots").document(snapshot.week_id)
        snap_ref.set(snapshot.model_dump(mode="json"))

    def list_weekly_snapshots(self, user_email: str, limit: int = 52) -> list[WeeklySnapshot]:
        key = user_email.lower().strip()
        snaps = (
            self.users_collection.document(key)
            .collection("snapshots")
            .order_by("week_id", direction=firestore.Query.DESCENDING)
            .limit(limit)
            .stream()
        )
        return [WeeklySnapshot(**doc.to_dict()) for doc in snaps if doc.exists]

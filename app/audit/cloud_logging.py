"""Google Cloud Logging audit emitter with structured JSON payload and severity mapping."""

import logging
from typing import Any

from google.cloud import logging as gcp_logging

from app.audit.emitter import BaseAuditEmitter
from app.db.models import AuditEvent

logger = logging.getLogger(__name__)

DEFAULT_SEVERITY_MAP: dict[str, str] = {
    "USER_DISCOVERED": "INFO",
    "USER_PREPROVISION": "NOTICE",
    "STATUS_CHANGE": "NOTICE",
    "GROUP_SWAP": "NOTICE",
    "MANUAL_LOCK": "WARNING",
    "MANUAL_UNLOCK": "NOTICE",
    "QUOTA_UPDATE": "NOTICE",
    "CONFIG_UPDATE": "NOTICE",
    "PRICING_MATRIX_UPDATE": "NOTICE",
    "PUBLISH_TRIGGERED": "INFO",
}


def resolve_event_severity(event: AuditEvent) -> str:
    """Determine GCP Cloud Logging severity based on event action and state transitions."""
    if event.action == "STATUS_CHANGE":
        to_status = event.details.get("to_status")
        if to_status in ("AUTO_DISABLED", "MANUALLY_DISABLED"):
            return "WARNING"
        return "NOTICE"

    if event.action == "GROUP_SWAP":
        direction = event.details.get("direction")
        if direction == "DISABLED":
            return "WARNING"
        return "NOTICE"

    return DEFAULT_SEVERITY_MAP.get(event.action, "NOTICE")


class CloudLoggingAuditEmitter(BaseAuditEmitter):
    """Dispatches structured audit entries to Google Cloud Logging."""

    def __init__(
        self,
        project_id: str,
        log_name: str = "antigravity-quota-audit",
        client: Any | None = None,
    ):
        self.project_id = project_id
        self.log_name = log_name
        try:
            self._client = client or gcp_logging.Client(project=project_id)
            self._logger = self._client.logger(log_name)
            logger.info(f"Initialized CloudLoggingAuditEmitter (project={project_id}, log_name={log_name})")
        except Exception:
            logger.exception("Failed to initialize GCP Cloud Logging client")
            self._client = None
            self._logger = None

    def emit(self, event: AuditEvent) -> None:
        """Format and write structured log entry to Google Cloud Logging."""
        if self._logger is None:
            logger.warning(f"Cloud Logging uninitialized; skipping remote emission of event {event.event_id}")
            return

        severity = resolve_event_severity(event)

        labels = {
            "action": event.action,
            "triggered_by": event.triggered_by,
            "service": "antigravity-quota-portal",
        }
        if event.target_user:
            labels["target_user"] = event.target_user

        struct_payload = {
            "eventId": event.event_id,
            "timestamp": event.timestamp.isoformat(),
            "action": event.action,
            "triggeredBy": event.triggered_by,
            "targetUser": event.target_user,
            "details": event.details,
            "service": "antigravity-quota-portal",
        }

        try:
            self._logger.log_struct(
                info=struct_payload,
                severity=severity,
                labels=labels,
            )
            logger.debug(f"[CloudLogging] Emitted audit event {event.event_id} ({event.action}, severity={severity})")
        except Exception:
            # Trap exception so audit logging failures never break primary application flows
            logger.exception(f"Failed to emit audit event {event.event_id} to GCP Cloud Logging")

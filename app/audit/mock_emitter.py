"""In-memory mock audit emitter for local testing and sandbox environments."""

import logging
import threading

from app.audit.emitter import BaseAuditEmitter
from app.db.models import AuditEvent

logger = logging.getLogger(__name__)


class MockAuditEmitter(BaseAuditEmitter):
    """In-memory audit emitter that records events for test verification."""

    def __init__(self):
        self._lock = threading.RLock()
        self.emitted_events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        with self._lock:
            self.emitted_events.append(event)
        logger.debug(f"[MockAuditEmitter] Recorded audit event {event.event_id} ({event.action})")

    def get_events(self) -> list[AuditEvent]:
        with self._lock:
            return list(self.emitted_events)

    def clear(self) -> None:
        with self._lock:
            self.emitted_events.clear()

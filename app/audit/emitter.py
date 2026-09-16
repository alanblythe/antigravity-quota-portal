"""Abstract base emitter interface for audit events."""

from typing import Protocol, runtime_checkable

from app.db.models import AuditEvent


@runtime_checkable
class BaseAuditEmitter(Protocol):
    """Protocol for audit event dispatchers."""

    def emit(self, event: AuditEvent) -> None:
        """Emit an audit event to the downstream audit sink."""
        ...

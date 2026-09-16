"""Unit and integration tests for GCP Cloud Logging audit event emission."""

from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.audit.cloud_logging import CloudLoggingAuditEmitter, resolve_event_severity
from app.bq.bq_client import UserUsageRecord
from app.db.models import AuditEvent


def test_severity_resolution_for_all_actions():
    """Verify proper GCP log severity is computed based on action and payload."""
    now = datetime.now(UTC)

    # Status changes
    stat_warn = AuditEvent(
        event_id="e1",
        timestamp=now,
        action="STATUS_CHANGE",
        triggered_by="worker",
        target_user="dev@test.com",
        details={"to_status": "AUTO_DISABLED"},
    )
    assert resolve_event_severity(stat_warn) == "WARNING"

    stat_manual = AuditEvent(
        event_id="e2",
        timestamp=now,
        action="STATUS_CHANGE",
        triggered_by="worker",
        target_user="dev@test.com",
        details={"to_status": "MANUALLY_DISABLED"},
    )
    assert resolve_event_severity(stat_manual) == "WARNING"

    stat_ok = AuditEvent(
        event_id="e3",
        timestamp=now,
        action="STATUS_CHANGE",
        triggered_by="worker",
        target_user="dev@test.com",
        details={"to_status": "ACTIVE"},
    )
    assert resolve_event_severity(stat_ok) == "NOTICE"

    # Group swaps
    swap_warn = AuditEvent(
        event_id="e4",
        timestamp=now,
        action="GROUP_SWAP",
        triggered_by="worker",
        target_user="dev@test.com",
        details={"direction": "DISABLED"},
    )
    assert resolve_event_severity(swap_warn) == "WARNING"

    swap_ok = AuditEvent(
        event_id="e5",
        timestamp=now,
        action="GROUP_SWAP",
        triggered_by="worker",
        target_user="dev@test.com",
        details={"direction": "ENABLED"},
    )
    assert resolve_event_severity(swap_ok) == "NOTICE"

    # Administrative and discovery events
    lock_evt = AuditEvent(event_id="e6", timestamp=now, action="MANUAL_LOCK", triggered_by="admin")
    assert resolve_event_severity(lock_evt) == "WARNING"

    unlock_evt = AuditEvent(event_id="e7", timestamp=now, action="MANUAL_UNLOCK", triggered_by="admin")
    assert resolve_event_severity(unlock_evt) == "NOTICE"

    disc_evt = AuditEvent(event_id="e8", timestamp=now, action="USER_DISCOVERED", triggered_by="worker")
    assert resolve_event_severity(disc_evt) == "INFO"

    pub_evt = AuditEvent(event_id="e9", timestamp=now, action="PUBLISH_TRIGGERED", triggered_by="admin")
    assert resolve_event_severity(pub_evt) == "INFO"

    quota_evt = AuditEvent(event_id="e10", timestamp=now, action="QUOTA_UPDATE", triggered_by="admin")
    assert resolve_event_severity(quota_evt) == "NOTICE"

    unknown_evt = AuditEvent(event_id="e11", timestamp=now, action="CUSTOM_ACTION", triggered_by="admin")
    assert resolve_event_severity(unknown_evt) == "NOTICE"


def test_cloud_logging_emitter_structured_payload():
    """Verify CloudLoggingAuditEmitter formats structured JSON and labels correctly."""
    mock_gcp_client = MagicMock()
    mock_logger = MagicMock()
    mock_gcp_client.logger.return_value = mock_logger

    emitter = CloudLoggingAuditEmitter(
        project_id="test-project",
        log_name="test-audit-log",
        client=mock_gcp_client,
    )

    now = datetime.now(UTC)
    event = AuditEvent(
        event_id="evt-test-123",
        timestamp=now,
        action="GROUP_SWAP",
        triggered_by="SYSTEM_WORKER",
        target_user="charlie.davis@example.com",
        details={"direction": "DISABLED", "reason": "Hard limit breached"},
    )

    emitter.emit(event)

    mock_logger.log_struct.assert_called_once()
    _, kwargs = mock_logger.log_struct.call_args

    assert kwargs["severity"] == "WARNING"
    assert kwargs["labels"]["action"] == "GROUP_SWAP"
    assert kwargs["labels"]["triggered_by"] == "SYSTEM_WORKER"
    assert kwargs["labels"]["target_user"] == "charlie.davis@example.com"
    assert kwargs["labels"]["service"] == "antigravity-quota-portal"

    info = kwargs["info"]
    assert info["eventId"] == "evt-test-123"
    assert info["action"] == "GROUP_SWAP"
    assert info["triggeredBy"] == "SYSTEM_WORKER"
    assert info["targetUser"] == "charlie.davis@example.com"
    assert info["details"]["direction"] == "DISABLED"
    assert info["service"] == "antigravity-quota-portal"


def test_cloud_logging_emitter_handles_none_target_user():
    """Verify events without target_user omit the label cleanly."""
    mock_gcp_client = MagicMock()
    mock_logger = MagicMock()
    mock_gcp_client.logger.return_value = mock_logger

    emitter = CloudLoggingAuditEmitter(
        project_id="test-project",
        client=mock_gcp_client,
    )

    event = AuditEvent(
        event_id="evt-sys-001",
        action="CONFIG_UPDATE",
        triggered_by="admin@example.com",
        target_user=None,
        details={"default_quota_usd": 15.0},
    )

    emitter.emit(event)

    _, kwargs = mock_logger.log_struct.call_args
    assert "target_user" not in kwargs["labels"]
    assert kwargs["labels"]["action"] == "CONFIG_UPDATE"


def test_cloud_logging_emitter_failure_isolation():
    """Verify that an exception raised by GCP Cloud Logging does not raise to caller."""
    mock_gcp_client = MagicMock()
    mock_logger = MagicMock()
    mock_logger.log_struct.side_effect = RuntimeError("GCP Cloud Logging network timeout")
    mock_gcp_client.logger.return_value = mock_logger

    emitter = CloudLoggingAuditEmitter(
        project_id="test-project",
        client=mock_gcp_client,
    )

    event = AuditEvent(
        event_id="evt-err-01",
        action="QUOTA_UPDATE",
        triggered_by="admin",
        details={},
    )

    # Must NOT raise exception
    emitter.emit(event)


def test_dual_write_with_database(mock_db, mock_audit_emitter):
    """Verify adding an audit event persists to DB and dispatches to audit emitter."""
    event = AuditEvent(
        event_id="evt-dw-01",
        action="USER_PREPROVISION",
        triggered_by="admin_ui",
        target_user="new.hire@example.com",
        details={"quota": 25.0},
    )

    mock_db.add_audit_event(event)

    # 1. Stored in DB
    db_events = mock_db.list_audit_events()
    assert any(e.event_id == "evt-dw-01" for e in db_events)

    # 2. Emitted to audit emitter
    emitted = mock_audit_emitter.get_events()
    assert any(e.event_id == "evt-dw-01" for e in emitted)


def test_api_actions_emit_audit_events(client, mock_audit_emitter):
    """Verify API endpoints dispatch audit events through the audit emitter."""
    mock_audit_emitter.clear()

    # 1. Lock a user
    res_lock = client.post("/api/users/bob.martin@example.com/lock")
    assert res_lock.status_code == 200

    emitted_lock = [e for e in mock_audit_emitter.get_events() if e.action == "MANUAL_LOCK"]
    assert len(emitted_lock) == 1
    assert emitted_lock[0].target_user == "bob.martin@example.com"

    # 2. Update user quota
    res_patch = client.patch(
        "/api/users/alice.chen@example.com",
        json={"has_custom_quota": True, "custom_quota_usd": 35.0},
    )
    assert res_patch.status_code == 200
    emitted_quota = [e for e in mock_audit_emitter.get_events() if e.action == "QUOTA_UPDATE"]
    assert len(emitted_quota) == 1
    assert emitted_quota[0].target_user == "alice.chen@example.com"


def test_evaluator_run_emits_audit_events(evaluator, mock_db, mock_bq, mock_audit_emitter):
    """Verify QuotaEvaluator execution emits expected audit events."""
    mock_audit_emitter.clear()

    # Add a synthetic user to trigger USER_DISCOVERED
    records = mock_bq.fetch_weekly_usage(None, None)
    records.append(
        UserUsageRecord(
            user_email="discovered.dev@example.com",
            model_name="gemini-3-pro",
            request_count=10,
            token_count=50000,
        )
    )
    mock_bq.set_custom_records(records)

    result = evaluator.run_evaluation(triggered_by="test_audit_run", is_publish=True)
    assert result.success is True

    actions = [e.action for e in mock_audit_emitter.get_events()]
    assert "USER_DISCOVERED" in actions
    assert "PUBLISH_TRIGGERED" in actions

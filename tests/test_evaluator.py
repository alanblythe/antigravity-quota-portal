"""Integration tests for the quota evaluator engine."""

from unittest.mock import MagicMock

from app.bq.bq_client import UserUsageRecord
from app.db.models import CurrentWeekUsage, User, UserStatus


def test_evaluator_runs_and_discovers_users(evaluator, mock_db, mock_bq, mock_identity):
    """Verify synthetic user discovery when an unknown email appears in BQ."""
    records = mock_bq.fetch_weekly_usage(None, None)
    records.append(
        UserUsageRecord(
            user_email="new.developer@example.com",
            model_name="gemini-3-pro",
            request_count=50,
            token_count=1000000,
        )
    )
    mock_bq.set_custom_records(records)

    result = evaluator.run_evaluation(triggered_by="test_run", is_publish=True)
    assert result.success is True
    assert result.users_evaluated >= 6

    # Verify discovered user was saved in DB
    new_user = mock_db.get_user("new.developer@example.com")
    assert new_user is not None
    assert new_user.status == UserStatus.ACTIVE
    assert new_user.current_week.total_tokens == 1000000


def test_evaluator_applies_group_swaps(evaluator, mock_db, mock_bq, mock_identity):
    """Verify evaluator moves breached users to disabled group and compliant users to enabled group."""
    result = evaluator.run_evaluation(triggered_by="test_swap", is_publish=True)
    assert result.success is True

    assert mock_identity.is_user_in_disabled_group("charlie.davis@example.com") is True
    assert mock_identity.is_user_in_enabled_group("alice.chen@example.com") is True


def test_evaluator_weekly_reset_and_snapshot_archival(evaluator, mock_db, mock_bq, mock_identity):
    """Verify Monday weekly boundary archives previous week snapshot and re-enables AUTO_DISABLED users."""
    old_week = "2026-W01"

    # Seed a user who was throttled during an older week
    user = User(
        email="rollover.dev@example.com",
        status=UserStatus.AUTO_DISABLED,
        current_week=CurrentWeekUsage(
            week_id=old_week,
            total_tokens=6000000,
            gross_spend_usd=13.50,
            quota_credits_usd=10.00,
            net_billable_cost_usd=3.50,
        ),
    )
    mock_db.save_user(user)
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, user.email)

    # Empty usage for the new week
    mock_bq.set_custom_records([])

    result = evaluator.run_evaluation(triggered_by="monday_cron", is_publish=True)
    assert result.success is True

    # 1. Snapshot was saved for old week
    snapshots = mock_db.list_weekly_snapshots("rollover.dev@example.com")
    assert len(snapshots) == 1
    assert snapshots[0].week_id == old_week
    assert snapshots[0].final_tokens == 6000000
    assert snapshots[0].final_gross_cost_usd == 13.50

    # 2. User status auto-re-enabled to ACTIVE
    updated_user = mock_db.get_user("rollover.dev@example.com")
    assert updated_user.status == UserStatus.ACTIVE
    assert updated_user.current_week.gross_spend_usd == 0.00

    # 3. User swapped from DISABLED -> ENABLED in Cloud Identity
    assert mock_identity.is_user_in_enabled_group("rollover.dev@example.com") is True
    assert mock_identity.is_user_in_disabled_group("rollover.dev@example.com") is False


def test_evaluator_weekly_reset_preserves_manually_disabled(evaluator, mock_db, mock_bq, mock_identity):
    """Verify weekly reset preserves MANUALLY_DISABLED status and leaves user in disabled group."""
    old_week = "2026-W01"
    user = User(
        email="locked.dev@example.com",
        status=UserStatus.MANUALLY_DISABLED,
        current_week=CurrentWeekUsage(
            week_id=old_week,
            total_tokens=100000,
            gross_spend_usd=0.25,
        ),
    )
    mock_db.save_user(user)
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, user.email)
    mock_bq.set_custom_records([])

    result = evaluator.run_evaluation(triggered_by="monday_cron", is_publish=True)
    assert result.success is True

    updated_user = mock_db.get_user("locked.dev@example.com")
    assert updated_user.status == UserStatus.MANUALLY_DISABLED
    assert mock_identity.is_user_in_disabled_group("locked.dev@example.com") is True
    assert mock_identity.is_user_in_enabled_group("locked.dev@example.com") is False


def test_evaluator_custom_quota_override(evaluator, mock_db, mock_bq, mock_identity):
    """Verify users with custom quotas are evaluated against custom limits instead of default."""
    # Default quota is $10.00 + $2.00 overage.
    # Alice has custom quota of $25.00 + $5.00 overage.
    # With ~2.5M Pro + 1.5M Flash tokens, her spend is ~$6.15 (under $25.00).
    result = evaluator.run_evaluation(triggered_by="test_custom", is_publish=True)
    assert result.success is True

    alice = mock_db.get_user("alice.chen@example.com")
    assert alice.has_custom_quota is True
    assert alice.custom_quota_usd == 25.00
    assert alice.status == UserStatus.ACTIVE
    assert mock_identity.is_user_in_enabled_group("alice.chen@example.com") is True


def test_evaluator_midweek_quota_increase_restores_access(evaluator, mock_db, mock_bq, mock_identity):
    """Verify increasing quota for an AUTO_DISABLED user restores ACTIVE status and swaps group."""
    # First run throttles Charlie
    evaluator.run_evaluation(triggered_by="run1", is_publish=True)
    charlie = mock_db.get_user("charlie.davis@example.com")
    assert charlie.status == UserStatus.AUTO_DISABLED
    assert mock_identity.is_user_in_disabled_group("charlie.davis@example.com") is True

    # Admin grants generous custom quota of $100.00
    charlie.has_custom_quota = True
    charlie.custom_quota_usd = 100.00
    charlie.custom_overage_usd = 20.00
    mock_db.save_user(charlie)

    # Second evaluation run reconciles and unlocks Charlie
    result = evaluator.run_evaluation(triggered_by="admin_recheck", is_publish=True)
    assert result.success is True

    updated_charlie = mock_db.get_user("charlie.davis@example.com")
    assert updated_charlie.status == UserStatus.ACTIVE
    assert mock_identity.is_user_in_enabled_group("charlie.davis@example.com") is True
    assert mock_identity.is_user_in_disabled_group("charlie.davis@example.com") is False


def test_evaluator_exempt_user_bypasses_throttling(evaluator, mock_db, mock_bq, mock_identity):
    """Verify exempt users can breach quotas without being throttled or swapped."""
    # Dana Scully is exempt with 12,000,000 tokens (> $27.00 gross spend vs $10 default quota)
    result = evaluator.run_evaluation(triggered_by="exempt_check", is_publish=True)
    assert result.success is True

    dana = mock_db.get_user("dana.scully@example.com")
    assert dana.is_exempt is True
    assert dana.status == UserStatus.ACTIVE
    assert mock_identity.is_user_in_enabled_group("dana.scully@example.com") is True
    assert mock_identity.is_user_in_disabled_group("dana.scully@example.com") is False


def test_evaluator_drift_reconciliation(evaluator, mock_db, mock_bq, mock_identity):
    """Verify evaluator corrects Cloud Identity group drift."""
    # Alice is ACTIVE, but simulate drift where she was accidentally added to disabled group
    mock_identity.remove_member_from_group(mock_identity.enabled_group_email, "alice.chen@example.com")
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, "alice.chen@example.com")
    assert mock_identity.is_user_in_disabled_group("alice.chen@example.com") is True

    result = evaluator.run_evaluation(triggered_by="reconcile_drift", is_publish=True)
    assert result.success is True

    # Evaluator should have corrected the membership
    assert mock_identity.is_user_in_enabled_group("alice.chen@example.com") is True
    assert mock_identity.is_user_in_disabled_group("alice.chen@example.com") is False


def test_evaluator_publish_flag_controls_last_publish_timestamp(evaluator, mock_db):
    """Verify is_publish flag updates config timestamp only when True."""
    config_before = mock_db.get_config()
    orig_ts = config_before.last_publish_timestamp

    # Non-publish run
    res_nopub = evaluator.run_evaluation(triggered_by="ad_hoc", is_publish=False)
    assert res_nopub.success is True
    assert mock_db.get_config().last_publish_timestamp == orig_ts

    # Publish run
    res_pub = evaluator.run_evaluation(triggered_by="publish_btn", is_publish=True)
    assert res_pub.success is True
    assert mock_db.get_config().last_publish_timestamp is not None
    assert mock_db.get_config().last_publish_timestamp != orig_ts


def test_evaluator_handles_bq_exception_gracefully(mock_db, mock_identity):
    """Verify evaluator catches BigQuery query failures without crashing."""
    failing_bq = MagicMock()
    failing_bq.fetch_weekly_usage.side_effect = RuntimeError("BigQuery connection failure")

    from app.core.evaluator import QuotaEvaluator

    faulty_evaluator = QuotaEvaluator(
        db_client=mock_db,
        bq_client=failing_bq,
        identity_client=mock_identity,
    )

    result = faulty_evaluator.run_evaluation(triggered_by="crash_test", is_publish=False)
    assert result.success is False
    assert "BigQuery connection failure" in result.error
    assert result.users_evaluated == 0


def test_evaluator_email_case_insensitivity(evaluator, mock_db, mock_bq):
    """Verify mixed-case and whitespace emails in BQ map to existing lowercased DB records."""
    mock_bq.set_custom_records(
        [
            UserUsageRecord(
                user_email="  Alice.Chen@Example.COM  ",
                model_name="gemini-3-pro",
                request_count=10,
                token_count=100000,
            )
        ]
    )

    result = evaluator.run_evaluation(triggered_by="case_test", is_publish=False)
    assert result.success is True

    # Check Alice received the update and no duplicate was created
    alice = mock_db.get_user("alice.chen@example.com")
    assert alice is not None
    assert alice.current_week.total_tokens == 100000
    assert mock_db.get_user("Alice.Chen@Example.COM") is not None  # get_user is also normalized
    all_users = mock_db.list_users()
    alice_matches = [u for u in all_users if u.email.lower().strip() == "alice.chen@example.com"]
    assert len(alice_matches) == 1

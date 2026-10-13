"""
End-to-End Simulation Test Suite for Multi-User Quota & Governance Lifecycle.

Tests session isolation between dev1 and dev2,
simulates token spend ingestion via BigQuery audit logs, verifies state machine transitions,
and ensures automated group membership reconciliation.
"""


import os
from datetime import UTC, datetime, timedelta

import pytest

from app.bq.bq_client import UserUsageRecord
from app.bq.mock_bq import MockBigQueryUsageClient
from app.config import settings
from app.core.evaluator import QuotaEvaluator
from app.core.timezone_engine import get_current_week_window
from app.db.mock_db import MockDatabase
from app.db.models import CurrentWeekUsage, User, UserStatus
from app.identity.mock_identity import MockCloudIdentityGroupManager
from tests.simulation.runner import IsolatedAgyRunner

ORG_DOMAIN = os.getenv("ORG_DOMAIN", "example.com")
DEV1_EMAIL = os.getenv("TEST_DEV1_EMAIL", f"dev1@{ORG_DOMAIN}")
DEV2_EMAIL = os.getenv("TEST_DEV2_EMAIL", f"dev2@{ORG_DOMAIN}")
WORKLOAD_PROJECT = os.getenv("WORKLOAD_PROJECT_ID", "my-workload-project")


@pytest.fixture
def test_env_dir(tmp_path):
    """Provides a temporary base directory for isolated user sessions."""
    return str(tmp_path / "agy_test_users")


@pytest.fixture
def dev1_runner(test_env_dir):
    return IsolatedAgyRunner(
        user_email=DEV1_EMAIL,
        base_dir=test_env_dir,
        project_id=WORKLOAD_PROJECT,
    )


@pytest.fixture
def dev2_runner(test_env_dir):
    return IsolatedAgyRunner(
        user_email=DEV2_EMAIL,
        base_dir=test_env_dir,
        project_id=WORKLOAD_PROJECT,
    )


def test_isolated_runner_environment_separation(dev1_runner, dev2_runner):
    """Verify dev1 and dev2 have completely distinct environments and paths."""
    dev1_runner.setup_directories()
    dev2_runner.setup_directories()

    assert dev1_runner.user_home != dev2_runner.user_home
    assert dev1_runner.gcloud_config != dev2_runner.gcloud_config
    assert "dev1" in str(dev1_runner.user_home)
    assert "dev2" in str(dev2_runner.user_home)

    env1 = dev1_runner.get_env()
    env2 = dev2_runner.get_env()

    assert env1["HOME"] == str(dev1_runner.user_home)
    assert env2["HOME"] == str(dev2_runner.user_home)
    assert env1["CLOUDSDK_CONFIG"] == str(dev1_runner.gcloud_config)
    assert env2["CLOUDSDK_CONFIG"] == str(dev2_runner.gcloud_config)


def test_multi_user_quota_lifecycle_simulation():
    """
    Simulate end-to-end multi-user spend lifecycle:
    1. Dev1 consumes moderate tokens (within $10 quota) -> Remains ACTIVE in enabled group.
    2. Dev2 consumes heavy tokens (breaches $10 quota + $2 overage buffer) -> AUTO_DISABLED & moved to disabled group.
    3. New week simulation occurs -> Dev2 restored to ACTIVE and moved back to enabled group.
    """
    mock_db = MockDatabase()
    mock_bq = MockBigQueryUsageClient()
    mock_identity = MockCloudIdentityGroupManager()
    evaluator = QuotaEvaluator(mock_db, mock_bq, mock_identity)

    now = datetime.now(UTC)
    current_week_id, _, _, _ = get_current_week_window(settings.APP_TIMEZONE, now)
    dev1_email = DEV1_EMAIL
    dev2_email = DEV2_EMAIL

    # Seed users in DB
    user1 = User(
        email=dev1_email,
        status=UserStatus.ACTIVE,
        is_exempt=False,
        current_week=CurrentWeekUsage(
            week_id=current_week_id,
            quota_credits_usd=10.00,
            overage_buffer_usd=2.00,
        ),
        created_at=now,
        updated_at=now,
    )
    user2 = User(
        email=dev2_email,
        status=UserStatus.ACTIVE,
        is_exempt=False,
        current_week=CurrentWeekUsage(
            week_id=current_week_id,
            quota_credits_usd=10.00,
            overage_buffer_usd=2.00,
        ),
        created_at=now,
        updated_at=now,
    )
    mock_db.save_users([user1, user2])
    mock_identity.reconcile_user_group(dev1_email, "ENABLED")
    mock_identity.reconcile_user_group(dev2_email, "ENABLED")

    # Ingest synthetic token usage for Dev1 (moderate) and Dev2 (heavy breach)
    records = [
        # Dev1: 3,000,000 tokens of gemini-3.6-flash (~$3.60 gross spend)
        UserUsageRecord(
            user_email=dev1_email,
            model_name="gemini-3.6-flash",
            request_count=100,
            token_count=3_000_000,
            last_active=now,
        ),
        # Dev2: 8,000,000 tokens of gemini-3-pro (~$28.00 gross spend - exceeds $12 hard limit)
        UserUsageRecord(
            user_email=dev2_email,
            model_name="gemini-3-pro",
            request_count=300,
            token_count=8_000_000,
            last_active=now,
        ),
    ]
    mock_bq.set_custom_records(records)

    # Execute Quota Evaluation
    result = evaluator.run_evaluation(triggered_by="multi_user_test", is_publish=True)
    assert result.success is True

    # Verify Dev1 status and group
    dev1_updated = mock_db.get_user(dev1_email)
    assert dev1_updated.status == UserStatus.ACTIVE
    assert mock_identity.is_user_in_enabled_group(dev1_email) is True
    assert mock_identity.is_user_in_disabled_group(dev1_email) is False

    # Verify Dev2 status and group (Throttled)
    dev2_updated = mock_db.get_user(dev2_email)
    assert dev2_updated.status == UserStatus.AUTO_DISABLED
    assert mock_identity.is_user_in_enabled_group(dev2_email) is False
    assert mock_identity.is_user_in_disabled_group(dev2_email) is True

    # Step 3: Simulate weekly reset (advance past wtu_window_reset_at with cleared new-week usage)
    expired_start = now - timedelta(days=7, hours=1)
    expired_reset = now - timedelta(hours=1)
    for email in (dev1_email, dev2_email):
        u = mock_db.get_user(email)
        u.current_week.wtu_window_start_at = expired_start
        u.current_week.wtu_window_reset_at = expired_reset
        mock_db.save_user(u)

    mock_bq.set_custom_records([])
    reset_result = evaluator.run_evaluation(triggered_by="weekly_reset_simulation", is_publish=True)
    assert reset_result.success is True

    dev2_restored = mock_db.get_user(dev2_email)
    assert dev2_restored.status == UserStatus.ACTIVE
    assert dev2_restored.current_week.gross_spend_usd == 0.0
    assert mock_identity.is_user_in_enabled_group(dev2_email) is True
    assert mock_identity.is_user_in_disabled_group(dev2_email) is False


def test_manually_locked_user_persists_across_evaluation():
    """Verify a manually locked user remains locked across an expired 7-day WTU cycle."""
    mock_db = MockDatabase()
    mock_bq = MockBigQueryUsageClient()
    mock_identity = MockCloudIdentityGroupManager()
    evaluator = QuotaEvaluator(mock_db, mock_bq, mock_identity)

    now = datetime.now(UTC)
    dev2_email = DEV2_EMAIL
    expired_start = now - timedelta(days=7, hours=2)
    expired_reset = now - timedelta(hours=2)

    locked_user = User(
        email=dev2_email,
        status=UserStatus.MANUALLY_DISABLED,
        is_exempt=False,
        current_week=CurrentWeekUsage(
            week_id="2026-W35",
            wtu_window_start_at=expired_start,
            wtu_window_reset_at=expired_reset,
            gross_spend_usd=15.00,
            quota_credits_usd=10.00,
            overage_buffer_usd=2.00,
        ),
        created_at=now,
        updated_at=now,
    )
    mock_db.save_users([locked_user])
    mock_identity.reconcile_user_group(dev2_email, "DISABLED")

    # Run evaluation with no usage across the expired 7-day WTU cycle boundary
    mock_bq.set_custom_records([])
    result = evaluator.run_evaluation(triggered_by="manual_lock_test", is_publish=True)
    assert result.success is True

    # User MUST remain MANUALLY_DISABLED and in disabled group even after cycle reset
    dev_after = mock_db.get_user(dev2_email)
    assert dev_after.status == UserStatus.MANUALLY_DISABLED
    assert dev_after.current_week.gross_spend_usd == 0.0
    assert mock_identity.is_user_in_disabled_group(dev2_email) is True
    assert mock_identity.is_user_in_enabled_group(dev2_email) is False


def test_isolated_runner_is_authenticated_and_clean_session_history(dev1_runner):
    """Verify IsolatedAgyRunner.is_authenticated() and clean_session_history()."""
    dev1_runner.setup_directories()
    assert dev1_runner.is_authenticated() is False

    # Create ADC file -> authenticated
    adc_path = dev1_runner.gcloud_config / "application_default_credentials.json"
    adc_path.write_text('{"type": "authorized_user"}')
    assert dev1_runner.is_authenticated() is True
    adc_path.unlink()
    assert dev1_runner.is_authenticated() is False

    # Create OAuth token file and conversation directory
    dev1_runner.token_path.parent.mkdir(parents=True, exist_ok=True)
    dev1_runner.token_path.write_text("oauth-token-value")
    assert dev1_runner.is_authenticated() is True

    conv_dir = dev1_runner.gemini_dir / "antigravity-cli" / "conversations"
    conv_dir.mkdir(parents=True, exist_ok=True)
    (conv_dir / "session1.db").write_text("sqlite-data")
    assert conv_dir.exists()

    # Clean session history removes conversations but preserves OAuth token
    dev1_runner.clean_session_history()
    assert not conv_dir.exists()
    assert dev1_runner.token_path.exists()
    assert dev1_runner.is_authenticated() is True


def test_isolated_runner_run_prompt_flags_and_error_handling(dev1_runner):
    """Verify IsolatedAgyRunner.run_prompt() model suffix stripping, --effort flag, JSON parsing, and error handling."""
    import subprocess
    from unittest.mock import MagicMock, patch

    # 1. Model with -high suffix on 3-pro strips suffix, passes --effort high, and parses JSON stdout
    completed_proc = MagicMock(
        returncode=0,
        stdout='{"response": "ok", "tokens": 42}',
        stderr="",
    )
    with patch("subprocess.run", return_value=completed_proc) as mock_run:
        res = dev1_runner.run_prompt(prompt="Hello", model="gemini-3-pro-high", timeout=30)
        assert res.success is True
        assert res.exit_code == 0
        assert res.parsed_json == {"response": "ok", "tokens": 42}
        called_cmd = mock_run.call_args[0][0]
        assert "--model" in called_cmd
        assert called_cmd[called_cmd.index("--model") + 1] == "gemini-3-pro"
        assert "--effort" in called_cmd
        assert called_cmd[called_cmd.index("--effort") + 1] == "high"

    # 2. TimeoutExpired returns exit_code == -1
    timeout_exc = subprocess.TimeoutExpired(cmd=["agy"], timeout=10, output=b"partial output")
    with patch("subprocess.run", side_effect=timeout_exc):
        res_timeout = dev1_runner.run_prompt(prompt="Slow prompt", timeout=10)
        assert res_timeout.success is False
        assert res_timeout.exit_code == -1
        assert res_timeout.stdout == "partial output"
        assert "Timed out after 10s" in res_timeout.stderr

    # 3. FileNotFoundError returns exit_code == -2
    with patch("subprocess.run", side_effect=FileNotFoundError("No such file")):
        res_missing = dev1_runner.run_prompt(prompt="Missing binary")
        assert res_missing.success is False
        assert res_missing.exit_code == -2
        assert "agy binary not found" in res_missing.stderr

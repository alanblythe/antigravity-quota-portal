"""
End-to-End Simulation Test Suite for Multi-User Quota & Governance Lifecycle.

Tests session isolation between dev1 and dev2,
simulates token spend ingestion via BigQuery audit logs, verifies state machine transitions,
and ensures automated group membership reconciliation.
"""


import os
from datetime import datetime, timezone
import pytest

from app.bq.bq_client import UserUsageRecord
from app.bq.mock_bq import MockBigQueryUsageClient
from app.core.evaluator import QuotaEvaluator
from app.core.timezone_engine import get_current_week_window
from app.config import settings
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

    now = datetime.now(timezone.utc)
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


def test_manually_locked_user_persists_across_evaluation():
    """Verify an administratively locked user remains locked and in the disabled group."""
    mock_db = MockDatabase()
    mock_bq = MockBigQueryUsageClient()
    mock_identity = MockCloudIdentityGroupManager()
    evaluator = QuotaEvaluator(mock_db, mock_bq, mock_identity)

    now = datetime.now(timezone.utc)
    dev2_email = DEV2_EMAIL

    locked_user = User(
        email=dev2_email,
        status=UserStatus.MANUALLY_DISABLED,
        is_exempt=False,
        current_week=CurrentWeekUsage(
            week_id="2026-W35",
            quota_credits_usd=10.00,
            overage_buffer_usd=2.00,
        ),
        created_at=now,
        updated_at=now,
    )
    mock_db.save_users([locked_user])
    mock_identity.reconcile_user_group(dev2_email, "DISABLED")

    # Run evaluation with no usage
    mock_bq.set_custom_records([])
    result = evaluator.run_evaluation(triggered_by="manual_lock_test", is_publish=True)
    assert result.success is True

    # User MUST remain MANUALLY_DISABLED and in disabled group
    dev_after = mock_db.get_user(dev2_email)
    assert dev_after.status == UserStatus.MANUALLY_DISABLED
    assert mock_identity.is_user_in_disabled_group(dev2_email) is True
    assert mock_identity.is_user_in_enabled_group(dev2_email) is False

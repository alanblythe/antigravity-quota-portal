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

    bob = mock_db.get_user("bob.martin@example.com")
    charlie = mock_db.get_user("charlie.davis@example.com")
    assert bob.status == UserStatus.ACTIVE
    assert bob.current_week.gross_spend_usd == 8.55
    assert charlie.status == UserStatus.AUTO_DISABLED
    throttled_users = [u for u in mock_db.list_users() if u.status == UserStatus.AUTO_DISABLED]
    assert len(throttled_users) == 1
    assert mock_identity.is_user_in_enabled_group("bob.martin@example.com") is True
    assert mock_identity.is_user_in_disabled_group("charlie.davis@example.com") is True
    assert mock_identity.is_user_in_enabled_group("alice.chen@example.com") is True


def test_evaluator_mock_services_default(evaluator, mock_db, mock_identity):
    """Verify default mock evaluation keeps bob.martin ACTIVE ($8.55 warning) and throttles only charlie."""
    result = evaluator.run_evaluation(triggered_by="default_mock_check", is_publish=True)
    assert result.success is True

    bob = mock_db.get_user("bob.martin@example.com")
    assert bob.status == UserStatus.ACTIVE
    assert bob.current_week.gross_spend_usd == 8.55
    assert bob.current_week.credit_utilization_percentage == 85.5
    assert mock_identity.is_user_in_enabled_group("bob.martin@example.com") is True

    throttled = [u for u in mock_db.list_users() if u.status == UserStatus.AUTO_DISABLED]
    assert [u.email for u in throttled] == ["charlie.davis@example.com"]


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
    # Default quota is $10.00 + $2.00 overage (Bob has $8.55 gross spend -> ~85.5% warning state, ACTIVE).
    # Alice has custom quota of $25.00 + $5.00 overage (spend ~$11.33, under $25.00).
    result = evaluator.run_evaluation(triggered_by="test_custom", is_publish=True)
    assert result.success is True

    alice = mock_db.get_user("alice.chen@example.com")
    assert alice.has_custom_quota is True
    assert alice.custom_quota_usd == 25.00
    assert alice.status == UserStatus.ACTIVE
    assert mock_identity.is_user_in_enabled_group("alice.chen@example.com") is True

    bob = mock_db.get_user("bob.martin@example.com")
    assert bob.status == UserStatus.ACTIVE
    assert bob.current_week.gross_spend_usd == 8.55


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


def test_evaluator_dual_membership_drift_reconciliation(evaluator, mock_db, mock_bq, mock_identity):
    """Verify evaluator removes user from disabled group if they are accidentally in both groups."""
    # Alice is already in enabled_group; also add her to disabled_group
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, "alice.chen@example.com")
    assert mock_identity.is_user_in_enabled_group("alice.chen@example.com") is True
    assert mock_identity.is_user_in_disabled_group("alice.chen@example.com") is True

    result = evaluator.run_evaluation(triggered_by="reconcile_dual_drift", is_publish=True)
    assert result.success is True

    assert mock_identity.is_user_in_enabled_group("alice.chen@example.com") is True
    assert mock_identity.is_user_in_disabled_group("alice.chen@example.com") is False


def test_evaluator_skips_status_update_when_identity_swap_fails(mock_db, mock_bq, mock_identity):
    """Verify user status is not updated in Firestore if Cloud Identity group swap returns False."""
    from app.core.evaluator import QuotaEvaluator

    failing_identity = MagicMock(wraps=mock_identity)
    failing_identity.reconcile_user_group.return_value = False

    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=failing_identity,
    )

    # Charlie starts ACTIVE in DB and breaches quota in mock_bq
    charlie_before = mock_db.get_user("charlie.davis@example.com")
    charlie_before.status = UserStatus.ACTIVE
    mock_db.save_user(charlie_before)
    mock_identity.add_member_to_group(mock_identity.enabled_group_email, "charlie.davis@example.com")

    res = eval_engine.run_evaluation(triggered_by="swap_fail_test", is_publish=False)
    assert res.success is True
    assert res.group_swaps_count == 0

    # Charlie should remain ACTIVE so the next evaluation run retries the group swap
    charlie_after = mock_db.get_user("charlie.davis@example.com")
    assert charlie_after.status == UserStatus.ACTIVE


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


def test_bq_client_sharded_and_fallback_queries():
    """Verify BigQueryUsageClient queries wildcard sharded tables first and falls back to unsharded table."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from google.api_core.exceptions import NotFound

    from app.bq.bq_client import BigQueryUsageClient

    mock_gcp_bq = MagicMock()
    with patch("app.bq.bq_client.bigquery.Client", return_value=mock_gcp_bq):
        client = BigQueryUsageClient(project_id="test-proj", dataset_name="test_ds")

    # Simulate NotFound on wildcard table (`businessaicode_googleapis_com_inference_response_*`),
    # then success on unsharded fallback (`businessaicode_googleapis_com_inference_response`).
    row = {
        "user_email": "dev@example.com",
        "model_name": "gemini-3-pro",
        "request_count": 5,
        "token_count": 10000,
        "total_tokens": 10000,
        "prompt_tokens": 8000,
        "candidates_tokens": 1500,
        "cached_tokens": 1000,
        "thoughts_tokens": 500,
        "fallback_tokens": 0,
        "last_active": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
    }

    fallback_job = MagicMock()
    fallback_job.result.return_value = [row]
    mock_gcp_bq.query.side_effect = [NotFound("Wildcard table not found"), fallback_job]

    start_utc = datetime(2026, 10, 5, 0, 0, tzinfo=UTC)
    end_utc = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)
    records = client.fetch_weekly_usage(start_utc, end_utc)

    assert mock_gcp_bq.query.call_count == 2
    first_sql = mock_gcp_bq.query.call_args_list[0][0][0]
    second_sql = mock_gcp_bq.query.call_args_list[1][0][0]
    assert "businessaicode_googleapis_com_inference_response_*" in first_sql
    assert "_TABLE_SUFFIX BETWEEN" in first_sql
    assert "`test-proj.test_ds.businessaicode_googleapis_com_inference_response`" in second_sql

    assert len(records) == 1
    assert records[0].user_email == "dev@example.com"
    assert records[0].prompt_tokens == 8000
    assert records[0].candidates_tokens == 1500
    assert records[0].cached_tokens == 1000
    assert records[0].thoughts_tokens == 500


def test_wtu_client_filters_expired_windows_and_populates_per_user():
    """Verify CloudMonitoringWTUClient ignores expired 7-day windows and populates per_user_wtu."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from app.bq.wtu_client import WEEK_SECONDS, CloudMonitoringWTUClient

    now_ts = int(datetime.now(UTC).timestamp())
    active_start = str(now_ts - 3600)  # Started 1h ago (valid)
    expired_start = str(now_ts - WEEK_SECONDS - 3600)  # Started 7d + 1h ago (expired)

    usage_series = [
        {
            "resource": {
                "labels": {"project_id": "workload-proj", "location": "global", "ge_user": "dev1@example.com"}
            },
            "metric": {"labels": {"window_start_time": active_start}},
            "points": [{"value": {"int64Value": "12500"}}],
        },
        {
            "resource": {
                "labels": {"project_id": "workload-proj", "location": "global", "ge_user": "dev1@example.com"}
            },
            "metric": {"labels": {"window_start_time": expired_start}},
            "points": [{"value": {"int64Value": "99999"}}],
        },
    ]
    limit_series = [
        {
            "resource": {"labels": {"project_id": "workload-proj", "location": "global"}},
            "metric": {"labels": {}},
            "points": [{"value": {"int64Value": "100000"}}],
        }
    ]

    client = CloudMonitoringWTUClient(project_id="workload-proj", quota_project_id="host-proj")
    with (
        patch.object(client, "_get_access_token", return_value="fake-token"),
        patch.object(client, "_query_time_series", side_effect=[usage_series, limit_series]),
    ):
        status = client.fetch_wtu_status()

    assert status.active is True
    assert status.total_active_wtu_consumed == 12500
    assert status.wtu_quota_limit == 100000
    assert status.wtu_utilization_pct == 12.5
    assert "dev1@example.com" in status.per_user_wtu
    assert status.per_user_wtu["dev1@example.com"].wtu_consumed == 12500


def test_wtu_locked_weekly_reset_rollover(mock_db, mock_bq, mock_identity):
    """Verify Weekly Reset triggers when a project's 7-day WTU sliding cycle rolls over."""
    from datetime import UTC, datetime, timedelta

    from app.bq.wtu_client import WTUQuotaStatus
    from app.core.evaluator import QuotaEvaluator
    from app.core.timezone_engine import get_current_week_window

    now = datetime.now(UTC)
    config = mock_db.get_config()
    week_id, _, _, _ = get_current_week_window(config.timezone, now)

    prev_cycle_start = now - timedelta(days=7, hours=1)
    prev_cycle_reset = prev_cycle_start + timedelta(days=7)
    new_cycle_start = now - timedelta(minutes=30)
    new_cycle_reset = new_cycle_start + timedelta(days=7)

    # Developer was AUTO_DISABLED during the previous 7-day WTU cycle (within the same ISO week_id)
    throttled_user = User(
        email="wtu.rollover@example.com",
        status=UserStatus.AUTO_DISABLED,
        project_id="workload-project-plus",
        license_tier="ENTERPRISE_PLUS",
        current_week=CurrentWeekUsage(
            week_id=week_id,
            total_tokens=450000,
            gross_spend_usd=15.00,
            quota_credits_usd=10.00,
            net_billable_cost_usd=5.00,
            wtu_window_start_at=prev_cycle_start,
            wtu_window_reset_at=prev_cycle_reset,
        ),
    )
    mock_db.save_user(throttled_user)
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, throttled_user.email)
    mock_bq.set_custom_records([])

    mock_wtu = MagicMock()
    mock_wtu.fetch_multi_project_wtu_status.return_value = {
        "workload-project-plus": WTUQuotaStatus(
            project_id="workload-project-plus",
            license_tier="ENTERPRISE_PLUS",
            active=True,
            tier_metric="agentic_wtu_tier_enterprise_plus_pp_weekly_regional",
            total_active_wtu_consumed=1200,
            wtu_quota_limit=500000,
            wtu_utilization_pct=0.2,
            active_cycle_start_utc=new_cycle_start,
            active_cycle_reset_utc=new_cycle_reset,
            days_until_reset=7.0,
        )
    }

    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
        wtu_client=mock_wtu,
    )

    result = eval_engine.run_evaluation(triggered_by="wtu_cycle_reset", is_publish=True)
    assert result.success is True

    updated = mock_db.get_user("wtu.rollover@example.com")
    assert updated.status == UserStatus.ACTIVE
    assert updated.current_week.wtu_window_start_at == new_cycle_start
    assert updated.current_week.wtu_window_reset_at == new_cycle_reset
    assert mock_identity.is_user_in_enabled_group("wtu.rollover@example.com") is True
    assert mock_identity.is_user_in_disabled_group("wtu.rollover@example.com") is False


def test_multi_project_tier_attribution(mock_db, mock_bq, mock_identity):
    """Verify developers across Enterprise Plus and Standard projects get distinct project_id, tier, and reset."""
    from datetime import UTC, datetime, timedelta

    from app.bq.wtu_client import WTUQuotaStatus
    from app.core.evaluator import QuotaEvaluator

    now = datetime.now(UTC)
    config = mock_db.get_config()
    config.monitored_projects = ["workload-project-plus", "workload-project-standard"]
    mock_db.update_config(config)

    plus_reset = now + timedelta(days=2, hours=21)
    std_reset = now + timedelta(days=5, hours=4)

    mock_wtu = MagicMock()
    mock_wtu.fetch_multi_project_wtu_status.return_value = {
        "workload-project-plus": WTUQuotaStatus(
            project_id="workload-project-plus",
            license_tier="ENTERPRISE_PLUS",
            active=True,
            tier_metric="agentic_wtu_tier_enterprise_plus_pp_weekly_regional",
            total_active_wtu_consumed=25000,
            wtu_quota_limit=500000,
            wtu_utilization_pct=5.0,
            active_cycle_start_utc=plus_reset - timedelta(days=7),
            active_cycle_reset_utc=plus_reset,
            days_until_reset=2.9,
        ),
        "workload-project-standard": WTUQuotaStatus(
            project_id="workload-project-standard",
            license_tier="ENTERPRISE_STANDARD",
            active=True,
            tier_metric="agentic_wtu_tier_enterprise_standard_pp_weekly_regional",
            total_active_wtu_consumed=48000,
            wtu_quota_limit=150000,
            wtu_utilization_pct=32.0,
            active_cycle_start_utc=std_reset - timedelta(days=7),
            active_cycle_reset_utc=std_reset,
            days_until_reset=5.2,
        ),
    }

    mock_bq.set_custom_records(
        [
            UserUsageRecord(
                user_email="plus.dev@example.com",
                model_name="gemini-3.8-flash",
                request_count=10,
                token_count=50000,
                project_id="workload-project-plus",
            ),
            UserUsageRecord(
                user_email="std.dev@example.com",
                model_name="gemini-3.8-flash",
                request_count=15,
                token_count=75000,
                project_id="workload-project-standard",
            ),
        ]
    )

    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
        wtu_client=mock_wtu,
    )
    res = eval_engine.run_evaluation(triggered_by="multi_proj_test", is_publish=True)
    assert res.success is True

    plus_user = mock_db.get_user("plus.dev@example.com")
    std_user = mock_db.get_user("std.dev@example.com")

    assert plus_user.project_id == "workload-project-plus"
    assert plus_user.license_tier == "ENTERPRISE_PLUS"
    assert plus_user.current_week.wtu_window_reset_at == plus_reset

    assert std_user.project_id == "workload-project-standard"
    assert std_user.license_tier == "ENTERPRISE_STANDARD"
    assert std_user.current_week.wtu_window_reset_at == std_reset


def test_staggered_multi_project_wtu_cycles_do_not_cross_contaminate(mock_db, mock_bq, mock_identity):
    """Verify a newly reset project excludes pre-reset logs even when another project's cycle started earlier."""
    from datetime import UTC, datetime, timedelta

    from app.bq.wtu_client import WTUQuotaStatus
    from app.core.evaluator import QuotaEvaluator
    from app.core.timezone_engine import get_current_week_window

    now = datetime.now(UTC)
    config = mock_db.get_config()
    config.monitored_projects = ["workload-project-plus", "workload-project-standard"]
    mock_db.update_config(config)
    week_id, _, _, _ = get_current_week_window(config.timezone, now)

    # Project Plus started 6 days ago; Project Standard just reset 30 minutes ago
    plus_start = now - timedelta(days=6)
    plus_reset = plus_start + timedelta(days=7)
    std_prev_start = now - timedelta(days=7, minutes=30)
    std_prev_reset = now - timedelta(minutes=30)
    std_new_start = now - timedelta(minutes=30)
    std_new_reset = std_new_start + timedelta(days=7)

    std_user = User(
        email="std.throttled@example.com",
        status=UserStatus.AUTO_DISABLED,
        project_id="workload-project-standard",
        license_tier="ENTERPRISE_STANDARD",
        current_week=CurrentWeekUsage(
            week_id=week_id,
            total_tokens=9000000,
            gross_spend_usd=25.00,
            quota_credits_usd=10.00,
            net_billable_cost_usd=15.00,
            wtu_window_start_at=std_prev_start,
            wtu_window_reset_at=std_prev_reset,
        ),
    )
    mock_db.save_user(std_user)
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, std_user.email)

    # Pre-reset usage record from 2 days ago (inside Project Plus's window, but BEFORE Project Standard's reset!)
    mock_bq.set_custom_records(
        [
            UserUsageRecord(
                user_email="std.throttled@example.com",
                model_name="gemini-3.8-flash",
                request_count=200,
                token_count=9000000,
                prompt_tokens=6000000,
                candidates_tokens=3000000,
                project_id="workload-project-standard",
                last_active=now - timedelta(days=2),
            )
        ]
    )

    mock_wtu = MagicMock()
    mock_wtu.fetch_multi_project_wtu_status.return_value = {
        "workload-project-plus": WTUQuotaStatus(
            project_id="workload-project-plus",
            license_tier="ENTERPRISE_PLUS",
            active=True,
            active_cycle_start_utc=plus_start,
            active_cycle_reset_utc=plus_reset,
        ),
        "workload-project-standard": WTUQuotaStatus(
            project_id="workload-project-standard",
            license_tier="ENTERPRISE_STANDARD",
            active=True,
            active_cycle_start_utc=std_new_start,
            active_cycle_reset_utc=std_new_reset,
        ),
    }

    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
        wtu_client=mock_wtu,
    )

    # Run 1 (reset run) AND Run 2 (subsequent hourly cron)
    eval_engine.run_evaluation(triggered_by="run1_reset", is_publish=False)
    eval_engine.run_evaluation(triggered_by="run2_hourly", is_publish=False)

    after = mock_db.get_user("std.throttled@example.com")
    assert after.status == UserStatus.ACTIVE
    assert after.current_week.gross_spend_usd == 0.0
    assert mock_identity.is_user_in_enabled_group("std.throttled@example.com") is True


def test_expired_wtu_window_resets_even_when_project_idle(mock_db, mock_bq, mock_identity):
    """Verify AUTO_DISABLED developer resets when wtu_window_reset_at passes even if Cloud Monitoring is idle."""
    from datetime import UTC, datetime, timedelta

    from app.bq.wtu_client import WTUQuotaStatus
    from app.core.evaluator import QuotaEvaluator
    from app.core.timezone_engine import get_current_week_window

    now = datetime.now(UTC)
    config = mock_db.get_config()
    config.monitored_projects = ["workload-project-plus"]
    mock_db.update_config(config)
    week_id, _, _, _ = get_current_week_window(config.timezone, now)

    expired_start = now - timedelta(days=7, seconds=10)
    expired_reset = now - timedelta(seconds=10)

    idle_throttled = User(
        email="idle.throttled@example.com",
        status=UserStatus.AUTO_DISABLED,
        project_id="workload-project-plus",
        license_tier="ENTERPRISE_PLUS",
        current_week=CurrentWeekUsage(
            week_id=week_id,
            total_tokens=8000000,
            gross_spend_usd=20.00,
            quota_credits_usd=10.00,
            net_billable_cost_usd=10.00,
            wtu_window_start_at=expired_start,
            wtu_window_reset_at=expired_reset,
        ),
    )
    mock_db.save_user(idle_throttled)
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, idle_throttled.email)
    mock_bq.set_custom_records([])

    # Cloud Monitoring reports active=False because all developers were throttled and no new inference occurred yet
    mock_wtu = MagicMock()
    mock_wtu.fetch_multi_project_wtu_status.return_value = {
        "workload-project-plus": WTUQuotaStatus(
            project_id="workload-project-plus",
            license_tier="ENTERPRISE_PLUS",
            active=False,
            active_cycle_start_utc=None,
            active_cycle_reset_utc=None,
        )
    }

    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
        wtu_client=mock_wtu,
    )

    res = eval_engine.run_evaluation(triggered_by="WTU_RESET_TIMER:workload-project-plus", is_publish=False)
    assert res.success is True

    after = mock_db.get_user("idle.throttled@example.com")
    assert after.status == UserStatus.ACTIVE
    assert after.current_week.wtu_window_start_at == expired_reset
    assert after.current_week.wtu_window_reset_at == expired_reset + timedelta(days=7)
    assert mock_identity.is_user_in_enabled_group("idle.throttled@example.com") is True
    assert mock_identity.is_user_in_disabled_group("idle.throttled@example.com") is False


def test_updated_at_does_not_block_bq_project_attribution(mock_db, mock_bq, mock_identity):
    """Verify save_users advancing user.updated_at does not block bq_proj attribution on subsequent runs."""
    from datetime import timedelta

    from app.core.evaluator import QuotaEvaluator

    config = mock_db.get_config()
    config.monitored_projects = ["workload-project-plus", "workload-project-standard"]
    mock_db.update_config(config)

    # First run saves all users and sets user.updated_at = now
    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
    )
    mock_bq.set_custom_records([])
    eval_engine.run_evaluation(triggered_by="run1_touch_updated_at", is_publish=False)

    alice_before = mock_db.get_user("alice.chen@example.com")
    assert alice_before.updated_at is not None

    # Subsequent run sees BQ usage record with a timestamp slightly older than alice_before.updated_at
    mock_bq.set_custom_records(
        [
            UserUsageRecord(
                user_email="alice.chen@example.com",
                model_name="gemini-3.8-flash",
                request_count=25,
                token_count=125000,
                project_id="workload-project-standard",
                last_active=alice_before.updated_at - timedelta(minutes=5),
            )
        ]
    )
    res = eval_engine.run_evaluation(triggered_by="run2_bq_proj", is_publish=False)
    assert res.success is True

    alice_after = mock_db.get_user("alice.chen@example.com")
    assert alice_after.project_id == "workload-project-standard"


def test_wtu_client_deduplicates_active_series_and_scales_ppu_pool_limit():
    """Verify WTU client deduplicates active series by latest window_start_time and scales ppu pool limit."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    from app.bq.wtu_client import CloudMonitoringWTUClient

    now_ts = int(datetime.now(UTC).timestamp())
    older_active = str(now_ts - 7200)
    newer_active = str(now_ts - 1800)

    usage_series = [
        # dev1 has two non-expired series within 24h; only newer_active (4000) should be counted
        {
            "resource": {
                "labels": {"project_id": "workload-proj", "location": "global", "ge_user": "dev1@example.com"}
            },
            "metric": {"labels": {"window_start_time": older_active}},
            "points": [{"value": {"int64Value": "9000"}}],
        },
        {
            "resource": {
                "labels": {"project_id": "workload-proj", "location": "global", "ge_user": "dev1@example.com"}
            },
            "metric": {"labels": {"window_start_time": newer_active}},
            "points": [{"value": {"int64Value": "4000"}}],
        },
        # dev2 has one active series (6000)
        {
            "resource": {
                "labels": {"project_id": "workload-proj", "location": "global", "ge_user": "dev2@example.com"}
            },
            "metric": {"labels": {"window_start_time": newer_active}},
            "points": [{"value": {"int64Value": "6000"}}],
        },
    ]
    limit_series = [
        {
            "resource": {"labels": {"project_id": "workload-proj", "location": "global"}},
            "metric": {"labels": {}},
            "points": [{"value": {"int64Value": "10000"}}],
        }
    ]

    client = CloudMonitoringWTUClient(project_id="workload-proj", quota_project_id="host-proj")
    with (
        patch.object(client, "_get_access_token", return_value="fake-token"),
        patch.object(client, "_query_time_series", side_effect=[usage_series, limit_series]),
    ):
        status = client.fetch_wtu_status(
            preferred_tier="agentic_wtu_tier_enterprise_plus_ppu_weekly_regional"
        )

    assert status.active is True
    assert status.total_active_wtu_consumed == 10000  # 4000 + 6000 (older 9000 deduplicated)
    assert status.per_user_wtu["dev1@example.com"].wtu_consumed == 4000
    assert status.per_user_wtu["dev1@example.com"].wtu_limit == 10000
    assert status.per_user_wtu["dev2@example.com"].wtu_consumed == 6000
    assert status.per_user_wtu["dev2@example.com"].wtu_limit == 10000
    # Pool limit for ppu tier scales by active user count (10000 * 2 = 20000)
    assert status.wtu_quota_limit == 20000
    assert status.wtu_utilization_pct == 50.0


def test_bq_client_queries_unsharded_directly_when_list_tables_finds_only_partitioned():
    """Verify BigQueryUsageClient skips failing wildcard query when list_tables detects only unsharded table."""
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.bq.bq_client import BigQueryUsageClient

    mock_gcp_bq = MagicMock()
    mock_gcp_bq.list_tables.return_value = [
        SimpleNamespace(table_id="businessaicode_googleapis_com_inference_response")
    ]
    row = {
        "user_email": "dev@example.com",
        "model_name": "gemini-3.8-flash",
        "request_count": 3,
        "token_count": 5000,
        "prompt_tokens": 4000,
        "candidates_tokens": 1000,
        "cached_tokens": 0,
        "thoughts_tokens": 0,
        "fallback_tokens": 0,
        "last_active": datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
    }
    job = MagicMock()
    job.result.return_value = [row]
    mock_gcp_bq.query.return_value = job

    with patch("app.bq.bq_client.bigquery.Client", return_value=mock_gcp_bq):
        client = BigQueryUsageClient(project_id="test-proj", dataset_name="test_ds")

    records = client.fetch_weekly_usage(
        datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
        datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        default_workload_project_id="custom-workload-proj",
    )
    assert mock_gcp_bq.query.call_count == 1
    sql = mock_gcp_bq.query.call_args_list[0][0][0]
    job_cfg = mock_gcp_bq.query.call_args_list[0][1]["job_config"]
    assert "businessaicode_googleapis_com_inference_response_*" not in sql
    assert "@fallback_project_id" in sql
    assert any(
        p.name == "fallback_project_id" and p.value == "custom-workload-proj"
        for p in job_cfg.query_parameters
    )
    assert len(records) == 1
    assert records[0].project_id == "custom-workload-proj"


def test_evaluator_scopes_per_user_wtu_to_assigned_project_and_clears_stale_wtu(
    mock_db, mock_bq, mock_identity
):
    """Verify per-user WTU lookup uses the user's assigned project and clears stale WTU fields when absent."""
    from datetime import UTC, datetime, timedelta

    from app.bq.wtu_client import UserWTUStatus, WTUQuotaStatus
    from app.core.evaluator import QuotaEvaluator

    now = datetime.now(UTC)
    config = mock_db.get_config()
    config.monitored_projects = ["proj-a", "proj-b"]
    mock_db.update_config(config)

    alice = mock_db.get_user("alice.chen@example.com")
    alice.project_id = "proj-a"
    mock_db.save_user(alice)

    bob = mock_db.get_user("bob.martin@example.com")
    bob.project_id = "proj-a"
    bob.current_week.wtu_net_usage = 5000.0
    bob.current_week.wtu_limit = 10000.0
    bob.current_week.wtu_utilization_pct = 50.0
    mock_db.save_user(bob)

    mock_bq.set_custom_records([])

    # Alice has different WTU values in proj-a (3000) vs proj-b (9000); Bob has no WTU entry
    mock_wtu = MagicMock()
    mock_wtu.fetch_multi_project_wtu_status.return_value = {
        "proj-a": WTUQuotaStatus(
            project_id="proj-a",
            active=True,
            active_cycle_start_utc=now - timedelta(days=1),
            active_cycle_reset_utc=now + timedelta(days=6),
            per_user_wtu={
                "alice.chen@example.com": UserWTUStatus(
                    ge_user="alice.chen@example.com",
                    wtu_consumed=3000,
                    wtu_limit=10000,
                    wtu_utilization_pct=30.0,
                )
            },
        ),
        "proj-b": WTUQuotaStatus(
            project_id="proj-b",
            active=True,
            active_cycle_start_utc=now - timedelta(days=2),
            active_cycle_reset_utc=now + timedelta(days=5),
            per_user_wtu={
                "alice.chen@example.com": UserWTUStatus(
                    ge_user="alice.chen@example.com",
                    wtu_consumed=9000,
                    wtu_limit=10000,
                    wtu_utilization_pct=90.0,
                )
            },
        ),
    }

    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
        wtu_client=mock_wtu,
    )
    res = eval_engine.run_evaluation(triggered_by="scoped_wtu_test", is_publish=False)
    assert res.success is True

    alice_after = mock_db.get_user("alice.chen@example.com")
    assert alice_after.current_week.wtu_net_usage == 3000.0
    assert alice_after.current_week.wtu_utilization_pct == 30.0

    bob_after = mock_db.get_user("bob.martin@example.com")
    assert bob_after.current_week.wtu_net_usage is None
    assert bob_after.current_week.wtu_limit is None
    assert bob_after.current_week.wtu_utilization_pct is None


def test_wtu_client_query_time_series_paginates_next_page_token():
    """Verify CloudMonitoringWTUClient._query_time_series follows nextPageToken across pages."""
    from unittest.mock import patch

    from app.bq.wtu_client import CloudMonitoringWTUClient

    page1_resp = MagicMock()
    page1_resp.status_code = 200
    page1_resp.json.return_value = {
        "timeSeries": [{"points": [{"value": {"int64Value": "100"}}]}],
        "nextPageToken": "token-page-2",
    }
    page2_resp = MagicMock()
    page2_resp.status_code = 200
    page2_resp.json.return_value = {
        "timeSeries": [{"points": [{"value": {"int64Value": "200"}}]}],
    }

    mock_http_client = MagicMock()
    mock_http_client.__enter__.return_value.get.side_effect = [page1_resp, page2_resp]

    client = CloudMonitoringWTUClient(project_id="workload-proj")
    with patch("app.bq.wtu_client.httpx.Client", return_value=mock_http_client):
        series = client._query_time_series("fake-token", "some.metric")

    assert len(series) == 2
    assert mock_http_client.__enter__.return_value.get.call_count == 2


def test_bq_client_merge_records_sums_granular_token_counts():
    """Verify BigQueryUsageClient._merge_records merges duplicate (project_id, user_email, model_name) entries."""
    from datetime import UTC, datetime

    from app.bq.bq_client import BigQueryUsageClient

    t1 = datetime(2026, 10, 6, 10, 0, tzinfo=UTC)
    t2 = datetime(2026, 10, 7, 14, 30, tzinfo=UTC)

    sharded_rec = UserUsageRecord(
        user_email="  Alice.Chen@Example.com ",
        project_id="workload-proj-1",
        model_name="gemini-3-pro",
        request_count=10,
        token_count=100000,
        prompt_tokens=70000,
        candidates_tokens=15000,
        cached_tokens=10000,
        thoughts_tokens=5000,
        prompt_tokens_gt_200k=20000,
        candidates_tokens_gt_200k=5000,
        cached_tokens_gt_200k=2000,
        thoughts_tokens_gt_200k=1000,
        fallback_tokens=0,
        last_active=t1,
    )
    unsharded_rec = UserUsageRecord(
        user_email="alice.chen@example.com",
        project_id="workload-proj-1",
        model_name="gemini-3-pro",
        request_count=5,
        token_count=50000,
        prompt_tokens=35000,
        candidates_tokens=8000,
        cached_tokens=5000,
        thoughts_tokens=2000,
        prompt_tokens_gt_200k=10000,
        candidates_tokens_gt_200k=2000,
        cached_tokens_gt_200k=1000,
        thoughts_tokens_gt_200k=500,
        fallback_tokens=1000,
        last_active=t2,
    )

    merged = BigQueryUsageClient._merge_records([sharded_rec, unsharded_rec])
    assert len(merged) == 1
    rec = merged[0]
    assert rec.user_email == "alice.chen@example.com"
    assert rec.project_id == "workload-proj-1"
    assert rec.model_name == "gemini-3-pro"
    assert rec.request_count == 15
    assert rec.token_count == 150000
    assert rec.prompt_tokens == 105000
    assert rec.candidates_tokens == 23000
    assert rec.cached_tokens == 15000
    assert rec.thoughts_tokens == 7000
    assert rec.prompt_tokens_gt_200k == 30000
    assert rec.candidates_tokens_gt_200k == 7000
    assert rec.cached_tokens_gt_200k == 3000
    assert rec.thoughts_tokens_gt_200k == 1500
    assert rec.fallback_tokens == 1000
    assert rec.last_active == t2


def test_bq_client_fetch_weekly_usage_reraises_forbidden():
    """Verify BigQueryUsageClient.fetch_weekly_usage re-raises fatal Forbidden (403) errors."""
    from datetime import UTC, datetime
    from unittest.mock import patch

    import pytest
    from google.api_core.exceptions import Forbidden

    from app.bq.bq_client import BigQueryUsageClient

    mock_gcp_bq = MagicMock()
    mock_gcp_bq.query.side_effect = Forbidden("Access Denied: BigQuery dataset permission denied")

    with patch("app.bq.bq_client.bigquery.Client", return_value=mock_gcp_bq):
        client = BigQueryUsageClient(project_id="test-proj", dataset_name="test_ds")

    with pytest.raises(Forbidden):
        client.fetch_weekly_usage(
            datetime(2026, 10, 5, 0, 0, tzinfo=UTC),
            datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
        )


def test_evaluator_weekly_reset_swap_failure_preserves_prior_week_usage(
    mock_db, mock_bq, mock_identity
):
    """Verify failed group swap during weekly reset preserves prior-week tokens, spend, and reset timestamp."""
    from datetime import UTC, datetime, timedelta

    from app.core.evaluator import QuotaEvaluator

    now = datetime.now(UTC)
    past_start = now - timedelta(days=8)
    past_reset = now - timedelta(days=1)

    user = User(
        email="reset.retry@example.com",
        status=UserStatus.AUTO_DISABLED,
        current_week=CurrentWeekUsage(
            week_id="2026-W01",
            total_tokens=6000000,
            gross_spend_usd=14.50,
            quota_credits_usd=10.00,
            remaining_credit_usd=0.00,
            net_billable_cost_usd=4.50,
            wtu_window_start_at=past_start,
            wtu_window_reset_at=past_reset,
        ),
    )
    mock_db.save_user(user)
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, user.email)
    mock_bq.set_custom_records([])

    failing_identity = MagicMock(wraps=mock_identity)
    failing_identity.reconcile_user_group.return_value = False

    eval_engine = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=failing_identity,
    )
    res = eval_engine.run_evaluation(triggered_by="weekly_reset_fail_test", is_publish=False)
    assert res.success is True

    saved_user = mock_db.get_user("reset.retry@example.com")
    assert saved_user.status == UserStatus.AUTO_DISABLED
    assert saved_user.current_week.total_tokens == 6000000
    assert saved_user.current_week.gross_spend_usd == 14.50
    assert saved_user.current_week.wtu_window_reset_at == past_reset
    assert mock_db.list_weekly_snapshots("reset.retry@example.com") == []




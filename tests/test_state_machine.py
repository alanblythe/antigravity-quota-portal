"""Unit tests for dual-group state transitions."""

from app.core.state_machine import evaluate_user_state_and_group
from app.db.models import UserStatus


def test_active_user_under_quota():
    res = evaluate_user_state_and_group(
        current_status=UserStatus.ACTIVE,
        current_in_enabled_group=True,
        is_exempt=False,
        gross_spend_usd=5.00,
        quota_credits_usd=10.00,
        overage_buffer_usd=2.00,
    )
    assert res.new_status == UserStatus.ACTIVE
    assert res.target_group == "ENABLED"
    assert res.state_changed is False
    assert res.group_changed is False


def test_active_user_breaches_hard_limit():
    res = evaluate_user_state_and_group(
        current_status=UserStatus.ACTIVE,
        current_in_enabled_group=True,
        is_exempt=False,
        gross_spend_usd=12.50,
        quota_credits_usd=10.00,
        overage_buffer_usd=2.00,
    )
    assert res.new_status == UserStatus.AUTO_DISABLED
    assert res.target_group == "DISABLED"
    assert res.state_changed is True
    assert res.group_changed is True


def test_auto_disabled_user_re_enabled_by_quota_increase():
    res = evaluate_user_state_and_group(
        current_status=UserStatus.AUTO_DISABLED,
        current_in_enabled_group=False,
        is_exempt=False,
        gross_spend_usd=12.50,
        quota_credits_usd=20.00,  # Increased from 10 to 20
        overage_buffer_usd=2.00,
    )
    assert res.new_status == UserStatus.ACTIVE
    assert res.target_group == "ENABLED"
    assert res.state_changed is True
    assert res.group_changed is True


def test_auto_disabled_user_weekly_reset():
    res = evaluate_user_state_and_group(
        current_status=UserStatus.AUTO_DISABLED,
        current_in_enabled_group=False,
        is_exempt=False,
        gross_spend_usd=0.00,
        quota_credits_usd=10.00,
        overage_buffer_usd=2.00,
        is_weekly_reset=True,
    )
    assert res.new_status == UserStatus.ACTIVE
    assert res.target_group == "ENABLED"
    assert res.state_changed is True
    assert res.group_changed is True


def test_manually_disabled_user_protected_from_reset():
    res = evaluate_user_state_and_group(
        current_status=UserStatus.MANUALLY_DISABLED,
        current_in_enabled_group=False,
        is_exempt=False,
        gross_spend_usd=0.00,
        quota_credits_usd=10.00,
        overage_buffer_usd=2.00,
        is_weekly_reset=True,
    )
    assert res.new_status == UserStatus.MANUALLY_DISABLED
    assert res.target_group == "DISABLED"
    assert res.state_changed is False
    assert res.group_changed is False


def test_exempt_user_bypasses_quota_breach():
    res = evaluate_user_state_and_group(
        current_status=UserStatus.ACTIVE,
        current_in_enabled_group=True,
        is_exempt=True,
        gross_spend_usd=500.00,
        quota_credits_usd=10.00,
        overage_buffer_usd=2.00,
    )
    assert res.new_status == UserStatus.ACTIVE
    assert res.target_group == "ENABLED"
    assert res.state_changed is False
    assert res.group_changed is False

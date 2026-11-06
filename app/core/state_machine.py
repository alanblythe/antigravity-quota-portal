"""Dual-group state machine and transition rules for Antigravity access governance."""

from app.core.cost_model import _finite_non_neg
from app.db.models import UserStatus


class StateTransitionResult:
    def __init__(
        self,
        new_status: UserStatus,
        target_group: str,  # "ENABLED" or "DISABLED"
        state_changed: bool,
        group_changed: bool,
        reason: str,
    ):
        self.new_status = new_status
        self.target_group = target_group
        self.state_changed = state_changed
        self.group_changed = group_changed
        self.reason = reason


def evaluate_user_state_and_group(
    current_status: UserStatus,
    current_in_enabled_group: bool,
    is_exempt: bool,
    gross_spend_usd: float,
    quota_credits_usd: float,
    overage_buffer_usd: float,
    is_weekly_reset: bool = False,
    current_in_disabled_group: bool | None = None,
) -> StateTransitionResult:
    """Determine the next status and target group for a user based on quota and state rules.

    Rules:
    1. MANUALLY_DISABLED:
       - Protected from weekly reset and exempt status. Evaluator never re-enables.
       - Always DISABLED group (and removed from ENABLED group). Status remains MANUALLY_DISABLED.
    2. is_exempt = True (when not MANUALLY_DISABLED):
       - Always ENABLED group (and removed from DISABLED group).
       - Status remains ACTIVE (or resets to ACTIVE if previously AUTO_DISABLED).
    3. AUTO_DISABLED:
       - If is_weekly_reset = True and spend < hard_limit -> Status ACTIVE, ENABLED group (Weekly auto-re-enable).
       - If gross_spend < (quota_credits + overage_buffer) -> Status ACTIVE, ENABLED group (Quota increase / recovery).
       - Otherwise -> Remains AUTO_DISABLED, DISABLED group.
    4. ACTIVE:
       - If gross_spend >= (quota_credits + overage_buffer) -> Status AUTO_DISABLED, DISABLED group (Quota breached).
       - Otherwise -> Remains ACTIVE, ENABLED group.
    """
    safe_quota = _finite_non_neg(quota_credits_usd)
    safe_overage = _finite_non_neg(overage_buffer_usd)
    safe_spend = _finite_non_neg(gross_spend_usd)
    hard_limit = round(_finite_non_neg(safe_quota + safe_overage), 2)
    current_spend = round(safe_spend, 2)

    def _needs_enabled_swap() -> bool:
        if current_in_disabled_group is not None:
            return (not current_in_enabled_group) or current_in_disabled_group
        return not current_in_enabled_group

    def _needs_disabled_swap() -> bool:
        if current_in_disabled_group is not None:
            return current_in_enabled_group or (not current_in_disabled_group)
        return current_in_enabled_group

    # 1. Manually disabled users are locked by admin intervention (highest precedence)
    if current_status == UserStatus.MANUALLY_DISABLED:
        new_status = UserStatus.MANUALLY_DISABLED
        target_group = "DISABLED"
        reason = "MANUALLY_LOCKED_BY_ADMIN"
        state_changed = False
        group_changed = _needs_disabled_swap()
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 2. Exempt users are always granted access
    if is_exempt:
        new_status = UserStatus.ACTIVE
        target_group = "ENABLED"
        reason = "EXEMPT_USER"
        state_changed = current_status != new_status
        group_changed = _needs_enabled_swap()
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 3. Weekly Reset Check (WTU 7-day cycle or Monday 00:00 fallback)
    if is_weekly_reset and current_status == UserStatus.AUTO_DISABLED and current_spend < hard_limit:
        new_status = UserStatus.ACTIVE
        target_group = "ENABLED"
        reason = "WEEKLY_MONDAY_RESET"
        state_changed = current_status != new_status
        group_changed = _needs_enabled_swap()
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 4. Auto-Disabled State Check
    if current_status == UserStatus.AUTO_DISABLED:
        # Check if user now has headroom (e.g. admin increased quota credits or overage buffer)
        if current_spend < hard_limit:
            new_status = UserStatus.ACTIVE
            target_group = "ENABLED"
            reason = f"QUOTA_EXPANDED: Spend ${current_spend:.2f} < Limit ${hard_limit:.2f}"
            state_changed = True
            group_changed = _needs_enabled_swap()
        else:
            new_status = UserStatus.AUTO_DISABLED
            target_group = "DISABLED"
            reason = f"THROTTLED: Spend ${current_spend:.2f} >= Limit ${hard_limit:.2f}"
            state_changed = False
            group_changed = _needs_disabled_swap()
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 5. Active State Check
    if current_spend >= hard_limit:
        new_status = UserStatus.AUTO_DISABLED
        target_group = "DISABLED"
        reason = f"HARD_LIMIT_BREACHED: Spend ${current_spend:.2f} >= Limit ${hard_limit:.2f}"
        state_changed = True
        group_changed = _needs_disabled_swap()
    else:
        new_status = UserStatus.ACTIVE
        target_group = "ENABLED"
        reason = "WITHIN_QUOTA"
        state_changed = current_status != new_status
        group_changed = _needs_enabled_swap()

    return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

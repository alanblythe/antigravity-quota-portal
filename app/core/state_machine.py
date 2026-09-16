"""Dual-group state machine and transition rules for Antigravity access governance."""

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
) -> StateTransitionResult:
    """Determine the next status and target group for a user based on quota and state rules.

    Rules:
    1. is_exempt = True:
       - Always ENABLED group.
       - Status remains ACTIVE (or resets to ACTIVE if previously AUTO_DISABLED).
    2. MANUALLY_DISABLED:
       - Protected from Monday reset. Evaluator never re-enables.
       - Always DISABLED group. Status remains MANUALLY_DISABLED.
    3. AUTO_DISABLED:
       - If is_weekly_reset = True -> Status ACTIVE, ENABLED group (Weekly auto-re-enable).
       - If gross_spend < (quota_credits + overage_buffer) -> Status ACTIVE, ENABLED group (Quota increase / recovery).
       - Otherwise -> Remains AUTO_DISABLED, DISABLED group.
    4. ACTIVE:
       - If gross_spend >= (quota_credits + overage_buffer) -> Status AUTO_DISABLED, DISABLED group (Quota breached).
       - Otherwise -> Remains ACTIVE, ENABLED group.
    """
    hard_limit = round(quota_credits_usd + overage_buffer_usd, 2)
    current_spend = round(gross_spend_usd, 2)

    # 1. Exempt users are always granted access
    if is_exempt:
        new_status = UserStatus.ACTIVE
        target_group = "ENABLED"
        reason = "EXEMPT_USER"
        state_changed = current_status != new_status
        group_changed = not current_in_enabled_group
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 2. Manually disabled users are locked by admin intervention
    if current_status == UserStatus.MANUALLY_DISABLED:
        new_status = UserStatus.MANUALLY_DISABLED
        target_group = "DISABLED"
        reason = "MANUALLY_LOCKED_BY_ADMIN"
        state_changed = False
        group_changed = current_in_enabled_group  # should NOT be in enabled group
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 3. Weekly Reset Check (Monday 00:00)
    if is_weekly_reset:
        new_status = UserStatus.ACTIVE
        target_group = "ENABLED"
        reason = "WEEKLY_MONDAY_RESET"
        state_changed = current_status != new_status
        group_changed = not current_in_enabled_group
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 4. Auto-Disabled State Check
    if current_status == UserStatus.AUTO_DISABLED:
        # Check if user now has headroom (e.g. admin increased quota credits or overage buffer)
        if current_spend < hard_limit:
            new_status = UserStatus.ACTIVE
            target_group = "ENABLED"
            reason = f"QUOTA_EXPANDED: Spend ${current_spend:.2f} < Limit ${hard_limit:.2f}"
            state_changed = True
            group_changed = not current_in_enabled_group
        else:
            new_status = UserStatus.AUTO_DISABLED
            target_group = "DISABLED"
            reason = f"THROTTLED: Spend ${current_spend:.2f} >= Limit ${hard_limit:.2f}"
            state_changed = False
            group_changed = current_in_enabled_group
        return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

    # 5. Active State Check
    if current_spend >= hard_limit:
        new_status = UserStatus.AUTO_DISABLED
        target_group = "DISABLED"
        reason = f"HARD_LIMIT_BREACHED: Spend ${current_spend:.2f} >= Limit ${hard_limit:.2f}"
        state_changed = True
        group_changed = current_in_enabled_group
    else:
        new_status = UserStatus.ACTIVE
        target_group = "ENABLED"
        reason = "WITHIN_QUOTA"
        state_changed = False
        group_changed = not current_in_enabled_group

    return StateTransitionResult(new_status, target_group, state_changed, group_changed, reason)

"""Mock Cloud Identity Directory client for local testing and sandbox."""

import logging
import threading

logger = logging.getLogger(__name__)


class MockCloudIdentityGroupManager:
    """In-memory mock for Google Cloud Identity dual-group management."""

    def __init__(
        self,
        enabled_group_email: str = "antigravity-enabled@example.com",
        disabled_group_email: str = "antigravity-disabled@example.com",
    ):
        self.enabled_group_email = enabled_group_email
        self.disabled_group_email = disabled_group_email
        self._lock = threading.RLock()
        self._enabled_members: set[str] = {
            "alice.chen@example.com",
            "bob.martin@example.com",
            "dana.scully@example.com",
        }
        self._disabled_members: set[str] = {
            "charlie.davis@example.com",
            "evan.wright@example.com",
        }

    def get_group_members(self, group_email: str) -> set[str]:
        with self._lock:
            if group_email == self.enabled_group_email:
                return set(self._enabled_members)
            elif group_email == self.disabled_group_email:
                return set(self._disabled_members)
            return set()

    def is_user_in_enabled_group(self, user_email: str) -> bool:
        with self._lock:
            return user_email.lower().strip() in self._enabled_members

    def is_user_in_disabled_group(self, user_email: str) -> bool:
        with self._lock:
            return user_email.lower().strip() in self._disabled_members

    def add_member_to_group(self, group_email: str, user_email: str) -> bool:
        with self._lock:
            user = user_email.lower().strip()
            if group_email == self.enabled_group_email:
                self._enabled_members.add(user)
                return True
            elif group_email == self.disabled_group_email:
                self._disabled_members.add(user)
                return True
            return False

    def remove_member_from_group(self, group_email: str, user_email: str) -> bool:
        with self._lock:
            user = user_email.lower().strip()
            if group_email == self.enabled_group_email:
                self._enabled_members.discard(user)
                return True
            elif group_email == self.disabled_group_email:
                self._disabled_members.discard(user)
                return True
            return False

    def reconcile_user_group(
        self,
        user_email: str,
        target_group: str,  # "ENABLED" or "DISABLED"
    ) -> bool:
        with self._lock:
            user = user_email.lower().strip()
            if target_group == "ENABLED":
                self._enabled_members.add(user)
                self._disabled_members.discard(user)
                logger.info(f"[MockIdentity] User {user} -> ENABLED group")
            else:
                self._disabled_members.add(user)
                self._enabled_members.discard(user)
                logger.info(f"[MockIdentity] User {user} -> DISABLED group")
            return True

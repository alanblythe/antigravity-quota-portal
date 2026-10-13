"""Mock Cloud Identity Directory client for local testing and sandbox."""

import logging
import threading

from app.identity.cloud_identity import is_protected_principal

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
        self._groups: dict[str, set[str]] = {
            self.enabled_group_email.lower().strip(): {
                "alice.chen@example.com",
                "bob.martin@example.com",
                "dana.scully@example.com",
            },
            self.disabled_group_email.lower().strip(): {
                "charlie.davis@example.com",
                "evan.wright@example.com",
            },
        }

    @property
    def _enabled_members(self) -> set[str]:
        return self._groups.setdefault(self.enabled_group_email.lower().strip(), set())

    @_enabled_members.setter
    def _enabled_members(self, value: set[str]) -> None:
        self._groups[self.enabled_group_email.lower().strip()] = set(value)

    @property
    def _disabled_members(self) -> set[str]:
        return self._groups.setdefault(self.disabled_group_email.lower().strip(), set())

    @_disabled_members.setter
    def _disabled_members(self, value: set[str]) -> None:
        self._groups[self.disabled_group_email.lower().strip()] = set(value)

    def _resolve_group_set(self, group_email: str) -> set[str]:
        norm = (group_email or "").lower().strip()
        if norm not in self._groups:
            en_key = self.enabled_group_email.lower().strip()
            dis_key = self.disabled_group_email.lower().strip()
            if "enabled" in norm and en_key in self._groups:
                self._groups[norm] = set(self._groups[en_key])
            elif "disabled" in norm and dis_key in self._groups:
                self._groups[norm] = set(self._groups[dis_key])
            else:
                self._groups[norm] = set()
        return self._groups[norm]

    def get_group_members(self, group_email: str, raise_on_error: bool = False) -> set[str]:
        with self._lock:
            norm = (group_email or "").lower().strip()
            if not self._is_valid_group_key(norm, self._groups):
                return set()
            return set(self._resolve_group_set(group_email))

    def is_user_in_enabled_group(self, user_email: str) -> bool:
        with self._lock:
            return user_email.lower().strip() in self._enabled_members

    def is_user_in_disabled_group(self, user_email: str) -> bool:
        with self._lock:
            return user_email.lower().strip() in self._disabled_members

    @staticmethod
    def _is_valid_group_key(norm: str, known_groups: dict[str, set[str]]) -> bool:
        if not norm:
            return False
        return norm in known_groups or "@" in norm or "enabled" in norm or "disabled" in norm

    def add_member_to_group(self, group_email: str, user_email: str) -> bool:
        user = user_email.lower().strip()
        if is_protected_principal(user):
            logger.warning(f"Skipping add_member_to_group for protected principal {user}")
            return False
        with self._lock:
            norm = (group_email or "").lower().strip()
            if not self._is_valid_group_key(norm, self._groups):
                return False
            self._resolve_group_set(group_email).add(user)
            return True

    def remove_member_from_group(self, group_email: str, user_email: str) -> bool:
        user = user_email.lower().strip()
        if is_protected_principal(user):
            logger.warning(f"Skipping remove_member_from_group for protected principal {user}")
            return False
        with self._lock:
            norm = (group_email or "").lower().strip()
            if not self._is_valid_group_key(norm, self._groups):
                return False
            self._resolve_group_set(group_email).discard(user)
            return True

    def reconcile_user_group(
        self,
        user_email: str,
        target_group: str,  # "ENABLED" or "DISABLED"
        enabled_group: str | None = None,
        disabled_group: str | None = None,
    ) -> bool:
        target = (target_group or "").upper().strip()
        if target not in ("ENABLED", "DISABLED"):
            raise ValueError(f"Invalid target_group: {target_group}")
        user = user_email.lower().strip()
        if is_protected_principal(user):
            logger.warning(f"Refusing to reconcile protected service account principal: {user}")
            return False
        with self._lock:
            en_group = (enabled_group or "").strip() or self.enabled_group_email
            dis_group = (disabled_group or "").strip() or self.disabled_group_email
            if target == "ENABLED":
                added = self.add_member_to_group(en_group, user)
                removed = self.remove_member_from_group(dis_group, user)
                logger.info(f"[MockIdentity] User {user} -> ENABLED group ({en_group})")
            else:
                added = self.add_member_to_group(dis_group, user)
                removed = self.remove_member_from_group(en_group, user)
                logger.info(f"[MockIdentity] User {user} -> DISABLED group ({dis_group})")
            return bool(added and removed)

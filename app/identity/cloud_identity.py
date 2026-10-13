"""Google Cloud Identity Groups API client for dual-group governance."""

import logging
import threading

import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def is_protected_principal(email: str) -> bool:
    """Protect service accounts (e.g., the portal's own OWNER SA) from group swaps."""
    normalized = email.lower().strip()
    return normalized.endswith(".gserviceaccount.com")


class CloudIdentityGroupManager:
    """Manages group memberships in enabled and disabled Antigravity groups using Cloud Identity API."""

    def __init__(self, enabled_group_email: str, disabled_group_email: str, quota_project_id: str | None = None):
        self.enabled_group_email = enabled_group_email
        self.disabled_group_email = disabled_group_email
        self.quota_project_id = quota_project_id
        self._lock = threading.RLock()
        self._service = None
        self._group_keys: dict[str, str] = {}  # email -> group resource name e.g. groups/{id}

    def _get_service(self):
        with self._lock:
            if self._service is None:
                credentials, project = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-identity.groups"]
                )
                quota_proj = self.quota_project_id or project
                if (
                    quota_proj
                    and hasattr(credentials, "with_quota_project")
                    and not getattr(credentials, "quota_project_id", None)
                ):
                    credentials = credentials.with_quota_project(quota_proj)
                self._service = build("cloudidentity", "v1", credentials=credentials)
            return self._service

    def _lookup_group_name(self, group_email: str, raise_on_error: bool = False) -> str | None:
        with self._lock:
            if group_email in self._group_keys:
                return self._group_keys[group_email]
            try:
                service = self._get_service()
                response = service.groups().lookup(groupKey_id=group_email).execute()
                name = response.get("name")
                if name:
                    self._group_keys[group_email] = name
                return name
            except Exception as e:
                logger.error(f"Failed to lookup Cloud Identity group {group_email}: {e}")
                if raise_on_error:
                    raise
                return None

    def _resolve_group_name(
        self, group_email: str, force_refresh: bool = False, raise_on_error: bool = False
    ) -> str | None:
        with self._lock:
            if force_refresh:
                self._group_keys.pop(group_email, None)
            return self._lookup_group_name(group_email, raise_on_error=raise_on_error)

    def get_group_members(self, group_email: str, raise_on_error: bool = False) -> set[str]:
        """Fetch all member emails for a given group."""
        with self._lock:
            was_cached = group_email in self._group_keys
            group_name = self._resolve_group_name(group_email, raise_on_error=raise_on_error)
            if not group_name:
                return set()

            for attempt in range(2 if was_cached else 1):
                members = set()
                try:
                    service = self._get_service()
                    page_token = None
                    for _ in range(1000):
                        response = (
                            service.groups()
                            .memberships()
                            .list(parent=group_name, pageToken=page_token)
                            .execute()
                        )
                        for membership in response.get("memberships", []):
                            member_key = membership.get("preferredMemberKey", {}).get("id")
                            if member_key:
                                members.add(member_key.lower().strip())
                        page_token = response.get("nextPageToken")
                        if not page_token:
                            break
                    if page_token:
                        logger.warning(
                            f"Pagination cap (1000 pages) reached while listing members "
                            f"for {group_email} ({group_name})"
                        )
                        if raise_on_error:
                            raise RuntimeError(f"Pagination cap exceeded for {group_email}")
                    return members
                except HttpError as e:
                    if attempt == 0 and was_cached and e.resp.status == 404:
                        logger.info(
                            f"Cached group resource {group_name} returned 404 for {group_email}; "
                            "evicting cache and retrying lookup"
                        )
                        group_name = self._resolve_group_name(
                            group_email, force_refresh=True, raise_on_error=raise_on_error
                        )
                        if not group_name:
                            return set()
                        continue
                    logger.error(f"Failed to list members of group {group_email}: {e}")
                    if raise_on_error:
                        raise
                    return set()
                except Exception as e:
                    logger.error(f"Failed to list members of group {group_email}: {e}")
                    if raise_on_error:
                        raise
                    return set()
            return set()

    def add_member_to_group(self, group_email: str, user_email: str) -> bool:
        """Add user email to group. Treats HTTP 409 ALREADY_EXISTS as idempotent success."""
        user = user_email.lower().strip()
        if is_protected_principal(user):
            logger.warning(f"Skipping add_member_to_group for protected principal {user}")
            return False

        with self._lock:
            was_cached = group_email in self._group_keys
            group_name = self._resolve_group_name(group_email)
            if not group_name:
                return False
            body = {
                "preferredMemberKey": {"id": user},
                "roles": [{"name": "MEMBER"}],
            }
            for attempt in range(2 if was_cached else 1):
                try:
                    service = self._get_service()
                    service.groups().memberships().create(parent=group_name, body=body).execute()
                    logger.info(f"Added {user} to {group_email}")
                    return True
                except HttpError as e:
                    if e.resp.status == 409 or "ALREADY_EXISTS" in str(e):
                        logger.debug(f"User {user} is already a member of {group_email} (409 idempotent)")
                        return True
                    if attempt == 0 and was_cached and e.resp.status == 404:
                        logger.info(
                            f"Cached group resource {group_name} returned 404 when adding {user} to {group_email}; "
                            "evicting cache and retrying lookup"
                        )
                        group_name = self._resolve_group_name(group_email, force_refresh=True)
                        if not group_name:
                            return False
                        continue
                    logger.error(f"Failed to add {user} to {group_email}: {e}")
                    return False
                except Exception as e:
                    logger.error(f"Failed to add {user} to {group_email}: {e}")
                    return False
            return False

    def remove_member_from_group(self, group_email: str, user_email: str) -> bool:
        """Remove user email from group. Treats HTTP 404 NOT_FOUND on membership as idempotent success."""
        user = user_email.lower().strip()
        if is_protected_principal(user):
            logger.warning(f"Skipping remove_member_from_group for protected principal {user}")
            return False

        with self._lock:
            was_cached = group_email in self._group_keys
            group_name = self._resolve_group_name(group_email)
            if not group_name:
                return False
            for attempt in range(2):
                try:
                    service = self._get_service()
                    # Cloud Identity uses lookupMembership or direct membership resource name
                    lookup_res = (
                        service.groups()
                        .memberships()
                        .lookup(parent=group_name, memberKey_id=user)
                        .execute()
                    )
                    name = lookup_res.get("name")
                    if not name:
                        logger.warning(f"Membership lookup for {user} in {group_email} returned empty resource name")
                        return False
                    service.groups().memberships().delete(name=name).execute()
                    logger.info(f"Removed {user} from {group_email}")
                    return True
                except HttpError as e:
                    if e.resp.status == 404 or "NOT_FOUND" in str(e):
                        err_lower = str(e).lower()
                        parent_missing_msg = "group" in err_lower and "member" not in err_lower
                        if attempt == 0 and (was_cached or parent_missing_msg):
                            fresh_group_name = self._resolve_group_name(group_email, force_refresh=True)
                            if not fresh_group_name:
                                logger.error(f"Group {group_email} not found during member removal: {e}")
                                return False
                            if fresh_group_name != group_name:
                                group_name = fresh_group_name
                                was_cached = False
                                continue
                            if parent_missing_msg:
                                logger.error(f"Parent group {group_email} reported missing during removal: {e}")
                                return False
                        elif parent_missing_msg:
                            self._group_keys.pop(group_email, None)
                            logger.error(f"Parent group {group_email} not found during member removal: {e}")
                            return False
                        logger.debug(f"User {user} is already absent from {group_email} (404 idempotent)")
                        return True
                    logger.error(f"Failed to remove {user} from {group_email}: {e}")
                    return False
                except Exception as e:
                    logger.error(f"Failed to remove {user} from {group_email}: {e}")
                    return False
            return False

    def reconcile_user_group(
        self,
        user_email: str,
        target_group: str,  # "ENABLED" or "DISABLED"
        enabled_group: str | None = None,
        disabled_group: str | None = None,
    ) -> bool:
        """Move user to target group and ensure removal from the opposite group."""
        target = (target_group or "").upper().strip()
        if target not in ("ENABLED", "DISABLED"):
            raise ValueError(f"Invalid target_group: {target_group}")

        user = user_email.lower().strip()
        if is_protected_principal(user):
            logger.warning(f"Refusing to reconcile protected service account principal: {user}")
            return False

        en_group = (enabled_group or "").strip() or self.enabled_group_email
        dis_group = (disabled_group or "").strip() or self.disabled_group_email

        if target == "ENABLED":
            added = self.add_member_to_group(en_group, user)
            removed = self.remove_member_from_group(dis_group, user)
        else:
            added = self.add_member_to_group(dis_group, user)
            removed = self.remove_member_from_group(en_group, user)
        return bool(added and removed)

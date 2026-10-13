"""Cloud Monitoring client for Discovery Engine Weighted Token Units (WTU) quota and sliding reset telemetry."""

import logging
import threading
from datetime import UTC, datetime, timedelta

import google.auth
import google.auth.transport.requests
import httpx
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

WEEK_SECONDS = 604800  # 7 days in seconds

CANDIDATE_WTU_TIERS = [
    "agentic_wtu_tier_enterprise_plus_pp_weekly_regional",
    "agentic_wtu_tier_enterprise_standard_pp_weekly_regional",
    "agentic_wtu_tier_enterprise_plus_pppu_weekly_regional",
    "agentic_wtu_tier_enterprise_standard_pppu_weekly_regional",
    "agentic_wtu_tier_enterprise_plus_ppu_weekly_regional",
    "agentic_wtu_tier_enterprise_standard_ppu_weekly_regional",
]


def derive_license_tier(tier_metric: str, project_id: str = "") -> str:
    """Derive license tier ('ENTERPRISE_PLUS' vs 'ENTERPRISE_STANDARD') from metric or project name."""
    t_low = (tier_metric or "").lower()
    p_low = (project_id or "").lower()
    if "standard" in t_low or any(tok in p_low for tok in ("standard", "-std", "_std")):
        return "ENTERPRISE_STANDARD"
    return "ENTERPRISE_PLUS"


class UserWTUStatus(BaseModel):
    """Per-user WTU quota telemetry when LocalizedUser (ppu) metrics are active."""

    ge_user: str
    wtu_consumed: int = 0
    wtu_limit: int = 0
    wtu_utilization_pct: float = 0.0
    cycle_start_utc: datetime | None = None
    cycle_reset_utc: datetime | None = None
    days_until_reset: float | None = None


class WTUQuotaStatus(BaseModel):
    """Project-level and optional per-user Discovery Engine WTU quota telemetry."""

    project_id: str = ""
    license_tier: str = "ENTERPRISE_PLUS"
    active: bool = False
    tier_metric: str = ""
    total_active_wtu_consumed: int = 0
    wtu_quota_limit: int = 0
    wtu_utilization_pct: float = 0.0
    active_cycle_start_utc: datetime | None = None
    active_cycle_reset_utc: datetime | None = None
    days_until_reset: float | None = None
    per_user_wtu: dict[str, UserWTUStatus] = Field(default_factory=dict)


class CloudMonitoringWTUClient:
    """Queries Cloud Monitoring timeSeries for Discovery Engine weekly WTU usage and limits."""

    def __init__(
        self,
        project_id: str,
        regional_location: str = "global",
        quota_project_id: str | None = None,
        cache_ttl_seconds: float = 15.0,
    ):
        self.project_id = project_id
        self.regional_location = regional_location
        self.quota_project_id = quota_project_id or project_id
        self.cache_ttl_seconds = cache_ttl_seconds
        self._lock = threading.RLock()
        self._credentials = None
        self._status_cache: dict[tuple[str, str, int], tuple[float, WTUQuotaStatus]] = {}
        self._thread_state = threading.local()

    def _get_access_token(self) -> str | None:
        try:
            with self._lock:
                if self._credentials is None:
                    self._credentials, _ = google.auth.default(
                        scopes=["https://www.googleapis.com/auth/monitoring.read"]
                    )
                if not self._credentials.valid:
                    auth_req = google.auth.transport.requests.Request()
                    self._credentials.refresh(auth_req)
                return self._credentials.token
        except Exception as e:
            logger.warning(f"Could not obtain Cloud Monitoring token: {e}")
            return None

    def _query_time_series(
        self,
        token: str,
        metric_type: str,
        lookback_hours: int = 2,
        target_project: str | None = None,
    ) -> list[dict]:
        proj = target_project or self.project_id
        now = datetime.now(UTC)
        start_time = (now - timedelta(hours=lookback_hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_time = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        url = f"https://monitoring.googleapis.com/v3/projects/{proj}/timeSeries"
        params = {
            "filter": f'metric.type="{metric_type}"',
            "interval.startTime": start_time,
            "interval.endTime": end_time,
            "pageSize": 500,
        }
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Goog-User-Project": self.quota_project_id,
        }
        all_series: list[dict] = []
        self._thread_state.last_query_had_http_error = False
        try:
            with httpx.Client(timeout=5.0) as client:
                page_token: str | None = None
                for _ in range(100):
                    req_params = dict(params)
                    if page_token:
                        req_params["pageToken"] = page_token
                    resp = client.get(url, params=req_params, headers=headers)
                    if resp.status_code != 200:
                        self._thread_state.last_query_had_http_error = True
                        logger.warning(
                            f"Cloud Monitoring timeSeries query returned HTTP {resp.status_code} "
                            f"for {metric_type} on {proj}"
                        )
                        return []
                    data = resp.json()
                    all_series.extend(data.get("timeSeries", []))
                    page_token = data.get("nextPageToken")
                    if not page_token:
                        break
                if page_token:
                    self._thread_state.last_query_had_http_error = True
                    logger.warning(
                        f"Cloud Monitoring timeSeries pagination exceeded 100 pages "
                        f"for {metric_type} on {proj}; results truncated."
                    )
                    return []
        except Exception as e:
            self._thread_state.last_query_had_http_error = True
            logger.debug(f"Cloud Monitoring timeSeries query failed for {metric_type} on {proj}: {e}")
            return []
        return all_series

    def fetch_wtu_status(
        self,
        preferred_tier: str = "agentic_wtu_tier_enterprise_plus_pp_weekly_regional",
        configured_wtu_limit: int = 0,
        project_id: str | None = None,
    ) -> WTUQuotaStatus:
        """Fetch active WTU consumption, limit, percentage used, and 7-day sliding reset window."""
        target_project = project_id or self.project_id
        now_epoch = datetime.now(UTC).timestamp()
        cache_key = (target_project, preferred_tier, configured_wtu_limit)
        with self._lock:
            cached = self._status_cache.get(cache_key)
            if cached is not None and (now_epoch - cached[0]) < self.cache_ttl_seconds:
                return cached[1].model_copy(deep=True)

        token = self._get_access_token()
        if not token:
            fallback = WTUQuotaStatus(
                project_id=target_project,
                license_tier=derive_license_tier(preferred_tier, target_project),
                tier_metric=preferred_tier,
                wtu_quota_limit=configured_wtu_limit,
            )
            return fallback

        tiers_to_try = [preferred_tier] + [t for t in CANDIDATE_WTU_TIERS if t != preferred_tier]

        def _point_val(s: dict) -> int:
            pts = s.get("points", [])
            if not pts:
                return 0
            try:
                return int(pts[0].get("value", {}).get("int64Value", 0))
            except (ValueError, TypeError):
                return 0

        def _window_start_int(s: dict) -> int:
            ws_raw = s.get("metric", {}).get("labels", {}).get("window_start_time")
            if ws_raw:
                try:
                    return int(ws_raw)
                except (ValueError, TypeError):
                    pass
            return 0

        def _parse_end_time_epoch(pt: dict) -> float:
            interval = pt.get("interval") or {}
            raw = interval.get("endTime") or interval.get("startTime") or ""
            if not raw or not isinstance(raw, str):
                return 0.0
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
            except (ValueError, TypeError):
                return 0.0

        def _extract_detected_limit(
            limit_series_list: list[dict], target_window_start: str | None = None
        ) -> int:
            best_key: tuple[int, float, int, int] | None = None
            best_val = 0
            for ls in limit_series_list:
                pts = ls.get("points", [])
                if not pts:
                    continue
                ws_raw = ls.get("metric", {}).get("labels", {}).get("window_start_time")
                ws_int = _window_start_int(ls)
                matches_ws = 1 if (target_window_start and ws_raw == target_window_start) else 0
                best_pt_key: tuple[float, int, int] | None = None
                for idx, pt in enumerate(pts):
                    try:
                        val = int(pt.get("value", {}).get("int64Value", 0))
                    except (ValueError, TypeError):
                        val = 0
                    if val <= 0:
                        continue
                    pt_key = (_parse_end_time_epoch(pt), -idx, val)
                    if best_pt_key is None or pt_key > best_pt_key:
                        best_pt_key = pt_key
                if best_pt_key is not None:
                    pt_end_epoch, _, pt_val = best_pt_key
                    cand_key = (matches_ws, pt_end_epoch, ws_int, pt_val)
                    if best_key is None or cand_key > best_key:
                        best_key = cand_key
                        best_val = pt_val
            return best_val

        had_upstream_error = False
        tier_usage_errors: dict[str, bool] = {}
        for tier in tiers_to_try:
            self._thread_state.last_query_had_http_error = False
            usage_metric = f"discoveryengine.googleapis.com/quota/ratev2/{tier}/net_usage"
            series_list = self._query_time_series(token, usage_metric, lookback_hours=24, target_project=target_project)
            tier_had_error = bool(getattr(self._thread_state, "last_query_had_http_error", False))
            tier_usage_errors[tier] = tier_had_error
            if tier_had_error:
                had_upstream_error = True
            # Filter to series with points whose 7-day sliding window has not yet expired,
            # deduplicating per resource key by latest window_start_time (tie-breaking by highest point value).
            dedup_map: dict[tuple | str, dict] = {}
            for s in series_list:
                if not s.get("points"):
                    continue
                ws_raw = s.get("metric", {}).get("labels", {}).get("window_start_time")
                if ws_raw:
                    try:
                        if int(ws_raw) + WEEK_SECONDS <= now_epoch:
                            continue
                    except (ValueError, TypeError):
                        pass
                res_labels = s.get("resource", {}).get("labels", {})
                met_labels = s.get("metric", {}).get("labels", {})
                ge_user = (res_labels.get("ge_user") or "").lower().strip()
                r_key: tuple | str = (
                    ge_user
                    if ge_user
                    else (
                        res_labels.get("project_id", ""),
                        res_labels.get("location", ""),
                        met_labels.get("quota_id", ""),
                    )
                )
                existing = dedup_map.get(r_key)
                if existing is None or (_window_start_int(s), _point_val(s)) > (
                    _window_start_int(existing),
                    _point_val(existing),
                ):
                    dedup_map[r_key] = s

            active_series = list(dedup_map.values())
            if not active_series:
                continue

            # Sort active series by consumed WTU descending to pick the primary cycle anchor
            sorted_series = sorted(active_series, key=_point_val, reverse=True)
            total_consumed = sum(_point_val(s) for s in sorted_series)

            primary = sorted_series[0]
            window_start_raw = primary.get("metric", {}).get("labels", {}).get("window_start_time")

            # Query corresponding /limit gauge metric (look back 24h since gauge limits emit less frequently)
            self._thread_state.last_query_had_http_error = False
            limit_metric = f"discoveryengine.googleapis.com/quota/ratev2/{tier}/limit"
            limit_series = self._query_time_series(
                token, limit_metric, lookback_hours=24, target_project=target_project
            )
            limit_had_error = bool(getattr(self._thread_state, "last_query_had_http_error", False))
            if limit_had_error:
                tier_had_error = True
                had_upstream_error = True
            elif not tier_had_error:
                had_upstream_error = False
            detected_limit = _extract_detected_limit(limit_series, target_window_start=window_start_raw)

            effective_configured = (
                configured_wtu_limit
                if (configured_wtu_limit > 0 and (tier == preferred_tier or detected_limit == 0))
                else 0
            )
            user_limit = effective_configured if effective_configured > 0 else detected_limit
            cycle_start_utc: datetime | None = None
            cycle_reset_utc: datetime | None = None
            days_until_reset: float | None = None

            if window_start_raw:
                try:
                    start_ts = int(window_start_raw)
                    reset_ts = start_ts + WEEK_SECONDS
                    cycle_start_utc = datetime.fromtimestamp(start_ts, tz=UTC)
                    cycle_reset_utc = datetime.fromtimestamp(reset_ts, tz=UTC)
                    days_until_reset = round(max(0.0, (reset_ts - now_epoch) / 86400.0), 1)
                except (ValueError, TypeError):
                    pass

            # Populate per-user WTU map if LocalizedUser (ppu) labels are present
            per_user: dict[str, UserWTUStatus] = {}
            for s in sorted_series:
                ge_user = (s.get("resource", {}).get("labels", {}).get("ge_user") or "").lower().strip()
                if ge_user and ge_user not in per_user:
                    u_val = _point_val(s)
                    u_start_raw = s.get("metric", {}).get("labels", {}).get("window_start_time")
                    u_start = None
                    u_reset = None
                    u_days = None
                    if u_start_raw:
                        try:
                            ust = int(u_start_raw)
                            urt = ust + WEEK_SECONDS
                            u_start = datetime.fromtimestamp(ust, tz=UTC)
                            u_reset = datetime.fromtimestamp(urt, tz=UTC)
                            u_days = round(max(0.0, (urt - now_epoch) / 86400.0), 1)
                        except (ValueError, TypeError):
                            pass
                    u_pct = round((u_val / user_limit) * 100.0, 1) if user_limit > 0 else 0.0
                    per_user[ge_user] = UserWTUStatus(
                        ge_user=ge_user,
                        wtu_consumed=u_val,
                        wtu_limit=user_limit,
                        wtu_utilization_pct=u_pct,
                        cycle_start_utc=u_start,
                        cycle_reset_utc=u_reset,
                        days_until_reset=u_days,
                    )

            is_per_user_tier = "_ppu_" in tier or "_pppu_" in tier
            pool_limit = (
                (user_limit * max(1, len(per_user)))
                if is_per_user_tier
                else user_limit
            )
            utilization_pct = (
                round((total_consumed / pool_limit) * 100.0, 1)
                if pool_limit > 0
                else 0.0
            )

            status_obj = WTUQuotaStatus(
                project_id=target_project,
                license_tier=derive_license_tier(tier, target_project),
                active=True,
                tier_metric=tier,
                total_active_wtu_consumed=total_consumed,
                wtu_quota_limit=pool_limit,
                wtu_utilization_pct=utilization_pct,
                active_cycle_start_utc=cycle_start_utc,
                active_cycle_reset_utc=cycle_reset_utc,
                days_until_reset=days_until_reset,
                per_user_wtu=per_user,
            )
            if not had_upstream_error:
                with self._lock:
                    self._status_cache[cache_key] = (now_epoch, status_obj)
            return status_obj.model_copy(deep=True)

        # Idle project: check /limit gauge across candidate tiers to detect true tier & quota ceiling
        idle_tier = preferred_tier
        idle_detected_limit = 0
        for tier in tiers_to_try:
            self._thread_state.last_query_had_http_error = False
            limit_metric = f"discoveryengine.googleapis.com/quota/ratev2/{tier}/limit"
            limit_series = self._query_time_series(
                token, limit_metric, lookback_hours=24, target_project=target_project
            )
            limit_had_error = bool(getattr(self._thread_state, "last_query_had_http_error", False))
            if limit_had_error:
                had_upstream_error = True
            detected_limit = _extract_detected_limit(limit_series)
            if detected_limit > 0:
                idle_tier = tier
                idle_detected_limit = detected_limit
                if not limit_had_error and not tier_usage_errors.get(tier, False):
                    had_upstream_error = False
                break

        idle_effective_limit = (
            configured_wtu_limit
            if (configured_wtu_limit > 0 and (idle_tier == preferred_tier or idle_detected_limit == 0))
            else idle_detected_limit
        )
        idle_status = WTUQuotaStatus(
            project_id=target_project,
            license_tier=derive_license_tier(idle_tier, target_project),
            active=False,
            tier_metric=idle_tier,
            wtu_quota_limit=idle_effective_limit,
        )
        if not had_upstream_error:
            with self._lock:
                self._status_cache[cache_key] = (now_epoch, idle_status)
        return idle_status.model_copy(deep=True)

    def fetch_multi_project_wtu_status(
        self,
        project_ids: list[str],
        preferred_tier: str = "agentic_wtu_tier_enterprise_plus_pp_weekly_regional",
        configured_wtu_limit: int = 0,
    ) -> dict[str, WTUQuotaStatus]:
        """Fetch WTU quota status across multiple monitored workload projects."""
        targets = [p.strip() for p in project_ids if p and p.strip()] or [self.project_id]
        result: dict[str, WTUQuotaStatus] = {}
        for pid in targets:
            result[pid] = self.fetch_wtu_status(
                preferred_tier=preferred_tier,
                configured_wtu_limit=configured_wtu_limit,
                project_id=pid,
            )
        return result


class MockWTUClient:
    """Mock Cloud Monitoring WTU client for local sandbox and unit tests."""

    def __init__(self, project_id: str = "local-project"):
        self.project_id = project_id

    def fetch_wtu_status(
        self,
        preferred_tier: str = "agentic_wtu_tier_enterprise_plus_pp_weekly_regional",
        configured_wtu_limit: int = 0,
        project_id: str | None = None,
    ) -> WTUQuotaStatus:
        target_project = project_id or self.project_id
        now = datetime.now(UTC)
        # Anchor cycle start to a deterministic hour timestamp in the active week
        hour_anchor = now.replace(minute=0, second=0, microsecond=0)
        is_standard = derive_license_tier(preferred_tier, target_project) == "ENTERPRISE_STANDARD"
        tier_metric = (
            "agentic_wtu_tier_enterprise_standard_pp_weekly_regional"
            if is_standard and preferred_tier == "agentic_wtu_tier_enterprise_plus_pp_weekly_regional"
            else preferred_tier
        )
        offset_hours = 8 if is_standard else 32
        cycle_start = hour_anchor - timedelta(hours=offset_hours)
        cycle_reset = cycle_start + timedelta(days=7)
        days_until = round(max(0.0, (cycle_reset - now).total_seconds() / 86400.0), 1)

        default_lim = 150_000 if is_standard else 500_000
        effective_limit = (
            configured_wtu_limit
            if (configured_wtu_limit > 0 and tier_metric == preferred_tier)
            else default_lim
        )
        consumed = 96_000 if is_standard else 214_500
        pct = round((consumed / effective_limit) * 100.0, 1) if effective_limit > 0 else 0.0

        return WTUQuotaStatus(
            project_id=target_project,
            license_tier=derive_license_tier(tier_metric, target_project),
            active=True,
            tier_metric=tier_metric,
            total_active_wtu_consumed=consumed,
            wtu_quota_limit=effective_limit,
            wtu_utilization_pct=pct,
            active_cycle_start_utc=cycle_start,
            active_cycle_reset_utc=cycle_reset,
            days_until_reset=days_until,
        )

    def fetch_multi_project_wtu_status(
        self,
        project_ids: list[str],
        preferred_tier: str = "agentic_wtu_tier_enterprise_plus_pp_weekly_regional",
        configured_wtu_limit: int = 0,
    ) -> dict[str, WTUQuotaStatus]:
        targets = [p.strip() for p in project_ids if p and p.strip()] or [self.project_id]
        return {
            pid: self.fetch_wtu_status(
                preferred_tier=preferred_tier,
                configured_wtu_limit=configured_wtu_limit,
                project_id=pid,
            )
            for pid in targets
        }

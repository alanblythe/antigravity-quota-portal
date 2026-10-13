"""BigQuery client for aggregating Antigravity inference token usage."""

import logging
import re
import threading
from datetime import UTC, datetime

from google.api_core import exceptions as api_exceptions
from google.cloud import bigquery
from pydantic import BaseModel

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_.-]+$")


def _ensure_utc(dt: datetime) -> datetime:
    """Normalize a datetime to timezone-aware UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _is_benign_missing_table_error(exc: Exception) -> bool:
    """Return True if the exception represents a missing table or schema mismatch (e.g. 404),
    and False for fatal auth, permission, quota, or server errors."""
    if isinstance(
        exc,
        (
            FileNotFoundError,
            PermissionError,
            api_exceptions.Forbidden,
            api_exceptions.Unauthorized,
            api_exceptions.PermissionDenied,
            api_exceptions.ServiceUnavailable,
            api_exceptions.InternalServerError,
            api_exceptions.TooManyRequests,
        ),
    ):
        return False
    code = getattr(exc, "code", None)
    if callable(code):
        try:
            code = code()
        except Exception:
            code = None
    if isinstance(code, int) and code in (401, 403, 429, 500, 502, 503, 504):
        return False
    if isinstance(exc, api_exceptions.NotFound) or code == 404:
        return True
    msg = str(exc).lower()
    if any(
        fatal_tok in msg
        for fatal_tok in (
            "access denied",
            "permission denied",
            "forbidden",
            "unauthorized",
            "credentials",
            "defaultcredentialserror",
            "quota exceeded",
            "rate limit",
            "service unavailable",
            "internal error",
            "connection",
            "timeout",
            "billing",
            "syntax error",
        )
    ):
        return False
    if isinstance(exc, api_exceptions.GoogleAPICallError):
        return any(
            benign_tok in msg
            for benign_tok in (
                "not found: table",
                "not found: dataset",
                "does not match any table",
                "matched no tables",
                "unrecognized name",
                "schema mismatch",
            )
        )
    if msg.strip() == "not found" or any(
        benign_tok in msg
        for benign_tok in (
            "table not found",
            "dataset not found",
            "not found: table",
            "not found: dataset",
            "does not match any table",
            "matched no tables",
            "sharded table missing",
            "table missing",
            "schema mismatch",
            "unrecognized name",
        )
    ):
        return True
    return False


class UserUsageRecord(BaseModel):
    user_email: str
    model_name: str
    request_count: int
    token_count: int
    project_id: str | None = None
    prompt_tokens: int = 0
    candidates_tokens: int = 0
    cached_tokens: int = 0
    thoughts_tokens: int = 0
    prompt_tokens_gt_200k: int = 0
    candidates_tokens_gt_200k: int = 0
    cached_tokens_gt_200k: int = 0
    thoughts_tokens_gt_200k: int = 0
    fallback_tokens: int = 0
    last_active: datetime | None = None


class BigQueryUsageClient:
    """Production BigQuery client for querying inference audit logs."""

    def __init__(self, project_id: str, dataset_name: str, default_workload_project_id: str | None = None):
        if not _IDENTIFIER_RE.match(str(project_id or "")) or not _IDENTIFIER_RE.match(str(dataset_name or "")):
            raise ValueError(f"Invalid BigQuery project_id or dataset_name: {project_id}.{dataset_name}")
        self.project_id = project_id
        self.dataset_name = dataset_name
        self.default_workload_project_id = default_workload_project_id or project_id
        self.client = bigquery.Client(project=project_id)
        self._cache_lock = threading.RLock()
        self._table_pattern_cache: tuple[float, tuple[bool, bool]] | None = None
        self._table_pattern_ttl_seconds: float = 60.0

    def _check_table_patterns(self) -> tuple[bool, bool]:
        """Detect whether date-sharded (*_YYYYMMDD) and/or unsharded tables exist in the dataset."""
        now_epoch = datetime.now(UTC).timestamp()
        with self._cache_lock:
            if (
                self._table_pattern_cache is not None
                and (now_epoch - self._table_pattern_cache[0]) < self._table_pattern_ttl_seconds
            ):
                return self._table_pattern_cache[1]

        result = (True, True)
        if hasattr(self.client, "list_tables"):
            try:
                tables = list(self.client.list_tables(f"{self.project_id}.{self.dataset_name}"))
                table_ids = [
                    t.table_id
                    for t in tables
                    if isinstance(getattr(t, "table_id", None), str)
                ]
                if table_ids:
                    has_sharded = any(
                        tid.startswith("businessaicode_googleapis_com_inference_response_")
                        for tid in table_ids
                    )
                    has_unsharded = any(
                        tid == "businessaicode_googleapis_com_inference_response"
                        for tid in table_ids
                    )
                    if has_sharded or has_unsharded:
                        result = (has_sharded, has_unsharded)
            except Exception as e:
                logger.debug(f"Could not list BigQuery tables for pattern detection: {e}")
                return (True, True)

        with self._cache_lock:
            self._table_pattern_cache = (now_epoch, result)
        return result

    def _build_usage_query(
        self,
        use_wildcard_shards: bool = True,
        project_start_map: dict[str, datetime] | None = None,
        exclude_sharded_insert_ids: bool = False,
        context_tier_threshold_tokens: int = 200000,
        default_workload_project_id: str | None = None,
    ) -> str:
        """Build the BigQuery SQL query for either date-sharded (*_YYYYMMDD) or unsharded/view tables."""
        if not _IDENTIFIER_RE.match(str(self.project_id or "")) or not _IDENTIFIER_RE.match(
            str(self.dataset_name or "")
        ):
            raise ValueError(f"Invalid BigQuery project_id or dataset_name: {self.project_id}.{self.dataset_name}")

        table_suffix = "_*" if use_wildcard_shards else ""
        shard_filter = (
            "AND _TABLE_SUFFIX BETWEEN FORMAT_TIMESTAMP('%Y%m%d', @min_start_timestamp_utc) "
            "AND FORMAT_TIMESTAMP('%Y%m%d', @end_timestamp_utc)"
            if use_wildcard_shards
            else ""
        )
        dedup_cross_table_filter = (
            f"""AND (
              insertId IS NULL
              OR insertId = ''
              OR insertId NOT IN (
                SELECT insertId
                FROM `{self.project_id}.{self.dataset_name}.businessaicode_googleapis_com_inference_response_*`
                WHERE timestamp >= @min_start_timestamp_utc
                  AND timestamp <= @end_timestamp_utc
                  AND _TABLE_SUFFIX BETWEEN FORMAT_TIMESTAMP('%Y%m%d', @min_start_timestamp_utc)
                  AND FORMAT_TIMESTAMP('%Y%m%d', @end_timestamp_utc)
                  AND insertId IS NOT NULL
                  AND insertId != ''
              )
            )"""
            if (not use_wildcard_shards and exclude_sharded_insert_ids)
            else ""
        )

        project_time_clauses = []
        if project_start_map:
            for pid, p_start in sorted(project_start_map.items()):
                if _IDENTIFIER_RE.match(pid):
                    iso_ts = _ensure_utc(p_start).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    project_time_clauses.append(f"WHEN '{pid}' THEN TIMESTAMP('{iso_ts}')")
        if project_time_clauses:
            cases_sql = " ".join(project_time_clauses)
            project_window_filter = (
                f"AND timestamp >= (CASE project_id {cases_sql} ELSE @start_timestamp_utc END)"
            )
        else:
            project_window_filter = "AND timestamp >= @start_timestamp_utc"

        thresh = int(context_tier_threshold_tokens)
        return f"""
        WITH raw_events AS (
          SELECT
            timestamp,
            COALESCE(
              resource.labels.project_id,
              REGEXP_EXTRACT(logName, r'^projects/([^/]+)/'),
              @fallback_project_id
            ) AS project_id,
            LOWER(TRIM(REPLACE(labels.user_id, 'user:', ''))) AS user_email,
            COALESCE(labels.model, 'gemini-3.8-flash') AS model_name,
            TO_JSON_STRING(jsonpayload_v1_inferenceresponselog.metadata) AS meta_json
          FROM
            `{self.project_id}.{self.dataset_name}.businessaicode_googleapis_com_inference_response{table_suffix}`
          WHERE
            timestamp >= @min_start_timestamp_utc
            AND timestamp <= @end_timestamp_utc
            {shard_filter}
            {dedup_cross_table_filter}
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY IF(insertId IS NOT NULL AND insertId != '', insertId, GENERATE_UUID())
            ORDER BY timestamp DESC
          ) = 1
        ),
        parsed_events AS (
          SELECT
            timestamp,
            project_id,
            user_email,
            model_name,
            COALESCE(
              SAFE_CAST(JSON_VALUE(meta_json, '$.totalTokenCount') AS INT64),
              SAFE_CAST(JSON_VALUE(meta_json, '$.totaltokencount') AS INT64),
              0
            ) AS total_tokens,
            COALESCE(
              SAFE_CAST(JSON_VALUE(meta_json, '$.promptTokenCount') AS INT64),
              SAFE_CAST(JSON_VALUE(meta_json, '$.prompttokencount') AS INT64),
              0
            ) AS prompt_tokens,
            COALESCE(
              SAFE_CAST(JSON_VALUE(meta_json, '$.candidatesTokenCount') AS INT64),
              SAFE_CAST(JSON_VALUE(meta_json, '$.candidatestokencount') AS INT64),
              0
            ) AS candidates_tokens,
            COALESCE(
              SAFE_CAST(JSON_VALUE(meta_json, '$.cachedContentTokenCount') AS INT64),
              SAFE_CAST(JSON_VALUE(meta_json, '$.cachedcontenttokencount') AS INT64),
              0
            ) AS cached_tokens,
            COALESCE(
              SAFE_CAST(JSON_VALUE(meta_json, '$.thoughtsTokenCount') AS INT64),
              SAFE_CAST(JSON_VALUE(meta_json, '$.thoughtstokencount') AS INT64),
              0
            ) AS thoughts_tokens
          FROM
            raw_events
        )
        SELECT
          project_id,
          user_email,
          model_name,
          COUNT(1) AS request_count,
          SUM(
            CASE
              WHEN total_tokens > 0 THEN total_tokens
              ELSE prompt_tokens + candidates_tokens + thoughts_tokens
            END
          ) AS token_count,
          SUM(prompt_tokens) AS prompt_tokens,
          SUM(candidates_tokens) AS candidates_tokens,
          SUM(cached_tokens) AS cached_tokens,
          SUM(thoughts_tokens) AS thoughts_tokens,
          SUM(CASE WHEN prompt_tokens > {thresh} THEN prompt_tokens ELSE 0 END) AS prompt_tokens_gt_200k,
          SUM(CASE WHEN prompt_tokens > {thresh} THEN candidates_tokens ELSE 0 END) AS candidates_tokens_gt_200k,
          SUM(CASE WHEN prompt_tokens > {thresh} THEN cached_tokens ELSE 0 END) AS cached_tokens_gt_200k,
          SUM(CASE WHEN prompt_tokens > {thresh} THEN thoughts_tokens ELSE 0 END) AS thoughts_tokens_gt_200k,
          SUM(
            CASE
              WHEN (prompt_tokens + candidates_tokens + cached_tokens + thoughts_tokens) = 0
                THEN total_tokens
              ELSE 0
            END
          ) AS fallback_tokens,
          MAX(timestamp) AS last_active
        FROM
          parsed_events
        WHERE
          user_email IS NOT NULL
          AND user_email != ''
          AND NOT ENDS_WITH(user_email, '.gserviceaccount.com')
          {project_window_filter}
        GROUP BY
          project_id,
          user_email,
          model_name;
        """

    def _execute_query(
        self,
        query: str,
        job_config: bigquery.QueryJobConfig,
        default_workload_project_id: str | None = None,
    ) -> list[UserUsageRecord]:
        fallback_proj = default_workload_project_id or self.default_workload_project_id
        query_job = self.client.query(query, job_config=job_config)
        results = query_job.result()

        records: list[UserUsageRecord] = []
        for row in results:
            row_dict = dict(row.items()) if hasattr(row, "items") else row
            token_count = int(row_dict.get("token_count") or row_dict.get("total_tokens") or 0)
            prompt_tokens = int(row_dict.get("prompt_tokens") or 0)
            candidates_tokens = int(row_dict.get("candidates_tokens") or 0)
            cached_tokens = int(row_dict.get("cached_tokens") or 0)
            thoughts_tokens = int(row_dict.get("thoughts_tokens") or 0)
            fallback_tokens = int(row_dict.get("fallback_tokens") or 0)
            if (
                fallback_tokens == 0
                and (prompt_tokens + candidates_tokens + cached_tokens + thoughts_tokens) == 0
                and token_count > 0
            ):
                fallback_tokens = token_count
            raw_last_active = row_dict.get("last_active")
            last_active = (
                _ensure_utc(raw_last_active)
                if isinstance(raw_last_active, datetime)
                else raw_last_active
            )
            records.append(
                UserUsageRecord(
                    user_email=str(row_dict["user_email"]).lower().strip(),
                    model_name=row_dict["model_name"],
                    request_count=int(row_dict.get("request_count") or 0),
                    token_count=token_count,
                    project_id=row_dict.get("project_id") or fallback_proj,
                    prompt_tokens=prompt_tokens,
                    candidates_tokens=candidates_tokens,
                    cached_tokens=cached_tokens,
                    thoughts_tokens=thoughts_tokens,
                    prompt_tokens_gt_200k=int(row_dict.get("prompt_tokens_gt_200k") or 0),
                    candidates_tokens_gt_200k=int(row_dict.get("candidates_tokens_gt_200k") or 0),
                    cached_tokens_gt_200k=int(row_dict.get("cached_tokens_gt_200k") or 0),
                    thoughts_tokens_gt_200k=int(row_dict.get("thoughts_tokens_gt_200k") or 0),
                    fallback_tokens=fallback_tokens,
                    last_active=last_active,
                )
            )
        return records

    @staticmethod
    def _merge_records(records: list[UserUsageRecord]) -> list[UserUsageRecord]:
        """Merge UserUsageRecord entries sharing (project_id, user_email, model_name)."""
        merged: dict[tuple[str | None, str, str], UserUsageRecord] = {}
        for rec in records:
            norm_email = rec.user_email.lower().strip()
            key = (rec.project_id, norm_email, rec.model_name)
            rec_last_active = _ensure_utc(rec.last_active) if rec.last_active is not None else None
            if key not in merged:
                copied = rec.model_copy(deep=True)
                copied.user_email = norm_email
                copied.last_active = rec_last_active
                merged[key] = copied
            else:
                existing = merged[key]
                existing.request_count += rec.request_count
                existing.token_count += rec.token_count
                existing.prompt_tokens += rec.prompt_tokens
                existing.candidates_tokens += rec.candidates_tokens
                existing.cached_tokens += rec.cached_tokens
                existing.thoughts_tokens += rec.thoughts_tokens
                existing.prompt_tokens_gt_200k += rec.prompt_tokens_gt_200k
                existing.candidates_tokens_gt_200k += rec.candidates_tokens_gt_200k
                existing.cached_tokens_gt_200k += rec.cached_tokens_gt_200k
                existing.thoughts_tokens_gt_200k += rec.thoughts_tokens_gt_200k
                existing.fallback_tokens += rec.fallback_tokens
                if rec_last_active is not None and (
                    existing.last_active is None or rec_last_active > _ensure_utc(existing.last_active)
                ):
                    existing.last_active = rec_last_active
        return list(merged.values())

    def fetch_weekly_usage(
        self,
        start_utc: datetime,
        end_utc: datetime,
        project_start_map: dict[str, datetime] | None = None,
        default_workload_project_id: str | None = None,
        context_tier_threshold_tokens: int = 200000,
    ) -> list[UserUsageRecord]:
        """Fetch aggregated token usage per user and model within the UTC time range.

        Queries date-sharded tables (`businessaicode_googleapis_com_inference_response_*`)
        and the partitioned table (`businessaicode_googleapis_com_inference_response`),
        deduplicating by `insertId` across both tables when both exist, and falls back
        cleanly if either table is absent. Re-raises fatal BigQuery errors (e.g., 403/500).
        """
        start_utc = _ensure_utc(start_utc)
        end_utc = _ensure_utc(end_utc)
        norm_project_start_map = (
            {pid: _ensure_utc(dt) for pid, dt in project_start_map.items()}
            if project_start_map
            else None
        )
        fallback_proj = default_workload_project_id or self.default_workload_project_id
        min_start_utc = (
            min([start_utc, *(norm_project_start_map.values())])
            if norm_project_start_map
            else start_utc
        )
        job_config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_timestamp_utc", "TIMESTAMP", start_utc),
                bigquery.ScalarQueryParameter("min_start_timestamp_utc", "TIMESTAMP", min_start_utc),
                bigquery.ScalarQueryParameter("end_timestamp_utc", "TIMESTAMP", end_utc),
                bigquery.ScalarQueryParameter("fallback_project_id", "STRING", fallback_proj),
            ]
        )

        has_sharded, has_unsharded = self._check_table_patterns()

        if not has_sharded and has_unsharded:
            try:
                unsharded_query = self._build_usage_query(
                    use_wildcard_shards=False,
                    project_start_map=norm_project_start_map,
                    exclude_sharded_insert_ids=False,
                    context_tier_threshold_tokens=context_tier_threshold_tokens,
                    default_workload_project_id=default_workload_project_id,
                )
                return self._merge_records(
                    self._execute_query(
                        unsharded_query,
                        job_config,
                        default_workload_project_id=default_workload_project_id,
                    )
                )
            except Exception as e:
                if _is_benign_missing_table_error(e):
                    logger.info(f"Unsharded BigQuery table not found ({e}); returning empty usage.")
                    return []
                logger.error(f"Fatal error querying BigQuery usage: {e}", exc_info=True)
                raise

        try:
            sharded_query = self._build_usage_query(
                use_wildcard_shards=True,
                project_start_map=norm_project_start_map,
                context_tier_threshold_tokens=context_tier_threshold_tokens,
                default_workload_project_id=default_workload_project_id,
            )
            sharded_records = self._execute_query(
                sharded_query,
                job_config,
                default_workload_project_id=default_workload_project_id,
            )
            if not has_unsharded:
                return self._merge_records(sharded_records)
            try:
                unsharded_query = self._build_usage_query(
                    use_wildcard_shards=False,
                    project_start_map=norm_project_start_map,
                    exclude_sharded_insert_ids=True,
                    context_tier_threshold_tokens=context_tier_threshold_tokens,
                    default_workload_project_id=default_workload_project_id,
                )
                unsharded_records = self._execute_query(
                    unsharded_query,
                    job_config,
                    default_workload_project_id=default_workload_project_id,
                )
                return self._merge_records(sharded_records + unsharded_records)
            except Exception as e:
                if not _is_benign_missing_table_error(e):
                    logger.error(f"Fatal error on secondary partitioned table query: {e}", exc_info=True)
                    raise
                logger.debug(f"Secondary partitioned table query skipped or failed: {e}")
                return self._merge_records(sharded_records)
        except Exception as sharded_err:
            if not _is_benign_missing_table_error(sharded_err):
                logger.error(f"Fatal error querying BigQuery sharded tables: {sharded_err}", exc_info=True)
                raise
            logger.info(
                f"Wildcard sharded table query did not match ({sharded_err}); "
                "falling back to unsharded table/view."
            )
            try:
                unsharded_query = self._build_usage_query(
                    use_wildcard_shards=False,
                    project_start_map=norm_project_start_map,
                    exclude_sharded_insert_ids=False,
                    context_tier_threshold_tokens=context_tier_threshold_tokens,
                    default_workload_project_id=default_workload_project_id,
                )
                return self._merge_records(
                    self._execute_query(
                        unsharded_query,
                        job_config,
                        default_workload_project_id=default_workload_project_id,
                    )
                )
            except Exception as e:
                if _is_benign_missing_table_error(e):
                    logger.info(f"Unsharded fallback table not found ({e}); returning empty usage.")
                    return []
                logger.error(f"Fatal error querying BigQuery usage: {e}", exc_info=True)
                raise

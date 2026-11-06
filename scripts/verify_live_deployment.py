#!/usr/bin/env python3
"""
End-to-End Live Production & BigQuery Sharded/Partitioned Telemetry Verification.

Verifies a deployed (or local) Antigravity Quota Portal environment across all 4 production gates:
1. Cloud Identity Group Pre-Flight: Confirms both `antigravity-enabled@` and `antigravity-disabled@`
   groups exist and the portal credentials have `MANAGER` permission to list/reconcile members.
2. Live BigQuery Multi-Day Sharded (`_YYYYMMDD`) & Partitioned Table Seeding:
   - Ensures today's shard, yesterday's shard, an expired (-10d) shard, and the partitioned
     `businessaicode_googleapis_com_metadata` table exist with the canonical Cloud Logging sink schema
     (`jsonpayload_v1_metadatalog.metadata`).
   - Inserts synthetic rows for a 3-user cohort (`dev1` breach, `dev2` healthy, `admin` exempt) via
     standard BigQuery DML `INSERT INTO` (avoiding streaming buffer locks), including cross-table
     duplicate `insertId`, `NULL` `insertId`, empty `''` `insertId`, and an out-of-window `-10d` shard.
3. Live Query, Spend Math & Quota State Machine Verification:
   - Executes `BigQueryUsageClient.fetch_weekly_usage()` and asserts `_TABLE_SUFFIX` shard pruning
     and exact deduplicated counts for the breach persona: 3 requests, 2,500,000 total tokens, and
     $15.78 gross spend ($2.775 on Gemini 4 Argon + $13.00 on Gemini 3.1 Pro).
   - Verifies 3-user cohort provisioning (`ACTIVE` -> `antigravity-enabled@`), hard-limit breach throttling
     (`dev1` -> `AUTO_DISABLED`), healthy under-quota retention (`dev2` -> `ACTIVE`), and exempt override
     retention (`admin` -> `ACTIVE`).
4. Clean Teardown (default unless `--keep-test-data` is passed):
   - Deletes the synthetic rows from all 4 BigQuery tables via DML `DELETE`, removes the 3 test cohort
     users, and restores the original portal configuration.
"""

import argparse
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from google.cloud import bigquery

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bq.bq_client import BigQueryUsageClient, UserUsageRecord
from app.bq.mock_bq import MockBigQueryUsageClient
from app.core.cost_model import calculate_total_usage_costs
from app.core.evaluator import QuotaEvaluator
from app.core.timezone_engine import get_current_week_window
from app.db.firestore_client import FirestoreDatabase
from app.db.mock_db import MockDatabase
from app.db.models import CurrentWeekUsage, User, UserStatus, load_default_model_pricing
from app.identity.cloud_identity import CloudIdentityGroupManager
from app.identity.mock_identity import MockCloudIdentityGroupManager

METADATA_TABLE_SCHEMA = [
    bigquery.SchemaField("timestamp", "TIMESTAMP", mode="NULLABLE"),
    bigquery.SchemaField("insertId", "STRING", mode="NULLABLE"),
    bigquery.SchemaField("logName", "STRING", mode="NULLABLE"),
    bigquery.SchemaField(
        "resource",
        "RECORD",
        mode="NULLABLE",
        fields=[
            bigquery.SchemaField("type", "STRING", mode="NULLABLE"),
            bigquery.SchemaField(
                "labels",
                "RECORD",
                mode="NULLABLE",
                fields=[bigquery.SchemaField("project_id", "STRING", mode="NULLABLE")],
            ),
        ],
    ),
    bigquery.SchemaField(
        "labels",
        "RECORD",
        mode="NULLABLE",
        fields=[
            bigquery.SchemaField("user_id", "STRING", mode="NULLABLE"),
            bigquery.SchemaField("model", "STRING", mode="NULLABLE"),
        ],
    ),
    bigquery.SchemaField(
        "jsonpayload_v1_metadatalog",
        "RECORD",
        mode="NULLABLE",
        fields=[
            bigquery.SchemaField(
                "metadata",
                "RECORD",
                mode="NULLABLE",
                fields=[
                    bigquery.SchemaField("totalTokenCount", "INT64", mode="NULLABLE"),
                    bigquery.SchemaField("promptTokenCount", "INT64", mode="NULLABLE"),
                    bigquery.SchemaField("candidatesTokenCount", "INT64", mode="NULLABLE"),
                    bigquery.SchemaField("cachedContentTokenCount", "INT64", mode="NULLABLE"),
                    bigquery.SchemaField("thoughtsTokenCount", "INT64", mode="NULLABLE"),
                    bigquery.SchemaField("truncated", "BOOL", mode="NULLABLE"),
                ],
            )
        ],
    ),
]


def ensure_metadata_tables(
    bq_raw_client: bigquery.Client,
    project_id: str,
    dataset_name: str,
    now_utc: datetime,
) -> tuple[str, str, str, str]:
    """Ensure today's shard, yesterday's shard, an expired (-10d) shard, and the partitioned table exist."""
    today_suffix = now_utc.strftime("%Y%m%d")
    yesterday_suffix = (now_utc - timedelta(days=1)).strftime("%Y%m%d")
    expired_suffix = (now_utc - timedelta(days=10)).strftime("%Y%m%d")

    today_shard_id = f"{project_id}.{dataset_name}.businessaicode_googleapis_com_metadata_{today_suffix}"
    yesterday_shard_id = f"{project_id}.{dataset_name}.businessaicode_googleapis_com_metadata_{yesterday_suffix}"
    expired_shard_id = f"{project_id}.{dataset_name}.businessaicode_googleapis_com_metadata_{expired_suffix}"
    partitioned_table_id = f"{project_id}.{dataset_name}.businessaicode_googleapis_com_metadata"

    for shard_id in (today_shard_id, yesterday_shard_id, expired_shard_id):
        sharded_table = bigquery.Table(shard_id, schema=METADATA_TABLE_SCHEMA)
        bq_raw_client.create_table(sharded_table, exists_ok=True)

    partitioned_table = bigquery.Table(partitioned_table_id, schema=METADATA_TABLE_SCHEMA)
    partitioned_table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field="timestamp",
    )
    bq_raw_client.create_table(partitioned_table, exists_ok=True)

    return today_shard_id, yesterday_shard_id, expired_shard_id, partitioned_table_id


def seed_synthetic_bq_rows(
    bq_raw_client: bigquery.Client,
    today_shard_id: str,
    yesterday_shard_id: str,
    expired_shard_id: str,
    partitioned_table_id: str,
    workload_project_id: str,
    test_user_email: str,
    run_id: str,
    now_utc: datetime,
    healthy_user_email: Optional[str] = None,
    exempt_user_email: Optional[str] = None,
) -> str:
    """Insert synthetic rows for the 3-user cohort across 3 date-sharded tables and the partitioned table."""
    domain = test_user_email.split("@")[1] if "@" in test_user_email else "example.com"
    dev2_email = healthy_user_email or f"portal-smoke-dev2-{run_id}@{domain}"
    admin_email = exempt_user_email or f"portal-smoke-admin-{run_id}@{domain}"

    dup_insert_id = f"portal-live-smoke-{run_id}-dup1"
    expired_insert_id = f"portal-live-smoke-{run_id}-expired"
    admin_shard_id = f"portal-live-smoke-{run_id}-admin1"
    admin_part_id = f"portal-live-smoke-{run_id}-admin2"
    dev2_shard_id = f"portal-live-smoke-{run_id}-dev2-1"
    dev2_part_id = f"portal-live-smoke-{run_id}-dev2-2"

    tagged_user = f"user:{test_user_email}"
    tagged_dev2 = f"user:{dev2_email}"
    tagged_admin = f"user:{admin_email}"
    log_name = f"projects/{workload_project_id}/logs/businessaicode.googleapis.com%2Fmetadata"

    t_dup = now_utc - timedelta(seconds=180)
    t_yesterday = now_utc - timedelta(days=1)
    t_expired = now_utc - timedelta(days=10)
    t_empty = now_utc - timedelta(seconds=60)

    # 1. Today's date-sharded table (_YYYYMMDD):
    #    - dev1 (throttled): 500k gemini-4-argon (insertId=dup_insert_id)
    #    - admin (exempt):   2M gemini-4-argon ($5.55)
    today_shard_dml = f"""
    INSERT INTO `{today_shard_id}` (
        timestamp, insertId, logName, resource, labels, jsonpayload_v1_metadatalog
    )
    VALUES
    (
        @ts_dup,
        @dup_insert_id,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_user AS user_id, 'gemini-4-argon' AS model),
        STRUCT(STRUCT(
            500000 AS totalTokenCount,
            400000 AS promptTokenCount,
            80000 AS candidatesTokenCount,
            100000 AS cachedContentTokenCount,
            20000 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    ),
    (
        @ts_dup,
        @admin_shard_id,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_admin AS user_id, 'gemini-4-argon' AS model),
        STRUCT(STRUCT(
            2000000 AS totalTokenCount,
            1600000 AS promptTokenCount,
            320000 AS candidatesTokenCount,
            400000 AS cachedContentTokenCount,
            80000 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    )
    """

    # 2. Yesterday's date-sharded table (_YYYYMMDD - 1 day):
    #    - dev1 (throttled): 500k gemini-4-argon (NULL insertId)
    #    - dev2 (healthy):   500k gemini-4-argon ($1.3875)
    yesterday_shard_dml = f"""
    INSERT INTO `{yesterday_shard_id}` (
        timestamp, insertId, logName, resource, labels, jsonpayload_v1_metadatalog
    )
    VALUES
    (
        @ts_yesterday,
        NULL,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_user AS user_id, 'gemini-4-argon' AS model),
        STRUCT(STRUCT(
            500000 AS totalTokenCount,
            400000 AS promptTokenCount,
            80000 AS candidatesTokenCount,
            100000 AS cachedContentTokenCount,
            20000 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    ),
    (
        @ts_yesterday,
        @dev2_shard_id,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_dev2 AS user_id, 'gemini-4-argon' AS model),
        STRUCT(STRUCT(
            500000 AS totalTokenCount,
            400000 AS promptTokenCount,
            80000 AS candidatesTokenCount,
            100000 AS cachedContentTokenCount,
            20000 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    )
    """

    # 3. Expired out-of-window shard (_YYYYMMDD - 10 days):
    #    - dev1: 9,000,000 tokens (must be excluded by _TABLE_SUFFIX pruning)
    expired_shard_dml = f"""
    INSERT INTO `{expired_shard_id}` (
        timestamp, insertId, logName, resource, labels, jsonpayload_v1_metadatalog
    )
    VALUES (
        @ts_expired,
        @expired_insert_id,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_user AS user_id, 'gemini-4-argon' AS model),
        STRUCT(STRUCT(
            9000000 AS totalTokenCount,
            7200000 AS promptTokenCount,
            1800000 AS candidatesTokenCount,
            0 AS cachedContentTokenCount,
            0 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    )
    """

    # 4. Time-partitioned table:
    #    - dev1: duplicate insertId (deduplicated against today_shard_id) + 1.5M gemini-3.1-pro >200k tier ('' insertId)
    #    - dev2: 2M gemini-3.8-flash ($2.565 -> $3.95 total for dev2)
    #    - admin: 3M gemini-3.1-pro >200k tier ($26.00 -> $31.55 total for admin)
    partitioned_dml = f"""
    INSERT INTO `{partitioned_table_id}` (
        timestamp, insertId, logName, resource, labels, jsonpayload_v1_metadatalog
    )
    VALUES
    (
        @ts_dup,
        @dup_insert_id,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_user AS user_id, 'gemini-4-argon' AS model),
        STRUCT(STRUCT(
            500000 AS totalTokenCount,
            400000 AS promptTokenCount,
            80000 AS candidatesTokenCount,
            100000 AS cachedContentTokenCount,
            20000 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    ),
    (
        @ts_empty,
        '',
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_user AS user_id, 'gemini-3.1-pro' AS model),
        STRUCT(STRUCT(
            1500000 AS totalTokenCount,
            1000000 AS promptTokenCount,
            500000 AS candidatesTokenCount,
            0 AS cachedContentTokenCount,
            0 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    ),
    (
        @ts_empty,
        @dev2_part_id,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_dev2 AS user_id, 'gemini-3.8-flash' AS model),
        STRUCT(STRUCT(
            2000000 AS totalTokenCount,
            1600000 AS promptTokenCount,
            300000 AS candidatesTokenCount,
            200000 AS cachedContentTokenCount,
            100000 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    ),
    (
        @ts_empty,
        @admin_part_id,
        @log_name,
        STRUCT('businessaicode.googleapis.com/BAICInstance' AS type, STRUCT(@workload_project AS project_id) AS labels),
        STRUCT(@tagged_admin AS user_id, 'gemini-3.1-pro' AS model),
        STRUCT(STRUCT(
            3000000 AS totalTokenCount,
            2000000 AS promptTokenCount,
            1000000 AS candidatesTokenCount,
            0 AS cachedContentTokenCount,
            0 AS thoughtsTokenCount,
            FALSE AS truncated
        ) AS metadata)
    )
    """

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("ts_dup", "TIMESTAMP", t_dup),
            bigquery.ScalarQueryParameter("ts_yesterday", "TIMESTAMP", t_yesterday),
            bigquery.ScalarQueryParameter("ts_expired", "TIMESTAMP", t_expired),
            bigquery.ScalarQueryParameter("ts_empty", "TIMESTAMP", t_empty),
            bigquery.ScalarQueryParameter("dup_insert_id", "STRING", dup_insert_id),
            bigquery.ScalarQueryParameter("expired_insert_id", "STRING", expired_insert_id),
            bigquery.ScalarQueryParameter("admin_shard_id", "STRING", admin_shard_id),
            bigquery.ScalarQueryParameter("admin_part_id", "STRING", admin_part_id),
            bigquery.ScalarQueryParameter("dev2_shard_id", "STRING", dev2_shard_id),
            bigquery.ScalarQueryParameter("dev2_part_id", "STRING", dev2_part_id),
            bigquery.ScalarQueryParameter("log_name", "STRING", log_name),
            bigquery.ScalarQueryParameter("workload_project", "STRING", workload_project_id),
            bigquery.ScalarQueryParameter("tagged_user", "STRING", tagged_user),
            bigquery.ScalarQueryParameter("tagged_dev2", "STRING", tagged_dev2),
            bigquery.ScalarQueryParameter("tagged_admin", "STRING", tagged_admin),
        ]
    )

    bq_raw_client.query(today_shard_dml, job_config=job_config).result()
    bq_raw_client.query(yesterday_shard_dml, job_config=job_config).result()
    bq_raw_client.query(expired_shard_dml, job_config=job_config).result()
    bq_raw_client.query(partitioned_dml, job_config=job_config).result()
    return dup_insert_id


def cleanup_synthetic_bq_rows(
    bq_raw_client: bigquery.Client,
    table_ids: list[str],
    user_emails: list[str] | str,
) -> None:
    """Delete all synthetic smoke-test rows for `user_emails` from all seeded tables."""
    emails = [user_emails] if isinstance(user_emails, str) else list(user_emails)
    targets = [e.lower() for e in emails] + [f"user:{e.lower()}" for e in emails]
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("target_users", "STRING", targets),
        ]
    )
    for table_id in table_ids:
        delete_dml = f"""
        DELETE FROM `{table_id}`
        WHERE LOWER(labels.user_id) IN UNNEST(@target_users)
        """
        bq_raw_client.query(delete_dml, job_config=job_config).result()


def verify_records_and_spend(
    records: list[UserUsageRecord],
    test_user_email: str,
) -> dict[str, Any]:
    """Assert that the fetched records match expected deduplicated counts and pricing math."""
    user_records = [r for r in records if r.user_email.lower() == test_user_email.lower()]
    if len(user_records) != 2:
        raise AssertionError(
            f"Expected 2 model records (gemini-4-argon and gemini-3.1-pro) for {test_user_email}, "
            f"got {len(user_records)}: {user_records}"
        )

    by_model = {r.model_name: r for r in user_records}
    argon = by_model.get("gemini-4-argon")
    pro = by_model.get("gemini-3.1-pro")
    if not argon or not pro:
        raise AssertionError(f"Missing expected model records in {list(by_model.keys())}")

    # Argon: 2 deduplicated requests (Row 1 + Row 3; Row 2 was deduplicated against Row 1)
    if argon.request_count != 2 or argon.token_count != 1_000_000:
        raise AssertionError(
            f"gemini-4-argon deduplication mismatch: expected 2 requests / 1,000,000 tokens, "
            f"got {argon.request_count} requests / {argon.token_count} tokens"
        )

    # Gemini 3.1 Pro: 1 request (Row 4 with empty '' insertId preserved alongside NULL insertId)
    if pro.request_count != 1 or pro.token_count != 1_500_000:
        raise AssertionError(
            f"gemini-3.1-pro mismatch: expected 1 request / 1,500,000 tokens, "
            f"got {pro.request_count} requests / {pro.token_count} tokens"
        )

    pricing = load_default_model_pricing()
    tokens_by_model = {r.model_name: r.token_count for r in user_records}
    granular_by_model = {
        r.model_name: {
            "prompt_tokens": r.prompt_tokens,
            "candidates_tokens": r.candidates_tokens,
            "cached_tokens": r.cached_tokens,
            "thoughts_tokens": r.thoughts_tokens,
            "prompt_tokens_gt_200k": r.prompt_tokens_gt_200k,
            "candidates_tokens_gt_200k": r.candidates_tokens_gt_200k,
            "cached_tokens_gt_200k": r.cached_tokens_gt_200k,
            "thoughts_tokens_gt_200k": r.thoughts_tokens_gt_200k,
            "fallback_tokens": r.fallback_tokens,
        }
        for r in user_records
    }
    gross_spend_usd, _, _, _, _, _, is_throttled = calculate_total_usage_costs(
        tokens_by_model=tokens_by_model,
        pricing_matrix=pricing,
        quota_credits=10.00,
        overage_buffer=2.00,
        granular_by_model=granular_by_model,
    )
    total_requests = sum(r.request_count for r in user_records)
    total_tokens = sum(r.token_count for r in user_records)

    # Argon: 2 * $1.3875 = $2.775 ($0.75 in + $2.00 out + $0.025 cache = $2.78 rounded)
    # Gemini 3.1 Pro (>200k): $4.00 in + $9.00 out = $13.00 | Total = $15.78
    if total_requests != 3 or total_tokens != 2_500_000:
        raise AssertionError(f"Unexpected total requests/tokens: {total_requests} requests, {total_tokens} tokens")
    if abs(gross_spend_usd - 15.78) > 0.02 or not is_throttled:
        raise AssertionError(f"Unexpected gross spend ({gross_spend_usd}) or breach status ({is_throttled})")

    return {
        "total_requests": total_requests,
        "total_tokens": total_tokens,
        "gross_spend_usd": gross_spend_usd,
        "is_hard_breach": is_throttled,
    }


class _CohortScopedDatabaseProxy:
    """Wraps a DatabaseClient so QuotaEvaluator only lists/modifies the ephemeral smoke-test cohort."""

    def __init__(self, inner_db: Any, cohort_emails: list[str]) -> None:
        self._inner = inner_db
        self._cohort = {e.lower().strip() for e in cohort_emails}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def get_all_users(self) -> list[User]:
        all_users = self._inner.get_all_users()
        return [u for u in all_users if u.email.lower().strip() in self._cohort]

    def list_users(self) -> list[User]:
        if hasattr(self._inner, "list_users"):
            all_users = self._inner.list_users()
        else:
            all_users = self._inner.get_all_users()
        return [u for u in all_users if u.email.lower().strip() in self._cohort]


def run_verification(
    project_id: str,
    workload_project_id: str,
    dataset_name: str,
    enabled_group: str,
    disabled_group: str,
    test_user_email: Optional[str] = None,
    portal_url: Optional[str] = None,
    bearer_token: Optional[str] = None,
    mock_mode: bool = False,
    keep_test_data: bool = False,
    bq_raw_client: Optional[Any] = None,
    bq_usage_client: Optional[Any] = None,
    db_client: Optional[Any] = None,
    identity_client: Optional[Any] = None,
    healthy_user_email: Optional[str] = None,
    exempt_user_email: Optional[str] = None,
) -> dict[str, Any]:
    """Execute the 4-stage verification pipeline and return a structured result summary."""
    now_utc = datetime.now(timezone.utc)
    run_id = uuid.uuid4().hex[:8]
    fallback_domain = enabled_group.split("@")[1] if "@" in enabled_group else "example.com"
    test_user_email = test_user_email or f"portal-smoke-dev1-{run_id}@{fallback_domain}"
    domain = test_user_email.split("@")[1] if "@" in test_user_email else fallback_domain
    dev2_email = healthy_user_email or f"portal-smoke-dev2-{run_id}@{domain}"
    admin_email = exempt_user_email or f"portal-smoke-admin-{run_id}@{domain}"
    cohort_emails = [test_user_email, dev2_email, admin_email]

    print("=" * 78)
    print(f"  ANTIGRAVITY QUOTA PORTAL — {'MOCK' if mock_mode else 'LIVE PRODUCTION'} VERIFICATION")
    print("=" * 78)
    print(f"Host Project:     {project_id}")
    print(f"Workload Project: {workload_project_id}")
    print(f"BigQuery Dataset: {dataset_name}")
    print(f"Enabled Group:    {enabled_group}")
    print(f"Disabled Group:   {disabled_group}")
    print(f"Test Cohort (3):  {test_user_email} (breach), {dev2_email} (healthy), {admin_email} (exempt)")

    if mock_mode:
        db = db_client or MockDatabase()
        identity = identity_client or MockCloudIdentityGroupManager(
            enabled_group_email=enabled_group,
            disabled_group_email=disabled_group,
        )
        bq = bq_usage_client or MockBigQueryUsageClient()
    else:
        db = db_client or FirestoreDatabase(project_id=project_id)
        identity = identity_client or CloudIdentityGroupManager(
            enabled_group_email=enabled_group,
            disabled_group_email=disabled_group,
            quota_project_id=project_id,
        )
        bq = bq_usage_client or BigQueryUsageClient(project_id=project_id, dataset_name=dataset_name)
        bq_raw_client = bq_raw_client or bigquery.Client(project=project_id)

    # ------------------------------------------------------------------
    # GATE 1: Pre-Flight Cloud Identity Group & MANAGER Access Check
    # ------------------------------------------------------------------
    print("\n==> [Gate 1/4] Checking Cloud Identity groups and MANAGER permissions...")
    en_members = identity.get_group_members(enabled_group)
    dis_members = identity.get_group_members(disabled_group)
    print(f"  + {enabled_group}: reachable ({len(en_members)} members)")
    print(f"  + {disabled_group}: reachable ({len(dis_members)} members)")

    seeded_table_ids: list[str] = []
    orig_cfg = None
    try:
        # ------------------------------------------------------------------
        # GATE 2: Seed Multi-Day Sharded & Partitioned BigQuery Tables
        # ------------------------------------------------------------------
        print("\n==> [Gate 2/4] Seeding multi-day sharded (_YYYYMMDD) and partitioned BigQuery tables...")
        if mock_mode:
            bq.set_custom_records(
                [
                    UserUsageRecord(
                        user_email=test_user_email,
                        project_id=workload_project_id,
                        model_name="gemini-4-argon",
                        request_count=2,
                        token_count=1_000_000,
                        last_active=now_utc,
                        prompt_tokens=800_000,
                        candidates_tokens=160_000,
                        cached_tokens=200_000,
                        thoughts_tokens=40_000,
                    ),
                    UserUsageRecord(
                        user_email=test_user_email,
                        project_id=workload_project_id,
                        model_name="gemini-3.1-pro",
                        request_count=1,
                        token_count=1_500_000,
                        last_active=now_utc,
                        prompt_tokens=1_000_000,
                        candidates_tokens=500_000,
                        cached_tokens=0,
                        thoughts_tokens=0,
                        prompt_tokens_gt_200k=1_000_000,
                        candidates_tokens_gt_200k=500_000,
                        cached_tokens_gt_200k=0,
                        thoughts_tokens_gt_200k=0,
                    ),
                    UserUsageRecord(
                        user_email=dev2_email,
                        project_id=workload_project_id,
                        model_name="gemini-4-argon",
                        request_count=1,
                        token_count=500_000,
                        last_active=now_utc - timedelta(days=1),
                        prompt_tokens=400_000,
                        candidates_tokens=80_000,
                        cached_tokens=100_000,
                        thoughts_tokens=20_000,
                    ),
                    UserUsageRecord(
                        user_email=dev2_email,
                        project_id=workload_project_id,
                        model_name="gemini-3.8-flash",
                        request_count=1,
                        token_count=2_000_000,
                        last_active=now_utc,
                        prompt_tokens=1_600_000,
                        candidates_tokens=300_000,
                        cached_tokens=200_000,
                        thoughts_tokens=100_000,
                    ),
                    UserUsageRecord(
                        user_email=admin_email,
                        project_id=workload_project_id,
                        model_name="gemini-4-argon",
                        request_count=1,
                        token_count=2_000_000,
                        last_active=now_utc,
                        prompt_tokens=1_600_000,
                        candidates_tokens=320_000,
                        cached_tokens=400_000,
                        thoughts_tokens=80_000,
                    ),
                    UserUsageRecord(
                        user_email=admin_email,
                        project_id=workload_project_id,
                        model_name="gemini-3.1-pro",
                        request_count=1,
                        token_count=3_000_000,
                        last_active=now_utc,
                        prompt_tokens=2_000_000,
                        candidates_tokens=1_000_000,
                        cached_tokens=0,
                        thoughts_tokens=0,
                        prompt_tokens_gt_200k=2_000_000,
                        candidates_tokens_gt_200k=1_000_000,
                        cached_tokens_gt_200k=0,
                        thoughts_tokens_gt_200k=0,
                    ),
                ]
            )
            print("  + Seeded 3-user cohort synthetic records into MockBigQueryUsageClient")
        else:
            today_shard_id, yesterday_shard_id, expired_shard_id, partitioned_table_id = ensure_metadata_tables(
                bq_raw_client, project_id, dataset_name, now_utc
            )
            seeded_table_ids = [today_shard_id, yesterday_shard_id, expired_shard_id, partitioned_table_id]
            dup_id = seed_synthetic_bq_rows(
                bq_raw_client,
                today_shard_id,
                yesterday_shard_id,
                expired_shard_id,
                partitioned_table_id,
                workload_project_id,
                test_user_email,
                run_id,
                now_utc,
                healthy_user_email=dev2_email,
                exempt_user_email=admin_email,
            )
            print(f"  + Seeded 2 rows in today's shard {today_shard_id} (insertId={dup_id} + {admin_email})")
            print(f"  + Seeded 2 rows in yesterday's shard {yesterday_shard_id} (NULL insertId + {dev2_email})")
            print(f"  + Seeded 1 row in expired (-10d) shard {expired_shard_id} (9M tokens; tests _TABLE_SUFFIX pruning)")
            print(f"  + Seeded 4 rows in {partitioned_table_id} (dup insertId, empty '', {dev2_email}, {admin_email})")

        # ------------------------------------------------------------------
        # GATE 3: Verify BigQuery Query, Spend Math & Quota State Machine
        # ------------------------------------------------------------------
        print("\n==> [Gate 3/4] Running BigQuery telemetry query & quota evaluation...")
        window_start = (now_utc - timedelta(days=2)).replace(microsecond=123456)
        window_reset = window_start + timedelta(days=7)
        records = bq.fetch_weekly_usage(
            window_start,
            now_utc,
            {workload_project_id: window_start},
        )
        ledger = verify_records_and_spend(records, test_user_email)
        print(
            f"  + BigQuery multi-shard pruning, deduplication & spend math verified for {test_user_email}: "
            f"{ledger['total_requests']} requests, {ledger['total_tokens']:,} tokens, "
            f"${ledger['gross_spend_usd']:.2f} gross spend"
        )

        if portal_url:
            base_url = portal_url.rstrip("/")
            headers = {"Authorization": f"Bearer {bearer_token}"} if bearer_token else {}
            with httpx.Client(timeout=20.0, headers=headers) as http_client:
                resp = http_client.post(
                    f"{base_url}/api/users",
                    json={
                        "email": test_user_email,
                        "project_id": workload_project_id,
                        "license_tier": "ENTERPRISE_PLUS",
                        "has_custom_quota": True,
                        "custom_quota_usd": 10.00,
                        "custom_overage_usd": 2.00,
                        "is_exempt": False,
                    },
                )
                if resp.status_code not in (200, 201, 400):
                    raise RuntimeError(f"POST /api/users failed ({resp.status_code}): {resp.text}")

                pub_resp = http_client.post(f"{base_url}/api/evaluator/publish")
                if pub_resp.status_code != 200:
                    raise RuntimeError(f"POST /api/evaluator/publish failed ({pub_resp.status_code}): {pub_resp.text}")

                u_resp = http_client.get(f"{base_url}/api/users/{test_user_email}")
                u_resp.raise_for_status()
                u_json = u_resp.json()
                if u_json.get("status") != UserStatus.AUTO_DISABLED.value:
                    raise AssertionError(f"Expected status AUTO_DISABLED via API, got {u_json.get('status')}")
                print(f"  + Live Portal API verified: {test_user_email} transitioned to AUTO_DISABLED")
        else:
            cfg = db.get_config()
            orig_cfg = cfg.model_copy(deep=True)
            cfg.enabled_group = enabled_group
            cfg.disabled_group = disabled_group
            if workload_project_id not in cfg.monitored_projects:
                cfg.monitored_projects.append(workload_project_id)
            db.update_config(cfg)

            week_id, _, _, _ = get_current_week_window(cfg.timezone, now_utc)
            for email in cohort_emails:
                identity.reconcile_user_group(
                    email,
                    "ENABLED",
                    enabled_group=enabled_group,
                    disabled_group=disabled_group,
                )

            # 1. Breach persona (dev1): $10 quota + $2 overage ($12 limit) -> $15.78 spend -> AUTO_DISABLED
            db.save_user(
                User(
                    email=test_user_email,
                    project_id=workload_project_id,
                    status=UserStatus.ACTIVE,
                    has_custom_quota=True,
                    custom_quota_usd=10.00,
                    custom_overage_usd=2.00,
                    is_exempt=False,
                    current_week=CurrentWeekUsage(
                        week_id=week_id,
                        quota_credits_usd=10.00,
                        remaining_credit_usd=10.00,
                        overage_buffer_usd=2.00,
                        wtu_window_start_at=window_start,
                        wtu_window_reset_at=window_reset,
                    ),
                )
            )
            # 2. Healthy persona (dev2): $10 default quota + $2 overage -> $3.95 spend -> remains ACTIVE
            db.save_user(
                User(
                    email=dev2_email,
                    project_id=workload_project_id,
                    status=UserStatus.ACTIVE,
                    has_custom_quota=False,
                    is_exempt=False,
                    current_week=CurrentWeekUsage(
                        week_id=week_id,
                        quota_credits_usd=10.00,
                        remaining_credit_usd=10.00,
                        overage_buffer_usd=2.00,
                        wtu_window_start_at=window_start,
                        wtu_window_reset_at=window_reset,
                    ),
                )
            )
            # 3. Exempt power-user persona (admin): $25 custom quota + $5 overage, is_exempt=True -> $31.55 spend -> remains ACTIVE
            db.save_user(
                User(
                    email=admin_email,
                    project_id=workload_project_id,
                    status=UserStatus.ACTIVE,
                    has_custom_quota=True,
                    custom_quota_usd=25.00,
                    custom_overage_usd=5.00,
                    is_exempt=True,
                    current_week=CurrentWeekUsage(
                        week_id=week_id,
                        quota_credits_usd=25.00,
                        remaining_credit_usd=25.00,
                        overage_buffer_usd=5.00,
                        wtu_window_start_at=window_start,
                        wtu_window_reset_at=window_reset,
                    ),
                )
            )

            scoped_db = _CohortScopedDatabaseProxy(db, cohort_emails)
            evaluator = QuotaEvaluator(db_client=scoped_db, bq_client=bq, identity_client=identity)
            evaluator.run_evaluation(triggered_by="LIVE_DEPLOYMENT_VERIFIER", is_publish=True)

            evaluated_user = db.get_user(test_user_email)
            if not evaluated_user or evaluated_user.status != UserStatus.AUTO_DISABLED:
                raise AssertionError(
                    f"Expected {test_user_email} to transition to AUTO_DISABLED, "
                    f"got {evaluated_user.status if evaluated_user else None}"
                )
            print(
                f"  + Evaluator state machine verified: {test_user_email} transitioned ACTIVE -> AUTO_DISABLED "
                f"(${evaluated_user.current_week.gross_spend_usd:.2f} >= $12.00 limit)"
            )
            evaluated_dev2 = db.get_user(dev2_email)
            if evaluated_dev2:
                print(
                    f"  + Healthy persona verified:         {dev2_email} remained {evaluated_dev2.status.value} "
                    f"(${evaluated_dev2.current_week.gross_spend_usd:.2f} / $10.00 quota, "
                    f"${evaluated_dev2.current_week.remaining_credit_usd:.2f} credit left)"
                )
            evaluated_admin = db.get_user(admin_email)
            if evaluated_admin:
                print(
                    f"  + Exempt persona verified:          {admin_email} remained {evaluated_admin.status.value} (EXEMPT) "
                    f"(${evaluated_admin.current_week.gross_spend_usd:.2f} / $25.00 quota)"
                )

        return {
            "status": "PASS",
            "test_user_email": test_user_email,
            "total_requests": ledger["total_requests"],
            "total_tokens": ledger["total_tokens"],
            "gross_spend_usd": ledger["gross_spend_usd"],
        }
    finally:
        # ------------------------------------------------------------------
        # GATE 4: Clean Teardown
        # ------------------------------------------------------------------
        if keep_test_data:
            print("\n==> [Gate 4/4] Skipping teardown (--keep-test-data enabled).")
        else:
            print("\n==> [Gate 4/4] Cleaning up synthetic BigQuery rows and test cohort users...")
            if not mock_mode and bq_raw_client and seeded_table_ids:
                cleanup_synthetic_bq_rows(
                    bq_raw_client,
                    seeded_table_ids,
                    cohort_emails,
                )
                print(f"  + Deleted synthetic BigQuery rows from {len(seeded_table_ids)} tables (3 shards + partitioned)")
            for email in cohort_emails:
                try:
                    identity.remove_member_from_group(enabled_group, email)
                    identity.remove_member_from_group(disabled_group, email)
                except Exception as exc:
                    print(f"  ! Warning cleaning up group membership for {email}: {exc}")
                try:
                    if hasattr(db, "delete_user"):
                        db.delete_user(email.lower())
                    elif hasattr(db, "_users") and isinstance(db._users, dict):
                        db._users.pop(email.lower(), None)
                    elif hasattr(db, "users_collection"):
                        db.users_collection.document(email.lower()).delete()
                    elif hasattr(db, "db") and hasattr(db, "users_col"):
                        db.db.collection(db.users_col).document(email.lower()).delete()
                except Exception as exc:
                    print(f"  ! Warning deleting test user document {email}: {exc}")
            if orig_cfg is not None:
                try:
                    db.update_config(orig_cfg)
                except Exception as exc:
                    print(f"  ! Warning restoring original portal config: {exc}")
            print("  + Teardown complete!")


def main() -> None:
    if "--mock" not in sys.argv:
        load_dotenv()

    parser = argparse.ArgumentParser(
        description="Verify live (or mock) Antigravity Quota Portal BigQuery & Cloud Identity deployment."
    )
    parser.add_argument(
        "--project-id",
        default=os.getenv("PORTAL_PROJECT_ID") or os.getenv("GCP_PROJECT_ID") or "your-portal-project",
        help="Host GCP project ID where BigQuery and Firestore reside",
    )
    parser.add_argument(
        "--workload-project-id",
        default=(os.getenv("WORKLOAD_PROJECT_IDS") or "").split(",")[0].strip() or "your-workload-project",
        help="Monitored workload GCP project ID",
    )
    parser.add_argument(
        "--dataset",
        default=os.getenv("BIGQUERY_DATASET") or "antigravity_audit_logs",
        help="BigQuery dataset name (default: antigravity_audit_logs)",
    )
    parser.add_argument(
        "--enabled-group",
        default=os.getenv("ENABLED_GROUP_EMAIL") or "antigravity-enabled@example.com",
        help="Cloud Identity enabled group email",
    )
    parser.add_argument(
        "--disabled-group",
        default=os.getenv("DISABLED_GROUP_EMAIL") or "antigravity-disabled@example.com",
        help="Cloud Identity disabled group email",
    )
    parser.add_argument(
        "--test-user",
        default=None,
        help="Smoke-test breach user email (defaults to portal-smoke-dev1-<id>@<enabled-group-domain>)",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Optional live portal URL (e.g. https://<LB_IP>.sslip.io or http://localhost:8080)",
    )
    parser.add_argument(
        "--bearer-token",
        default=os.getenv("BEARER_TOKEN"),
        help="Optional IAP / OAuth Bearer token when calling --url",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run self-test verification in offline mock mode without live GCP calls",
    )
    parser.add_argument(
        "--keep-test-data",
        action="store_true",
        help="Keep synthetic BigQuery rows and test user after verification for UI inspection",
    )
    args = parser.parse_args()

    result = run_verification(
        project_id=args.project_id,
        workload_project_id=args.workload_project_id,
        dataset_name=args.dataset,
        enabled_group=args.enabled_group,
        disabled_group=args.disabled_group,
        test_user_email=args.test_user,
        portal_url=args.url,
        bearer_token=args.bearer_token,
        mock_mode=args.mock,
        keep_test_data=args.keep_test_data,
    )
    print("\n" + "=" * 78)
    print(
        f"✅ VERIFICATION PASSED: {result['total_requests']} deduplicated requests, "
        f"{result['total_tokens']:,} tokens, ${result['gross_spend_usd']:.2f} gross spend"
    )
    print("=" * 78)


if __name__ == "__main__":
    main()

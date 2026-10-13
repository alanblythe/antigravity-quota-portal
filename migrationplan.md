# Production Migration Plan: Upgrading to v1.1.0 (Multi-Project WTU Governance)

This document provides step-by-step instructions for upgrading an existing deployment from **v1.0.0** (legacy single-project, calendar-reset baseline) to **v1.1.0** (multi-project WTU governance, partitioned BigQuery logging, and dynamic context-tiered pricing) across **BigQuery**, **Cloud Logging Sinks**, **Firestore Native**, **Cloud Identity**, and **OpenTofu / Terraform State**.

## Quick 3-Step Migration Summary

1. Remove duplicate manual Cloud Logging sinks in workload projects.
2. Apply Terraform to provision workload sinks, IAM, and container.
3. Reset Pricing Matrix and click **Publish & Sync** once.

---

## 1. Architecture & Schema Evolution: v1.0.0 vs. v1.1.0

| Subsystem | Legacy v1.0.0 Baseline | v1.1.0 Architecture | Upgrade Impact & Actions |
| :--- | :--- | :--- | :--- |
| **BigQuery Audit Log Schema & Queries** ([`app/bq/bq_client.py`](app/bq/bq_client.py)) | Queried only unsharded `businessaicode_googleapis_com_inference_response` for `totalTokenCount`; no workload `project_id`, no granular token tiers (`prompt`, `candidates`, `cached`, `thoughts`), and no `>200k` long-context split. | Queries date-sharded (`_*`) and time-partitioned tables with cross-table `insertId` deduplication and per-project cycle start filtering; extracts `project_id` via `COALESCE(resource.labels.project_id, REGEXP_EXTRACT(logName, ...))`; parses JSON metadata for granular token categories and `>200k` context tiers; filters out `.gserviceaccount.com` principals. | **Medium–High**: Existing datasets transitioning from date-sharded (`_YYYYMMDD`) sinks to Terraform's `use_partitioned_tables = true` sink are automatically unified and deduplicated across the 7-day cutover window. |
| **Cloud Logging Router Sinks** ([`terraform/bigquery.tf`](terraform/bigquery.tf)) | Single log sink created only in the **host** project (`var.project_id`), missing inference logs emitted in spoke workload projects. | `for_each = toset(var.monitored_project_ids)` creates `antigravity-inference-log-sink-<workload_project>` in each workload project routing into the central host BigQuery dataset, with per-sink `roles/bigquery.dataEditor`. | **High**: Destroys the legacy host-project sink resource in Terraform state and provisions per-workload-project sinks. Any pre-existing manual sinks in workload projects must be imported or deleted to prevent duplicate log ingestion. |
| **WTU Telemetry & Weekly Reset Engine** ([`app/bq/wtu_client.py`](app/bq/wtu_client.py), [`app/core/evaluator.py`](app/core/evaluator.py), [`app/main.py`](app/main.py)) | Fixed calendar reset (`Monday 00:00` local time); no Cloud Monitoring WTU integration; single-project assumption. | Queries Cloud Monitoring `discoveryengine.googleapis.com/quota/ratev2/...` per monitored project; detects `ENTERPRISE_PLUS` vs. `ENTERPRISE_STANDARD`; phase-locks **Weekly Reset** (`wtu_window_start_at` / `wtu_window_reset_at`) and dynamic APScheduler `date` triggers to each project's 7-day WTU sliding window. | **Medium**: Existing Firestore `users` documents have `week_id = "YYYY-Www"` and `wtu_window_start_at = null`. First evaluation run backfills `project_id`, `license_tier`, `wtu_window_start_at`, and `wtu_window_reset_at`. |
| **Firestore Data Models & Pricing Catalog** ([`app/db/models.py`](app/db/models.py), [`config/model_pricing.json`](config/model_pricing.json)) | Hardcoded 6-model pricing dictionary in Python; `User` and `AppConfig` lacked multi-project fields and WTU fields. | Externalized pricing catalog (`config/model_pricing.json`) with `>200k` context tier pricing and `POST /api/config/pricing/reset`; added `project_id`, `license_tier`, `wtu_*`, `monitored_projects`, and `project_pools`. | **Low–Medium**: Firestore `config/app_config` retains cached `models` map until merged or reset via `/api/config/pricing/reset`. |
| **Cloud Identity & Workload IAM** ([`app/identity/cloud_identity.py`](app/identity/cloud_identity.py), [`terraform/iam.tf`](terraform/iam.tf), [`terraform/variables.tf`](terraform/variables.tf)) | Static Terraform group memberships in `terraform/users.tf`; `state_machine.py` only checked `enabled_group` membership; no service-account exclusion guard. | Removed static Terraform group membership management (`terraform/users.tf`) in favor of runtime reconciliation; added dual-group drift correction (`current_in_disabled_group`), transactional swap rollback on API error, and `roles/businessaicode.user` + `roles/monitoring.viewer` across `var.monitored_project_ids`. | **Medium**: Terraform state drops `google_cloud_identity_group_membership.initial_developer_memberships` (must be removed from state with `tofu state rm` if previously applied so developers aren't removed from `enabled_group` during `tofu apply`). |

---

## 2. BigQuery & Cloud Logging Sink Migration Plan

### 2.1. Resolving Date-Sharded (`_YYYYMMDD`) vs. Time-Partitioned Tables
When a Cloud Logging sink is created manually in the GCP Console or via `gcloud logging sinks create` without `--use-partitioned-tables`, Cloud Logging exports to daily date-sharded tables:
* `businessaicode_googleapis_com_inference_response_20261012`
* `businessaicode_googleapis_com_client_telemetry_20261012`

By contrast, the Terraform resource `google_logging_project_sink.antigravity_sink` in [`terraform/bigquery.tf`](terraform/bigquery.tf) specifies `use_partitioned_tables = true`, which causes Cloud Logging to write new events to a single ingestion-time partitioned table:
* `businessaicode_googleapis_com_inference_response` (no `_YYYYMMDD` suffix)

#### Migration Risks & Automated Mitigation
1. **Duplicate Sinks in Workload Projects**: If an operator previously created a manual sink in a workload project (e.g., `your-workload-project`) and then runs `tofu apply`, two sinks will export identical `businessaicode.googleapis.com` logs into the same dataset.
2. **Split 7-Day Window Across Sharded & Partitioned Tables**: In [`app/bq/bq_client.py`](app/bq/bq_client.py), `_build_usage_query(use_wildcard_shards=True)` queries `businessaicode_googleapis_com_inference_response_*` (`_YYYYMMDD` sharded tables), while `_build_usage_query(use_wildcard_shards=False, exclude_sharded_insert_ids=True)` queries `businessaicode_googleapis_com_inference_response` and excludes any `insertId` already present in `_*`. `fetch_weekly_usage()` merges both results so that historical `_YYYYMMDD` shards and newly ingested partitioned rows are aggregated across the 7-day cutover window without double-counting.

#### Step-by-Step BigQuery Migration Procedure

1. **Audit Existing Sinks in Each Monitored Workload Project**:
   ```bash
   for PROJ in your-workload-project; do
     gcloud logging sinks list --project="${PROJ}" \
       --filter="destination:antigravity_inference_logs"
   done
   ```
   Delete any legacy manual sinks so only `antigravity-inference-log-sink-${PROJ}` remains active.

2. **Check Existing Table Topology in BigQuery**:
   ```bash
   bq ls --project_id=your-portal-project antigravity_inference_logs
   ```

3. **Consolidate Sharded Tables (Automatic or Optional One-Time SQL Backfill)**:
   * **Option A (Recommended — Zero Manual SQL Needed)**: `BigQueryUsageClient.fetch_weekly_usage` automatically queries both `businessaicode_googleapis_com_inference_response_*` and `businessaicode_googleapis_com_inference_response` while deduplicating by `insertId`. After 7 days, all active cycle logs reside in the partitioned table and legacy `_YYYYMMDD` tables can be dropped.
   * **Option B (Optional One-Time SQL Backfill + Shard Cleanup)**: Once the partitioned table `businessaicode_googleapis_com_inference_response` is created by the new sink, you may optionally copy rows from the active week's `_YYYYMMDD` shards and drop the old shards:
     ```sql
     INSERT INTO `your-portal-project.antigravity_inference_logs.businessaicode_googleapis_com_inference_response`
     SELECT *
     FROM `your-portal-project.antigravity_inference_logs.businessaicode_googleapis_com_inference_response_*`
     WHERE _TABLE_SUFFIX >= FORMAT_TIMESTAMP('%Y%m%d', TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY))
       AND (
         insertId IS NULL
         OR insertId NOT IN (
           SELECT insertId
           FROM `your-portal-project.antigravity_inference_logs.businessaicode_googleapis_com_inference_response`
           WHERE timestamp >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
             AND insertId IS NOT NULL
         )
       );
     ```

### 2.2. Schema Compatibility & `project_id` Extraction
* Cloud Logging sinks exporting `businessaicode.googleapis.com` logs include `resource.labels.project_id` and `logName` (`projects/<project_id>/logs/...`).
* The v1.1.0 SQL query in [`app/bq/bq_client.py`](app/bq/bq_client.py) uses:
  ```sql
  COALESCE(
    resource.labels.project_id,
    REGEXP_EXTRACT(logName, r'^projects/([^/]+)/'),
    @fallback_project_id
  ) AS project_id
  ```
* **No DDL schema changes are required** on existing BigQuery tables because `resource.labels.project_id` and `logName` are standard top-level Cloud Logging export columns, and `TO_JSON_STRING(jsonpayload_v1_inferenceresponselog.metadata)` dynamically extracts `promptTokenCount`, `candidatesTokenCount`, `cachedContentTokenCount`, and `thoughtsTokenCount` without failing if a sub-field is absent in older log entries.

---

## 3. Firestore Native Database Migration Plan

### 3.1. `config/app_config` Document Migration
* **What Changed**:
  * Added `monitored_projects: list[str]`, `weekly_team_budget_usd: float`, `weekly_wtu_quota: float`, and `wtu_tier_metric: str`.
  * Added `context_tier_threshold_tokens` and `*_price_gt_threshold_per_million` to `ModelPricing` in `config/model_pricing.json` (plus new models such as `gemini-3.8-flash`).
* **Migration Behavior**:
  * [`FirestoreDatabase.get_config()`](app/db/firestore_client.py) loads `config/model_pricing.json` first and merges existing Firestore `models` keys on top.
  * However, any **existing** model keys already persisted in Firestore `config/app_config.models` will retain their old Firestore-cached pricing values unless reset.
* **Action Required**:
  * After deploying, invoke the pricing reset endpoint (or click **Reload Config Defaults** in the Pricing Matrix UI) to flush stale Firestore model entries and sync `config/model_pricing.json`:
    ```bash
    curl -X POST https://<YOUR_PORTAL_URL>/api/config/pricing/reset
    ```
  * Verify `monitored_projects` in **Settings** (e.g., `["your-workload-project"]` or additional workload projects).

### 3.2. `users/{email}` Collection & WTU-Locked Weekly Reset Cutover
* **What Changed**:
  * Added `project_id: str | None` and `license_tier: str | None` (`"ENTERPRISE_PLUS"` | `"ENTERPRISE_STANDARD"`) to `User`.
  * Added `wtu_window_start_at: datetime | None`, `wtu_window_reset_at: datetime | None`, `wtu_net_usage: float | None`, `wtu_limit: float | None`, and `wtu_utilization_pct: float | None` to `CurrentWeekUsage`.
  * Changed the weekly governance reset condition in [`QuotaEvaluator.run_evaluation()`](app/core/evaluator.py) from the legacy `Monday 00:00` calendar boundary (`is_new_week`) to the project's 7-day WTU sliding cycle rollover (`eval_time >= user.current_week.wtu_window_reset_at` or `proj_wtu.active_cycle_start_utc > user.current_week.wtu_window_start_at`).
* **Zero-Downtime Cutover Behavior**:
  1. Legacy `users/{email}` documents in Firestore have `wtu_window_start_at = null` and `week_id = "2026-W15"`.
  2. On the **first evaluation run** (`POST /api/evaluator/publish`) after deployment:
     * Because `wtu_window_start_at` is `null` and `week_id` matches the current ISO week (`2026-W15`), the evaluator **does not** trigger a false mid-cycle reset—`AUTO_DISABLED` developers (such as `dev1@example.com`) remain `AUTO_DISABLED`.
     * The evaluator stamps `project_id`, `license_tier`, `wtu_window_start_at`, and `wtu_window_reset_at` onto each developer's Firestore document.
  3. On **Monday 00:00**:
     * Because `wtu_window_start_at` is now populated on every user document, the evaluator ignores the Monday 00:00 calendar crossover and keeps throttled developers locked until their project's actual WTU reset timestamp.
  4. **Service Account Cleanup**:
     * If any `.gserviceaccount.com` principals were previously discovered and written to the `users` collection by an initial v1.0.0 deployment, delete those documents once from Firestore:
       ```bash
       gcloud firestore documents delete \
         "projects/your-portal-project/databases/(default)/documents/users/<sa-email>" --quiet
       ```

---

## 4. Terraform / OpenTofu State & IAM Migration Plan

1. **`google_cloud_identity_group_membership` Removal ([`terraform/iam.tf`](terraform/iam.tf), [`terraform/variables.tf`](terraform/variables.tf))**:
   * Static group memberships were removed from the deleted `terraform/users.tf` file because group memberships are dynamically swapped at runtime between `antigravity-enabled@` and `antigravity-disabled@` by the portal.
   * **Action for Existing Environments**: Before running `tofu apply` on an environment that still tracks `google_cloud_identity_group_membership.initial_developer_memberships` in `terraform.tfstate`, remove it from state so OpenTofu does not delete active developers from the group:
     ```bash
     tofu state rm 'google_cloud_identity_group_membership.initial_developer_memberships' || true
     ```

2. **Log Sink Resource Indexing (`google_logging_project_sink.antigravity_sink`)**:
   * Changed from a single unindexed resource on `var.project_id` to `for_each = toset(var.monitored_project_ids)` on each workload project.
   * Applying Terraform automatically destroys the unused host-project sink (`antigravity-inference-log-sink-<host_project>`) and creates `antigravity-inference-log-sink-<workload_project>` along with `google_bigquery_dataset_iam_member.sink_writer_permission["<workload_project>"]`.

3. **Workload Project IAM Bindings ([`terraform/iam.tf`](terraform/iam.tf))**:
   * Added `google_project_iam_member.workload_monitoring_viewer` (`roles/monitoring.viewer` for `portal_sa` on each workload project) and `google_project_iam_member.workload_businessaicode_user` (`roles/businessaicode.user` for `group:${var.enabled_group_email}` on each workload project).
   * **Deployer Prerequisite**: The identity executing `tofu apply` must hold `roles/resourcemanager.projectIamAdmin` and `roles/logging.configWriter` on every project listed in `var.monitored_project_ids`.

---

## 5. Post-Migration Verification Checklist

- [ ] **Verify Sinks**: Confirm exactly one Cloud Logging sink per workload project routes `businessaicode` logs to `your-portal-project.antigravity_inference_logs`.
- [ ] **Trigger Publish & Sync**: Run `POST /api/evaluator/publish` (or click **Publish & Sync** in the UI) so all Firestore `users` documents are backfilled with `project_id`, `license_tier`, `wtu_window_start_at`, and `wtu_window_reset_at`.
- [ ] **Verify APScheduler One-Shot Reset Job**: Check Cloud Run logs for `Scheduled exact Weekly Reset trigger for project '<project_id>' at <ISO_TIMESTAMP>`.
- [ ] **Verify KPI & Developer Table UI**: Confirm the top project card displays the workload project ID, license tier badge (`Enterprise Plus` / `Enterprise Standard`), WTU pool utilization, and **Weekly Reset** countdown, and that each developer row displays the matching **Weekly Reset** timestamp.

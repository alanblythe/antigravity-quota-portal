# Antigravity Quota & Usage Management Portal

Project-level context, architectural guidelines, and key operational patterns for the `antigravity-quota-portal` codebase.

---

## 1. Project Overview & Architecture

* **Purpose**: Enterprise FinOps credit management, token consumption tracking, and automated access control for **Google Antigravity** (`roles/businessaicode.user`).
* **Runtime Architecture**: Single always-on Google Cloud Run container (`min_instances = 1, max_instances = 1`) hosting:
  * **FastAPI Web Server** (`app/main.py`) serving REST APIs and compiled React dashboard (`app/static/`).
  * **APScheduler Background Worker** executing hourly quota evaluations and dynamic one-shot **Weekly Reset** triggers aligned to each monitored project's 7-day WTU sliding cycle.
* **Dual-Group Cloud Identity Model**:
  * `antigravity-enabled@<org>`: Members possess GCP IAM permission `roles/businessaicode.user` across all monitored workload projects (e.g. `your-workload-project`).
  * `antigravity-disabled@<org>`: Zero GCP IAM permissions (developer paused/throttled).
  * **Zero Runtime IAM Admin Required**: The portal service account only needs Cloud Identity group membership management permissions (`roles/cloudidentity.groupAdmin` or Group Editor) at runtime—no GCP project-level IAM admin rights.
* **Hub-and-Spoke Multi-Project Workload Architecture**:
  * **Host Project (`your-portal-project`)**: Houses Cloud Run, Firestore Native, central BigQuery dataset, and portal service account.
  * **Monitored Workload Projects (`your-workload-project`)**: Workload projects (`Enterprise Plus` or `Enterprise Standard`) where developers execute Antigravity inference. Cloud Logging sinks route `businessaicode.googleapis.com` logs across projects into the central BigQuery dataset, and Cloud Monitoring provides per-project 7-day WTU sliding quota telemetry.

---

## 2. Core FinOps & Cost Calculation Rules

* **WTU-Locked Weekly Reset & Fallback Window** ([`app/bq/wtu_client.py`](app/bq/wtu_client.py), [`app/core/timezone_engine.py`](app/core/timezone_engine.py)):
  * Each monitored project's 7-day cycle window is anchored to its Discovery Engine `window_start_time` (`active_cycle_start_utc` to `active_cycle_reset_utc = start + 7 days`).
  * Falls back to **Monday 00:00:00 to Sunday 23:59:59** in the configured application timezone (default: `America/Los_Angeles`) when no WTU window has been established yet.
* **Credit & Billable Overages Math** ([`app/core/cost_model.py`](app/core/cost_model.py)):
  * **Gross Spend ($C_{\text{gross}}$)**: Total estimated token value consumed in the active 7-day cycle (supporting granular `prompt`, `candidates`, `cached`, `thoughts` token telemetry and `>200k` context tier rates).
  * **Remaining Credit ($B_{\text{credit}}$)**: $\max(0, Q - C_{\text{gross}})$, where $Q$ is developer quota.
  * **Net Billable Cost ($C_{\text{net}}$)**: $\max(0, C_{\text{gross}} - Q)$ (liability after credit depletion).
  * **Hard Quota Limit**: $Q + O$, where $O$ is overage buffer. Auto-throttles when $C_{\text{gross}} \ge Q + O$.
* **Blended Token Rate Matrix** ([`config/model_pricing.json`](config/model_pricing.json)):
  * Formulated using estimated input/output/caching hinge ratios when granular token counts are absent:
    $$R_{\text{blended}} = w_{\text{in}} P_{\text{in}} + w_{\text{out}} P_{\text{out}} + w_{\text{cache}} P_{\text{cache}}$$
  * Dynamic model matching using wildcard patterns (e.g. `%3.8%flash%`, `%2.5%pro%`) with fallback default pricing.

---

## 3. Developer Access State Machine

* **State Transitions** ([`app/core/state_machine.py`](app/core/state_machine.py)):
  * `ACTIVE`: Normal operating status; member of enabled group.
  * `AUTO_DISABLED`: Automatically throttled when $C_{\text{gross}} \ge Q + O$; swapped to disabled group.
  * `MANUALLY_DISABLED`: Administratively locked by admin; swapped to disabled group.
  * `is_exempt = True`: Bypass all quota limits; guaranteed membership in enabled group.
* **Weekly Reset Policy**:
  * Phase-locked to the developer's workload project 7-day WTU sliding cycle reset (`wtu_window_reset_at`), with Monday 00:00:00 local time as fallback.
  * `AUTO_DISABLED` developers are automatically restored to `ACTIVE` and swapped back to `antigravity-enabled@`.
  * `MANUALLY_DISABLED` developers **remain locked** across weekly resets until manually unlocked by an admin.

---

## 4. Key Directory & Code Organization

```
antigravity-quota-portal/
├── app/
│   ├── main.py                  # FastAPI server, lifespan events, APScheduler & WTU triggers
│   ├── config.py                # Pydantic Settings & GCP environment config
│   ├── api/                     # REST endpoints (/api/health, /api/users, /api/config, /api/evaluator, /api/audit)
│   ├── audit/                   # GCP Cloud Logging audit emitter & mock
│   ├── core/                    # Core logic: cost_model, state_machine, timezone_engine, evaluator
│   ├── db/                      # Firestore client, Pydantic models, in-memory MockDatabase
│   ├── bq/                      # BigQuery log reader, CloudMonitoringWTUClient, & mocks
│   ├── identity/                # Cloud Identity API manager & MockCloudIdentityGroupManager
│   └── static/                  # Compiled Vite + React SPA static bundle
├── config/
│   └── model_pricing.json       # Externalized model pricing & >200K context tier catalog
├── frontend/                    # Vite + React 18 + TypeScript + MUI 5 modular codebase
├── terraform/                   # Production GCP Terraform IaC (Cloud Run, LB/IAP, BQ, Firestore, IAM)
├── scripts/
│   ├── auth-dev1.sh             # Isolated dev1 CLI authentication helper
│   ├── auth-dev2.sh             # Isolated dev2 CLI authentication helper
│   ├── build_and_push.sh        # Podman build & push script
│   ├── capture_screenshot.py    # Headless Chrome dashboard screenshot capture
│   ├── deploy_cloudbuild.sh     # Cloud Build deployment automation script
│   ├── deploy_terraform.sh      # OpenTofu / Terraform deploy script
│   ├── run_local_demo.py        # Local sandbox runner (seeds 3 demo users)
│   ├── security_scan.sh         # Security & secret scan script
│   ├── seed_demo_data.py        # REST API demo user seeder script
│   ├── simulate_lifecycle.py    # Multi-user quota lifecycle simulator script
│   └── test_ge_quota_exhaustion.py # Gemini Enterprise WTU exhaustion test script
└── tests/                       # Pytest test suite
    ├── conftest.py              # Shared test fixtures & deterministic mock environment
    ├── simulation/              # Multi-user CLI isolation runner & lifecycle simulation tests
    ├── test_api.py              # REST API endpoint integration tests
    ├── test_audit_gcp.py        # GCP Cloud Logging audit emitter tests
    ├── test_cost_model.py       # Token cost & context-tier math tests
    ├── test_evaluator.py        # Quota evaluator & WTU cycle rollover tests
    ├── test_state_machine.py    # Dual-group state transition tests
    └── test_timezone.py         # Timezone & weekly window boundary tests
```

---

## 5. Development & Testing Workflow

* **Local Sandbox Mode**:
  * Set `USE_MOCK_SERVICES=true` (or run out-of-the-box defaults) to test without active GCP credentials.
  * Uses thread-safe mock stores pre-seeded with synthetic developer profiles and usage logs.
* **Running Tests**:
  ```bash
  uv run pytest -v
  ```
* **Running the Application**:
  ```bash
  uv run uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
  ```
* **Publish & Sync Workflow**:
  * Saving user edits (`PATCH /api/users/{email}`) persists changes to Firestore and immediately reconciles that user's Cloud Identity group membership.
  * Triggering `POST /api/evaluator/publish` (or clicking **Publish & Sync**) triggers an immediate full-org BigQuery telemetry evaluation and Cloud Identity group sync across all developers.

---

## 6. Implementation & Operational Considerations

1. **Firestore Batch Limits**: Firestore batches support up to 500 operations per batch. Ensure bulk updates chunk requests when scaling beyond 500 developers.
2. **BigQuery Table Partitioning & Shards**: `BigQueryUsageClient` queries both date-sharded (`businessaicode_googleapis_com_inference_response_*`) and partitioned (`businessaicode_googleapis_com_inference_response`) tables with cross-table `insertId` deduplication and per-project cycle start filtering.
3. **Frontend Builds**: Build the TypeScript/Vite application in `frontend/` (`npm run build`), which outputs directly to `app/static/` via `vite.config.ts` (`outDir: '../app/static'`); `Dockerfile` and `Containerfile` compile `frontend/` automatically during container builds.
4. **Default GCP Region**: Defaulted to `us-east5` across Terraform and deployment scripts.
5. **External HTTPS Load Balancer & IAP Security**: Provisioned via `terraform/lb.tf` with an External Application Load Balancer, automated `sslip.io` (or custom domain) Google-managed SSL certificate, and Identity-Aware Proxy (IAP).

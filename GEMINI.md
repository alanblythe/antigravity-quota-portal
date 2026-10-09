# Antigravity Quota & Usage Management Portal

Project-level context, architectural guidelines, and key operational patterns for the `antigravity-quota-portal` codebase.

---

## 1. Project Overview & Architecture

* **Purpose**: Enterprise FinOps credit management, token consumption tracking, and automated access control for **Google Antigravity** (`roles/businessaicode.user`).
* **Runtime Architecture**: Single always-on Google Cloud Run container (`min_instances = 1, max_instances = 1`) hosting:
  * **FastAPI Web Server** (`app/main.py`) serving REST APIs and static React dashboard.
  * **APScheduler Background Worker** executing hourly quota evaluations and Monday resets.
* **Dual-Group Cloud Identity Model**:
  * `antigravity-enabled@<org>`: Members possess GCP IAM permission `roles/businessaicode.user` across all monitored workload projects (e.g. `your-workload-project`).
  * `antigravity-disabled@<org>`: Zero GCP IAM permissions (developer paused/throttled).
  * **Zero IAM Admin Required**: The portal service account only needs Directory API group membership management permissions—no GCP project-level IAM admin rights.
* **Hub-and-Spoke Workload Architecture**:
  * **Host Project (`your-portal-project`)**: Houses Cloud Run, Firestore Native, central BigQuery dataset, and portal service account.
  * **Monitored Workload Projects (`your-workload-project`)**: Workload projects where developers execute Antigravity inference. Cloud Logging sinks route `businessaicode.googleapis.com` logs across projects into the central BigQuery dataset.

---

## 2. Core FinOps & Cost Calculation Rules

* **Weekly Boundary Window** ([`app/core/timezone_engine.py`](file:///mnt/data/repos/antigravity-quota-portal/app/core/timezone_engine.py)):
  * Calculated from **Monday 00:00:00 to Sunday 23:59:59** in the configured application timezone (default: `America/Los_Angeles`).
  * Converted dynamically to UTC timestamps for BigQuery audit log queries.
* **Credit & Billable Overages Math** ([`app/core/cost_model.py`](file:///mnt/data/repos/antigravity-quota-portal/app/core/cost_model.py)):
  * **Gross Spend ($C_{\text{gross}}$)**: Total estimated token value consumed this week.
  * **Remaining Credit ($B_{\text{credit}}$)**: $\max(0, Q - C_{\text{gross}})$, where $Q$ is developer quota.
  * **Net Billable Cost ($C_{\text{net}}$)**: $\max(0, C_{\text{gross}} - Q)$ (liability after credit depletion).
  * **Hard Quota Limit**: $Q + O$, where $O$ is overage buffer. Auto-throttles when $C_{\text{gross}} \ge Q + O$.
* **Blended Token Rate Matrix**:
  * Formulated using estimated input/output/caching hinge ratios:
    $$R_{\text{blended}} = w_{\text{in}} P_{\text{in}} + w_{\text{out}} P_{\text{out}} + w_{\text{cache}} P_{\text{cache}}$$
  * Dynamic model matching using wildcard patterns (e.g. `%3%pro%`, `%3.6%flash%`, `*`) with fallback default pricing.

---

## 3. Developer Access State Machine

* **State Transitions** ([`app/core/state_machine.py`](file:///mnt/data/repos/antigravity-quota-portal/app/core/state_machine.py)):
  * `ACTIVE`: Normal operating status; member of enabled group.
  * `AUTO_DISABLED`: Automatically throttled when $C_{\text{gross}} \ge Q + O$; swapped to disabled group.
  * `MANUALLY_DISABLED`: Administratively locked by admin; swapped to disabled group.
  * `is_exempt = True`: Bypass all quota limits; guaranteed membership in enabled group.
* **Weekly Reset Policy**:
  * Occurs on Monday at 00:00:00 local time.
  * `AUTO_DISABLED` developers are automatically restored to `ACTIVE` and swapped back to `antigravity-enabled@`.
  * `MANUALLY_DISABLED` developers **remain locked** across weekly resets until manually unlocked by an admin.

---

## 4. Key Directory & Code Organization

```
antigravity-quota-portal/
├── app/
│   ├── main.py                  # FastAPI server, lifespan events, SPA static serving
│   ├── config.py                # Pydantic Settings & GCP environment config
│   ├── api/                     # REST endpoints (/api/users, /api/config, /api/evaluator, /api/audit)
│   ├── core/                    # Core logic: cost_model, state_machine, timezone_engine, evaluator
│   ├── db/                      # Firestore client, Pydantic models, in-memory MockDatabase
│   ├── bq/                      # BigQuery audit log reader & MockBigQueryUsageClient
│   ├── identity/                # Cloud Identity Directory API manager & MockCloudIdentityGroupManager
│   └── static/index.html        # Production standalone single-file Material UI bundle (zero-build)
├── frontend/                    # Vite + React 18 + TypeScript + MUI 5 modular codebase
├── terraform/                   # Production GCP Terraform IaC (Cloud Run, BQ, Firestore, IAM)
├── scripts/                     # Build, push, and deployment automation scripts
└── tests/                       # Pytest test suite (cost model, state machine, evaluator, API)
```

---

## 5. Development & Testing Workflow

* **Local Sandbox Mode**:
  * Set `USE_MOCK_SERVICES=true` (or run out-of-the-box defaults) to test without active GCP credentials.
  * Uses thread-safe mock stores pre-seeded with synthetic developer profiles and usage logs.
* **Running Tests**:
  ```bash
  pytest -v
  ```
* **Running the Application**:
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
  ```
* **Publish & Sync Workflow**:
  * Changes saved in UI or API are stored in Firestore as drafts.
  * Triggering `/api/evaluator/publish` (or clicking **Publish & Sync**) reconciles BigQuery logs and commits group swaps to Cloud Identity immediately.

---

## 6. Implementation & Operational Considerations

1. **Firestore Batch Limits**: Firestore batches support up to 500 operations per batch. Ensure bulk updates chunk requests when scaling beyond 500 developers.
2. **BigQuery Table Partitioning**: The Log Router exports to `businessaicode_googleapis_com_inference_response`. Filter by partition timestamp (`timestamp >= @start_timestamp_utc`) to minimize scan costs.
3. **Frontend Builds**: The repo includes both a standalone zero-build HTML bundle in `app/static/index.html` and a TypeScript/Vite application in `frontend/`. If building via Vite, output writes to `dist/` and is packaged into `app/static/` during container builds.
4. **Default GCP Region**: Defaulted to `us-east5` across Terraform and deployment scripts.
5. **IAP & Direct Cloud Run Security**: Cloud Run is protected with native Cloud Run Identity-Aware Proxy (`--iap`). All requests to `https://*.run.app` require authentication through Google Identity, and access is restricted to authorized identities (e.g., `domain:example.com`) via `roles/iap.httpsResourceAccessor`. Unauthenticated requests receive HTTP 302 redirects to Google OAuth login. Zero custom Load Balancer or SSL infrastructure overhead required.


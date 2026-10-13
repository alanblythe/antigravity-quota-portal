# Terraform & OpenTofu Infrastructure Guide

This directory contains the production infrastructure-as-code (IaC) configuration for deploying the **Antigravity Quota & Usage Management Portal** to Google Cloud using OpenTofu or HashiCorp Terraform.

---

## 1. Hub-and-Spoke Multi-Project Architecture

The portal operates on a **Hub-and-Spoke** topology:

* **Host Project (`project_id`)**: Houses the single always-on Cloud Run service (`min=1, max=1`), Cloud Firestore Native database, central BigQuery dataset (`antigravity_inference_logs`), Artifact Registry repository, and the External Application Load Balancer with Identity-Aware Proxy (IAP).
* **Monitored Workload Projects (`monitored_project_ids`)**: Workload projects where developers run Antigravity inference. Terraform automatically provisions:
  1. **Cloud Logging Router Sinks** (`antigravity-inference-log-sink-<workload_project>`) routing `businessaicode.googleapis.com` logs into the central host BigQuery dataset.
  2. **Workload IAM Bindings**:
     * `roles/businessaicode.user` granted to `group:${var.enabled_group_email}` across each workload project.
     * `roles/monitoring.viewer` granted to the portal service account (`portal_sa`) on each workload project for live 7-day WTU sliding quota telemetry.
  3. **Workload API Enablement**: `logging.googleapis.com` and `monitoring.googleapis.com`.

> [!NOTE]
> **Single-Project Support**: If your organization operates out of a single GCP project for both the portal and developer inference, simply set `monitored_project_ids = ["<YOUR_PROJECT_ID>"]` and `project_id = "<YOUR_PROJECT_ID>"`.

---

## 2. Quick 5-Step Installation Walkthrough

### Step 1: Clone & Configure Variables
```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your project IDs, Cloud Identity groups, and region (see [Variable Catalog](#3-terraform-variables-catalog) below).

---

### Step 2: Initialize OpenTofu / Terraform
```bash
tofu init
# (or: terraform init)
```

---

### Step 3: Provision Artifact Registry First
Before Cloud Run can deploy, its container image must exist in Artifact Registry. Provision the repository first:
```bash
tofu apply -target=google_artifact_registry_repository.docker_repo
```

---

### Step 4: Build & Push the Container Image
From the repository root, build and push the production image:
```bash
cd ..
./scripts/build_and_push.sh <YOUR_PORTAL_PROJECT_ID> us-east5 latest
cd terraform
```

*(Alternatively, use Google Cloud Build: `./scripts/deploy_cloudbuild.sh <YOUR_PORTAL_PROJECT_ID> us-east5 latest`)*.

---

### Step 5: Deploy the Complete Infrastructure Stack
```bash
tofu apply
```

This provisions:
* Host project Google Cloud APIs (`run`, `firestore`, `bigquery`, `iap`, etc.)
* Spoke project Google Cloud APIs (`logging`, `monitoring`)
* Cloud Firestore Native database
* Central BigQuery dataset and sink permissions
* Cross-project Cloud Logging sinks on all workload projects
* Cloud Run v2 service with runtime environment variables
* External HTTPS Application Load Balancer with automated `sslip.io` Google-managed SSL certificate
* Identity-Aware Proxy (IAP) OAuth backend configuration and access policies

---

## 3. Terraform Variables Catalog

### Core Project & Multi-Project Settings

| Variable | Type | Default | Required? | Description & Rules |
| :--- | :---: | :---: | :---: | :--- |
| `project_id` | `string` | — | **Yes** | GCP Host Project ID where Cloud Run, Firestore, BigQuery, and the Load Balancer reside. |
| `monitored_project_ids` | `list(string)` | — | **Yes** | List of GCP workload project IDs where developers execute Antigravity inference. For a single project setup, pass `[var.project_id]`. For multiple projects, pass `["project-alpha", "project-beta"]`. |
| `region` | `string` | `"us-east5"` | No | GCP region for Cloud Run, Artifact Registry, Firestore, and BigQuery. |
| `bigquery_dataset_name` | `string` | `"antigravity_inference_logs"` | No | Central BigQuery dataset name where inference logs from all workload projects are routed. |

---

### Cloud Identity & Access Governance

| Variable | Type | Default | Required? | Description & Rules |
| :--- | :---: | :---: | :---: | :--- |
| `enabled_group_email` | `string` | — | **Yes** | Cloud Identity group email for active developers. Granted `roles/businessaicode.user` across all `monitored_project_ids`. Must be a valid email address. |
| `disabled_group_email` | `string` | — | **Yes** | Cloud Identity group email for paused/throttled developers. Possesses zero GCP IAM permissions. Must be distinct from `enabled_group_email`. |
| `app_timezone` | `string` | `"America/Los_Angeles"` | No | Application timezone used for Monday `00:00:00` calendar reset fallback calculations. |

---

### FinOps Quotas & Evaluation Rules

| Variable | Type | Default | Required? | Description & Rules |
| :--- | :---: | :---: | :---: | :--- |
| `default_quota_usd` | `number` | `10.00` | No | Default weekly developer credit allocation in USD ($Q$). |
| `default_overage_usd` | `number` | `2.00` | No | Default overage buffer in USD before auto-throttling ($O$). Hard limit is $Q + O = \$12.00$. |
| `preset_quotas` | `string` | `"[10.0, 15.0, 25.0, 50.0, 100.0]"` | No | JSON-encoded array of suggested dollar quota options presented in the admin dashboard. |
| `scheduler_interval_hours` | `number` | `1` | No | Frequency in hours for background evaluation and dual-group reconciliation runs. |
| `enable_gcp_audit_logging` | `bool` | `true` | No | Emits structured FinOps and access governance audit logs to Cloud Logging. |
| `gcp_audit_log_name` | `string` | `"antigravity-quota-audit"` | No | Log name under which portal governance audit events are emitted. |
| `model_pricing_config_path` | `string` | `"config/model_pricing.json"` | No | Path to the model pricing catalog relative to the container root. |
| `webhook_alert_url` | `string` | `null` | No | Optional HTTPS webhook URL (Slack, Google Chat, Teams, PagerDuty) for real-time quota throttling notifications. |

---

### Ingress, Load Balancer & Identity-Aware Proxy (IAP)

| Variable | Type | Default | Required? | Description & Rules |
| :--- | :---: | :---: | :---: | :--- |
| `enable_lb` | `bool` | `true` | No | Provisions External HTTPS Application Load Balancer with Google-managed SSL. Set `false` for internal-only Cloud Run deployments. |
| `domain_override` | `string` | `""` | No | Custom domain (e.g. `quota.example.com`). If left empty, derives an automated magic DNS certificate via `<IP>.sslip.io`. |
| `iap_oauth_client_id` | `string` | `""` | **When IAP enabled** | OAuth 2.0 Client ID for Identity-Aware Proxy. Created under **APIs & Services > Credentials**. |
| `iap_oauth_client_secret` | `string` | `""` | **When IAP enabled** | OAuth 2.0 Client Secret for Identity-Aware Proxy (sensitive). |
| `iap_allowed_members` | `list(string)` | `[]` | **When IAP enabled** | Principals granted `roles/iap.httpsResourceAccessor` (e.g. `["domain:example.com"]`, `["user:admin@example.com"]`). Rejects public principals (`allUsers`, `allAuthenticatedUsers`). |
| `allow_unauthenticated_lb` | `bool` | `false` | No | Bypasses IAP authentication on the load balancer (not recommended for production). |

---

## 4. Deployer IAM Prerequisites

The identity (user or service account) executing `tofu apply` must hold the following permissions:

1. **Host Project (`project_id`)**:
   * `roles/owner` OR `roles/editor` + `roles/resourcemanager.projectIamAdmin` + `roles/iam.serviceAccountAdmin`
2. **Workload Projects (`monitored_project_ids`)**:
   * `roles/resourcemanager.projectIamAdmin` (to bind `roles/businessaicode.user` and `roles/monitoring.viewer`)
   * `roles/logging.configWriter` (to create cross-project Cloud Logging sinks)
   * `roles/serviceusage.serviceUsageAdmin` (to enable `logging.googleapis.com` and `monitoring.googleapis.com`)
3. **Cloud Identity Domain**:
   * The portal runtime service account (`portal_sa@<project_id>.iam.gserviceaccount.com`) must be granted `Group Editor` or `roles/cloudidentity.groupAdmin` on the `antigravity-enabled@` and `antigravity-disabled@` groups in the Google Admin Console.

---

## 5. Workload Project Prerequisite: Enable Metadata Logging

In order for Antigravity inference logs to emit token counts into the BigQuery sink, each monitored workload project must have **Metadata logging** enabled:

1. Open **Gemini Enterprise > Settings > AI developer tools** in the workload project:
   `https://console.cloud.google.com/gemini-enterprise/settings/ai-developer-tools?project=<WORKLOAD_PROJECT_ID>`
2. Under **Compliance**, click **Edit**.
3. Toggle **Metadata logging** to **Enabled** and click **Save**.

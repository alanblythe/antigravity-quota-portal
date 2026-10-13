variable "project_id" {
  type        = string
  description = "Google Cloud Project ID"

  validation {
    condition     = length(trimspace(var.project_id)) > 0 && var.project_id != "your-portal-host-project" && var.project_id != "your-portal-project"
    error_message = "project_id must be a valid non-placeholder GCP project ID."
  }
}

variable "region" {
  type        = string
  description = "GCP Region for Cloud Run, Artifact Registry, Firestore and BigQuery"
  default     = "us-east5"
}

variable "bigquery_dataset_name" {
  type        = string
  description = "BigQuery dataset name for exported Antigravity inference audit logs"
  default     = "antigravity_inference_logs"
}

variable "enabled_group_email" {
  type        = string
  description = "Cloud Identity group email for active developers with roles/businessaicode.user"

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.enabled_group_email)) && var.enabled_group_email != "antigravity-enabled@yourcompany.com"
    error_message = "enabled_group_email must be a valid non-placeholder email address."
  }
}

variable "disabled_group_email" {
  type        = string
  description = "Cloud Identity group email for throttled/locked developers"

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.disabled_group_email)) && var.disabled_group_email != "antigravity-disabled@yourcompany.com"
    error_message = "disabled_group_email must be a valid non-placeholder email address."
  }
}

variable "app_timezone" {
  type        = string
  description = "Application timezone for weekly Monday 00:00 boundary calculation"
  default     = "America/Los_Angeles"
}

variable "default_quota_usd" {
  type        = number
  description = "Default weekly developer quota credit allocation in USD"
  default     = 10.00

  validation {
    condition     = var.default_quota_usd >= 0
    error_message = "default_quota_usd must be greater than or equal to 0."
  }
}

variable "default_overage_usd" {
  type        = number
  description = "Default weekly developer overage buffer before auto-throttling in USD"
  default     = 2.00

  validation {
    condition     = var.default_overage_usd >= 0
    error_message = "default_overage_usd must be greater than or equal to 0."
  }
}

variable "image_tag" {
  type        = string
  description = "Docker image tag to deploy to Cloud Run"
  default     = "latest"
}

variable "domain_override" {
  type        = string
  description = "Optional custom public hostname for the LB. If empty, derives a sslip.io hostname from the static IP."
  default     = ""

  validation {
    condition     = var.domain_override == "" || can(regex("^[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$", var.domain_override))
    error_message = "domain_override must be empty or a valid DNS hostname."
  }
}

variable "iap_oauth_client_id" {
  type        = string
  description = "OAuth 2.0 Client ID for Identity-Aware Proxy (IAP)"
  default     = ""
}

variable "iap_oauth_client_secret" {
  type        = string
  description = "OAuth 2.0 Client Secret for Identity-Aware Proxy (IAP)"
  default     = ""
  sensitive   = true
}

variable "enable_lb" {
  type        = bool
  description = "Enable provisioning the External Application Load Balancer + sslip.io (or custom domain) + optional IAP stack. Set to false for internal/direct Cloud Run deployment without an external IP."
  default     = true
}

variable "allow_unauthenticated_lb" {
  description = "Explicitly allow provisioning the external Load Balancer without IAP OAuth credentials (not recommended for production)"
  type        = bool
  default     = false
}

variable "iap_allowed_members" {
  type        = list(string)
  description = "List of members (e.g. domain:example.com, user:foo@example.com) granted roles/iap.httpsResourceAccessor on the web backend service."
  default     = []

  validation {
    condition = alltrue([
      for m in var.iap_allowed_members :
      length(trimspace(m)) > 0 &&
      !contains(["domain:yourcompany.com", "user:admin@yourcompany.com", "allUsers", "allAuthenticatedUsers"], trimspace(m))
    ])
    error_message = "iap_allowed_members must not contain placeholder values ('domain:yourcompany.com', 'user:admin@yourcompany.com') or public principals ('allUsers', 'allAuthenticatedUsers')."
  }
}

variable "monitored_project_ids" {
  type        = list(string)
  description = "List of GCP workload project IDs where developers run Antigravity inference"

  validation {
    condition = (
      length(var.monitored_project_ids) > 0 &&
      alltrue([
        for p in var.monitored_project_ids :
        length(trimspace(p)) > 0 && !contains(["my-workload-project", "your-workload-project", "your-workload-project-std"], trimspace(p))
      ])
    )
    error_message = "monitored_project_ids must contain at least one valid non-placeholder GCP workload project ID."
  }
}

variable "webhook_alert_url" {
  type        = string
  description = "Optional HTTPS webhook URL (Google Chat, Slack, PagerDuty) for real-time quota throttling notifications"
  default     = null
}

variable "preset_quotas" {
  type        = string
  description = "JSON-encoded list of preset weekly quota amounts in USD displayed in the admin portal UI"
  default     = "[10.0, 15.0, 25.0, 50.0, 100.0]"

  validation {
    condition     = can(jsondecode(var.preset_quotas))
    error_message = "preset_quotas must be a valid JSON-encoded list of numbers."
  }
}

variable "cors_origins" {
  type        = string
  description = "Comma-separated list of allowed CORS origins for the FastAPI backend"
  default     = "*"
}

variable "scheduler_interval_hours" {
  type        = number
  description = "Interval in hours between background APScheduler quota evaluation runs"
  default     = 1

  validation {
    condition     = var.scheduler_interval_hours >= 1
    error_message = "scheduler_interval_hours must be at least 1."
  }
}

variable "enable_gcp_audit_logging" {
  type        = bool
  description = "Enable emitting structured FinOps and access state audit logs to Google Cloud Logging"
  default     = true
}

variable "gcp_audit_log_name" {
  type        = string
  description = "Google Cloud Logging log name for Antigravity Quota Portal audit events"
  default     = "antigravity-quota-audit"
}

variable "model_pricing_config_path" {
  type        = string
  description = "Relative or absolute path to the externalized model pricing configuration JSON file"
  default     = "config/model_pricing.json"
}

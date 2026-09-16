variable "project_id" {
  type        = string
  description = "Google Cloud Project ID"
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
}

variable "disabled_group_email" {
  type        = string
  description = "Cloud Identity group email for throttled/locked developers"
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
}

variable "default_overage_usd" {
  type        = number
  description = "Default weekly developer overage buffer before auto-throttling in USD"
  default     = 2.00
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
  description = "Enable provisioning the full External Application Load Balancer + IAP stack. Requires iap_oauth_client_id and iap_oauth_client_secret."
  default     = false
}

variable "iap_allowed_members" {
  type        = list(string)
  description = "List of members (e.g. domain:example.com, user:foo@example.com) granted roles/iap.httpsResourceAccessor on the web backend service."
  default     = []
}

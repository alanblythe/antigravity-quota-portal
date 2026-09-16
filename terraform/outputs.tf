output "cloud_run_url" {
  description = "URL of the deployed Antigravity Quota Portal Cloud Run service"
  value       = google_cloud_run_v2_service.portal_service.uri
}

output "service_account_email" {
  description = "Dedicated Service Account email used by Cloud Run"
  value       = google_service_account.portal_sa.email
}

output "bigquery_dataset_id" {
  description = "BigQuery dataset ID for inference audit logs"
  value       = google_bigquery_dataset.audit_dataset.dataset_id
}

output "artifact_registry_repository" {
  description = "Artifact Registry Docker repository URL"
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker_repo.repository_id}"
}

output "cloud_logging_sink_writer_identity" {
  description = "Service Account identity used by Cloud Logging Log Router Sink"
  value       = google_logging_project_sink.antigravity_sink.writer_identity
}

output "lb_ip_address" {
  description = "Global Static IPv4 address reserved for the Load Balancer"
  value       = google_compute_global_address.lb_ip.address
}

output "lb_hostname" {
  description = "Public hostname derived via sslip.io or domain override"
  value       = local.lb_hostname
}

output "iap_oauth_redirect_uri" {
  description = "Authorized redirect URI to register in the Google Cloud Console OAuth 2.0 Web Client credentials"
  value       = var.iap_oauth_client_id != "" ? "https://iap.googleapis.com/v1/oauth/clientIds/${var.iap_oauth_client_id}:handleRedirect" : "https://iap.googleapis.com/v1/oauth/clientIds/<CLIENT_ID>:handleRedirect"
}

output "portal_url" {
  description = "Active portal URL (Load Balancer HTTPS URL when enable_lb is true, else Cloud Run direct URL)"
  value       = var.enable_lb ? "https://${local.lb_hostname}" : google_cloud_run_v2_service.portal_service.uri
}

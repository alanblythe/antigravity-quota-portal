# BigQuery Dataset for Antigravity Inference Logs
resource "google_bigquery_dataset" "audit_dataset" {
  dataset_id  = var.bigquery_dataset_name
  project     = var.project_id
  location    = var.region
  description = "Antigravity developer inference audit logs dataset"

  depends_on = [google_project_service.apis]
}

# Cloud Logging Log Router Sink for Antigravity Audit Events
resource "google_logging_project_sink" "antigravity_sink" {
  name                   = "antigravity-inference-log-sink-${var.project_id}"
  project                = var.project_id
  destination            = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.audit_dataset.dataset_id}"
  filter                 = "protoPayload.serviceName=\"businessaicode.googleapis.com\" OR jsonPayload.serviceName=\"businessaicode.googleapis.com\" OR logName:\"businessaicode\""
  unique_writer_identity = true

  depends_on = [google_project_service.apis, google_bigquery_dataset.audit_dataset]
}

# Grant BigQuery Data Editor to Log Router Sink Writer Identity
resource "google_bigquery_dataset_iam_member" "sink_writer_permission" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.audit_dataset.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.antigravity_sink.writer_identity
}

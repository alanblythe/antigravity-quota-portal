# Dedicated User-Managed Service Account for Cloud Run
resource "google_service_account" "portal_sa" {
  account_id   = "antigravity-quota-portal"
  display_name = "Antigravity Quota Portal Service Account"
  project      = var.project_id

  depends_on = [google_project_service.apis]
}

# 1. Project-level: BigQuery Job User (to run query jobs)
resource "google_project_iam_member" "bq_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = "serviceAccount:${google_service_account.portal_sa.email}"
}

# 2. Dataset-level: BigQuery Data Viewer (scoped only to the inference logs dataset)
resource "google_bigquery_dataset_iam_member" "bq_data_viewer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.audit_dataset.dataset_id
  role       = "roles/bigquery.dataViewer"
  member     = "serviceAccount:${google_service_account.portal_sa.email}"
}

# 3. Project-level: Firestore User (read/write application state and quotas)
resource "google_project_iam_member" "firestore_user" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.portal_sa.email}"
}

# 4. Project-level: Service Usage Consumer (required for API client quota tracking)
resource "google_project_iam_member" "service_usage_consumer" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageConsumer"
  member  = "serviceAccount:${google_service_account.portal_sa.email}"
}

# 5. Project-level: Logging Log Writer (required to emit structured audit logs to Cloud Logging)
resource "google_project_iam_member" "logging_log_writer" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.portal_sa.email}"
}

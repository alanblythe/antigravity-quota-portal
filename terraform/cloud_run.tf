# Artifact Registry Docker repository
resource "google_artifact_registry_repository" "docker_repo" {
  repository_id = "antigravity-portal"
  format        = "DOCKER"
  project       = var.project_id
  location      = var.region
  description   = "Docker repository for Antigravity Quota Portal container images"

  depends_on = [google_project_service.apis]
}

# Cloud Run v2 Service (Always-On min=1, max=1)
resource "google_cloud_run_v2_service" "portal_service" {
  name     = "antigravity-quota-portal"
  location = var.region
  project  = var.project_id

  ingress = var.enable_lb ? "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" : "INGRESS_TRAFFIC_INTERNAL_ONLY"

  template {
    service_account = google_service_account.portal_sa.email

    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    containers {
      image = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.docker_repo.repository_id}/app:${var.image_tag}"

      resources {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }

      env {
        name  = "PROJECT_ID"
        value = var.project_id
      }
      env {
        name  = "WORKLOAD_PROJECT_ID"
        value = length(var.monitored_project_ids) > 0 ? var.monitored_project_ids[0] : var.project_id
      }
      env {
        name  = "WORKLOAD_PROJECT_IDS"
        value = join(",", var.monitored_project_ids)
      }
      env {
        name  = "REGION"
        value = var.region
      }
      env {
        name  = "BIGQUERY_DATASET"
        value = google_bigquery_dataset.audit_dataset.dataset_id
      }
      env {
        name  = "ENABLED_GROUP_EMAIL"
        value = var.enabled_group_email
      }
      env {
        name  = "DISABLED_GROUP_EMAIL"
        value = var.disabled_group_email
      }
      env {
        name  = "APP_TIMEZONE"
        value = var.app_timezone
      }
      env {
        name  = "DEFAULT_QUOTA_USD"
        value = tostring(var.default_quota_usd)
      }
      env {
        name  = "DEFAULT_OVERAGE_USD"
        value = tostring(var.default_overage_usd)
      }
      env {
        name  = "USE_MOCK_SERVICES"
        value = "false"
      }
      env {
        name  = "ENABLE_GCP_AUDIT_LOGGING"
        value = tostring(var.enable_gcp_audit_logging)
      }
      env {
        name  = "GCP_AUDIT_LOG_NAME"
        value = var.gcp_audit_log_name
      }
      env {
        name  = "MODEL_PRICING_CONFIG_PATH"
        value = var.model_pricing_config_path
      }
      env {
        name  = "SCHEDULER_INTERVAL_HOURS"
        value = tostring(var.scheduler_interval_hours)
      }
      env {
        name  = "PRESET_QUOTAS"
        value = var.preset_quotas
      }
      env {
        name  = "CORS_ORIGINS"
        value = var.cors_origins
      }
      dynamic "env" {
        for_each = var.webhook_alert_url != null && trimspace(var.webhook_alert_url) != "" ? [var.webhook_alert_url] : []
        content {
          name  = "WEBHOOK_ALERT_URL"
          value = env.value
        }
      }

      startup_probe {
        http_get {
          path = "/api/health"
          port = 8080
        }
        initial_delay_seconds = 5
        period_seconds        = 10
        timeout_seconds       = 5
        failure_threshold     = 3
      }
    }
  }

  depends_on = [
    google_project_service.apis,
    google_firestore_database.database,
    google_bigquery_dataset.audit_dataset,
    google_project_iam_member.bq_job_user,
    google_bigquery_dataset_iam_member.bq_data_viewer,
    google_project_iam_member.firestore_user,
    google_project_iam_member.logging_log_writer,
    google_project_iam_member.monitoring_viewer,
    google_project_iam_member.workload_monitoring_viewer
  ]
}

# IAP Service Agent Identity
resource "google_project_service_identity" "iap_sa" {
  count    = var.enable_lb ? 1 : 0
  provider = google-beta
  project  = var.project_id
  service  = "iap.googleapis.com"

  depends_on = [google_project_service.apis]
}

# Grant Cloud Run Invoker to IAP Service Agent when IAP is enabled, or allow LB ingress when allow_unauthenticated_lb is explicitly set
resource "google_cloud_run_v2_service_iam_member" "iap_invoker" {
  count    = (local.iap_enabled || (var.enable_lb && var.allow_unauthenticated_lb)) ? 1 : 0
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.portal_service.name
  role     = "roles/run.invoker"
  member   = local.iap_enabled ? "serviceAccount:${google_project_service_identity.iap_sa[0].email}" : "allUsers"
}

terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

# Enable required Google Cloud APIs
resource "google_project_service" "apis" {
  for_each = toset([
    "run.googleapis.com",
    "firestore.googleapis.com",
    "bigquery.googleapis.com",
    "logging.googleapis.com",
    "artifactregistry.googleapis.com",
    "cloudidentity.googleapis.com",
    "iam.googleapis.com",
    "compute.googleapis.com",
    "iap.googleapis.com",
    "monitoring.googleapis.com",
  ])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

locals {
  spoke_project_ids = toset([for p in var.monitored_project_ids : trimspace(p) if trimspace(p) != "" && trimspace(p) != var.project_id])
  spoke_apis = {
    for pair in setproduct(local.spoke_project_ids, toset(["logging.googleapis.com", "monitoring.googleapis.com"])) :
    "${pair[0]}/${pair[1]}" => {
      project = pair[0]
      service = pair[1]
    }
  }
}

resource "google_project_service" "workload_apis" {
  for_each           = local.spoke_apis
  project            = each.value.project
  service            = each.value.service
  disable_on_destroy = false
}

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
  ])

  service            = each.key
  disable_on_destroy = false
}

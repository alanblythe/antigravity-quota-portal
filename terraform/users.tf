# Developer memberships in Antigravity Enabled Group are managed dynamically
# at runtime by the Antigravity Quota Portal (CloudIdentityGroupManager) via the
# Cloud Identity Groups API.
#
# When var.manage_group_manager_binding = true (default), Terraform automatically
# binds the portal runtime service account as MANAGER + MEMBER on both groups so
# it can query and reconcile developer memberships at runtime.

data "google_cloud_identity_group_lookup" "portal_groups" {
  provider = google.cloudidentity
  for_each = var.manage_group_manager_binding ? toset([var.enabled_group_email, var.disabled_group_email]) : toset([])

  group_key {
    id = each.value
  }

  depends_on = [google_project_service.apis]

  lifecycle {
    postcondition {
      condition     = self.name != null && self.name != ""
      error_message = "Cloud Identity group '${each.value}' was not found. Pre-create both groups via 'gcloud identity groups create' (or run './scripts/deploy_terraform.sh' with CREATE_MISSING_GROUPS=true) before applying Terraform."
    }
  }
}

resource "google_cloud_identity_group_membership" "portal_sa_group_manager" {
  provider = google.cloudidentity
  for_each = data.google_cloud_identity_group_lookup.portal_groups

  group                        = each.value.name
  create_ignore_already_exists = true

  preferred_member_key {
    id = google_service_account.portal_sa.email
  }

  roles {
    name = "MEMBER"
  }

  roles {
    name = "MANAGER"
  }
}


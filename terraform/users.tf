# Developer memberships in Antigravity Enabled Group

resource "google_cloud_identity_group_membership" "initial_developer_memberships" {
  for_each = var.manage_groups_in_terraform ? toset(var.initial_developer_emails) : []
  group    = google_cloud_identity_group.enabled_group[0].id

  preferred_member_key {
    id = each.value
  }

  roles {
    name = "MEMBER"
  }

  depends_on = [google_cloud_identity_group.enabled_group]
}

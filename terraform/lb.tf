# ==============================================================================
# External Application Load Balancer with Cloud Run Serverless NEG & IAP
# ==============================================================================

# 1. Global Static IPv4 Address for the Load Balancer
resource "google_compute_global_address" "lb_ip" {
  count      = var.enable_lb ? 1 : 0
  project    = var.project_id
  name       = "antigravity-portal-ip"
  ip_version = "IPV4"

  depends_on = [google_project_service.apis]
}

locals {
  # Whether Identity-Aware Proxy (IAP) is enabled on the backend service
  iap_enabled = var.enable_lb && trimspace(var.iap_oauth_client_id) != "" && nonsensitive(trimspace(var.iap_oauth_client_secret) != "")

  # If domain_override is set, use it.
  # Otherwise, when enable_lb is true, derive a sslip.io magic-DNS hostname from the reserved global static IP.
  lb_hostname = !var.enable_lb ? "" : (
    var.domain_override != "" ? var.domain_override : "${replace(google_compute_global_address.lb_ip[0].address, ".", "-")}.sslip.io"
  )
}

# 2. Serverless Network Endpoint Group (NEG) pointing to the Cloud Run service
resource "google_compute_region_network_endpoint_group" "serverless_neg" {
  count                 = var.enable_lb ? 1 : 0
  project               = var.project_id
  region                = var.region
  name                  = "antigravity-portal-neg"
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.portal_service.name
  }
}

# 3. Global Backend Service with Identity-Aware Proxy (IAP) enabled when OAuth credentials are provided
resource "google_compute_backend_service" "portal_backend" {
  count                 = var.enable_lb ? 1 : 0
  project               = var.project_id
  name                  = "antigravity-portal-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.serverless_neg[0].id
  }

  dynamic "iap" {
    for_each = local.iap_enabled ? [1] : []
    content {
      enabled              = true
      oauth2_client_id     = var.iap_oauth_client_id
      oauth2_client_secret = var.iap_oauth_client_secret
    }
  }

  log_config {
    enable      = true
    sample_rate = 1.0
  }

  lifecycle {
    precondition {
      condition = (
        (
          local.iap_enabled &&
          var.iap_oauth_client_id != "your-iap-client-id.apps.googleusercontent.com" &&
          nonsensitive(var.iap_oauth_client_secret != "your-iap-client-secret")
        ) || var.allow_unauthenticated_lb
      ) && (!local.iap_enabled || length(var.iap_allowed_members) > 0)
      error_message = "When enable_lb = true, you must provide non-placeholder iap_oauth_client_id, iap_oauth_client_secret, and at least one iap_allowed_members principal to protect the portal with Identity-Aware Proxy (IAP), or explicitly set allow_unauthenticated_lb = true."
    }
  }
}

# 4. IAP Web Backend Service IAM Members (roles/iap.httpsResourceAccessor)
resource "google_iap_web_backend_service_iam_member" "iap_members" {
  for_each = local.iap_enabled ? toset(var.iap_allowed_members) : toset([])

  project             = var.project_id
  web_backend_service = google_compute_backend_service.portal_backend[0].name
  role                = "roles/iap.httpsResourceAccessor"
  member              = each.value
}

# 5. Google-Managed SSL Certificate for sslip.io or custom domain
resource "google_compute_managed_ssl_certificate" "lb_cert" {
  count    = var.enable_lb ? 1 : 0
  project  = var.project_id
  provider = google-beta
  name     = "antigravity-portal-cert-${substr(sha1(local.lb_hostname), 0, 8)}"

  managed {
    domains = [local.lb_hostname]
  }

  lifecycle {
    create_before_destroy = true
  }
}

# 6. HTTPS URL Map routing all traffic to the backend service
resource "google_compute_url_map" "https_url_map" {
  count           = var.enable_lb ? 1 : 0
  project         = var.project_id
  name            = "antigravity-portal-url-map"
  default_service = google_compute_backend_service.portal_backend[0].id
}

# 7. HTTP URL Map redirecting all HTTP requests to HTTPS
resource "google_compute_url_map" "http_redirect_map" {
  count   = var.enable_lb ? 1 : 0
  project = var.project_id
  name    = "antigravity-portal-http-redirect"

  default_url_redirect {
    https_redirect         = true
    redirect_response_code = "MOVED_PERMANENTLY_DEFAULT"
    strip_query            = false
  }

  depends_on = [google_project_service.apis]
}

# 8. SSL Policy enforcing TLS 1.2+ with MODERN cipher suite
resource "google_compute_ssl_policy" "portal_ssl_policy" {
  count           = var.enable_lb ? 1 : 0
  project         = var.project_id
  name            = "antigravity-portal-ssl-policy"
  profile         = "MODERN"
  min_tls_version = "TLS_1_2"

  depends_on = [google_project_service.apis]
}

# 9. Target HTTPS Proxy
resource "google_compute_target_https_proxy" "https_proxy" {
  count            = var.enable_lb ? 1 : 0
  project          = var.project_id
  name             = "antigravity-portal-https-proxy"
  url_map          = google_compute_url_map.https_url_map[0].id
  ssl_certificates = [google_compute_managed_ssl_certificate.lb_cert[0].id]
  ssl_policy       = google_compute_ssl_policy.portal_ssl_policy[0].id
}

# 10. Target HTTP Proxy
resource "google_compute_target_http_proxy" "http_proxy" {
  count   = var.enable_lb ? 1 : 0
  project = var.project_id
  name    = "antigravity-portal-http-proxy"
  url_map = google_compute_url_map.http_redirect_map[0].id
}

# 11. Global HTTPS Forwarding Rule (Port 443)
resource "google_compute_global_forwarding_rule" "https_forwarding_rule" {
  count                 = var.enable_lb ? 1 : 0
  project               = var.project_id
  name                  = "antigravity-portal-https-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_https_proxy.https_proxy[0].id
  port_range            = "443"
  ip_address            = google_compute_global_address.lb_ip[0].address
}

# 12. Global HTTP Forwarding Rule (Port 80 -> HTTPS redirect)
resource "google_compute_global_forwarding_rule" "http_forwarding_rule" {
  count                 = var.enable_lb ? 1 : 0
  project               = var.project_id
  name                  = "antigravity-portal-http-fr"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  target                = google_compute_target_http_proxy.http_proxy[0].id
  port_range            = "80"
  ip_address            = google_compute_global_address.lb_ip[0].address
}

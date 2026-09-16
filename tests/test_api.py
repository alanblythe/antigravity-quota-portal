"""Integration tests for all REST API endpoints."""


def test_health_check(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "antigravity-quota-portal"


def test_list_users(client):
    response = client.get("/api/users")
    assert response.status_code == 200
    users = response.json()
    assert len(users) >= 5


def test_get_user_detail(client):
    response = client.get("/api/users/alice.chen@example.com")
    assert response.status_code == 200
    user = response.json()
    assert user["email"] == "alice.chen@example.com"
    assert "current_week" in user
    assert user["current_week"]["quota_credits_usd"] == 25.0


def test_update_user_quota(client):
    payload = {
        "has_custom_quota": True,
        "custom_quota_usd": 50.0,
        "custom_overage_usd": 10.0,
    }
    response = client.patch("/api/users/bob.martin@example.com", json=payload)
    assert response.status_code == 200
    updated = response.json()
    assert updated["has_custom_quota"] is True
    assert updated["custom_quota_usd"] == 50.0
    assert updated["current_week"]["quota_credits_usd"] == 50.0


def test_toggle_user_lock(client):
    response = client.post("/api/users/bob.martin@example.com/lock")
    assert response.status_code == 200
    user = response.json()
    assert user["status"] == "MANUALLY_DISABLED"

    # Unlock again
    response_unlock = client.post("/api/users/bob.martin@example.com/lock")
    assert response_unlock.status_code == 200
    user_unlocked = response_unlock.json()
    assert user_unlocked["status"] == "ACTIVE"


def test_get_and_update_config(client):
    response = client.get("/api/config")
    assert response.status_code == 200
    config = response.json()
    assert "default_quota_usd" in config

    update_payload = {"default_quota_usd": 15.0}
    response_put = client.put("/api/config", json=update_payload)
    assert response_put.status_code == 200
    assert response_put.json()["default_quota_usd"] == 15.0


def test_publish_and_sync(client):
    response = client.post("/api/evaluator/publish")
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["users_evaluated"] >= 5


def test_kpi_stats(client):
    response = client.get("/api/evaluator/kpis")
    assert response.status_code == 200
    kpis = response.json()
    assert "active_developers" in kpis
    assert "total_gross_usage_usd" in kpis
    assert "total_tokens_this_week" in kpis


def test_audit_log_endpoint(client):
    response = client.get("/api/audit?limit=10")
    assert response.status_code == 200
    events = response.json()
    assert isinstance(events, list)
    assert len(events) >= 1

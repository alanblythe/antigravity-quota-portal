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
    assert "weekly_team_budget_usd" in config

    update_payload = {
        "default_quota_usd": 15.0,
        "weekly_team_budget_usd": 250.0,
        "weekly_wtu_quota": 750000.0,
    }
    response_put = client.put("/api/config", json=update_payload)
    assert response_put.status_code == 200
    updated = response_put.json()
    assert updated["default_quota_usd"] == 15.0
    assert updated["weekly_team_budget_usd"] == 250.0
    assert updated["weekly_wtu_quota"] == 750000.0


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
    assert "week_reset_utc" in kpis
    assert "days_until_reset" in kpis
    assert "weekly_team_budget_usd" in kpis
    assert "weekly_budget_utilization_pct" in kpis
    assert kpis["wtu_pool_net_usage"] == 214500
    assert kpis["wtu_pool_limit"] is not None
    assert kpis["wtu_pool_utilization_pct"] is not None
    assert kpis["wtu_window_reset_utc"] is not None


def test_audit_log_endpoint(client):
    response = client.get("/api/audit?limit=10")
    assert response.status_code == 200
    events = response.json()
    assert isinstance(events, list)
    assert len(events) >= 1


def test_reset_pricing_matrix_endpoint(client):
    response = client.post("/api/config/pricing/reset")
    assert response.status_code == 200
    matrix = response.json()
    assert "gemini-3-pro" in matrix
    assert "gemini-3.8-flash" in matrix
    assert matrix["gemini-3-pro"]["input_price_per_million"] == 2.00
    assert matrix["gemini-3-pro"]["input_price_gt_threshold_per_million"] == 4.00


def test_patch_auto_disabled_user_exempt_unthrottles(client):
    """Toggling is_exempt=True on an AUTO_DISABLED user un-throttles them to ACTIVE."""
    # charlie.davis@example.com is pre-seeded as AUTO_DISABLED ($14.95 spend vs $12 hard limit)
    response = client.patch(
        "/api/users/charlie.davis@example.com",
        json={"is_exempt": True},
    )
    assert response.status_code == 200
    user = response.json()
    assert user["is_exempt"] is True
    assert user["status"] == "ACTIVE"

    # Revert exemption: should evaluate back to AUTO_DISABLED
    response_revert = client.patch(
        "/api/users/charlie.davis@example.com",
        json={"is_exempt": False},
    )
    assert response_revert.status_code == 200
    assert response_revert.json()["status"] == "AUTO_DISABLED"


def test_unlock_user_over_quota_transitions_to_auto_disabled(client):
    """Unlocking a MANUALLY_DISABLED user whose spend exceeds hard limit sets AUTO_DISABLED."""
    # Lock charlie.davis@example.com (spend $14.95 > $12.00 hard limit)
    lock_resp = client.post("/api/users/charlie.davis@example.com/lock")
    assert lock_resp.status_code == 200
    assert lock_resp.json()["status"] == "MANUALLY_DISABLED"

    # Unlock charlie.davis@example.com -> should become AUTO_DISABLED, not ACTIVE
    unlock_resp = client.post("/api/users/charlie.davis@example.com/lock")
    assert unlock_resp.status_code == 200
    assert unlock_resp.json()["status"] == "AUTO_DISABLED"


def test_reassign_user_project_id_syncs_wtu_window_without_false_reset(
    client, mock_db, mock_bq, mock_identity
):
    """Reassigning a user's project_id via PATCH /api/users/{email} syncs WTU window and avoids false weekly reset."""
    from app.api.deps import get_evaluator, get_wtu_client
    from app.bq.wtu_client import MockWTUClient
    from app.core.evaluator import QuotaEvaluator
    from app.main import app

    wtu_client = MockWTUClient(project_id="workload-project-plus")
    eval_with_wtu = QuotaEvaluator(
        db_client=mock_db,
        bq_client=mock_bq,
        identity_client=mock_identity,
        wtu_client=wtu_client,
    )
    app.dependency_overrides[get_wtu_client] = lambda: wtu_client
    app.dependency_overrides[get_evaluator] = lambda: eval_with_wtu

    config = mock_db.get_config()
    config.monitored_projects = ["workload-project-plus", "workload-project-standard"]
    mock_db.update_config(config)

    # Charlie is AUTO_DISABLED on workload-project-plus
    charlie = mock_db.get_user("charlie.davis@example.com")
    plus_wtu = wtu_client.fetch_wtu_status(project_id="workload-project-plus")
    charlie.project_id = "workload-project-plus"
    charlie.current_week.wtu_window_start_at = plus_wtu.active_cycle_start_utc
    charlie.current_week.wtu_window_reset_at = plus_wtu.active_cycle_reset_utc
    mock_db.save_user(charlie)

    # Reassign Charlie to workload-project-standard via PATCH
    patch_resp = client.patch(
        "/api/users/charlie.davis@example.com",
        json={"project_id": "workload-project-standard"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()
    assert patched["project_id"] == "workload-project-standard"
    assert patched["license_tier"] == "ENTERPRISE_STANDARD"

    std_wtu = wtu_client.fetch_wtu_status(project_id="workload-project-standard")
    updated_charlie = mock_db.get_user("charlie.davis@example.com")
    assert updated_charlie.current_week.wtu_window_start_at == std_wtu.active_cycle_start_utc
    assert updated_charlie.current_week.wtu_window_reset_at == std_wtu.active_cycle_reset_utc

    # Next evaluation run with empty usage must NOT trigger a false WEEKLY_MONDAY_RESET
    mock_bq.set_custom_records([])
    snaps_before = len(mock_db.list_weekly_snapshots("charlie.davis@example.com"))
    run_resp = client.post("/api/evaluator/run")
    assert run_resp.status_code == 200
    changes = run_resp.json()["changes"]
    charlie_reset_changes = [
        c for c in changes if c["user"] == "charlie.davis@example.com" and c["reason"] == "WEEKLY_MONDAY_RESET"
    ]
    assert charlie_reset_changes == []
    assert len(mock_db.list_weekly_snapshots("charlie.davis@example.com")) == snaps_before


def test_toggle_user_lock_returns_502_when_identity_reconcile_fails(client, mock_identity):
    """Verify POST /api/users/{email}/lock returns HTTP 502 if Cloud Identity group reconciliation fails."""
    from unittest.mock import MagicMock

    from app.api.deps import get_identity
    from app.main import app

    failing_identity = MagicMock(wraps=mock_identity)
    failing_identity.reconcile_user_group.return_value = False
    app.dependency_overrides[get_identity] = lambda: failing_identity

    resp = client.post("/api/users/alice.chen@example.com/lock")
    assert resp.status_code == 502
    assert "Failed to reconcile Cloud Identity group membership" in resp.json()["detail"]


def test_patch_user_manual_lock_or_unlock_returns_502_when_identity_fails(client, mock_db, mock_identity):
    """Verify PATCH /api/users/{email} returns HTTP 502 when manual lock/unlock group reconciliation fails."""
    from unittest.mock import MagicMock

    from app.api.deps import get_identity
    from app.db.models import UserStatus
    from app.main import app

    failing_identity = MagicMock(wraps=mock_identity)
    failing_identity.reconcile_user_group.return_value = False
    app.dependency_overrides[get_identity] = lambda: failing_identity

    # Attempting to manually lock via PATCH must return 502
    resp_lock = client.patch(
        "/api/users/alice.chen@example.com",
        json={"status": "MANUALLY_DISABLED"},
    )
    assert resp_lock.status_code == 502

    # Attempting to unlock a MANUALLY_DISABLED user via PATCH when reconcile fails must return 502
    alice = mock_db.get_user("alice.chen@example.com")
    alice.status = UserStatus.MANUALLY_DISABLED
    mock_db.save_user(alice)
    mock_identity.remove_member_from_group(mock_identity.enabled_group_email, "alice.chen@example.com")
    mock_identity.add_member_to_group(mock_identity.disabled_group_email, "alice.chen@example.com")

    resp_unlock = client.patch(
        "/api/users/alice.chen@example.com",
        json={"status": "ACTIVE"},
    )
    assert resp_unlock.status_code == 502


def test_patch_user_disable_custom_quota_clears_custom_values(client):
    """Toggling has_custom_quota=False clears custom_quota_usd and custom_overage_usd to None."""
    # Alice is pre-seeded with has_custom_quota=True, custom_quota_usd=25.0, custom_overage_usd=5.0
    resp = client.patch(
        "/api/users/alice.chen@example.com",
        json={"has_custom_quota": False},
    )
    assert resp.status_code == 200
    updated = resp.json()
    assert updated["has_custom_quota"] is False
    assert updated["custom_quota_usd"] is None
    assert updated["custom_overage_usd"] is None
    assert updated["current_week"]["quota_credits_usd"] == 10.0


def test_create_user_and_duplicate_returns_400(client, mock_identity):
    """Verify POST /api/users pre-provisions a new user (201) and rejects duplicates (400)."""
    payload = {
        "email": "frank.castle@example.com",
        "is_exempt": False,
        "has_custom_quota": True,
        "custom_quota_usd": 30.0,
        "custom_overage_usd": 5.0,
        "project_id": "workload-project-plus",
        "license_tier": "ENTERPRISE_PLUS",
    }
    resp = client.post("/api/users", json=payload)
    assert resp.status_code == 201
    created = resp.json()
    assert created["email"] == "frank.castle@example.com"
    assert created["status"] == "ACTIVE"
    assert created["has_custom_quota"] is True
    assert created["custom_quota_usd"] == 30.0
    assert created["current_week"]["quota_credits_usd"] == 30.0
    assert created["project_id"] == "workload-project-plus"
    assert created["license_tier"] == "ENTERPRISE_PLUS"
    assert mock_identity.is_user_in_enabled_group("frank.castle@example.com") is True

    # Duplicate POST must return 400
    dup_resp = client.post("/api/users", json=payload)
    assert dup_resp.status_code == 400
    assert "already exists" in dup_resp.json()["detail"]


def test_get_user_not_found_returns_404(client):
    """Verify GET /api/users/{email} returns 404 for unknown users."""
    resp = client.get("/api/users/nonexistent.user@example.com")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_list_users_query_filters(client, mock_db):
    """Verify GET /api/users query filters (search, status, project_id, exempt_only)."""
    # Filter by search
    search_resp = client.get("/api/users", params={"search": "alice.chen"})
    assert search_resp.status_code == 200
    search_users = search_resp.json()
    assert len(search_users) == 1
    assert search_users[0]["email"] == "alice.chen@example.com"

    # Filter by status
    status_resp = client.get("/api/users", params={"status": "MANUALLY_DISABLED"})
    assert status_resp.status_code == 200
    status_users = status_resp.json()
    assert len(status_users) >= 1
    assert all(u["status"] == "MANUALLY_DISABLED" for u in status_users)

    # Filter by exempt_only
    exempt_resp = client.get("/api/users", params={"exempt_only": True})
    assert exempt_resp.status_code == 200
    exempt_users = exempt_resp.json()
    assert len(exempt_users) >= 1
    assert all(u["is_exempt"] is True for u in exempt_users)

    # Filter by project_id
    alice = mock_db.get_user("alice.chen@example.com")
    alice.project_id = "custom-filter-project"
    mock_db.save_user(alice)
    proj_resp = client.get("/api/users", params={"project_id": "custom-filter-project"})
    assert proj_resp.status_code == 200
    proj_users = proj_resp.json()
    assert [u["email"] for u in proj_users] == ["alice.chen@example.com"]


def test_get_and_put_pricing_matrix(client):
    """Verify GET /api/config/pricing and PUT /api/config/pricing."""
    get_resp = client.get("/api/config/pricing")
    assert get_resp.status_code == 200
    matrix = get_resp.json()
    assert "gemini-3-pro" in matrix

    matrix["gemini-3-pro"]["input_price_per_million"] = 2.75
    put_resp = client.put("/api/config/pricing", json=matrix)
    assert put_resp.status_code == 200
    updated_matrix = put_resp.json()
    assert updated_matrix["gemini-3-pro"]["input_price_per_million"] == 2.75


def test_update_config_default_quota_immediately_updates_non_custom_users(client, mock_db):
    """Verify PUT /api/config updating default_quota_usd immediately updates non-custom users' ledger."""
    bob_before = mock_db.get_user("bob.martin@example.com")
    assert bob_before.has_custom_quota is False
    bob_gross = bob_before.current_week.gross_spend_usd

    alice_before = mock_db.get_user("alice.chen@example.com")
    assert alice_before.has_custom_quota is True
    alice_quota_before = alice_before.current_week.quota_credits_usd

    put_resp = client.put("/api/config", json={"default_quota_usd": 20.0, "default_overage_usd": 4.0})
    assert put_resp.status_code == 200

    bob_after = mock_db.get_user("bob.martin@example.com")
    assert bob_after.current_week.quota_credits_usd == 20.0
    assert bob_after.current_week.overage_buffer_usd == 4.0
    assert bob_after.current_week.remaining_credit_usd == round(max(0.0, 20.0 - bob_gross), 2)

    # Custom quota user (Alice) must remain unchanged
    alice_after = mock_db.get_user("alice.chen@example.com")
    assert alice_after.current_week.quota_credits_usd == alice_quota_before


def test_update_config_invalid_timezone_returns_422(client):
    """Verify PUT /api/config with an invalid timezone returns HTTP 422."""
    resp = client.put("/api/config", json={"timezone": "Invalid/Timezone_Name"})
    assert resp.status_code == 422
    assert "Invalid timezone" in resp.json()["detail"]


def test_audit_log_filtering_by_action_and_user(client):
    """Verify GET /api/audit?action=...&user=... filters audit events accurately."""
    # Trigger a user lock on bob.martin@example.com so a specific audit event exists
    lock_resp = client.post("/api/users/bob.martin@example.com/lock")
    assert lock_resp.status_code == 200

    all_events_resp = client.get("/api/audit", params={"user": "bob.martin@example.com"})
    assert all_events_resp.status_code == 200
    bob_events = all_events_resp.json()
    assert len(bob_events) >= 1
    assert all("bob.martin@example.com" in (e.get("target_user") or "").lower() for e in bob_events)

    target_action = bob_events[0]["action"]
    filtered_resp = client.get(
        "/api/audit",
        params={"action": target_action, "user": "bob.martin@example.com"},
    )
    assert filtered_resp.status_code == 200
    filtered_events = filtered_resp.json()
    assert len(filtered_events) >= 1
    assert all(
        e["action"].upper() == target_action.upper()
        and "bob.martin@example.com" in (e.get("target_user") or "").lower()
        for e in filtered_events
    )


def test_list_users_sorting(client):
    """Verify GET /api/users sorting by email ascending and descending."""
    asc_resp = client.get(
        "/api/users",
        params={"sort_by": "email", "sort_order": "asc", "sort_desc": "false"},
    )
    assert asc_resp.status_code == 200
    asc_emails = [u["email"] for u in asc_resp.json()]
    assert asc_emails == sorted(asc_emails)

    desc_resp = client.get(
        "/api/users",
        params={"sort_by": "email", "sort_order": "desc", "sort_desc": "true"},
    )
    assert desc_resp.status_code == 200
    desc_emails = [u["email"] for u in desc_resp.json()]
    assert desc_emails == sorted(desc_emails, reverse=True)


def test_evaluator_publish_and_run_return_502_on_failure(client):
    """Verify POST /api/evaluator/publish and POST /api/evaluator/run return HTTP 502 when evaluation fails."""
    from datetime import UTC, datetime
    from unittest.mock import MagicMock

    from app.api.deps import get_evaluator
    from app.db.models import EvaluationResult
    from app.main import app

    failing_evaluator = MagicMock()
    failing_evaluator.run_evaluation.return_value = EvaluationResult(
        success=False,
        evaluated_at=datetime.now(UTC),
        week_id="2026-W35",
        users_evaluated=0,
        group_swaps_count=0,
        error="BigQuery connection timeout",
    )
    app.dependency_overrides[get_evaluator] = lambda: failing_evaluator

    pub_resp = client.post("/api/evaluator/publish")
    assert pub_resp.status_code == 502
    assert "BigQuery connection timeout" in pub_resp.json()["detail"]

    run_resp = client.post("/api/evaluator/run")
    assert run_resp.status_code == 502
    assert "BigQuery connection timeout" in run_resp.json()["detail"]


def test_create_user_invalid_email_returns_422(client):
    """Verify POST /api/users with an invalid email returns HTTP 422."""
    resp = client.post("/api/users", json={"email": "invalid-email-without-at-sign"})
    assert resp.status_code == 422
    assert "Invalid email address" in resp.json()["detail"]


def test_put_pricing_matrix_preserves_gt_200k_tokens_and_reconciles_state(
    client, mock_db, mock_identity
):
    """Verify PUT /api/config/pricing preserves >200k context-tier token counts and re-evaluates user status."""
    from app.db.models import ModelTokenUsage, UserStatus

    bob = mock_db.get_user("bob.martin@example.com")
    bob.has_custom_quota = True
    bob.custom_quota_usd = 10.0
    bob.custom_overage_usd = 2.0
    bob.status = UserStatus.ACTIVE
    bob.current_week.tokens_by_model = {"gemini-3-pro": 1_000_000}
    bob.current_week.token_details_by_model = {
        "gemini-3-pro": ModelTokenUsage(
            total_tokens=1_000_000,
            prompt_tokens=1_000_000,
            candidates_tokens=0,
            cached_tokens=0,
            thoughts_tokens=0,
            prompt_tokens_gt_200k=1_000_000,
            candidates_tokens_gt_200k=0,
            cached_tokens_gt_200k=0,
            thoughts_tokens_gt_200k=0,
            fallback_tokens=0,
        )
    }
    mock_db.save_user(bob)
    mock_identity.reconcile_user_group("bob.martin@example.com", "ENABLED")

    matrix = client.get("/api/config/pricing").json()
    # Increase >200k input price from $4.00/M to $15.00/M -> 1M gt_200k prompt tokens = $15.00 (> $12.00 hard limit)
    matrix["gemini-3-pro"]["input_price_gt_threshold_per_million"] = 15.00

    put_resp = client.put("/api/config/pricing", json=matrix)
    assert put_resp.status_code == 200

    bob_after = mock_db.get_user("bob.martin@example.com")
    assert bob_after.current_week.gross_spend_usd == 15.00
    assert (
        bob_after.current_week.token_details_by_model["gemini-3-pro"].prompt_tokens_gt_200k
        == 1_000_000
    )
    assert bob_after.status == UserStatus.AUTO_DISABLED
    assert mock_identity.is_user_in_disabled_group("bob.martin@example.com") is True
    assert mock_identity.is_user_in_enabled_group("bob.martin@example.com") is False


def test_patch_user_auto_disabled_status_returns_422(client):
    """Verify PATCH /api/users/{email} with status='AUTO_DISABLED' returns HTTP 422."""
    resp = client.patch(
        "/api/users/alice.chen@example.com",
        json={"status": "AUTO_DISABLED"},
    )
    assert resp.status_code == 422
    assert "Cannot manually set status to AUTO_DISABLED" in resp.json()["detail"]


def test_update_config_invalid_or_identical_groups_returns_422(client):
    """Verify PUT /api/config with identical enabled/disabled groups or missing '@' returns HTTP 422."""
    dup_resp = client.put(
        "/api/config",
        json={
            "enabled_group": "antigravity-same@example.com",
            "disabled_group": "antigravity-same@example.com",
        },
    )
    assert dup_resp.status_code == 422
    assert "distinct Cloud Identity groups" in dup_resp.json()["detail"]

    missing_at_resp = client.put(
        "/api/config",
        json={"enabled_group": "invalid-group-without-at"},
    )
    assert missing_at_resp.status_code == 422
    assert "valid email addresses containing '@'" in missing_at_resp.json()["detail"]





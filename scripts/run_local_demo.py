#!/usr/bin/env python3
"""Run Antigravity Quota Portal locally with 3 demo users and mock usage."""

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Ensure mock services mode and sanitized demo defaults are forced regardless of local .env
os.environ["USE_MOCK_SERVICES"] = "true"
os.environ["PROJECT_ID"] = "your-portal-project"
os.environ["WORKLOAD_PROJECT_ID"] = "your-workload-project"
os.environ["WORKLOAD_PROJECT_IDS"] = "your-workload-project,your-workload-project-std"
os.environ["ENABLED_GROUP_EMAIL"] = "antigravity-enabled@example.com"
os.environ["DISABLED_GROUP_EMAIL"] = "antigravity-disabled@example.com"
os.environ["DEFAULT_QUOTA_USD"] = "10.00"
os.environ["DEFAULT_OVERAGE_USD"] = "2.00"

import uvicorn

from app.api.deps import get_bq, get_db, get_identity, get_wtu_client
from app.bq.bq_client import UserUsageRecord
from app.config import settings
from app.core.timezone_engine import get_current_week_window
from app.db.models import (
    AuditEvent,
    CurrentWeekUsage,
    ModelTokenUsage,
    SpendBreakdown,
    User,
    UserStatus,
)
from app.main import app


def seed_three_demo_users():
    """Seed exactly 3 users demonstrating each quota state."""
    db = get_db()
    bq = get_bq()
    identity = get_identity()
    wtu_client = get_wtu_client()

    # Clear existing state
    with db._lock:
        db._users.clear()
        db._audit_events.clear()
    identity._enabled_members.clear()
    identity._disabled_members.clear()

    now = datetime.now(UTC)
    config = db.get_config()
    config.enabled_group = settings.ENABLED_GROUP_EMAIL
    config.disabled_group = settings.DISABLED_GROUP_EMAIL
    if len(config.monitored_projects) < 2:
        config.monitored_projects = ["your-workload-project", "your-workload-project-std"]
    db.update_config(config)

    week_id, week_start_utc, _, _ = get_current_week_window(config.timezone, now)
    wtu_map = wtu_client.fetch_multi_project_wtu_status(["your-workload-project", "your-workload-project-std"])
    wtu_start_plus = wtu_map["your-workload-project"].active_cycle_start_utc or week_start_utc
    wtu_reset_plus = wtu_map["your-workload-project"].active_cycle_reset_utc or (now + timedelta(days=4, hours=6))
    wtu_start_std = wtu_map["your-workload-project-std"].active_cycle_start_utc or week_start_utc
    wtu_reset_std = wtu_map["your-workload-project-std"].active_cycle_reset_utc or (now + timedelta(days=2, hours=14))

    # 1. Alice - Active, custom quota ($4.20 / $25.00, 16.8% util)
    user_alice = User(
        email="alice.chen@example.com",
        project_id="your-workload-project",
        license_tier="ENTERPRISE_PLUS",
        status=UserStatus.ACTIVE,
        is_exempt=False,
        has_custom_quota=True,
        custom_quota_usd=25.00,
        custom_overage_usd=5.00,
        current_week=CurrentWeekUsage(
            week_id=week_id,
            total_tokens=2500000,
            total_requests=180,
            wtu_net_usage=120,
            wtu_window_start_at=wtu_start_plus,
            wtu_window_reset_at=wtu_reset_plus,
            gross_spend_usd=4.20,
            quota_credits_usd=25.00,
            remaining_credit_usd=20.80,
            net_billable_cost_usd=0.00,
            overage_buffer_usd=5.00,
            spend_breakdown=SpendBreakdown(
                input_spend_usd=2.12,
                output_spend_usd=1.95,
                cached_spend_usd=0.13,
                input_tokens=1410000,
                output_tokens=190000,
                cached_tokens=900000,
            ),
            tokens_by_model={"gemini-3-pro": 1500000, "gemini-3.8-flash": 1000000},
            token_details_by_model={
                "gemini-3-pro": ModelTokenUsage(
                    total_tokens=1500000,
                    prompt_tokens=1350000,
                    candidates_tokens=150000,
                    cached_tokens=500000,
                    thoughts_tokens=0,
                ),
                "gemini-3.8-flash": ModelTokenUsage(
                    total_tokens=1000000,
                    prompt_tokens=960000,
                    candidates_tokens=40000,
                    cached_tokens=400000,
                    thoughts_tokens=0,
                ),
            },
            spend_by_model={"gemini-3-pro": 3.60, "gemini-3.8-flash": 0.60},
            credit_utilization_percentage=16.8,
            last_active=now - timedelta(minutes=12),
        ),
        created_at=now - timedelta(days=30),
        updated_at=now - timedelta(minutes=12),
    )

    # 2. Bob - Active, warning threshold ($8.55 / $10.00, 85.5% util)
    user_bob = User(
        email="bob.martin@example.com",
        project_id="your-workload-project",
        license_tier="ENTERPRISE_PLUS",
        status=UserStatus.ACTIVE,
        is_exempt=False,
        has_custom_quota=False,
        current_week=CurrentWeekUsage(
            week_id=week_id,
            total_tokens=3800000,
            total_requests=290,
            wtu_net_usage=260,
            wtu_window_start_at=wtu_start_plus,
            wtu_window_reset_at=wtu_reset_plus,
            gross_spend_usd=8.55,
            quota_credits_usd=10.00,
            remaining_credit_usd=1.45,
            net_billable_cost_usd=0.00,
            overage_buffer_usd=2.00,
            spend_breakdown=SpendBreakdown(
                input_spend_usd=3.85,
                output_spend_usd=4.63,
                cached_spend_usd=0.07,
                input_tokens=2670000,
                output_tokens=530000,
                cached_tokens=600000,
            ),
            tokens_by_model={"gemini-3-pro": 2000000, "gemini-3.8-flash": 1800000},
            token_details_by_model={
                "gemini-3-pro": ModelTokenUsage(
                    total_tokens=2000000,
                    prompt_tokens=1680000,
                    candidates_tokens=320000,
                    cached_tokens=200000,
                    thoughts_tokens=0,
                ),
                "gemini-3.8-flash": ModelTokenUsage(
                    total_tokens=1800000,
                    prompt_tokens=1590000,
                    candidates_tokens=210000,
                    cached_tokens=400000,
                    thoughts_tokens=0,
                ),
            },
            spend_by_model={"gemini-3-pro": 6.84, "gemini-3.8-flash": 1.71},
            credit_utilization_percentage=85.5,
            last_active=now - timedelta(minutes=3),
        ),
        created_at=now - timedelta(days=20),
        updated_at=now - timedelta(minutes=3),
    )

    # 3. Charlie - Auto-throttled ($13.40 / $10.00 + $2.00 buffer breached)
    user_charlie = User(
        email="charlie.davis@example.com",
        project_id="your-workload-project-std",
        license_tier="ENTERPRISE_STANDARD",
        status=UserStatus.AUTO_DISABLED,
        is_exempt=False,
        has_custom_quota=False,
        current_week=CurrentWeekUsage(
            week_id=week_id,
            total_tokens=6800000,
            total_requests=520,
            wtu_net_usage=480,
            wtu_window_start_at=wtu_start_std,
            wtu_window_reset_at=wtu_reset_std,
            gross_spend_usd=13.40,
            quota_credits_usd=10.00,
            remaining_credit_usd=0.00,
            net_billable_cost_usd=3.40,
            overage_buffer_usd=2.00,
            spend_breakdown=SpendBreakdown(
                input_spend_usd=7.78,
                output_spend_usd=5.20,
                cached_spend_usd=0.43,
                input_tokens=4150000,
                output_tokens=450000,
                cached_tokens=2200000,
            ),
            tokens_by_model={"gemini-3-pro": 5800000, "gemini-2.5-pro": 1000000},
            token_details_by_model={
                "gemini-3-pro": ModelTokenUsage(
                    total_tokens=5800000,
                    prompt_tokens=5450000,
                    candidates_tokens=350000,
                    cached_tokens=2000000,
                    thoughts_tokens=0,
                ),
                "gemini-2.5-pro": ModelTokenUsage(
                    total_tokens=1000000,
                    prompt_tokens=900000,
                    candidates_tokens=100000,
                    cached_tokens=200000,
                    thoughts_tokens=0,
                ),
            },
            spend_by_model={"gemini-3-pro": 11.50, "gemini-2.5-pro": 1.90},
            credit_utilization_percentage=134.0,
            last_active=now - timedelta(hours=1),
        ),
        created_at=now - timedelta(days=15),
        updated_at=now - timedelta(hours=1),
    )

    # Save users
    db.save_user(user_alice)
    db.save_user(user_bob)
    db.save_user(user_charlie)

    # Reconcile mock Cloud Identity group memberships
    identity.reconcile_user_group(
        user_alice.email, "ENABLED", enabled_group=config.enabled_group, disabled_group=config.disabled_group
    )
    identity.reconcile_user_group(
        user_bob.email, "ENABLED", enabled_group=config.enabled_group, disabled_group=config.disabled_group
    )
    identity.reconcile_user_group(
        user_charlie.email, "DISABLED", enabled_group=config.enabled_group, disabled_group=config.disabled_group
    )

    # Configure mock BigQuery so Publish & Sync preserves these 3 users and exact spend math
    bq.set_custom_records(
        [
            UserUsageRecord(
                user_email="alice.chen@example.com",
                project_id="your-workload-project",
                model_name="gemini-3-pro",
                request_count=100,
                token_count=1500000,
                prompt_tokens=1350000,
                candidates_tokens=150000,
                cached_tokens=500000,
                last_active=now - timedelta(minutes=12),
            ),
            UserUsageRecord(
                user_email="alice.chen@example.com",
                project_id="your-workload-project",
                model_name="gemini-3.8-flash",
                request_count=80,
                token_count=1000000,
                prompt_tokens=960000,
                candidates_tokens=40000,
                cached_tokens=400000,
                last_active=now - timedelta(minutes=12),
            ),
            UserUsageRecord(
                user_email="bob.martin@example.com",
                project_id="your-workload-project",
                model_name="gemini-3-pro",
                request_count=210,
                token_count=2000000,
                prompt_tokens=1680000,
                candidates_tokens=320000,
                cached_tokens=200000,
                last_active=now - timedelta(minutes=3),
            ),
            UserUsageRecord(
                user_email="bob.martin@example.com",
                project_id="your-workload-project",
                model_name="gemini-3.8-flash",
                request_count=80,
                token_count=1800000,
                prompt_tokens=1590000,
                candidates_tokens=210000,
                cached_tokens=400000,
                last_active=now - timedelta(minutes=3),
            ),
            UserUsageRecord(
                user_email="charlie.davis@example.com",
                project_id="your-workload-project-std",
                model_name="gemini-3-pro",
                request_count=380,
                token_count=5800000,
                prompt_tokens=5450000,
                candidates_tokens=350000,
                cached_tokens=2000000,
                last_active=now - timedelta(hours=1),
            ),
            UserUsageRecord(
                user_email="charlie.davis@example.com",
                project_id="your-workload-project-std",
                model_name="gemini-2.5-pro",
                request_count=140,
                token_count=1000000,
                prompt_tokens=900000,
                candidates_tokens=100000,
                cached_tokens=200000,
                last_active=now - timedelta(hours=1),
            ),
        ]
    )

    # Seed initial audit events for demo audit trail viewer
    db.add_audit_event(
        AuditEvent(
            event_id="evt-seed-demo-1",
            timestamp=now - timedelta(hours=2, minutes=15),
            action="QUOTA_UPDATE",
            triggered_by="admin_ui",
            target_user="alice.chen@example.com",
            details={
                "custom_quota_usd": 25.00,
                "custom_overage_usd": 5.00,
                "reason": "Admin assigned $25.00 custom weekly quota",
            },
        )
    )
    db.add_audit_event(
        AuditEvent(
            event_id="evt-seed-demo-2",
            timestamp=now - timedelta(hours=1, minutes=4),
            action="GROUP_SWAP",
            triggered_by="HOURLY_APS_CRON",
            target_user="charlie.davis@example.com",
            details={
                "direction": "DISABLED",
                "from_group": "antigravity-enabled@example.com",
                "to_group": "antigravity-disabled@example.com",
                "current_spend_usd": 13.40,
                "quota_usd": 10.00,
                "overage_limit_usd": 2.00,
                "reason": "HARD_LIMIT_BREACHED: Spend $13.40 >= Limit $12.00 ($10 quota + $2 buffer)",
            },
        )
    )
    db.add_audit_event(
        AuditEvent(
            event_id="evt-seed-demo-3",
            timestamp=now - timedelta(minutes=22),
            action="PUBLISH_TRIGGERED",
            triggered_by="admin_publish_button",
            target_user=None,
            details={
                "users_evaluated": 3,
                "group_swaps": 1,
                "status": "SUCCESS",
            },
        )
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run Antigravity Quota Portal locally with 3 demo users and mock usage."
    )
    parser.parse_args()

    port = int(os.environ.get("PORT", "8080"))
    host = os.environ.get("HOST", "0.0.0.0")

    seed_three_demo_users()

    print("\n" + "=" * 72)
    print("  🚀 Antigravity Quota Portal - Local Sandbox (3 Demo Users)")
    print("=" * 72)
    print(f"  Portal Dashboard:  http://localhost:{port}")
    print(f"  Interactive API:   http://localhost:{port}/docs")
    print(f"  Health Endpoint:   http://localhost:{port}/api/health")
    print("-" * 72)
    print("  Seeded 3 Demo Users:")
    print("   1. alice.chen@example.com    - ACTIVE        (Spend $4.20 / $25.00,  16.8% util - Custom Quota)")
    print("   2. bob.martin@example.com    - ACTIVE        (Spend $8.55 / $10.00,  85.5% util - Warning)")
    print("   3. charlie.davis@example.com - AUTO_DISABLED (Spend $13.40 / $10.00, 134% util  - Throttled)")
    print("=" * 72 + "\n")

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()

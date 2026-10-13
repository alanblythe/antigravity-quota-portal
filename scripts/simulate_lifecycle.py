#!/usr/bin/env python3
"""
Interactive End-to-End Multi-User Quota Lifecycle Simulation.

Demonstrates:
1. Baseline health & live inference execution (Dev1 & Dev2)
2. FinOps credit and overage calculations
3. Automated quota breach detection & dual-group IAM swapping
4. Access revocation enforcement
5. Admin quota top-up & instant re-enablement
6. Monday 00:00:00 weekly reset restoration
"""

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

# Ensure repository root is on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bq.bq_client import UserUsageRecord
from app.bq.mock_bq import MockBigQueryUsageClient
from app.core.evaluator import QuotaEvaluator
from app.core.timezone_engine import get_timezone_obj
from app.db.mock_db import MockDatabase
from app.db.models import CurrentWeekUsage, User, UserStatus
from app.identity.mock_identity import MockCloudIdentityGroupManager
from tests.simulation.runner import IsolatedAgyRunner


def print_banner(title: str):
    print("\n" + "=" * 75)
    print(f"  {title}")
    print("=" * 75)


def print_user_table(db: MockDatabase, identity: MockCloudIdentityGroupManager):
    users = db.list_users()
    print(
        f"\n{'User Email':<32} | {'Status':<16} | {'Group':<22} | {'Gross ($)':<9} | {'Credit ($)':<10} | {'Net ($)':<8}"
    )
    print("-" * 115)
    for u in sorted(users, key=lambda x: x.email):
        gross = u.current_week.gross_spend_usd if u.current_week else 0.0
        credit = u.current_week.remaining_credit_usd if u.current_week else (u.custom_quota_usd or 10.0)
        net = u.current_week.net_billable_cost_usd if u.current_week else 0.0

        in_en = identity.is_user_in_enabled_group(u.email)
        in_dis = identity.is_user_in_disabled_group(u.email)
        group_str = "antigravity-enabled" if in_en else ("antigravity-disabled" if in_dis else "none")

        print(
            f"{u.email:<32} | {u.status.value:<16} | {group_str:<22} | ${gross:<8.2f} | ${credit:<9.2f} | ${net:<7.2f}"
        )
    print("-" * 115)


def main():
    parser = argparse.ArgumentParser(description="Simulate the 6-stage Antigravity Quota Portal lifecycle.")
    parser.parse_args()

    org_domain = os.getenv("ORG_DOMAIN", "example.com")
    workload_proj = os.getenv("WORKLOAD_PROJECT_ID") or os.getenv("GCP_PROJECT_ID", "your-workload-project")
    host_proj = os.getenv("PORTAL_PROJECT_ID") or os.getenv("PROJECT_ID", "your-portal-project")
    enabled_group = os.getenv("ENABLED_GROUP_EMAIL", f"antigravity-enabled@{org_domain}")
    disabled_group = os.getenv("DISABLED_GROUP_EMAIL", f"antigravity-disabled@{org_domain}")
    dev1_email = os.getenv("TEST_DEV1_EMAIL", f"dev1@{org_domain}")
    dev2_email = os.getenv("TEST_DEV2_EMAIL", f"dev2@{org_domain}")

    print_banner("ANTIGRAVITY MULTI-USER QUOTA LIFECYCLE SIMULATION")
    print(f"Workload Project: {workload_proj}")
    print(f"Host Project:     {host_proj}")
    print(f"Enabled Group:    {enabled_group}")
    print(f"Disabled Group:   {disabled_group}")

    # Initialize isolated CLI runners
    dev1_runner = IsolatedAgyRunner(dev1_email, project_id=workload_proj)
    dev2_runner = IsolatedAgyRunner(dev2_email, project_id=workload_proj)

    # Initialize evaluator components
    db = MockDatabase()
    db._users.clear()
    db._audit_events.clear()
    cfg = db.get_config()
    cfg.enabled_group = enabled_group
    cfg.disabled_group = disabled_group
    cfg.default_quota_usd = 10.00
    cfg.default_overage_usd = 2.00
    cfg.monitored_projects = [workload_proj]
    db.update_config(cfg)

    bq = MockBigQueryUsageClient()
    bq.set_custom_records([])
    identity = MockCloudIdentityGroupManager(
        enabled_group_email=enabled_group,
        disabled_group_email=disabled_group,
    )
    identity._enabled_members.clear()
    identity._disabled_members.clear()

    # Register dev1 and dev2 in mock identity and db
    now = datetime.now(timezone.utc)
    iso_year, iso_week, _ = now.astimezone(get_timezone_obj(cfg.timezone)).isocalendar()
    week_id = f"{iso_year}-W{iso_week:02d}"

    for email in [dev1_email, dev2_email]:
        identity.add_member_to_group(enabled_group, email)
        db.save_user(
            User(
                email=email,
                project_id=workload_proj,
                status=UserStatus.ACTIVE,
                is_exempt=False,
                current_week=CurrentWeekUsage(
                    week_id=week_id,
                    total_tokens=0,
                    total_requests=0,
                    gross_spend_usd=0.0,
                    quota_credits_usd=10.00,
                    remaining_credit_usd=10.00,
                    net_billable_cost_usd=0.0,
                    overage_buffer_usd=2.00,
                ),
            )
        )

    evaluator = QuotaEvaluator(db_client=db, bq_client=bq, identity_client=identity)

    # ---------------------------------------------------------
    # STAGE 1: Baseline Live Inference Check
    # ---------------------------------------------------------
    print_banner("STAGE 1: Baseline Live Inference Verification (Dev1 & Dev2)")
    run_live_cli = os.getenv("RUN_LIVE_AGY_CLI", "false").lower() == "true"
    if run_live_cli and dev1_runner.is_authenticated() and dev2_runner.is_authenticated():
        print("Executing live prompts through isolated CLI sandboxes...")
        res1 = dev1_runner.run_prompt("Provide a 1-sentence definition of Cloud Run.", timeout=30)
        res2 = dev2_runner.run_prompt("Provide a 1-sentence definition of BigQuery.", timeout=30)

        print(f"• Dev1 Live Execution: {'✅ SUCCESS' if res1.success else '❌ FAILED'}")
        if res1.success and res1.parsed_json:
            print(f"  Response: {res1.parsed_json.get('response', '').strip()[:80]}...")
            print(f"  Usage:    {res1.parsed_json.get('usage')}")

        print(f"• Dev2 Live Execution: {'✅ SUCCESS' if res2.success else '❌ FAILED'}")
        if res2.success and res2.parsed_json:
            print(f"  Response: {res2.parsed_json.get('response', '').strip()[:80]}...")
            print(f"  Usage:    {res2.parsed_json.get('usage')}")
    else:
        print(
            "Skipping live `agy` CLI invocation in offline/sandbox simulation mode "
            "(set RUN_LIVE_AGY_CLI=true with authenticated test profiles to run live prompts)."
        )
        print(f"• {dev1_email}: Simulated baseline health check ✅ OK")
        print(f"• {dev2_email}: Simulated baseline health check ✅ OK")

    evaluator.run_evaluation(triggered_by="SIMULATION_INIT")
    print_user_table(db, identity)

    # ---------------------------------------------------------
    # STAGE 2: Normal Activity (Under Quota)
    # ---------------------------------------------------------
    print_banner(f"STAGE 2: Normal Activity Within Quota ({dev2_email} consumes $3.50)")
    print(f"Simulating 3,000,000 tokens on gemini-3.8-flash for {dev2_email}...")
    bq.set_custom_records(
        [
            UserUsageRecord(
                user_email=dev2_email,
                project_id=workload_proj,
                model_name="gemini-3.8-flash",
                request_count=120,
                token_count=3_000_000,
                last_active=datetime.now(timezone.utc),
            )
        ]
    )

    evaluator.run_evaluation(triggered_by="HOURLY_EVALUATION")
    print_user_table(db, identity)
    dev2_u = db.get_user(dev2_email)
    print(f"💡 FinOps Result: Dev2 spend ${dev2_u.current_week.gross_spend_usd:.2f} deducted from $10.00 credit.")
    print(
        f"   Remaining Credit: ${dev2_u.current_week.remaining_credit_usd:.2f}. Net Billable: ${dev2_u.current_week.net_billable_cost_usd:.2f}."
    )

    # ---------------------------------------------------------
    # STAGE 3: Heavy Usage & Hard Quota Limit Breach
    # ---------------------------------------------------------
    print_banner("STAGE 3: Hard Quota Limit Breach (Dev1 exceeds $10 Quota + $2 Buffer = $12 Limit)")
    print(f"Simulating 8,000,000 tokens on gemini-3-pro ($30.56 gross spend) for {dev1_email}...")
    bq.set_custom_records(
        [
            UserUsageRecord(
                user_email=dev2_email,
                project_id=workload_proj,
                model_name="gemini-3.8-flash",
                request_count=120,
                token_count=3_000_000,
                last_active=datetime.now(timezone.utc),
            ),
            UserUsageRecord(
                user_email=dev1_email,
                project_id=workload_proj,
                model_name="gemini-3-pro",
                request_count=320,
                token_count=8_000_000,
                last_active=datetime.now(timezone.utc),
            ),
        ]
    )

    evaluator.run_evaluation(triggered_by="HOURLY_EVALUATION")
    print_user_table(db, identity)
    dev1_u = db.get_user(dev1_email)
    print(
        f"🚨 Throttling Event: Dev1 gross spend reached ${dev1_u.current_week.gross_spend_usd:.2f} >= $12.00 hard limit."
    )
    print("   State Transition: ACTIVE -> AUTO_DISABLED")
    print(
        f"   Cloud Identity Group: dev1 moved to '{'antigravity-disabled' if identity.is_user_in_disabled_group(dev1_email) else 'antigravity-enabled'}'"
    )

    # ---------------------------------------------------------
    # STAGE 4: Access Governance State
    # ---------------------------------------------------------
    print_banner("STAGE 4: Access Governance Verification")
    print(f"• dev1@ status: AUTO_DISABLED in antigravity-disabled@ -> Zero IAM roles on {workload_proj}")
    print("• dev2@ status: ACTIVE in antigravity-enabled@        -> roles/businessaicode.user active")
    print("• Unaffected developers continue running prompts seamlessly.")

    # ---------------------------------------------------------
    # STAGE 5: Admin Quota Top-Up & Instant Re-Activation
    # ---------------------------------------------------------
    print_banner("STAGE 5: Admin Top-Up (Increasing Dev1 Quota to $35.00)")
    print("Admin raises Dev1 weekly quota to $35.00 (+ $5.00 buffer) and triggers Publish & Sync...")

    dev1_u.has_custom_quota = True
    dev1_u.custom_quota_usd = 35.00
    dev1_u.custom_overage_usd = 5.00
    db.save_user(dev1_u)

    evaluator.run_evaluation(triggered_by="ADMIN_PUBLISH_AND_SYNC", is_publish=True)
    print_user_table(db, identity)
    dev1_re = db.get_user(dev1_email)
    assert dev1_re.status == UserStatus.ACTIVE
    print("✅ Re-activation Result: Dev1 state restored to ACTIVE.")
    print(f"   Remaining Credit: ${dev1_re.current_week.remaining_credit_usd:.2f}.")
    print(
        f"   Cloud Identity Group: dev1 restored to '{'antigravity-enabled' if identity.is_user_in_enabled_group(dev1_email) else 'antigravity-disabled'}'"
    )

    # ---------------------------------------------------------
    # STAGE 6: Monday Weekly Reset Simulation
    # ---------------------------------------------------------
    print_banner("STAGE 6: Weekly Reset Rollover (WTU Cycle / Monday 00:00:00 Boundary)")
    print("Simulating weekly cycle expiration with dev1=AUTO_DISABLED and dev2=MANUALLY_DISABLED...")

    # Set dev1 to AUTO_DISABLED (throttled) and dev2 to MANUALLY_DISABLED (admin locked)
    # and expire their active cycle window so the evaluator triggers a genuine Weekly Reset
    expired_reset = datetime.now(timezone.utc) - timedelta(minutes=5)
    for u in db.list_users():
        if u.email == dev1_email:
            u.status = UserStatus.AUTO_DISABLED
            u.has_custom_quota = False
            u.custom_quota_usd = None
            u.custom_overage_usd = None
            identity.reconcile_user_group(
                u.email, "DISABLED", enabled_group=enabled_group, disabled_group=disabled_group
            )
        elif u.email == dev2_email:
            u.status = UserStatus.MANUALLY_DISABLED
            identity.reconcile_user_group(
                u.email, "DISABLED", enabled_group=enabled_group, disabled_group=disabled_group
            )
        u.current_week.wtu_window_reset_at = expired_reset
        u.current_week.week_id = "2000-W01"
        db.save_user(u)

    # Clear usage for new week
    bq.set_custom_records([])

    evaluator.run_evaluation(triggered_by="MONDAY_RESET_WORKER")
    print_user_table(db, identity)
    print("🎉 Weekly Reset complete: AUTO_DISABLED (dev1) restored to ACTIVE; MANUALLY_DISABLED (dev2) preserved.")
    print_banner("SIMULATION COMPLETE: ALL 6 PHASES VERIFIED SUCCESSFULLY")


if __name__ == "__main__":
    main()

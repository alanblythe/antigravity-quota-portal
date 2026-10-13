#!/usr/bin/env python3
"""
Repeatable Gemini Enterprise (GE) License Quota Exhaustion Test.

This script:
1. Validates isolated session credentials for target (dev1) and control (dev2) users.
2. Updates the target user in Firestore with an elevated quota or exemption so the
   Antigravity Quota Portal does not throttle them prematurely.
3. Executes a progressive prompt load loop using `IsolatedAgyRunner` against the workload project.
4. Tracks cumulative token consumption (input, output, thinking) and estimated spend.
5. Detects when Google's Gemini Enterprise backend rejects requests (e.g., RESOURCE_EXHAUSTED / 429 / quota exceeded).
6. Verifies that the control user (dev2) remains completely healthy and unaffected.
"""

import argparse
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from app.bq.bq_client import BigQueryUsageClient
from app.bq.wtu_client import CloudMonitoringWTUClient
from app.core.cost_model import (
    calculate_blended_rate_per_million,
    calculate_model_spend,
    match_model_pricing,
)
from app.core.evaluator import QuotaEvaluator
from app.core.timezone_engine import get_current_week_window
from app.db.firestore_client import FirestoreDatabase
from app.db.models import CurrentWeekUsage, User, UserStatus, load_default_model_pricing
from app.identity.cloud_identity import CloudIdentityGroupManager
from tests.simulation.runner import IsolatedAgyRunner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ge_quota_test")


def print_banner(title: str, char: str = "="):
    line = char * 80
    print(f"\n{line}\n  {title}\n{line}")


def is_quota_exhaustion_error(error_text: str) -> bool:
    """Check if an error string matches Google Cloud / GE quota exhaustion signatures."""
    if not error_text:
        return False
    patterns = [
        r"resource_exhausted",
        r"quota_exceeded",
        r"quota exceeded",
        r"rate_limit_exceeded",
        r"exhausted.*quota",
        r"credit.*exhausted",
        r"exceeded.*limit",
        r"429",
    ]
    text_lower = error_text.lower()
    return any(re.search(pat, text_lower) for pat in patterns)


def elevate_user_in_firestore(
    db: FirestoreDatabase,
    email: str,
    custom_quota_usd: float = 250.0,
    custom_overage_usd: float = 50.0,
    make_exempt: bool = True,
    workload_project: str | None = None,
) -> User:
    """Set custom quota or exemption for target user and sync current_week fields for dashboard visibility."""
    cfg = db.get_config()
    user = db.get_user(email)
    now = datetime.now(timezone.utc)
    if not user:
        logger.info(f"User {email} not found in Firestore. Creating new user record.")
        week_id, _, _, _ = get_current_week_window(cfg.timezone, now)
        user = User(
            email=email,
            project_id=workload_project,
            status=UserStatus.ACTIVE,
            is_exempt=make_exempt,
            has_custom_quota=True,
            custom_quota_usd=custom_quota_usd,
            custom_overage_usd=custom_overage_usd,
            current_week=CurrentWeekUsage(
                week_id=week_id,
                quota_credits_usd=custom_quota_usd,
                remaining_credit_usd=custom_quota_usd,
                overage_buffer_usd=custom_overage_usd,
            ),
            created_at=now,
            updated_at=now,
        )
    else:
        if workload_project:
            user.project_id = workload_project
        user.is_exempt = make_exempt
        user.has_custom_quota = True
        user.custom_quota_usd = custom_quota_usd
        user.custom_overage_usd = custom_overage_usd
        user.status = UserStatus.ACTIVE
        user.updated_at = now

    user.current_week.quota_credits_usd = custom_quota_usd
    user.current_week.overage_buffer_usd = custom_overage_usd
    user.current_week.remaining_credit_usd = max(0.0, round(custom_quota_usd - user.current_week.gross_spend_usd, 2))
    user.current_week.net_billable_cost_usd = max(0.0, round(user.current_week.gross_spend_usd - custom_quota_usd, 2))
    if custom_quota_usd > 0:
        user.current_week.credit_utilization_percentage = round(
            (user.current_week.gross_spend_usd / custom_quota_usd) * 100.0, 2
        )

    db.save_user(user)
    try:
        identity = CloudIdentityGroupManager(
            enabled_group_email=cfg.enabled_group,
            disabled_group_email=cfg.disabled_group,
            quota_project_id=db.project_id,
        )
        identity.reconcile_user_group(
            user.email,
            "ENABLED",
            enabled_group=cfg.enabled_group,
            disabled_group=cfg.disabled_group,
        )
    except Exception as e:
        logger.warning(f"Failed to reconcile Cloud Identity group for {user.email}: {e}")
    return user


def reset_user_in_firestore(
    db: FirestoreDatabase,
    email: str,
) -> User | None:
    """Reset target user back to default non-exempt baseline in Firestore."""
    user = db.get_user(email)
    if user:
        cfg = db.get_config()
        now = datetime.now(timezone.utc)
        week_id, _, _, _ = get_current_week_window(cfg.timezone, now)
        user.current_week.week_id = week_id
        user.is_exempt = False
        user.has_custom_quota = False
        user.custom_quota_usd = None
        user.custom_overage_usd = None
        user.current_week.quota_credits_usd = cfg.default_quota_usd
        user.current_week.overage_buffer_usd = cfg.default_overage_usd
        user.current_week.remaining_credit_usd = max(
            0.0, round(cfg.default_quota_usd - user.current_week.gross_spend_usd, 2)
        )
        user.current_week.net_billable_cost_usd = max(
            0.0, round(user.current_week.gross_spend_usd - cfg.default_quota_usd, 2)
        )
        if cfg.default_quota_usd > 0:
            user.current_week.credit_utilization_percentage = round(
                (user.current_week.gross_spend_usd / cfg.default_quota_usd) * 100.0, 2
            )
        hard_limit = cfg.default_quota_usd + cfg.default_overage_usd
        if user.status != UserStatus.MANUALLY_DISABLED:
            user.status = (
                UserStatus.AUTO_DISABLED if user.current_week.gross_spend_usd >= hard_limit else UserStatus.ACTIVE
            )
        user.updated_at = now
        db.save_user(user)
        try:
            identity = CloudIdentityGroupManager(
                enabled_group_email=cfg.enabled_group,
                disabled_group_email=cfg.disabled_group,
                quota_project_id=db.project_id,
            )
            target_dir = "ENABLED" if user.status == UserStatus.ACTIVE else "DISABLED"
            identity.reconcile_user_group(
                user.email,
                target_dir,
                enabled_group=cfg.enabled_group,
                disabled_group=cfg.disabled_group,
            )
        except Exception as e:
            logger.warning(f"Failed to reconcile Cloud Identity group for {user.email}: {e}")
    return user


def run_test(args: argparse.Namespace):
    print_banner("GEMINI ENTERPRISE (GE) QUOTA EXHAUSTION TEST RUNNER")
    print(f"Target User:        {args.target_user}")
    print(f"Control User:       {args.control_user}")
    print(f"Workload Project:   {args.workload_project}")
    print(f"Host Project:       {args.host_project}")
    print(f"Selected Model:     {args.model}")
    print(f"Max Requests:       {args.max_requests}")
    print(f"Batch Delay:        {args.batch_delay}s")
    print(f"Elevated Quota:     ${args.elevated_quota:.2f} (Exempt: {args.make_exempt})")
    print(f"Stop on Exhaustion: {args.stop_on_exhaustion}")

    # Initialize isolated runners
    target_runner = IsolatedAgyRunner(args.target_user, project_id=args.workload_project)
    control_runner = IsolatedAgyRunner(args.control_user, project_id=args.workload_project)

    # -------------------------------------------------------------
    # Phase 1: Pre-Flight Health Checks
    # -------------------------------------------------------------
    if not args.skip_preflight:
        print_banner("PHASE 1: Pre-Flight Health & Credential Verification", "-")
        if not target_runner.is_authenticated():
            logger.error(f"Target user {args.target_user} is not authenticated! Run ./scripts/auth-dev1.sh first.")
            sys.exit(1)
        if not control_runner.is_authenticated():
            logger.error(f"Control user {args.control_user} is not authenticated! Run ./scripts/auth-dev2.sh first.")
            sys.exit(1)

        logger.info(f"Sending baseline verification prompt for target user: {args.target_user}...")
        res_target = target_runner.run_prompt("Respond with OK.", model="gemini-3.8-flash", timeout=30)
        if not res_target.success:
            logger.error(
                f"Target user baseline check failed! Exit code: {res_target.exit_code}, stderr: {res_target.stderr}"
            )
            sys.exit(1)
        dur_target = res_target.parsed_json.get("duration_seconds", 0) if res_target.parsed_json else 0
        logger.info(f"Target user baseline check PASSED ({dur_target:.2f}s).")

        logger.info(f"Sending baseline verification prompt for control user: {args.control_user}...")
        res_ctrl = control_runner.run_prompt("Respond with OK.", model="gemini-3.8-flash", timeout=30)
        if not res_ctrl.success:
            logger.error(
                f"Control user baseline check failed! Exit code: {res_ctrl.exit_code}, stderr: {res_ctrl.stderr}"
            )
            sys.exit(1)
        dur_ctrl = res_ctrl.parsed_json.get("duration_seconds", 0) if res_ctrl.parsed_json else 0
        logger.info(f"Control user baseline check PASSED ({dur_ctrl:.2f}s).")

    # -------------------------------------------------------------
    # Phase 2: Configure Target User Quota in Firestore
    # -------------------------------------------------------------
    db = FirestoreDatabase(project_id=args.host_project)
    if args.test_portal_throttle:
        print_banner("PHASE 2: Configuring Low Quota for Portal Throttle Test", "-")
        throttle_quota = args.throttle_quota
        throttle_overage = args.throttle_overage
        updated_user = elevate_user_in_firestore(
            db=db,
            email=args.target_user,
            custom_quota_usd=throttle_quota,
            custom_overage_usd=throttle_overage,
            make_exempt=False,
            workload_project=args.workload_project,
        )
        print(f"User {updated_user.email} configured for Portal Throttling:")
        print(f"  • Status:           {updated_user.status.value}")
        print(f"  • Custom Quota:     ${updated_user.custom_quota_usd:.2f}")
        print(
            f"  • Overage Buffer:   ${updated_user.custom_overage_usd:.2f} (Hard Limit: ${throttle_quota + throttle_overage:.2f})"
        )
        print(
            f"  • Is Exempt:        {updated_user.is_exempt} (Portal WILL throttle when gross spend >= ${throttle_quota + throttle_overage:.2f})"
        )
    else:
        print_banner("PHASE 2: Configuring Elevated Quota in Firestore", "-")
        updated_user = elevate_user_in_firestore(
            db=db,
            email=args.target_user,
            custom_quota_usd=args.elevated_quota,
            custom_overage_usd=args.elevated_overage,
            make_exempt=args.make_exempt,
            workload_project=args.workload_project,
        )
        print(f"User {updated_user.email} updated in Firestore:")
        print(f"  • Status:           {updated_user.status.value}")
        print(f"  • Custom Quota:     ${updated_user.custom_quota_usd:.2f}")
        print(f"  • Overage Buffer:   ${updated_user.custom_overage_usd:.2f}")
        print(f"  • Is Exempt:        {updated_user.is_exempt} (Protected from portal throttling)")

    if args.dry_run:
        print("\nDry-run complete. Exiting before traffic loop.")
        return

    # -------------------------------------------------------------
    # Phase 3: Progressive Load Execution on Target User
    # -------------------------------------------------------------
    pricing_map = load_default_model_pricing()
    matched_pricing = match_model_pricing(args.model, pricing_map)
    blended_rate = calculate_blended_rate_per_million(matched_pricing)
    threshold_tokens = matched_pricing.context_tier_threshold_tokens or 200_000

    print_banner(f"PHASE 3: Driving Traffic via {args.target_user}", "-")
    print(f"Model: {args.model} ({matched_pricing.display_name}) | Workload Project: {args.workload_project}")
    print(
        f"{'Req #':<6} | {'Status':<10} | {'Duration':<10} | {'Input Tok':<10} | {'Out Tok':<8} | {'Total Tok':<10} | {'Cumul Tok':<10}"
    )
    print("-" * 80)

    # Prompt designed to burn tokens (requires thinking + code generation)
    test_prompt = (
        "Write a comprehensive Python module implementing an asynchronous publish-subscribe "
        "event broker with thread-safe queue buffering, wildcard topic matching, dead-letter "
        "replays, and unit tests."
    )

    cumulative_tokens = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_thinking_tokens = 0
    total_cached_tokens = 0
    prompt_gt_200k = 0
    candidates_gt_200k = 0
    cached_gt_200k = 0
    thoughts_gt_200k = 0
    tokens_le_threshold = 0
    tokens_gt_threshold = 0
    exact_token_spend_usd = 0.0
    exhaustion_detected = False
    exhaustion_error_detail = ""
    successful_requests = 0
    failed_requests = 0

    try:
        for i in range(1, args.max_requests + 1):
            res = target_runner.run_prompt(test_prompt, model=args.model, timeout=90)

            if res.success and res.parsed_json:
                usage = res.parsed_json.get("usage", {})
                inp = usage.get("input_tokens", 0)
                out = usage.get("output_tokens", 0)
                thk = usage.get("thinking_tokens", 0)
                cached = usage.get("cache_read_tokens", 0)
                tot = usage.get("total_tokens", inp + out + thk)
                dur = res.parsed_json.get("duration_seconds", 0.0)

                cumulative_tokens += tot
                total_input_tokens += inp
                total_output_tokens += out
                total_thinking_tokens += thk
                total_cached_tokens += cached
                successful_requests += 1

                is_gt_tier = matched_pricing.context_tier_threshold_tokens is not None and inp > threshold_tokens
                if is_gt_tier:
                    tokens_gt_threshold += tot
                    prompt_gt_200k += inp
                    candidates_gt_200k += out
                    cached_gt_200k += cached
                    thoughts_gt_200k += thk
                    p_in = (
                        matched_pricing.input_price_gt_threshold_per_million or matched_pricing.input_price_per_million
                    )
                    p_out = (
                        matched_pricing.output_price_gt_threshold_per_million
                        or matched_pricing.output_price_per_million
                    )
                    p_cache = matched_pricing.cached_price_gt_threshold_per_million
                    if p_cache is None:
                        p_cache = matched_pricing.cached_price_per_million
                else:
                    tokens_le_threshold += tot
                    p_in = matched_pricing.input_price_per_million
                    p_out = matched_pricing.output_price_per_million
                    p_cache = matched_pricing.cached_price_per_million

                non_cached_inp = max(0, inp - cached)
                exact_token_spend_usd += (
                    (non_cached_inp / 1_000_000.0) * p_in
                    + ((out + thk) / 1_000_000.0) * p_out
                    + (cached / 1_000_000.0) * p_cache
                )

                print(
                    f"{i:<6} | {'SUCCESS':<10} | {dur:>7.2f}s  | {inp:>9} | {out:>7} | {tot:>9} | {cumulative_tokens:>9}"
                )

            else:
                failed_requests += 1
                raw_err = ""
                if res.parsed_json and res.parsed_json.get("error"):
                    raw_err = res.parsed_json.get("error")
                elif res.stderr:
                    raw_err = res.stderr
                elif res.stdout:
                    raw_err = res.stdout

                is_exhaust = is_quota_exhaustion_error(raw_err)
                status_str = "EXHAUSTED" if is_exhaust else "ERROR"
                print(
                    f"{i:<6} | {status_str:<10} | {'-':>8}   | {'-':>9} | {'-':>7} | {'-':>9} | {cumulative_tokens:>9}"
                )
                print(f"  ❌ Error on request {i}: {raw_err.strip()[:200]}")

                if is_exhaust:
                    exhaustion_detected = True
                    exhaustion_error_detail = raw_err.strip()
                    logger.warning(f"🚨 Google GE Quota Exhaustion Detected on request #{i}!")
                    if args.stop_on_exhaustion:
                        break

            if i < args.max_requests:
                time.sleep(args.batch_delay)

    except KeyboardInterrupt:
        print("\n\nExecution interrupted by user.")

    # -------------------------------------------------------------
    # Phase 4: Control Subject Verification (Dev2)
    # -------------------------------------------------------------
    print_banner(f"PHASE 4: Control Subject Health Verification ({args.control_user})", "-")
    logger.info(f"Executing post-test verification on control user {args.control_user}...")
    res_ctrl_post = control_runner.run_prompt(
        "Provide a 1-sentence verification of BigQuery.", model="gemini-3.8-flash", timeout=30
    )
    control_healthy = res_ctrl_post.success
    if control_healthy:
        dur = res_ctrl_post.parsed_json.get("duration_seconds", 0) if res_ctrl_post.parsed_json else 0
        print(f"✅ Control user {args.control_user} is HEALTHY and executing normally ({dur:.2f}s).")
    else:
        print(f"❌ Control user {args.control_user} encountered an issue: {res_ctrl_post.stderr[:200]}")

    # -------------------------------------------------------------
    # Phase 4b (Optional): Portal Quota Evaluation & Group Swap Verification
    # -------------------------------------------------------------
    if args.test_portal_throttle:
        print_banner("PHASE 4b: Portal Quota Evaluation & Cloud Identity Group Swap", "-")
        config = db.get_config()
        bq_dataset = os.getenv("BIGQUERY_DATASET", "antigravity_inference_logs")
        bq_client = BigQueryUsageClient(
            project_id=args.host_project,
            dataset_name=bq_dataset,
        )
        identity_client = CloudIdentityGroupManager(
            enabled_group_email=config.enabled_group,
            disabled_group_email=config.disabled_group,
            quota_project_id=args.host_project,
        )
        wtu_client = CloudMonitoringWTUClient(
            project_id=args.workload_project,
            quota_project_id=args.host_project,
        )
        evaluator = QuotaEvaluator(
            db_client=db,
            bq_client=bq_client,
            identity_client=identity_client,
            wtu_client=wtu_client,
        )
        eval_res = evaluator.run_evaluation(triggered_by="test_ge_quota_exhaustion", is_publish=True)
        post_user = db.get_user(args.target_user)
        in_enabled = args.target_user.lower() in identity_client.get_group_members(config.enabled_group)
        in_disabled = args.target_user.lower() in identity_client.get_group_members(config.disabled_group)
        print(f"• Evaluation Success:   {eval_res.success} (Group swaps: {eval_res.group_swaps_count})")
        if post_user:
            print(f"• Post-Eval Status:     {post_user.status.value}")
            print(f"• Post-Eval Gross Spend:${post_user.current_week.gross_spend_usd:.2f}")
        print(f"• In ENABLED Group:     {in_enabled} ({config.enabled_group})")
        print(f"• In DISABLED Group:    {in_disabled} ({config.disabled_group})")

    # -------------------------------------------------------------
    # Phase 5: Final Summary Report
    # -------------------------------------------------------------
    portal_cost_model_spend_usd, _ = calculate_model_spend(
        token_count=cumulative_tokens,
        pricing=matched_pricing,
        prompt_tokens=total_input_tokens,
        candidates_tokens=total_output_tokens,
        cached_tokens=total_cached_tokens,
        thoughts_tokens=total_thinking_tokens,
        prompt_tokens_gt_200k=prompt_gt_200k,
        candidates_tokens_gt_200k=candidates_gt_200k,
        cached_tokens_gt_200k=cached_gt_200k,
        thoughts_tokens_gt_200k=thoughts_gt_200k,
        fallback_tokens=0,
    )
    blended_fallback_spend_usd, _ = calculate_model_spend(
        token_count=cumulative_tokens,
        pricing=matched_pricing,
    )

    print_banner("TEST EXECUTION SUMMARY")
    print(f"• Target User:              {args.target_user}")
    print(f"• Pricing Profile:          {matched_pricing.display_name} (config/model_pricing.json)")
    print(f"• Total Requests Attempted: {successful_requests + failed_requests}")
    print(f"• Successful Inferences:    {successful_requests}")
    print(f"• Failed Inferences:        {failed_requests}")
    print(f"• Cumulative Tokens Burned: {cumulative_tokens:,}")
    print(f"    - Input Tokens:         {total_input_tokens:,}")
    print(f"    - Output Tokens:        {total_output_tokens:,}")
    print(f"    - Thinking Tokens:      {total_thinking_tokens:,}")
    print(f"    - Cached Read Tokens:   {total_cached_tokens:,}")
    print(
        f"• Exact Token-Level Spend:  ${exact_token_spend_usd:.4f} USD (in=${matched_pricing.input_price_per_million:.2f}/M, out=${matched_pricing.output_price_per_million:.2f}/M)"
    )
    print(f"• Portal Cost Model Spend:  ${portal_cost_model_spend_usd:.4f} USD (granular token telemetry)")
    print(f"• Blended Fallback Spend:   ${blended_fallback_spend_usd:.4f} USD (@ ${blended_rate:.3f}/M blended)")
    print(f"• Backend Quota Exhausted:  {'🚨 YES' if exhaustion_detected else 'NO (Quota ceiling not reached)'}")
    if exhaustion_detected:
        print(f"  Error Snippet:            {exhaustion_error_detail[:300]}")
    print(f"• Control User Functional:  {'✅ YES' if control_healthy else '❌ NO'}")

    if args.reset_after:
        print_banner("Post-Test Cleanup: Resetting Target User in Firestore", "-")
        reset_user_in_firestore(db, args.target_user)
        print(f"Target user {args.target_user} reset to non-exempt baseline.")

    print_banner("TEST RUN COMPLETE")


def parse_args() -> argparse.Namespace:
    org_domain = os.getenv("ORG_DOMAIN", "example.com")
    default_dev1 = os.getenv("TEST_DEV1_EMAIL", f"dev1@{org_domain}")
    default_dev2 = os.getenv("TEST_DEV2_EMAIL", f"dev2@{org_domain}")
    default_workload_proj = os.getenv("WORKLOAD_PROJECT_ID") or os.getenv("GCP_PROJECT_ID", "your-workload-project")
    default_host_proj = os.getenv("PORTAL_PROJECT_ID") or os.getenv("PROJECT_ID", "your-portal-project")

    parser = argparse.ArgumentParser(description="Repeatable Gemini Enterprise (GE) License Quota Exhaustion Test")
    parser.add_argument(
        "--target-user",
        default=default_dev1,
        help="Email of the target developer to load-test and exhaust quota.",
    )
    parser.add_argument(
        "--control-user",
        default=default_dev2,
        help="Email of the control developer to verify isolation.",
    )
    parser.add_argument(
        "--model",
        default="gemini-3.8-flash",
        help="Model identifier (default: gemini-3.8-flash).",
    )
    parser.add_argument(
        "--workload-project",
        default=default_workload_proj,
        help="GCP workload project where inference requests execute.",
    )
    parser.add_argument(
        "--host-project",
        default=default_host_proj,
        help="GCP host project housing Firestore and BigQuery dataset.",
    )
    parser.add_argument(
        "--elevated-quota",
        type=float,
        default=250.00,
        help="Elevated custom quota in USD set for target user in Firestore.",
    )
    parser.add_argument(
        "--elevated-overage",
        type=float,
        default=50.00,
        help="Elevated custom overage buffer in USD.",
    )
    parser.add_argument(
        "--make-exempt",
        action="store_true",
        default=True,
        help="Set is_exempt=True on target user in Firestore (default: True).",
    )
    parser.add_argument(
        "--no-exempt",
        dest="make_exempt",
        action="store_false",
        help="Do not mark target user as exempt (rely only on elevated custom quota).",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=30,
        help="Maximum prompt requests to execute before stopping.",
    )
    parser.add_argument(
        "--batch-delay",
        type=float,
        default=2.0,
        help="Delay in seconds between requests to avoid transient rate limits.",
    )
    parser.add_argument(
        "--stop-on-exhaustion",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Halt prompt execution immediately upon detecting quota exhaustion.",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        default=False,
        help="Skip pre-flight authentication verification checks.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Run pre-flight check and Firestore update without driving traffic.",
    )
    parser.add_argument(
        "--reset-after",
        action="store_true",
        default=False,
        help="Reset target user in Firestore back to default baseline after test.",
    )
    parser.add_argument(
        "--test-portal-throttle",
        action="store_true",
        default=False,
        help="Configure a low non-exempt quota ($0.01 quota + $0.00 overage) and run QuotaEvaluator to verify AUTO_DISABLED group swap.",
    )
    parser.add_argument(
        "--throttle-quota",
        type=float,
        default=0.01,
        help="Custom quota in USD when --test-portal-throttle is enabled (default: 0.01).",
    )
    parser.add_argument(
        "--throttle-overage",
        type=float,
        default=0.00,
        help="Custom overage buffer in USD when --test-portal-throttle is enabled (default: 0.00).",
    )
    return parser.parse_args()


if __name__ == "__main__":
    run_test(parse_args())

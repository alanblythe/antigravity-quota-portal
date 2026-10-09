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
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from dotenv import load_dotenv

# Load local .env if present
load_dotenv()

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tests.simulation.runner import IsolatedAgyRunner, AgyRunResult
from app.db.firestore_client import FirestoreDatabase
from app.db.models import User, UserStatus

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
) -> User:
    """Set custom quota or exemption for target user so portal evaluator never throttles them."""
    user = db.get_user(email)
    now = datetime.now(timezone.utc)
    if not user:
        logger.info(f"User {email} not found in Firestore. Creating new user record.")
        user = User(
            email=email,
            status=UserStatus.ACTIVE,
            is_exempt=make_exempt,
            has_custom_quota=True,
            custom_quota_usd=custom_quota_usd,
            custom_overage_usd=custom_overage_usd,
            created_at=now,
            updated_at=now,
        )
    else:
        user.is_exempt = make_exempt
        user.has_custom_quota = True
        user.custom_quota_usd = custom_quota_usd
        user.custom_overage_usd = custom_overage_usd
        user.status = UserStatus.ACTIVE
        user.updated_at = now

    db.save_user(user)
    return user


def reset_user_in_firestore(
    db: FirestoreDatabase,
    email: str,
) -> User:
    """Reset target user back to default non-exempt baseline in Firestore."""
    user = db.get_user(email)
    if user:
        user.is_exempt = False
        user.has_custom_quota = False
        user.custom_quota_usd = None
        user.custom_overage_usd = None
        user.updated_at = datetime.now(timezone.utc)
        db.save_user(user)
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
        res_target = target_runner.run_prompt("Respond with OK.", model="gemini-3.7-flash-medium", timeout=30)
        if not res_target.success:
            logger.error(f"Target user baseline check failed! Exit code: {res_target.exit_code}, stderr: {res_target.stderr}")
            sys.exit(1)
        dur_target = res_target.parsed_json.get("duration_seconds", 0) if res_target.parsed_json else 0
        logger.info(f"Target user baseline check PASSED ({dur_target:.2f}s).")

        logger.info(f"Sending baseline verification prompt for control user: {args.control_user}...")
        res_ctrl = control_runner.run_prompt("Respond with OK.", model="gemini-3.7-flash-medium", timeout=30)
        if not res_ctrl.success:
            logger.error(f"Control user baseline check failed! Exit code: {res_ctrl.exit_code}, stderr: {res_ctrl.stderr}")
            sys.exit(1)
        dur_ctrl = res_ctrl.parsed_json.get("duration_seconds", 0) if res_ctrl.parsed_json else 0
        logger.info(f"Control user baseline check PASSED ({dur_ctrl:.2f}s).")

    # -------------------------------------------------------------
    # Phase 2: Elevate Target User Quota in Firestore
    # -------------------------------------------------------------
    print_banner("PHASE 2: Configuring Elevated Quota in Firestore", "-")
    db = FirestoreDatabase(project_id=args.host_project)
    updated_user = elevate_user_in_firestore(
        db=db,
        email=args.target_user,
        custom_quota_usd=args.elevated_quota,
        custom_overage_usd=args.elevated_overage,
        make_exempt=args.make_exempt,
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
    print_banner(f"PHASE 3: Driving Traffic via {args.target_user}", "-")
    print(f"Model: {args.model} | Workload Project: {args.workload_project}")
    print(f"{'Req #':<6} | {'Status':<10} | {'Duration':<10} | {'Input Tok':<10} | {'Out Tok':<8} | {'Total Tok':<10} | {'Cumul Tok':<10}")
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
                tot = usage.get("total_tokens", inp + out)
                dur = res.parsed_json.get("duration_seconds", 0.0)

                cumulative_tokens += tot
                total_input_tokens += inp
                total_output_tokens += out
                total_thinking_tokens += thk
                successful_requests += 1

                print(f"{i:<6} | {'SUCCESS':<10} | {dur:>7.2f}s  | {inp:>9} | {out:>7} | {tot:>9} | {cumulative_tokens:>9}")

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
                print(f"{i:<6} | {status_str:<10} | {'-':>8}   | {'-':>9} | {'-':>7} | {'-':>9} | {cumulative_tokens:>9}")
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
    res_ctrl_post = control_runner.run_prompt("Provide a 1-sentence verification of BigQuery.", model="gemini-3.7-flash-medium", timeout=30)
    control_healthy = res_ctrl_post.success
    if control_healthy:
        dur = res_ctrl_post.parsed_json.get("duration_seconds", 0) if res_ctrl_post.parsed_json else 0
        print(f"✅ Control user {args.control_user} is HEALTHY and executing normally ({dur:.2f}s).")
    else:
        print(f"❌ Control user {args.control_user} encountered an issue: {res_ctrl_post.stderr[:200]}")

    # -------------------------------------------------------------
    # Phase 5: Final Summary Report
    # -------------------------------------------------------------
    rate_per_million = 3.50 if "pro" in args.model.lower() else 0.40
    estimated_spend_usd = (cumulative_tokens / 1_000_000.0) * rate_per_million

    print_banner("TEST EXECUTION SUMMARY")
    print(f"• Target User:              {args.target_user}")
    print(f"• Total Requests Attempted: {successful_requests + failed_requests}")
    print(f"• Successful Inferences:    {successful_requests}")
    print(f"• Failed Inferences:        {failed_requests}")
    print(f"• Cumulative Tokens Burned: {cumulative_tokens:,}")
    print(f"    - Input Tokens:         {total_input_tokens:,}")
    print(f"    - Output Tokens:        {total_output_tokens:,}")
    print(f"    - Thinking Tokens:      {total_thinking_tokens:,}")
    print(f"• Estimated Gross Value:    ${estimated_spend_usd:.4f} USD (@ ${rate_per_million:.2f}/M tokens)")
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
    default_workload_proj = os.getenv("WORKLOAD_PROJECT_ID", os.getenv("GCP_PROJECT_ID", "my-workload-project"))
    default_host_proj = os.getenv("PORTAL_PROJECT_ID", "my-portal-host-project")

    parser = argparse.ArgumentParser(
        description="Repeatable Gemini Enterprise (GE) License Quota Exhaustion Test"
    )
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
        default="gemini-3.1-pro-high",
        help="Model identifier (e.g. gemini-3.1-pro-high, gemini-3.7-flash-medium).",
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
        action="store_true",
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
    return parser.parse_args()


if __name__ == "__main__":
    run_test(parse_args())

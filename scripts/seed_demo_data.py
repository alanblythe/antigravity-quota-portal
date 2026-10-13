#!/usr/bin/env python3
"""Seed 3 demo users into a running Antigravity Quota Portal via REST API."""

import argparse
import sys

import httpx

DEMO_USERS = [
    {
        "email": "alice.chen@example.com",
        "project_id": "your-workload-project",
        "license_tier": "ENTERPRISE_PLUS",
        "has_custom_quota": True,
        "custom_quota_usd": 25.00,
        "custom_overage_usd": 5.00,
        "is_exempt": False,
    },
    {
        "email": "bob.martin@example.com",
        "project_id": "your-workload-project",
        "license_tier": "ENTERPRISE_PLUS",
        "has_custom_quota": False,
        "custom_quota_usd": None,
        "custom_overage_usd": None,
        "is_exempt": False,
    },
    {
        "email": "charlie.davis@example.com",
        "project_id": "your-workload-project-std",
        "license_tier": "ENTERPRISE_STANDARD",
        "has_custom_quota": False,
        "custom_quota_usd": None,
        "custom_overage_usd": None,
        "is_exempt": False,
    },
]


def main():
    parser = argparse.ArgumentParser(description="Seed demo users into Antigravity Quota Portal")
    parser.add_argument(
        "--url", default="http://localhost:8080", help="Base URL of portal (default: http://localhost:8080)"
    )
    args = parser.parse_args()

    base_url = args.url.rstrip("/")
    with httpx.Client(timeout=10.0) as client:
        # 1. Check health
        try:
            resp = client.get(f"{base_url}/api/health")
            resp.raise_for_status()
            health = resp.json()
            print(f"✅ Connected to portal at {base_url} (mock_mode={health.get('mock_mode')})")
        except Exception as e:
            print(f"❌ Could not connect to portal at {base_url}: {e}")
            print("   Make sure the portal is running (e.g., `uv run uvicorn app.main:app --port 8080`)")
            sys.exit(1)

        # 2. Seed Users
        print("\n==> Seeding 3 demo users...")
        for user_data in DEMO_USERS:
            email = user_data["email"]
            q = user_data["custom_quota_usd"]
            quota_str = f"${q:.2f}" if q is not None else "Default"
            try:
                r = client.post(f"{base_url}/api/users", json=user_data)
                if r.status_code == 201:
                    print(f"  + Created: {email} (Quota: {quota_str})")
                elif r.status_code == 400:
                    patch_payload = {k: v for k, v in user_data.items() if k != "email"}
                    pr = client.patch(f"{base_url}/api/users/{email}", json=patch_payload)
                    if pr.status_code == 200:
                        print(f"  * Updated existing: {email} (Quota: {quota_str})")
                    else:
                        print(f"  ! Error updating {email}: {pr.status_code} {pr.text}")
                else:
                    print(f"  ! Error creating {email}: {r.status_code} {r.text}")
            except Exception as err:
                print(f"  ! Request failed for {email}: {err}")

        # 3. Trigger evaluation to calculate usage and group state
        print("\n==> Triggering 'Publish & Sync' evaluation...")
        try:
            r = client.post(f"{base_url}/api/evaluator/publish")
            if r.status_code == 200:
                result = r.json()
                print("✅ Evaluation complete:")
                print(f"   - Users evaluated: {result.get('users_evaluated')}")
                print(f"   - Group swaps:     {result.get('group_swaps_count')}")
            else:
                print(f"⚠️ Publish evaluation returned {r.status_code}: {r.text}")
        except Exception as err:
            print(f"⚠️ Publish evaluation request failed: {err}")

    print(f"\n🎉 Done! View the dashboard at: {base_url}\n")


if __name__ == "__main__":
    main()

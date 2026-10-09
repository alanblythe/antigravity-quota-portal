# Multi-User Antigravity CLI Simulation & Isolation Guide

This document details the multi-user testing framework designed to simulate real developer activity using the **Antigravity CLI (`agy`)** while maintaining complete isolation between test personas and the host workstation environment.

---

## 1. Isolation Architecture

Antigravity CLI and Google Cloud SDK store tokens, configuration files, and conversation histories relative to the standard user home directory:

| Component | Default Location | Isolated Test Location |
| :--- | :--- | :--- |
| **Google Cloud ADC** | `~/.config/gcloud/` | `~/.agy_test_users/<user>/.config/gcloud/` |
| **Antigravity OAuth Token** | `~/.gemini/antigravity-cli/antigravity-oauth-token` | `~/.agy_test_users/<user>/.gemini/antigravity-cli/antigravity-oauth-token` |
| **Session SQLite DBs & Brain** | `~/.gemini/antigravity-cli/conversations/` | `~/.agy_test_users/<user>/.gemini/antigravity-cli/conversations/` |

By injecting dedicated `HOME` and `CLOUDSDK_CONFIG` environment variables into subprocesses, multiple developer sessions (`dev1@yourcompany.com`, `dev2@yourcompany.com`) run concurrently without cross-contamination or modifying your host credentials.

---

## 2. Prerequisites: Workspace User Account Provisioning

Because Google Workspace security policies restrict arbitrary OAuth clients from programmatically provisioning user accounts via the Admin SDK without pre-configured domain-wide delegation, developer accounts are created directly in the Google Admin Console.

### Step 1: Create Developer Accounts
1. Open the [Google Admin Console > Users](https://admin.google.com/ac/users).
2. Click **Add new user** and create:
   * **Developer 1**: `dev1@<your-domain>` (First: `Dev`, Last: `One`)
   * **Developer 2**: `dev2@<your-domain>` (First: `Dev`, Last: `Two`)
3. Set their initial passwords (e.g. `Antigravity2026#Dev1`, `Antigravity2026#Dev2`) and assign a Gemini Enterprise license if seat-based licensing is enabled.

> [!NOTE]
> Terraform automatically provisions their Cloud Identity group memberships (`antigravity-enabled@<your-domain>`) and GCP IAM permissions (`roles/businessaicode.user`). Once the user accounts exist in Workspace, they will immediately inherit full Antigravity access.

---

## 3. One-Time Interactive Authentication

Run the provided helper scripts to authenticate each developer session in their isolated sandbox directory:

### Developer 1 (`dev1@yourcompany.com`)

```bash
./scripts/auth-dev1.sh
```
*(Or with `--no-browser` for headless terminals)*:
```bash
./scripts/auth-dev1.sh --no-browser
```

### Developer 2 (`dev2@yourcompany.com`)

```bash
./scripts/auth-dev2.sh
```
*(Or with `--no-browser` for headless terminals)*:
```bash
./scripts/auth-dev2.sh --no-browser
```

Both scripts automatically:
1. Create isolated directory trees under `~/.agy_test_users/<user>`.
2. Authenticate Google Cloud Application Default Credentials (ADC) for the user.
3. Launch an interactive Antigravity CLI session allowing the developer to select their Gemini Enterprise subscription and cloud project.

---

## 4. Automated Simulation Runner (`IsolatedAgyRunner`)

The [`tests/simulation/runner.py`](file:///mnt/data/repos/antigravity-quota-portal/tests/simulation/runner.py) module provides a programmatic Python wrapper for launching isolated prompts:

```python
from tests.simulation.runner import IsolatedAgyRunner

# Initialize runner for Dev1
runner = IsolatedAgyRunner(
    user_email="dev1@yourcompany.com",
    project_id="your-workload-project"
)

# Execute a non-interactive prompt
result = runner.run_prompt(
    prompt="Explain the difference between BigQuery partitioning and clustering in 2 sentences.",
    model="gemini-2.5-flash"
)

if result.success:
    print(f"Output: {result.stdout}")
```

---

## 5. Running the Pytest Simulation Suite

To execute the multi-user simulation tests and verify quota lifecycle handling:

```bash
# Run simulation suite only
uv run pytest -v tests/simulation/

# Run the complete test suite
uv run pytest -v tests/
```

### What the Simulation Suite Validates:

1. **Session & Directory Isolation**: Confirms independent `HOME`, `CLOUDSDK_CONFIG`, and credential paths for `dev1` and `dev2`.
2. **Quota Tracking**: Simulates `dev1` (under quota) and `dev2` (breaching hard limit $Q + O$).
3. **Automated Group Governance**: Verifies that the Quota Evaluator moves throttled users to `antigravity-disabled@` and retains active users in `antigravity-enabled@`.
4. **Monday Weekly Resets**: Verifies automatic restoration of `AUTO_DISABLED` developers back to `ACTIVE` status and the enabled group.
5. **Administrative Lock Persistence**: Verifies `MANUALLY_DISABLED` users remain locked across weekly reset boundaries.

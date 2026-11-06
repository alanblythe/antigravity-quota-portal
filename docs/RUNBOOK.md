# Verification Runbook — Rapid Testing & Lifecycle Validation

This runbook provides the most efficient, minimal-command path to verify 100% of the Antigravity Quota Portal's functionality—including token spend calculations, hard quota throttling, dual-group Cloud Identity swapping, weekly WTU resets, and administrative overrides—with **zero cloud credentials** in **under 5 seconds**.

---

## 1. Prerequisites (Local Environment Setup)

Generate your local virtual environment and install dependencies:

```bash
uv sync --extra dev
```

---

## 2. Minimal 2-Command Verification Path

### Step 1: Run the Automated Pytest Suite (90 Tests)

```bash
uv run pytest -v
```

**What this verifies:**
* **Token Math & Context Tiers:** Exact calculations across prompt, candidates, cached, thoughts, fallback tokens, and `>200k` context tiers ([`tests/test_cost_model.py`](../tests/test_cost_model.py)).
* **State Machine & Group Governance:** Dual-group state transitions (`ACTIVE`, `AUTO_DISABLED`, `MANUALLY_DISABLED`, `is_exempt`) and swap conditions ([`tests/test_state_machine.py`](../tests/test_state_machine.py)).
* **BigQuery Deduplication & WTU Windows:** Partition filters, `insertId` cross-table deduplication, and 7-day WTU sliding window math ([`tests/test_evaluator.py`](../tests/test_evaluator.py), [`tests/test_timezone.py`](../tests/test_timezone.py)).
* **REST API Contracts:** Status codes, validation guards, and error surfaces ([`tests/test_api.py`](../tests/test_api.py)).
* **Multi-User Sandbox Isolation:** Independent `HOME`, `CLOUDSDK_CONFIG`, and credential sandboxing for `dev1` and `dev2` ([`tests/simulation/test_user_simulation.py`](../tests/simulation/test_user_simulation.py)).

---

### Step 2: Run the 6-Stage End-to-End Lifecycle Simulator

```bash
uv run python scripts/simulate_lifecycle.py
```

**What this verifies end-to-end (in < 1 second):**
1. **Stage 1 (Baseline Initialization):** Provisions `dev1` and `dev2` with the default $10.00 credit quota + $2.00 overage buffer ($12.00 hard limit).
2. **Stage 2 (Normal Spend Within Quota):** Simulates 3M tokens for `dev2` ($3.50 spend). Verifies $3.50 is deducted from credit balance ($6.50 remaining), net billable is $0.00, and `dev2` remains `ACTIVE` in `antigravity-enabled@`.
3. **Stage 3 (Hard Quota Breach & Automated Throttling):** Simulates 8M tokens on `gemini-3-pro` for `dev1` ($30.56 spend $\ge$ $12.00 hard limit). Verifies instant state transition to `AUTO_DISABLED` and Cloud Identity group swap to `antigravity-disabled@`.
4. **Stage 4 (Access Governance Check):** Verifies `dev1` loses Antigravity access, while `dev2` continues operating unhindered.
5. **Stage 5 (Admin Top-Up & Instant Recovery):** Raises `dev1`'s quota to $35.00 via admin API. Verifies `dev1` is restored to `ACTIVE` and swapped back to `antigravity-enabled@`.
6. **Stage 6 (Weekly Reset Rollover):** Simulates 7-day cycle expiration. Verifies `AUTO_DISABLED` developers automatically reset to `ACTIVE` with refreshed credits, while `MANUALLY_DISABLED` developers remain locked.

---

## 3. Interactive UI Verification (Optional)

If you wish to visually inspect the React dashboard, KPI cards, and user drawer:

```bash
uv run scripts/run_local_demo.py
```

* Open `http://localhost:8080` in your browser.
* Pre-seeded with synthetic developer profiles, token breakdowns, audit log entries, and project WTU gauges for direct UI verification.

---

## 4. Rapid Verification Checklist

| Check | Command | Expected Result | Pass? |
| :--- | :--- | :--- | :---: |
| **Unit & Integration Suite** | `uv run pytest -v` | `90 passed` in < 2s, 0 warnings | [ ] |
| **Full Lifecycle Simulation** | `uv run python scripts/simulate_lifecycle.py` | `ALL 6 PHASES VERIFIED SUCCESSFULLY` in < 1s | [ ] |
| **Terraform IaC Validation** | `tofu -chdir=terraform validate` | `Success! The configuration is valid.` | [ ] |
| **Hard Limit Throttling** | Simulated in Stage 3 | State $\rightarrow$ `AUTO_DISABLED`, Group $\rightarrow$ `antigravity-disabled@` | [ ] |
| **Normal Within-Quota Activity** | Simulated in Stage 2 | State $\rightarrow$ `ACTIVE`, Group $\rightarrow$ `antigravity-enabled@` | [ ] |
| **Weekly Reset Rollover** | Simulated in Stage 6 | `AUTO_DISABLED` resets to `ACTIVE`; `MANUALLY_DISABLED` remains locked | [ ] |

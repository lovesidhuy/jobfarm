# Local Testing Guide — Before Provisioning a VM

**Goal:** Validate every component on your Mac so that when you deploy, it just works.

---

## Testing Philosophy

```
Layer 1: Unit tests        → Does each function work in isolation? (fast, no network)
Layer 2: Integration tests  → Do components talk to each other correctly? (mocked deps)
Layer 3: Contract tests     → Does each bot actually hit the right URLs and parse correctly? (live network, no apply)
Layer 4: E2E dry-run        → Does the full pipeline work end-to-end without applying? (live, no side effects)
Layer 5: E2E live (staging) → Does it actually apply to a test job? (real, limited scope)
```

Run layers 1-4 locally. Layer 5 only when you're ready.

---

## Prerequisites — What You Need Running Locally

```bash
# 1. MongoDB (via docker-compose)
docker-compose up -d mongodb

# 2. Verify it's running
mongosh --eval "db.runCommand({ ping: 1 })" --host localhost:27017

# 3. Python env
cd automation_monorepo
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt  # or however deps are managed

# 4. .env file (copy from .env.example, fill in local values)
cp .env.example .env
# Minimum needed for local testing:
#   INFISICAL_CLIENT_ID=...
#   INFISICAL_CLIENT_SECRET=...
#   MONGODB_URI=mongodb://localhost:27017/
#   BYPASS_PROXY=1          ← disables proxy for local dev
#   BROWSER_VENDOR=chrome   ← uses local Chrome instead of NSTbrowser
```

---

## Layer 1: Unit Tests (No Network, No Browser)

These already exist. Run them first as a baseline.

```bash
# Run the existing suite
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_core_unit.py -v

# What it tests:
# - supervised_bot_configs() returns correct bot list
# - subprocess env construction (CDP_PORT, CHROME_PROFILE_DIR, etc.)
# - Datadog metrics fire-and-forget
# - Sentry init doesn't crash
# - CapMonster solver URL construction
# - Easy Apply gate logic
# - NSTBrowser profile handling
```

**What to ADD before orchestration work:**

```bash
# Create: tests/test_orchestrator_unit.py
# Test these NEW components in isolation:

# 1. DailyOrchestrator state transitions
def test_orchestrator_phases_execute_in_order():
    """Discovery must complete before application starts."""

def test_orchestrator_skips_discovery_if_no_sessions_ok():
    """If all sessions expired, don't waste time discovering."""

def test_orchestrator_skips_application_if_queue_empty():
    """If discovery found nothing, don't launch workers."""

def test_orchestrator_sends_summary_on_completion():
    """Summary must be generated even if some phases fail."""

# 2. Session refresh logic
def test_session_refresh_attempts_totp():
    """If TOTP seed exists, generate code and submit."""

def test_session_refresh_falls_back_to_manual():
    """If auto-refresh fails, alert via Telegram."""

def test_session_precheck_detects_expired():
    """HTTP check to portal login page returns redirect → expired."""

# 3. Scheduler triggers
def test_scheduler_invokes_orchestrator_at_correct_time():
    """Mock time.sleep, verify orchestrator.run_daily_cycle() called."""

# 4. Error recovery
def test_self_healing_kills_orphan_chromes():
    """If CDP port in use, kill orphan and retry."""

def test_self_healing_restores_corrupted_profile():
    """If profile dir is corrupt, restore from backup."""
```

**How to run:**
```bash
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/ -v --tb=short
```

---

## Layer 2: Integration Tests (Mocked External Services)

Test that components wire together correctly without hitting real sites.

```bash
# Create: tests/test_integration.py
```

### 2A: Bot → Queue Integration

```python
# Test: Discovery enqueues jobs, Application claims them

def test_discovery_to_queue_flow(monkeypatch):
    """
    1. Mock a provider to return 5 fake jobs
    2. Run discovery planner with dry_run=False
    3. Query MongoDB: verify 5 jobs in application_queue
    4. Run application_worker with --once
    5. Verify jobs move to 'applied' or 'dead' state
    """
    # Use tmp MongoDB (already supported by docker-compose on random port)
    # Or use mongomock if available

def test_queue_dedup_across_providers(monkeypatch):
    """
    1. Mock Indeed provider → same job posted on Indeed + Glassdoor
    2. Run discovery
    3. Verify only 1 entry in queue (deduplication worked)
    """

def test_queue_retry_on_failure(monkeypatch):
    """
    1. Enqueue a job
    2. Mock bot to fail with 'captcha' outcome
    3. Run application worker
    4. Verify job is requeued (not dead)
    """
```

### 2B: Health Controller Integration

```python
def test_health_records_crash_and_blocks_restart():
    """
    1. Record 3 crashes within 10 minutes
    2. Verify is_bot_allowed_to_start() returns False
    3. Wait 30 minutes (mock time)
    4. Verify bot is allowed again
    """

def test_health_sends_telegram_on_crash(monkeypatch):
    """
    1. Mock Telegram send_message
    2. Record a crash
    3. Verify send_message was called with crash alert
    """
```

### 2C: Supervisor → Bot Subprocess Integration

```python
def test_supervisor_launches_bot_with_correct_env(monkeypatch):
    """
    1. Mock subprocess.Popen
    2. Run supervisor with --only indeed_it --once
    3. Verify Popen was called with:
       - BOT_NAME=indeed_it
       - CDP_PORT=9222
       - JOB_PROFILE=IT
       - BROWSER_VENDOR=chrome (for local testing)
    """

def test_supervisor_records_exit_code(monkeypatch):
    """
    1. Mock subprocess to exit with code 1
    2. Run supervisor
    3. Verify MongoDB run record shows exit_code=1
    """
```

**How to run:**
```bash
# Start local MongoDB first
docker-compose up -d mongodb

PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_integration.py -v
```

---

## Layer 3: Contract Tests (Live Network, No Apply)

**These are the most important.** They verify each bot can actually navigate to the site, search for jobs, and parse results — WITHOUT clicking Apply.

```bash
# Create: tests/test_contracts.py
```

### Per-Bot Contract Test

```python
import pytest

@pytest.mark.contract
class TestIndeedContract:
    """Verify Indeed bot can search and parse jobs."""

    def test_search_returns_results(self):
        """
        1. Launch browser (BROWSER_VENDOR=chrome, local Chrome)
        2. Navigate to ca.indeed.com
        3. Search for "python developer" in "Vancouver"
        4. Verify: at least 1 job card is parsed
        5. Verify: each job has title, company, location, url
        6. DO NOT click Apply
        """

    def test_search_pagination_works(self):
        """
        1. Search with pagination
        2. Verify: page 2 has different jobs than page 1
        """

    def test_login_page_detected(self):
        """
        1. Clear cookies / use fresh profile
        2. Navigate to Indeed
        3. Verify: login page is detected (not treated as search results)
        """

@pytest.mark.contract
class TestGlassdoorContract:
    """Verify Glassdoor bot can search and parse jobs."""

    def test_search_returns_results(self):
        """
        1. Navigate to glassdoor.ca
        2. Search for "software engineer" in "Vancouver"
        3. Verify: job cards parsed with title, company, rating, url
        """

    def test_captcha_detection(self):
        """
        1. Run multiple searches rapidly
        2. Verify: CAPTCHA page is detected (not treated as results)
        """

@pytest.mark.contract
class TestWorkopolisContract:
    """Verify Workopolis bot can search and parse jobs."""

    def test_search_returns_results(self):
        """
        1. Navigate to workopolis.com
        2. Search for "developer" in "Toronto"
        3. Verify: jobs parsed
        """

@pytest.mark.contract
class TestLinkedInContract:
    """Verify LinkedIn bot can search jobs."""

    def test_search_returns_results(self):
        """
        1. Navigate to linkedin.com/jobs
        2. Search for "python developer"
        3. Verify: job listings parsed
        """
```

### How to Run Contract Tests

```bash
# Single bot, verbose output
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_contracts.py \
    -v -k "TestIndeedContract" \
    -m contract \
    --tb=long

# All bots
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_contracts.py \
    -v -m contract

# With visible browser (for debugging)
HEADLESS=0 PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_contracts.py \
    -v -m contract -k "TestIndeedContract"
```

**What to watch for:**
- Does the browser actually open?
- Does the search work?
- Are jobs parsed correctly (title, company, URL)?
- Are login/CAPTCHA pages detected?
- Does the proxy work (or bypass correctly with `BYPASS_PROXY=1`)?

---

## Layer 4: E2E Dry-Run (Full Pipeline, No Side Effects)

Run the entire discovery → queue → (skip apply) flow with real sites.

```bash
# Step 1: Discovery dry-run (scrapes real sites, doesn't enqueue)
PYTHONPATH=automation_monorepo python automation_monorepo/scripts/discovery_runner.py \
    --profile it \
    --portals indeed,glassdoor,workopolis \
    --dry-run \
    --max-results 10

# What to verify:
# - Jobs were scraped from each portal
# - Normalization worked (location, title, company)
# - Deduplication removed cross-portal duplicates
# - Screening classified jobs correctly (IT fit vs not)
# - No errors in output

# Step 2: Discovery + Enqueue (real, but don't apply)
DISCOVERY_ENGINE=new PYTHONPATH=automation_monorepo python automation_monorepo/supervisor.py \
    --stage discover \
    --portal indeed \
    --once

# Verify in MongoDB:
mongosh --eval "db.application_queue.countDocuments()" jobbots
# Should show N jobs enqueued

# Step 3: Queue inspection
PYTHONPATH=automation_monorepo python automation_monorepo/scripts/queue_hygiene.py

# What to verify:
# - Queue has jobs
# - No orphaned leases
# - No duplicate entries
# - Status distribution looks sane

# Step 4: Application dry-run (would apply but we intercept)
# Modify application_worker.py temporarily to log而不actually dispatch:
#   - Add `--dry-run` flag that logs the job but doesn't start the bot
#   - Or mock the bot subprocess to just echo the job info
```

### How to Run Full E2E Dry-Run

```bash
# One-shot: discovery → inspect queue
docker-compose up -d mongodb
PYTHONPATH=automation_monorepo python automation_monorepo/supervisor.py \
    --stage discover --portal indeed --once 2>&1 | tee /tmp/e2e_dryrun.log

# Check results
grep "enqueued" /tmp/e2e_dryrun.log | wc -l
mongosh --eval "db.application_queue.find().pretty()" jobbots
```

---

## Layer 5: E2E Live (Real Apply — Staging)

**Only do this when layers 1-4 pass.** Apply to ONE test job.

```bash
# Step 1: Find a clearly fake/test job or use a known easy-apply listing
# Step 2: Run application worker targeting that specific job
PYTHONPATH=automation_monorepo python automation_monorepo/scripts/application_worker.py \
    --job-id <specific_job_id> \
    --once

# Step 3: Verify in MongoDB
mongosh --eval "db.application_queue.findOne({source_job_id: '<job_id>'})" jobbots
# Should show status: 'applied'

# Step 4: Verify on Indeed/Glassdoor
# - Log in manually to the portal
# - Check "Applied Jobs" section
# - Confirm the application went through
```

---

## Testing Each Bot Individually

### Indeed

```bash
# 1. Login check
PYTHONPATH=automation_monorepo python automation_monorepo/test_logins.py --portal indeed

# 2. Contract test (search only)
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_contracts.py \
    -k "TestIndeedContract" -v -m contract

# 3. Single bot run (applies to real jobs — use --once)
PYTHONPATH=automation_monorepo python automation_monorepo/supervisor.py \
    --only indeed_it --once --include-not-ok

# 4. Verify results
mongosh --eval "db.applications.find({bot_name: 'indeed_it'}).sort({created_at: -1}).limit(5).pretty()" jobbots
```

### Glassdoor

```bash
# 1. Login check
PYTHONPATH=automation_monorepo python automation_monorepo/test_logins.py --portal glassdoor

# 2. Contract test
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_contracts.py \
    -k "TestGlassdoorContract" -v -m contract

# 3. Single bot run
PYTHONPATH=automation_monorepo python automation_monorepo/supervisor.py \
    --only glassdoor_it --once --include-not-ok

# 4. Verify
mongosh --eval "db.applications.find({bot_name: 'glassdoor_it'}).sort({created_at: -1}).limit(5).pretty()" jobbots
```

### Workopolis

```bash
# 1. Contract test
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_contracts.py \
    -k "TestWorkopolisContract" -v -m contract

# 2. Single bot run
PYTHONPATH=automation_monorepo python automation_monorepo/supervisor.py \
    --only workopolis_it --once --include-not-ok

# 3. Verify
mongosh --eval "db.applications.find({bot_name: 'workopolis_it'}).sort({created_at: -1}).limit(5).pretty()" jobbots
```

### LinkedIn

```bash
# 1. Login check
PYTHONPATH=automation_monorepo python automation_monorepo/test_logins.py --portal linkedin

# 2. Contract test
PYTHONPATH=automation_monorepo pytest automation_monorepo/tests/test_contracts.py \
    -k "TestLinkedInContract" -v -m contract

# 3. Single bot run
PYTHONPATH=automation_monorepo python automation_monorepo/supervisor.py \
    --only linkedin_it --once --include-not-ok

# 4. Verify
mongosh --eval "db.applications.find({bot_name: 'linkedin_it'}).sort({created_at: -1}).limit(5).pretty()" jobbots
```

---

## Testing the New Orchestration Components

### Test the Scheduler

```python
# tests/test_scheduler.py

def test_scheduler_triggers_daily_cycle(monkeypatch):
    """
    Mock time module. Verify that when clock hits 06:00,
    daily_orchestrator.run_daily_cycle() is called.
    """

def test_scheduler_respects_enabled_days():
    """
    Verify weekend-only mode skips Mon-Fri.
    """

def test_scheduler_handles_overlapping_runs():
    """
    If previous cycle is still running when next trigger fires,
    verify it doesn't start a second instance.
    """
```

### Test the Session Refresh

```python
# tests/test_session_refresh.py

def test_auto_refresh_indeed_with_totp(monkeypatch):
    """
    Mock Playwright browser. Simulate:
    1. Navigate to Indeed login
    2. Enter email/password (from env)
    3. TOTP page appears → generate code from seed
    4. Submit → redirected to dashboard
    5. Verify cookies saved to profile
    """

def test_auto_refresh_glassdoor_without_totp(monkeypatch):
    """
    Mock Playwright. Simulate:
    1. Navigate to Glassdoor login
    2. Enter email/password
    3. No TOTP → verify session saved
    """

def test_auto_refresh_failure_triggers_alert(monkeypatch):
    """
    Mock Playwright to fail login.
    Verify Telegram alert sent with 'NEEDS_MANUAL_LOGIN'.
    """
```

### Test the Daily Orchestrator

```python
# tests/test_daily_orchestrator.py

def test_full_cycle_happy_path(monkeypatch):
    """
    Mock all external deps. Verify:
    1. Pre-flight passes
    2. Sessions all valid
    3. Discovery runs → N jobs enqueued
    4. Application runs → M jobs applied
    5. Summary generated and sent
    """

def test_full_cycle_with_expired_session(monkeypatch):
    """
    Mock Indeed session as expired.
    Verify:
    1. Auto-refresh attempted
    2. If refresh succeeds → continue
    3. If refresh fails → skip Indeed, continue with others
    4. Summary notes Indeed was skipped
    """

def test_full_cycle_with_bot_crash(monkeypatch):
    """
    Mock Glassdoor bot to crash.
    Verify:
    1. Crash recorded in health controller
    2. Other bots continue
    3. Summary notes Glassdoor crashed
    4. No infinite restart loop
    """
```

---

## Test Configuration

### Create `pytest.ini` or add to `pyproject.toml`

```ini
# automation_monorepo/pytest.ini
[pytest]
testpaths = tests
markers =
    contract: Live network tests (no apply)
    slow: Tests that take > 30 seconds
    integration: Tests requiring MongoDB
addopts = -v --tb=short
```

### Create `conftest.py` for Shared Fixtures

```python
# automation_monorepo/conftest.py
import pytest
import os

@pytest.fixture(autouse=True)
def ci_env(monkeypatch):
    """Set safe defaults for CI/local testing."""
    monkeypatch.setenv("BYPASS_PROXY", "1")
    monkeypatch.setenv("BROWSER_VENDOR", "chrome")
    monkeypatch.setenv("AUTONOMOUS_SUPERVISOR", "1")
    monkeypatch.setenv("SENTRY_DSN", "")  # disable in tests
    monkeypatch.setenv("DD_METRICS_ENABLED", "")

@pytest.fixture
def mock_mongodb():
    """Use a temporary MongoDB for tests."""
    # Connect to docker-compose MongoDB on random test database
    from pymongo import MongoClient
    client = MongoClient("mongodb://localhost:27017/")
    db = client["jobbots_test"]
    yield db
    client.drop_database("jobbots_test")

@pytest.fixture
def mock_telegram(monkeypatch):
    """Mock Telegram API calls."""
    from unittest.mock import MagicMock
    mock_send = MagicMock()
    monkeypatch.setattr("core.alerts.send_telegram_message", mock_send)
    return mock_send
```

---

## Quick Reference: What to Run Before VM

```bash
# ═══════════════════════════════════════════════
# PHASE 1: Fast checks (< 30 seconds)
# ═══════════════════════════════════════════════
cd automation_monorepo

# Unit tests
PYTHONPATH=. pytest tests/test_core_unit.py tests/test_master_shims.py -v

# Compile check all bot scripts
python -c "
import py_compile, sys, glob
files = glob.glob('bots/*.py') + glob.glob('core/*.py') + ['supervisor.py']
ok = all(py_compile.compile(f, doraise=True) for f in files)
print('All files compile' if ok else 'COMPILE ERRORS')
sys.exit(0 if ok else 1)
"

# ═══════════════════════════════════════════════
# PHASE 2: Integration tests (needs MongoDB, ~2 min)
# ═══════════════════════════════════════════════
docker-compose up -d mongodb
sleep 3
PYTHONPATH=. pytest tests/test_job_queue.py tests/test_discovery.py -v

# ═══════════════════════════════════════════════
# PHASE 3: Contract tests (live network, ~5 min each)
# ═══════════════════════════════════════════════
# Run each portal one at a time, watch the browser:
PYTHONPATH=. pytest tests/test_contracts.py -v -m contract -k "Indeed" --headed
# If Indeed passes, try Glassdoor:
PYTHONPATH=. pytest tests/test_contracts.py -v -m contract -k "Glassdoor" --headed

# ═══════════════════════════════════════════════
# PHASE 4: E2E dry-run (~10 min)
# ═══════════════════════════════════════════════
PYTHONPATH=. python scripts/discovery_runner.py \
    --profile it --portals indeed --dry-run --max-results 5

# ═══════════════════════════════════════════════
# PHASE 5: Single bot live run (~5 min)
# ═══════════════════════════════════════════════
PYTHONPATH=. python supervisor.py --only indeed_it --once --include-not-ok

# Check what happened
mongosh --eval "db.applications.find().sort({created_at:-1}).limit(3).pretty()" jobbots

# ═══════════════════════════════════════════════
# PHASE 6: Orchestration smoke test
# ═══════════════════════════════════════════════
PYTHONPATH=. pytest tests/test_orchestrator_unit.py tests/test_scheduler.py -v

# ═══════════════════════════════════════════════
# ALL PASS? → You're ready for VM provisioning.
# ═══════════════════════════════════════════════
```

---

## What Each Test Layer Catches

| Layer | Catches | Misses |
|-------|---------|--------|
| Unit | Logic bugs, env var issues, config errors | Network issues, site changes, real CAPTCHAs |
| Integration | Wiring bugs, queue logic, health state machine | Site-specific parsing, login flows |
| Contract | Site structure changes, login detection, CAPTCHA detection | Apply flow correctness |
| E2E dry-run | Pipeline orchestration, dedup, screening accuracy | Apply success rate |
| E2E live | Full apply flow, email verification, form submission | Scale, reliability over time |

**Layer 3 (contract tests) is your highest ROI.** If the sites change their HTML or block your bot, contract tests catch it before you deploy.

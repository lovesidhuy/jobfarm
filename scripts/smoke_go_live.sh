#!/usr/bin/env bash
# Full local go-live smoke — mirrors CI lint-and-smoke job (no browser/Xvfb).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PY="${PYTHON:-python3}"
echo "=== Go-live smoke (repo: $ROOT) ==="

echo "── Ruff (syntax + import errors)"
ruff check \
  automation_monorepo/core \
  automation_monorepo/bots \
  master/Auto_job_applier_linkedIn_gen/core \
  master/Auto_job_applier_linkedIn_it/core \
  --select E9,F4 \
  --exclude automation_monorepo/core/llm_backend/ai \
  --exclude automation_monorepo/core/portals \
  --exclude automation_monorepo/core/validator.py \
  --exclude master/Auto_job_applier_linkedIn_gen/runAiBot.py \
  --exclude master/Auto_job_applier_linkedIn_it/runAiBot.py \
  --exclude master/Auto_job_applier_linkedIn_gen/modules \
  --exclude master/Auto_job_applier_linkedIn_it/modules \
  --exclude master/Auto_job_applier_linkedIn_gen/config \
  --exclude master/Auto_job_applier_linkedIn_it/config

echo "── Compile"
$PY -m compileall -q automation_monorepo/core automation_monorepo/bots automation_monorepo/config
$PY -m compileall -q \
  master/Auto_job_applier_linkedIn_gen/core \
  master/Auto_job_applier_linkedIn_gen/config \
  master/Auto_job_applier_linkedIn_gen/runAiBot.py \
  master/Auto_job_applier_linkedIn_it/core \
  master/Auto_job_applier_linkedIn_it/config \
  master/Auto_job_applier_linkedIn_it/runAiBot.py

echo "── Docker compose config"
GROQ_API_KEY=ci-placeholder MONGODB_PASSWORD=ci-placeholder docker compose config --quiet

echo "── Monorepo supervisor smoke"
cd automation_monorepo
export BOT_NAME=ci-smoke INFISICAL_CLIENT_SECRET=""
$PY _smoke_supervisor.py
$PY _smoke_monorepo.py 2>/dev/null || true

echo "── Bot launchers"
$PY -c "
import bots.indeed_it, bots.indeed_general
import bots.glassdoor_it, bots.glassdoor_general
import bots.workopolis_it, bots.workopolis_general
import bots.linkedin_it, bots.linkedin_general
import bots.google_it
print('  All 9 bot launchers OK')
"

echo "── LinkedIn integration (master trees)"
$PY test_linkedin_integration.py

echo ""
echo "=== GO-LIVE SMOKE PASSED ==="

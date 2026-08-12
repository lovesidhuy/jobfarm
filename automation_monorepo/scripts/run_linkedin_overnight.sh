#!/usr/bin/env bash
# Keep LinkedIn JobSpy discovery + application worker running; dump review snapshots.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p artifacts/mac-linkedin
export PYTHONPATH=.
export PYTHONUNBUFFERED=1
export DISCOVERY_ENGINE=new
export LINKEDIN_DISCOVERY_SEQUENTIAL=1
export BROWSER_VENDOR=nstbrowser
export NSTBROWSER_FORBID_CREATE=1
export NSTBROWSER_ACTIVE_SLOT="${NSTBROWSER_ACTIVE_SLOT:-2}"
export LINKEDIN_USE_EXTENSION=1
export LINKEDIN_EXTENSION_SKIP_BACKEND=1
export LINKEDIN_JOB_PROFILE=it

PY="${ROOT}/.venv/bin/python"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
DISC_LOG="artifacts/mac-linkedin/discovery_overnight_${TS}.log"
WORK_LOG="artifacts/mac-linkedin/worker_overnight_${TS}.log"
REV_LOG="artifacts/mac-linkedin/review_loop_${TS}.log"

# Discovery (if not already running)
if ! pgrep -f "scripts/discovery_runner.py --profile it --portals linkedin" >/dev/null 2>&1; then
  echo "[overnight] starting discovery → $DISC_LOG"
  nohup "$PY" scripts/discovery_runner.py --profile it --portals linkedin \
    --max-results 25 --freshness-days 7 >"$DISC_LOG" 2>&1 &
  echo $! > artifacts/mac-linkedin/discovery.pid
else
  echo "[overnight] discovery already running"
fi

# Continuous application worker (not one-shot job-ids)
if ! pgrep -f "scripts/application_worker.py --portal linkedin --profile it" >/dev/null 2>&1; then
  echo "[overnight] starting application worker → $WORK_LOG"
  nohup env PYTHONPATH=. PYTHONUNBUFFERED=1 BROWSER_VENDOR=nstbrowser \
    NSTBROWSER_FORBID_CREATE=1 NSTBROWSER_ACTIVE_SLOT="${NSTBROWSER_ACTIVE_SLOT}" \
    LINKEDIN_USE_EXTENSION=1 LINKEDIN_EXTENSION_SKIP_BACKEND=1 LINKEDIN_JOB_PROFILE=it \
    "$PY" scripts/application_worker.py --portal linkedin --profile it --poll-seconds 25 \
    >"$WORK_LOG" 2>&1 &
  echo $! > artifacts/mac-linkedin/worker.pid
else
  echo "[overnight] application worker already running"
fi

# Review dump now + every 30m for ~12h (background)
nohup bash -c "
  for i in \$(seq 1 24); do
    \"$PY\" scripts/dump_linkedin_review.py >>\"$REV_LOG\" 2>&1 || true
    sleep 1800
  done
" >/dev/null 2>&1 &
echo $! > artifacts/mac-linkedin/review_loop.pid

"$PY" scripts/dump_linkedin_review.py || true
echo "[overnight] discovery.pid=$(cat artifacts/mac-linkedin/discovery.pid 2>/dev/null || echo n/a)"
echo "[overnight] worker.pid=$(cat artifacts/mac-linkedin/worker.pid 2>/dev/null || echo n/a)"
echo "[overnight] review_loop.pid=$(cat artifacts/mac-linkedin/review_loop.pid 2>/dev/null || echo n/a)"
echo "[overnight] review pack: $(cat artifacts/mac-linkedin/LATEST_REVIEW.txt 2>/dev/null || echo pending)"

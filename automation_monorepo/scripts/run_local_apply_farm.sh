#!/usr/bin/env bash
# Local Mac apply farm — same queue workers as VM, dual NST modes.
#
# Modes
# -----
#   local-agent (default on Mac): use Nstbrowser.app on :8848 (recommended for logins)
#   docker: use nstbrowser/browserless container (VM-like; needs cloud userdata sync)
#
# Usage
# -----
#   source automation_monorepo/.venv/bin/activate   # after setup_local_env.sh
#   export PYTHONPATH=automation_monorepo
#   bash automation_monorepo/scripts/run_local_apply_farm.sh start
#   bash automation_monorepo/scripts/run_local_apply_farm.sh status
#   bash automation_monorepo/scripts/run_local_apply_farm.sh stop
#
# Env
# ---
#   NST_MODE=local-agent|docker
#   NSTBROWSER_ACTIVE_SLOT=1|2
#   MONITOR=1  (default) run full-queue monitor
set -euo pipefail

MONO="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="$(cd "$MONO/.." && pwd)"
cd "$MONO"

if [[ -f "$MONO/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$MONO/.venv/bin/activate"
elif [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi

export PYTHONPATH="${PYTHONPATH:-$MONO}"
export PYTHONUNBUFFERED=1

if [[ -f "$MONO/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$MONO/.env"
  set +a
fi

NST_MODE="${NST_MODE:-local-agent}"
ACTIVE_SLOT="${NSTBROWSER_ACTIVE_SLOT:-2}"
export NSTBROWSER_ACTIVE_SLOT="$ACTIVE_SLOT"
export BROWSER_VENDOR="${BROWSER_VENDOR:-nstbrowser}"
export NSTBROWSER_FORBID_CREATE="${NSTBROWSER_FORBID_CREATE:-1}"
export KEEP_BROWSER="${KEEP_BROWSER:-1}"
export NSTBROWSER_KEEP_ALIVE="${NSTBROWSER_KEEP_ALIVE:-1}"

# Dual-slot: stamp primary API key for the active slot so Node/bots always see one key
if [[ "$ACTIVE_SLOT" == "2" && -n "${NSTBROWSER_API_KEY_2:-}" ]]; then
  export NSTBROWSER_API_KEY="$NSTBROWSER_API_KEY_2"
fi

LOG_W="$MONO/logs/workers"
LOG_M="$MONO/logs/monitor"
mkdir -p "$LOG_W" "$LOG_M" "$MONO/outputs"

cmd="${1:-status}"

_agent_ok() {
  local key="${NSTBROWSER_API_KEY:-}"
  [[ -n "$key" ]] || return 1
  curl -sS -m 3 -o /dev/null -w "%{http_code}" -H "x-api-key: $key" \
    "http://127.0.0.1:8848/api/v2/browsers" 2>/dev/null | grep -q 200
}

_start_docker_nst() {
  local token="${NSTBROWSER_API_KEY:-}"
  [[ -n "$token" ]] || { echo "NSTBROWSER_API_KEY required"; exit 1; }
  if lsof -nP -iTCP:8848 -sTCP:LISTEN 2>/dev/null | grep -qi agent; then
    echo "ERROR: Nstbrowser.app agent is on :8848 — quit the desktop app for docker mode"
    exit 1
  fi
  docker network create jobbots-net 2>/dev/null || true
  docker rm -f jobbots-nstbrowser 2>/dev/null || true
  mkdir -p "$ROOT/data/nstbrowser"
  docker run -d --name jobbots-nstbrowser --network jobbots-net \
    -p 127.0.0.1:8848:8848 --restart unless-stopped \
    --mount type=bind,src="$ROOT/data/nstbrowser",dst=/data \
    -e "TOKEN=${token}" -e "DATADIR=/data" \
    nstbrowser/browserless:latest
  for i in $(seq 1 40); do
    _agent_ok && { echo "Docker NST API ready (${i}s)"; return 0; }
    sleep 1
  done
  echo "Docker NST API not ready"; exit 1
}

_start_local_agent() {
  if docker ps --format '{{.Names}}' 2>/dev/null | grep -qx jobbots-nstbrowser; then
    echo "Stopping Docker NST so local agent can own :8848"
    docker rm -f jobbots-nstbrowser 2>/dev/null || true
    sleep 1
  fi
  if ! _agent_ok; then
    if [[ "$(uname -s)" == "Darwin" ]]; then
      echo "Opening Nstbrowser.app…"
      open -a Nstbrowser 2>/dev/null || true
      for i in $(seq 1 45); do
        _agent_ok && { echo "Local agent ready (${i}s)"; return 0; }
        sleep 1
      done
    fi
    echo "ERROR: NST local API not on http://127.0.0.1:8848 — start Nstbrowser or use NST_MODE=docker"
    exit 1
  fi
  echo "Local NST agent OK on :8848 (slot=$ACTIVE_SLOT)"
}

_stop_workers() {
  for pf in "$LOG_W"/*.pid "$LOG_M"/monitor.pid; do
    [[ -f "$pf" ]] || continue
    pid="$(cat "$pf" 2>/dev/null || true)"
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
      echo "stopped $pf ($pid)"
    fi
    rm -f "$pf"
  done
}

_status() {
  echo "=== mode NST_MODE=$NST_MODE slot=$ACTIVE_SLOT ==="
  if _agent_ok; then echo "NST API: OK"; else echo "NST API: DOWN"; fi
  lsof -nP -iTCP:8848 -sTCP:LISTEN 2>/dev/null | head -3 || true
  docker ps --filter name=jobbots-nstbrowser --format 'docker {{.Names}} {{.Status}}' 2>/dev/null || true
  for n in ats linkedin workopolis indeed; do
    pf="$LOG_W/${n}_worker.pid"
    if [[ -f "$pf" ]] && kill -0 "$(cat "$pf")" 2>/dev/null; then
      echo "worker $n: UP $(cat "$pf")"
    else
      echo "worker $n: down"
    fi
  done
  if [[ -f "$LOG_M/full_queue_status.json" ]]; then
    echo "--- full_queue_status ---"
    python3 -c "import json;print(json.dumps(json.load(open('$LOG_M/full_queue_status.json')),indent=2)[:1200])" 2>/dev/null || cat "$LOG_M/full_queue_status.json"
  fi
  PYTHONPATH="$MONO" python3 - <<'PY'
from core.job_queue import JobQueue
q=JobQueue()
print("queue counts", q.counts())
for x in q.jobs.aggregate([{"$match":{"status":{"$in":["queued","retry","leased"]}}},{"$group":{"_id":"$portal","c":{"$sum":1}}}]):
    print(" active", x["_id"], x["c"])
PY
}

case "$cmd" in
  start)
    echo "Starting local apply farm (mode=$NST_MODE slot=$ACTIVE_SLOT)"
    if [[ "$NST_MODE" == "docker" ]]; then
      _start_docker_nst
    else
      _start_local_agent
    fi
    # Full-queue monitor: ATS parallel + NST portals serialized
    if [[ "${MONITOR:-1}" == "1" ]]; then
      if [[ -f "$MONO/scripts/_mac_full_queue_monitor.py" ]]; then
        nohup env PYTHONPATH="$MONO" PYTHONUNBUFFERED=1 \
          NSTBROWSER_ACTIVE_SLOT="$ACTIVE_SLOT" \
          NSTBROWSER_API_KEY="${NSTBROWSER_API_KEY:-}" \
          NSTBROWSER_API_KEY_2="${NSTBROWSER_API_KEY_2:-}" \
          BROWSER_VENDOR=nstbrowser \
          python3 -u "$MONO/scripts/_mac_full_queue_monitor.py" \
          >"$LOG_M/full_queue_monitor.stdout" 2>&1 &
        echo $! >"$LOG_M/monitor.pid"
        echo "monitor pid=$(cat "$LOG_M/monitor.pid")"
      fi
    fi
    sleep 2
    _status
    ;;
  stop)
    _stop_workers
    if [[ "$NST_MODE" == "docker" ]]; then
      docker rm -f jobbots-nstbrowser 2>/dev/null || true
    fi
    echo "stopped"
    ;;
  status)
    _status
    ;;
  setup)
    bash "$MONO/scripts/setup_local_env.sh"
    ;;
  test)
    FORM_ANSWERS_DISABLE_AI=1 PYTHONPATH="$MONO" python3 -m pytest \
      "$MONO/tests/test_form_answers_smart_fill.py" \
      "$MONO/tests/test_core_unit.py" \
      "$MONO/tests/test_job_queue.py" \
      -q --tb=line
    ;;
  *)
    echo "Usage: $0 {start|stop|status|setup|test}"
    echo "  NST_MODE=local-agent|docker  NSTBROWSER_ACTIVE_SLOT=1|2"
    exit 1
    ;;
esac

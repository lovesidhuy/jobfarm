#!/usr/bin/env bash
# Auto-start indeed_it on login (used by LaunchAgent com.jobbots.indeed-it).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MONOREPO="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$HOME/Library/Logs/jobbots"
mkdir -p "$LOG_DIR"

export BROWSER_VENDOR=ixbrowser
export ADSPOWER_HEADLESS=0
export CAPTCHA_ALLOW_MANUAL_FALLBACK=1
export RUN_IN_BACKGROUND=0
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_DIR/indeed_it_launch.log"; }

log "LaunchAgent start_indeed_it_local.sh invoked"

sleep 20

if ! curl -sf --max-time 3 http://127.0.0.1:53200 >/dev/null 2>&1; then
  log "Starting ixBrowser app"
  open -a ixBrowser || true
  for _ in $(seq 1 45); do
    if curl -sf --max-time 3 http://127.0.0.1:53200 >/dev/null 2>&1; then
      log "ixBrowser API ready"
      break
    fi
    sleep 2
  done
fi

if ! curl -sf --max-time 3 http://127.0.0.1:53200 >/dev/null 2>&1; then
  log "ERROR: ixBrowser API not reachable; skipping bot start"
  exit 1
fi

cd "$MONOREPO"

if pgrep -f "bot_manager.py serve" >/dev/null 2>&1; then
  log "Stopping stale bot_manager serve"
  pkill -f "bot_manager.py serve" || true
  sleep 2
fi

log "Starting indeed_it via bot_manager (ixBrowser profile from .env)"
.venv/bin/python bot_manager.py start indeed_it >> "$LOG_DIR/indeed_it_launch.log" 2>&1
log "bot_manager start command finished (serve keeps running)"

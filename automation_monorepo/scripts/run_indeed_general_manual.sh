#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROFILE_DIR="$REPO_DIR/data/browser_profiles/indeed_general"
CDP_PORT="${CDP_PORT:-9223}"
CDP_URL="http://127.0.0.1:${CDP_PORT}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PYTHON="${REPO_DIR}/.venv/bin/python"

echo "=========================================================="
echo " Indeed General - Manual / Attach Mode Launcher"
echo "=========================================================="
echo ""

_cdp_alive() {
  curl -sf --max-time 2 "${CDP_URL}/json/version" 2>/dev/null | grep -q webSocketDebuggerUrl
}

_wait_for_cdp() {
  for _ in $(seq 1 20); do
    if _cdp_alive; then
      return 0
    fi
    sleep 1
  done
  return 1
}

_cleanup_stale_debug_chrome() {
  echo "Cleaning stale debug Chrome / ChromeDriver on port ${CDP_PORT}..."
  pkill -f "remote-debugging-port=${CDP_PORT}" 2>/dev/null || true
  pkill -f "user-data-dir=${PROFILE_DIR}" 2>/dev/null || true
  pkill -f "chromedriver" 2>/dev/null || true
  pkill -f "uc_driver" 2>/dev/null || true
  sleep 2
}

if _cdp_alive; then
  echo "✓ CDP already reachable at ${CDP_URL}"
else
  if lsof -nP -iTCP:"${CDP_PORT}" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "⚠️ Port ${CDP_PORT} is in use but CDP is not responding."
    _cleanup_stale_debug_chrome
  fi

  if [[ ! -x "$CHROME" ]]; then
    echo "ERROR: Google Chrome not found at: $CHROME"
    exit 1
  fi

  echo "1. Launching Google Chrome with remote debugging on port ${CDP_PORT}..."
  mkdir -p "$PROFILE_DIR"
  "$CHROME" \
    --remote-debugging-port="${CDP_PORT}" \
    --user-data-dir="$PROFILE_DIR" \
    >/dev/null 2>&1 &

  if ! _wait_for_cdp; then
    echo "ERROR: Chrome did not expose CDP at ${CDP_URL}"
    echo "Try closing all Chrome windows and run this script again."
    exit 1
  fi
  echo "✓ Chrome CDP ready at ${CDP_URL}"
fi

echo ""
echo "👉 Action Required:"
echo "   - In the Chrome window that opened, go to https://ca.indeed.com"
echo "   - Log in if needed (solve Cloudflare manually if it appears)"
echo ""
read -r -p "Press [ENTER] here once Indeed is loaded to start the bot..."
echo ""

if ! _cdp_alive; then
  echo "ERROR: CDP at ${CDP_URL} died before bot attach. Re-run this script."
  exit 1
fi

echo "2. Connecting the bot to the running Chrome session..."
export BYPASS_PROXY=1
export USE_CAPMONSTER_CAPTCHA_SOLVER=1
export CAPTCHA_ALLOW_MANUAL_FALLBACK=1
export EXISTING_CDP_URL="$CDP_URL"
export CDP_URL="$CDP_URL"
export PLAYWRIGHT_CDP_URL="$CDP_URL"
"$PYTHON" -u "$SCRIPT_DIR/run_indeed_general_existing_cdp.py"

#!/usr/bin/env bash
# Affordable reset: clear Indeed/Cloudflare cookies in the bot profile (keeps login when possible).
# Use --full to wipe the entire profile directory instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROFILE_DIR="${CHROME_PROFILE_DIR:-$REPO_DIR/data/browser_profiles/indeed_general}"
FULL_RESET=0

for arg in "$@"; do
  case "$arg" in
    --full) FULL_RESET=1 ;;
    -h|--help)
      echo "Usage: $0 [--full]"
      echo "  default  delete Cloudflare/Indeed cookies + local storage (cheap reset)"
      echo "  --full   move entire profile aside and start fresh"
      exit 0
      ;;
  esac
done

echo "Stopping debug Chrome on port 9223 (if any)..."
pkill -f "remote-debugging-port=9223.*indeed_general" 2>/dev/null || true
sleep 1

if [[ ! -d "$PROFILE_DIR" ]]; then
  echo "Profile not found (already clean): $PROFILE_DIR"
  exit 0
fi

if [[ "$FULL_RESET" -eq 1 ]]; then
  BACKUP="${PROFILE_DIR}.bak.$(date +%Y%m%d_%H%M%S)"
  echo "Full reset: moving profile to $BACKUP"
  mv "$PROFILE_DIR" "$BACKUP"
  echo "Done. Open Indeed once in a fresh bot Chrome, log in, then run the bot."
  exit 0
fi

echo "Affordable reset: clearing Indeed + Cloudflare site data in:"
echo "  $PROFILE_DIR"

python3 - "$PROFILE_DIR" <<'PY'
import json
import sys
from pathlib import Path

profile = Path(sys.argv[1])
default_dir = profile / "Default"
if not default_dir.is_dir():
    print("No Default/ subfolder — nothing to clean.")
    sys.exit(0)

hosts = ("indeed.com", "cloudflare.com", "challenges.cloudflare.com")
removed_cookies = 0

for name in ("Cookies", "Cookies-journal"):
    path = default_dir / name
    if path.exists():
        path.unlink()
        removed_cookies += 1
        print(f"  removed {path.name}")

ls_path = default_dir / "Local Storage" / "leveldb"
if ls_path.is_dir():
    for f in ls_path.glob("*.ldb"):
        f.unlink(missing_ok=True)
    for f in ls_path.glob("*.log"):
        f.unlink(missing_ok=True)
    print("  cleared Local Storage leveldb files")

# Chrome extension storage for Indeed domains (best effort)
for storage in default_dir.glob("**/Cookies"):
    try:
        if storage.is_file() and "Network" in str(storage):
            storage.unlink(missing_ok=True)
    except Exception:
        pass

print("Affordable reset complete.")
print("Next:")
print("  1. bash automation_monorepo/scripts/run_indeed_general_manual.sh")
print("  2. Log into Indeed in that Chrome window")
print("  3. Press Enter in the terminal to start the bot")
PY

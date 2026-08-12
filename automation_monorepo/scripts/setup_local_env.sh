#!/usr/bin/env bash
# Create a local venv with the same Python deps as CI/VM (requirements.txt).
# Usage (from repo root or monorepo):
#   bash automation_monorepo/scripts/setup_local_env.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MONO="$ROOT/automation_monorepo"
VENV="${JOBBOTS_VENV:-$MONO/.venv}"
REQ="$ROOT/requirements.txt"

echo "[setup] repo=$ROOT"
echo "[setup] venv=$VENV"

python3 -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --upgrade pip
python -m pip install -r "$REQ"
python -m pip install pytest ruff
# Playwright browsers (local apply farm)
python -m playwright install chromium || true

echo "[setup] verify key packages"
python - <<'PY'
import importlib
for m in ("playwright", "seleniumbase", "pymongo", "requests", "openai"):
    try:
        mod = importlib.import_module(m)
        ver = getattr(mod, "__version__", "?")
        print(f"  OK {m} {ver}")
    except Exception as e:
        print(f"  FAIL {m}: {e}")
        raise SystemExit(1)
print("[setup] all critical imports OK")
PY

echo ""
echo "Activate with:"
echo "  source $VENV/bin/activate"
echo "  export PYTHONPATH=$MONO"
echo "Then run:"
echo "  bash $MONO/scripts/run_local_apply_farm.sh --help"

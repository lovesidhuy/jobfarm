#!/usr/bin/env bash
# Unified Parallel One-Shot Bot Runner: Indeed + Glassdoor + Workopolis
# Runs discovery + application worker once in parallel, then auto-stops the EC2 worker.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [ -r /etc/jobbots/runtime.conf ]; then
  source /etc/jobbots/runtime.conf
fi
if [ -r /etc/jobbots/secrets.env ]; then
  source /etc/jobbots/secrets.env
fi
if [ -r /etc/jobbots/runtime-prod-overrides.conf ]; then
  source /etc/jobbots/runtime-prod-overrides.conf
fi

export WORKOPOLIS_ALLOW_BROWSER_FALLBACK="${WORKOPOLIS_ALLOW_BROWSER_FALLBACK:-1}"
export JOBBOTS_DISCOVERY_FRESHNESS_DAYS="${JOBBOTS_DISCOVERY_FRESHNESS_DAYS:-7}"
export JOBBOTS_DISCOVERY_MAX_RESULTS="${JOBBOTS_DISCOVERY_MAX_RESULTS:-60}"
export PYTHONUNBUFFERED=1

echo "============================================================"
echo "  Starting One-Shot Parallel Run: Indeed + Glassdoor + Workopolis"
echo "  Date: $(date -u)"
echo "============================================================"

# 1. Run Multi-Portal Discovery
/usr/bin/xvfb-run -a /opt/jobbots/venv/bin/python scripts/discovery_runner.py \
  --profile it \
  --portals indeed,glassdoor,workopolis \
  --freshness-days "$JOBBOTS_DISCOVERY_FRESHNESS_DAYS" \
  --max-results "$JOBBOTS_DISCOVERY_MAX_RESULTS" || true

# 2. Run Application Worker on all PENDING items
/usr/bin/xvfb-run -a /opt/jobbots/venv/bin/python scripts/application_worker.py \
  --profile it \
  --portals indeed,glassdoor,workopolis \
  --once || true

echo "============================================================"
echo "  One-Shot Run Complete. Triggering EC2 Auto-Shutdown..."
echo "============================================================"

INSTANCE_ID="$(curl -s --connect-timeout 2 http://169.254.169.254/latest/meta-data/instance-id 2>/dev/null || true)"
if [ -n "$INSTANCE_ID" ]; then
  echo "Auto-stopping EC2 instance: $INSTANCE_ID"
  aws ec2 stop-instances --instance-ids "$INSTANCE_ID" || true
else
  echo "Not running on EC2 or instance ID unavailable. Skipping auto-stop."
fi

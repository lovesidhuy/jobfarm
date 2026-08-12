#!/usr/bin/env bash
# Verify Infisical has everything needed for AdsPower IT-only deploy + run-bots.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_SLUG="${INFISICAL_ENV:-dev}"
PROJECT_ID="${INFISICAL_PROJECT_ID:-a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe}"

REQUIRED=(
  BROWSER_VENDOR
  ADSPOWER_API_KEY
  ADSPOWER_API_URL
  ADSPOWER_HEADLESS
  ADSPOWER_BOT_SCOPE
  ADSPOWER_USER_ID_INDEED_IT
  ADSPOWER_USER_ID_LINKEDIN_IT
  PROXY_URL
  CAPMONSTER_API_KEY
)

login_if_needed() {
  if [[ -n "${INFISICAL_CLIENT_ID:-}" && -n "${INFISICAL_CLIENT_SECRET:-}" ]]; then
    export INFISICAL_TOKEN
    INFISICAL_TOKEN="$(infisical login --method=universal-auth \
      --client-id="$INFISICAL_CLIENT_ID" \
      --client-secret="$INFISICAL_CLIENT_SECRET" \
      --silent --plain)"
  fi
}

if ! command -v infisical >/dev/null 2>&1; then
  echo "FAIL: infisical CLI not installed"
  exit 1
fi

# Machine identity from monorepo .env if not in shell
if [[ -z "${INFISICAL_CLIENT_ID:-}" && -f automation_monorepo/.env ]]; then
  INFISICAL_CLIENT_ID="$(grep '^INFISICAL_CLIENT_ID=' automation_monorepo/.env | cut -d= -f2- || true)"
  INFISICAL_CLIENT_SECRET="$(grep '^INFISICAL_CLIENT_SECRET=' automation_monorepo/.env | cut -d= -f2- || true)"
  export INFISICAL_CLIENT_ID INFISICAL_CLIENT_SECRET
fi

login_if_needed

echo "Checking Infisical secrets (env=$ENV_SLUG) ..."
missing=0
for key in "${REQUIRED[@]}"; do
  val="$(infisical secrets get "$key" --env="$ENV_SLUG" --projectId="$PROJECT_ID" --plain 2>/dev/null | tail -1 || true)"
  if [[ -z "$val" || "$val" == "*not found*" ]]; then
    echo "  MISSING  $key"
    missing=1
  elif [[ "$key" == *KEY* || "$key" == *SECRET* || "$key" == *PASSWORD* ]]; then
    echo "  OK       $key (${#val} chars)"
  else
    echo "  OK       $key=$val"
  fi
done

if [[ "$missing" -ne 0 ]]; then
  echo ""
  echo "FAIL: run ./scripts/sync_infisical_adspower_secrets.sh with ADSPOWER_API_KEY set"
  exit 1
fi

echo ""
echo "All required Infisical secrets present."

#!/usr/bin/env bash
# Push AdsPower + browser-vendor secrets to Infisical (dev environment).
#
# Prerequisites:
#   infisical login          # once, interactive
#   infisical init           # once, in repo root — link to project mybots-r46g / dev
#
# Auth alternative (same as GitHub Actions machine identity):
#   export INFISICAL_CLIENT_ID=...
#   export INFISICAL_CLIENT_SECRET=...
#
# Usage:
#   ADSPOWER_API_KEY=your-key ./scripts/sync_infisical_adspower_secrets.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_SLUG="${INFISICAL_ENV:-dev}"
SECRET_PATH="${INFISICAL_SECRET_PATH:-/}"
PROJECT_ID="${INFISICAL_PROJECT_ID:-a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe}"

if ! command -v infisical >/dev/null 2>&1; then
  echo "ERROR: infisical CLI not found. Install: brew install infisical/get-cli/infisical"
  exit 1
fi

if [[ ! -f .infisical.json ]]; then
  echo "ERROR: .infisical.json missing. Run from repo root:"
  echo "  infisical login"
  echo "  infisical init   # select project mybots-r46g, environment dev"
  exit 1
fi

login_if_needed() {
  if [[ -n "${INFISICAL_CLIENT_ID:-}" && -n "${INFISICAL_CLIENT_SECRET:-}" ]]; then
    echo "Logging in with machine identity..."
    export INFISICAL_TOKEN
    INFISICAL_TOKEN="$(infisical login --method=universal-auth \
      --client-id="$INFISICAL_CLIENT_ID" \
      --client-secret="$INFISICAL_CLIENT_SECRET" \
      --silent --plain)"
  fi
}

set_secret() {
  local key="$1"
  local value="$2"
  if [[ -z "$value" ]]; then
    echo "  skip $key (empty)"
    return 0
  fi
  infisical secrets set "${key}=${value}" \
    --env="$ENV_SLUG" \
    --path="$SECRET_PATH" \
    --projectId="$PROJECT_ID" \
    --silent
  echo "  set $key"
}

login_if_needed

BROWSER_VENDOR="${BROWSER_VENDOR:-adspower}"
ADSPOWER_API_URL="${ADSPOWER_API_URL:-http://127.0.0.1:50325}"
ADSPOWER_HEADLESS="${ADSPOWER_HEADLESS:-1}"
ADSPOWER_BOT_SCOPE="${ADSPOWER_BOT_SCOPE:-indeed_it_linkedin_it}"

# Profile user_ids from AdsPower UI
ADSPOWER_USER_ID_INDEED_IT="${ADSPOWER_USER_ID_INDEED_IT:-k1d7ae40}"
ADSPOWER_USER_ID_LINKEDIN_IT="${ADSPOWER_USER_ID_LINKEDIN_IT:-k1d7ae4r}"
ADSPOWER_USER_ID_INDEED_GENERAL="${ADSPOWER_USER_ID_INDEED_GENERAL:-}"
ADSPOWER_USER_ID_LINKEDIN_GENERAL="${ADSPOWER_USER_ID_LINKEDIN_GENERAL:-}"

if [[ -z "${ADSPOWER_API_KEY:-}" ]]; then
  echo "ERROR: ADSPOWER_API_KEY is required."
  echo "  export ADSPOWER_API_KEY=...   # from AdsPower → Automation → Local API"
  exit 1
fi

echo "Syncing AdsPower secrets to Infisical (env=$ENV_SLUG) ..."

set_secret "BROWSER_VENDOR" "$BROWSER_VENDOR"
set_secret "ADSPOWER_API_KEY" "$ADSPOWER_API_KEY"
set_secret "ADSPOWER_API_URL" "$ADSPOWER_API_URL"
set_secret "ADSPOWER_HEADLESS" "$ADSPOWER_HEADLESS"
set_secret "ADSPOWER_BOT_SCOPE" "$ADSPOWER_BOT_SCOPE"
set_secret "ADSPOWER_USER_ID_INDEED_IT" "$ADSPOWER_USER_ID_INDEED_IT"
set_secret "ADSPOWER_USER_ID_LINKEDIN_IT" "$ADSPOWER_USER_ID_LINKEDIN_IT"
set_secret "ADSPOWER_USER_ID_INDEED_GENERAL" "$ADSPOWER_USER_ID_INDEED_GENERAL"
set_secret "ADSPOWER_USER_ID_LINKEDIN_GENERAL" "$ADSPOWER_USER_ID_LINKEDIN_GENERAL"

echo "Done. Verify:"
echo "  ./scripts/verify_infisical_secrets.sh"

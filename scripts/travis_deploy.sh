#!/usr/bin/env bash
# Compatibility entry point for Travis. Production behavior lives in the
# lifecycle controller so local, GitHub, and Travis deployments cannot drift.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/cloud_environment.sh"

: "${CLOUD_PROVIDER:=aws}"
load_cloud_environment "$CLOUD_PROVIDER"

# GCP compute uses gcloud/SSH lifecycle orchestration and keeps the AWS
# artifact, lease, and secret backends. Do not require AWS EC2-only variables
# before routing this branch.
if [ "$(printf '%s' "$CLOUD_PROVIDER" | tr '[:upper:]' '[:lower:]')" = "gcp" ]; then
  : "${GCP_ZONE:=us-west1-a}"
  : "${GCP_VM_NAME:=jobbots-gcp-worker}"
  if [ -n "${GCP_SERVICE_ACCOUNT_KEY_B64:-}" ]; then
    gcp_key="$(mktemp)"
    trap 'rm -f "$gcp_key"' EXIT
    printf '%s' "$GCP_SERVICE_ACCOUNT_KEY_B64" | base64 --decode >"$gcp_key"
    gcloud auth activate-service-account --key-file="$gcp_key" >/dev/null
  fi
  gcloud config set project "$GCP_PROJECT_ID" >/dev/null
  commit_message="${TRAVIS_COMMIT_MESSAGE:-}"
  action="${JOBBOTS_ACTION:-}"
  if [ -z "$action" ] && [[ "$commit_message" == *"[full-destroy]"* ]]; then
    action="full-destroy"
  elif [ -z "$action" ] && ([[ "$commit_message" == *"[die]"* ]] || [[ "$commit_message" == *"[destroy]"* ]]); then
    action="die"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[soft-destroy]"* ]]; then
    action="soft-destroy"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[sleep]"* ]]; then
    action="sleep"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[awake]"* ]]; then
    action="awake"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[preflight]"* ]]; then
    action="preflight"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[verify-logins]"* ]]; then
    action="verify-logins"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[health]"* ]]; then
    action="health"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[recover]"* ]]; then
    action="recover"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[status]"* ]]; then
    action="status"
  elif [ -z "$action" ] && [[ "$commit_message" == *"[sync]"* ]]; then
    action="sync"
  fi
  action="${action:-run}"
  echo "GCP production cycle action=$action"
  case "$action" in
    run) bash scripts/gcp_lifecycle.sh run ;;
    sync)
      # A code push must not power on an intentionally-off farm or make the
      # deployment red after a soft/full destroy. The next explicit ON
      # operation deploys the checked-out release.
      state="$(gcloud compute instances describe "$GCP_VM_NAME" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" --format='value(status)' 2>/dev/null || true)"
      if [ "$state" = "RUNNING" ]; then
        bash scripts/gcp_lifecycle.sh sync
      else
        echo "GCP worker is OFF or not provisioned; sync is a successful no-op."
      fi
      ;;
    awake|sleep|soft-destroy|die|destroy|full-destroy|status|health|preflight|verify-logins|recover)
      bash scripts/gcp_lifecycle.sh "$action"
      ;;
    *) echo "Unsupported GCP action: $action" >&2; exit 2 ;;
  esac
  exit 0
fi

: "${RESOURCE_PREFIX:?RESOURCE_PREFIX is required}"
: "${AWS_DEFAULT_REGION:?AWS_DEFAULT_REGION is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${AVAILABILITY_ZONE:?AVAILABILITY_ZONE is required}"
: "${TARGET_ENVIRONMENT:?TARGET_ENVIRONMENT is required}"
: "${VM_NAME:?VM_NAME is required}"
: "${ARTIFACT_PREFIX:?ARTIFACT_PREFIX is required}"
: "${TF_STATE_BUCKET:?TF_STATE_BUCKET is required}"

export NSTBROWSER_ACTIVE_SLOT="${NSTBROWSER_ACTIVE_SLOT:-1}"
commit_message="${TRAVIS_COMMIT_MESSAGE:-}"
action="${JOBBOTS_ACTION:-}"

if [ -z "$action" ] && [[ "$commit_message" == *"[full-destroy]"* ]]; then
  action="full-destroy"
elif [ -z "$action" ] && ([[ "$commit_message" == *"[die]"* ]] || [[ "$commit_message" == *"[destroy]"* ]]); then
  action="die"
elif [ -z "$action" ] && [[ "$commit_message" == *"[soft-destroy]"* ]]; then
  action="soft-destroy"
elif [ -z "$action" ] && [[ "$commit_message" == *"[sleep]"* ]]; then
  action="sleep"
elif [ -z "$action" ] && [[ "$commit_message" == *"[awake]"* ]]; then
  action="awake"
elif [ -z "$action" ] && [[ "$commit_message" == *"[preflight]"* ]]; then
  action="preflight"
elif [ -z "$action" ] && [[ "$commit_message" == *"[verify-logins]"* ]]; then
  action="verify-logins"
elif [ -z "$action" ] && [[ "$commit_message" == *"[health]"* ]]; then
  action="health"
elif [ -z "$action" ] && [[ "$commit_message" == *"[recover]"* ]]; then
  action="recover"
elif [ -z "$action" ] && [[ "$commit_message" == *"[status]"* ]]; then
  action="status"
elif [ -z "$action" ] && [[ "$commit_message" == *"[sync]"* ]]; then
  action="sync"
fi
action="${action:-run}"

echo "Production Cycle action=$action active_slot=$NSTBROWSER_ACTIVE_SLOT"
case "$action" in
  run)
    bash scripts/lifecycle.sh run
    ;;
  awake|sleep|soft-destroy|die|destroy|full-destroy|status|health|preflight|verify-logins|recover)
    bash scripts/lifecycle.sh "$action"
    ;;
esac

#!/usr/bin/env bash
# Shared credential/config resolver for local, CI, AWS, and GCP entry points.
# Infisical is the source of deployment values; existing environment variables
# always win so CI and deliberate overrides remain possible.

cloud_env_set_default() {
  local name="$1" value="${2:-}"
  if [[ -z "${!name:-}" && -n "$value" ]]; then
    printf -v "$name" '%s' "$value"
    export "$name"
  fi
}

cloud_env_login_infisical() {
  [[ -n "${INFISICAL_CLIENT_ID:-}" && -n "${INFISICAL_CLIENT_SECRET:-}" ]] || return 0
  export INFISICAL_TOKEN
  INFISICAL_TOKEN="$(infisical login --method=universal-auth \
    --client-id="$INFISICAL_CLIENT_ID" \
    --client-secret="$INFISICAL_CLIENT_SECRET" \
    --silent --plain)"
}

cloud_env_parse_json() {
  # Emit tab-separated key/value pairs. Values never pass through eval.
  python3 - "$1" <<'PY'
import base64
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    payload = json.load(handle)

items = []
if isinstance(payload, dict):
    if isinstance(payload.get("secrets"), list):
        for item in payload["secrets"]:
            if isinstance(item, dict):
                key = item.get("secretKey") or item.get("key") or item.get("name")
                value = item.get("secretValue") if "secretValue" in item else item.get("value")
                if key is not None and value is not None:
                    items.append((key, value))
    else:
        for key, value in payload.items():
            if not isinstance(value, (dict, list)):
                items.append((key, value))
elif isinstance(payload, list):
    for item in payload:
        if isinstance(item, dict):
            key = item.get("secretKey") or item.get("key") or item.get("name")
            value = item.get("secretValue") if "secretValue" in item else item.get("value")
            if key is not None and value is not None:
                items.append((key, value))

for key, value in items:
    if not isinstance(key, str) or not key.replace("_", "").isalnum():
        continue
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif value is None:
        value = ""
    elif not isinstance(value, str):
        value = json.dumps(value, separators=(",", ":"))
    encoded = base64.b64encode(value.encode("utf-8")).decode("ascii")
    print(f"{key}\t{encoded}")
PY
}

cloud_env_load_infisical() {
  [[ "${CLOUD_ENV_SKIP_INFISICAL:-0}" == "1" ]] && return 0
  command -v infisical >/dev/null 2>&1 || return 0

  # Read-only lifecycle actions (status, sleep, guarded destroy) do not need
  # runtime secrets. Do not start an interactive Infisical login when neither
  # universal-auth credentials nor an existing session are available.
  if [[ -z "${INFISICAL_CLIENT_ID:-}" || -z "${INFISICAL_CLIENT_SECRET:-}" ]]; then
    infisical whoami --plain >/dev/null 2>&1 || return 0
  fi
  cloud_env_login_infisical
  local project_id="${INFISICAL_PROJECT_ID:-a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe}"
  local env_slug="${INFISICAL_ENV:-dev}"
  local json_file
  json_file="$(mktemp "${TMPDIR:-/tmp}/jobbots-infisical.XXXXXX")"

  if ! infisical export --env="$env_slug" --projectId="$project_id" \
      --format=json --silent >"$json_file"; then
    rm -f "$json_file"
    return 1
  fi

  while IFS=$'\t' read -r key encoded_value; do
    [[ -n "$key" ]] || continue
    if [[ -z "${!key:-}" ]]; then
      local value
      value="$(printf '%s' "$encoded_value" | base64 --decode)"
      printf -v "$key" '%s' "$value"
      export "$key"
    fi
  done < <(cloud_env_parse_json "$json_file")
  rm -f "$json_file"
}

cloud_env_resolve_gcp() {
  command -v gcloud >/dev/null 2>&1 || return 0

  if [[ -n "${GCP_SERVICE_ACCOUNT_KEY_B64:-}" ]]; then
    local key_file
    key_file="$(mktemp "${TMPDIR:-/tmp}/jobbots-gcp-key.XXXXXX")"
    printf '%s' "$GCP_SERVICE_ACCOUNT_KEY_B64" | base64 --decode >"$key_file"
    gcloud auth activate-service-account --key-file="$key_file" >/dev/null
    rm -f "$key_file"
  elif [[ -n "${GCP_SERVICE_ACCOUNT_JSON:-}" ]]; then
    local json_file
    json_file="$(mktemp "${TMPDIR:-/tmp}/jobbots-gcp-key.XXXXXX.json")"
    printf '%s' "$GCP_SERVICE_ACCOUNT_JSON" >"$json_file"
    gcloud auth activate-service-account --key-file="$json_file" >/dev/null
    rm -f "$json_file"
  fi

  cloud_env_set_default GCP_PROJECT_ID "$(gcloud config get-value project 2>/dev/null || true)"
  if [[ -n "${GCP_PROJECT_ID:-}" ]]; then
    gcloud config set project "$GCP_PROJECT_ID" >/dev/null
  fi
}

cloud_env_resolve_aws() {
  cloud_env_set_default AWS_DEFAULT_REGION "${AWS_REGION:-us-west-2}"
  cloud_env_set_default AWS_REGION "$AWS_DEFAULT_REGION"
}

cloud_env_normalize() {
  cloud_env_set_default GCP_REGION "${AWS_GCP_REGION:-us-west1}"
  cloud_env_set_default GCP_ZONE "${AWS_GCP_ZONE:-us-west1-a}"
  cloud_env_set_default GCP_VM_NAME "${VM_NAME:-jobbots-gcp-worker}"
  cloud_env_set_default GCP_RESOURCE_PREFIX "${RESOURCE_PREFIX:-jobbots-production-13}"
  cloud_env_set_default RESOURCE_PREFIX "$GCP_RESOURCE_PREFIX"
  cloud_env_set_default TARGET_ENVIRONMENT "${ENVIRONMENT:-production}"
  cloud_env_set_default DEPLOYMENT_TIER "$TARGET_ENVIRONMENT"
  cloud_env_set_default TF_STATE_BUCKET "jobbots-tfstate-bucket"
  cloud_env_set_default GCP_TF_STATE_KEY "${GCP_RESOURCE_PREFIX}/gcp-worker.tfstate"
  cloud_env_set_default GCP_ARTIFACT_PREFIX "${ARTIFACT_PREFIX:-gcp/${GCP_RESOURCE_PREFIX}}"
  cloud_env_set_default GCP_PROFILE_LEASE_TABLE "${PROFILE_LEASE_TABLE_NAME:-${GCP_RESOURCE_PREFIX}-profile-leases}"
  cloud_env_set_default GCP_RUNTIME_SECRET_NAME "${RUNTIME_SECRET_NAME:-${GCP_RESOURCE_PREFIX}/runtime}"
  cloud_env_set_default GCP_GOLDEN_IMAGE_FAMILY "jobbots-gcp-golden"
  cloud_env_set_default GCP_GOLDEN_IMAGE_PROJECT "${GCP_PROJECT_ID:-ubuntu-os-cloud}"
}

cloud_env_validate() {
  local provider="${1:-${CLOUD_PROVIDER:-aws}}"
  provider="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')"
  command -v aws >/dev/null 2>&1 || { echo "aws CLI is required" >&2; return 1; }
  aws sts get-caller-identity >/dev/null 2>&1 || {
    echo "AWS CLI is installed but not authenticated" >&2; return 1;
  }
  if [[ "$provider" == "gcp" ]]; then
    command -v gcloud >/dev/null 2>&1 || { echo "gcloud CLI is required" >&2; return 1; }
    [[ -n "${GCP_PROJECT_ID:-}" ]] || { echo "GCP_PROJECT_ID is required" >&2; return 1; }
    gcloud auth print-access-token >/dev/null 2>&1 || {
      echo "gcloud is installed but not authenticated" >&2; return 1;
    }
  fi
}

load_cloud_environment() {
  local provider="${1:-${CLOUD_PROVIDER:-aws}}"
  cloud_env_load_infisical
  cloud_env_normalize
  cloud_env_resolve_aws
  if [[ "$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')" == "gcp" ]]; then
    cloud_env_resolve_gcp
  fi
  export CLOUD_ENVIRONMENT_LOADED=1
}

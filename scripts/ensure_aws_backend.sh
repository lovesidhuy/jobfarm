#!/usr/bin/env bash
# Idempotently prepare the small AWS backend required by a disposable GCP bot
# worker. This does not manage the GCP VM and is safe to run on every ON.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

: "${AWS_DEFAULT_REGION:=us-west-2}"
: "${AWS_REGION:=$AWS_DEFAULT_REGION}"
: "${TF_STATE_BUCKET:=jobbots-tfstate-bucket}"
: "${GCP_RESOURCE_PREFIX:=jobbots-production-13}"
: "${GCP_TARGET_ENVIRONMENT:=production}"
: "${AVAILABILITY_ZONE:=us-west-2a}"
: "${PERSISTENT_VOLUME_SIZE_GB:=20}"
: "${AWS_PERSISTENT_STATE_KEY:=${GCP_RESOURCE_PREFIX}/persistent.tfstate}"

output_file=""
if [[ "${1:-}" == "--output-file" ]]; then
  output_file="${2:?output file is required}"
fi

command -v aws >/dev/null || { echo "aws CLI is required" >&2; exit 1; }
command -v terraform >/dev/null || { echo "terraform is required" >&2; exit 1; }
command -v infisical >/dev/null || { echo "infisical CLI is required" >&2; exit 1; }

tmp_vars="$(mktemp)"
infisical_json="$(mktemp)"
existing_json="$(mktemp)"
secret_json="$(mktemp)"
trap 'rm -f "$tmp_vars" "$infisical_json" "$existing_json" "$secret_json"' EXIT
umask 077

cat >"$tmp_vars" <<EOF
aws_region                         = "$AWS_REGION"
availability_zone                  = "$AVAILABILITY_ZONE"
environment                        = "$GCP_TARGET_ENVIRONMENT"
deployment_tier                    = "$GCP_TARGET_ENVIRONMENT"
resource_prefix                    = "$GCP_RESOURCE_PREFIX"
volume_size_gb                     = $PERSISTENT_VOLUME_SIZE_GB
volume_iops                        = 3000
volume_throughput                  = 125
artifact_transition_days           = 30
artifact_retention_days            = 90
artifact_noncurrent_retention_days = 90
EOF

terraform -chdir=terraform/persistent init -reconfigure -upgrade=false \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="region=$AWS_REGION" \
  -backend-config="key=$AWS_PERSISTENT_STATE_KEY"

# A prior full-destroy may have scheduled the runtime secret for deletion.
# Restore it before Terraform attempts to create the named resource; this is
# safe when the secret is already active and keeps ON idempotent.
aws secretsmanager restore-secret \
  --secret-id "${GCP_RESOURCE_PREFIX}/runtime" >/dev/null 2>&1 || true

if aws secretsmanager describe-secret \
    --secret-id "${GCP_RESOURCE_PREFIX}/runtime" >/dev/null 2>&1; then
  terraform -chdir=terraform/persistent import \
    -var-file="$tmp_vars" \
    aws_secretsmanager_secret.runtime "${GCP_RESOURCE_PREFIX}/runtime" \
    >/dev/null 2>&1 || true
fi

terraform -chdir=terraform/persistent apply -auto-approve -var-file="$tmp_vars"

table_name="$(terraform -chdir=terraform/persistent output -raw profile_lease_table_name)"
artifact_bucket="$(terraform -chdir=terraform/persistent output -raw artifact_bucket_name)"
runtime_secret="$(terraform -chdir=terraform/persistent output -raw runtime_secret_name)"

infisical export --env="${INFISICAL_ENV:-dev}" \
  --projectId="${INFISICAL_PROJECT_ID:-a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe}" \
  --format=json --silent >"$infisical_json"
aws secretsmanager get-secret-value --secret-id="$runtime_secret" \
  --query SecretString --output text >"$existing_json" 2>/dev/null || true

python3 - "$infisical_json" "$existing_json" "$secret_json" <<'PY'
import json
import os
import sys
from pathlib import Path

infisical_path, existing_path, output_path = map(Path, sys.argv[1:])

def as_mapping(payload):
    if isinstance(payload, dict) and isinstance(payload.get("secrets"), list):
        payload = payload["secrets"]
    if isinstance(payload, list):
        result = {}
        for item in payload:
            if not isinstance(item, dict):
                continue
            key = item.get("secretKey") or item.get("key") or item.get("name")
            value = item.get("secretValue") if "secretValue" in item else item.get("value")
            if key and value is not None:
                result[str(key)] = value
        return result
    return payload if isinstance(payload, dict) else {}

data = {}
try:
    existing = existing_path.read_text(encoding="utf-8").strip()
    if existing and existing != "None":
        data.update(json.loads(existing))
except (OSError, json.JSONDecodeError):
    pass

data.update(as_mapping(json.loads(infisical_path.read_text(encoding="utf-8"))))
# CI can supply the OAuth refresh token without putting multiline JSON into
# the shell environment on the worker. Preserve these values on subsequent
# ON/sync runs even when Infisical does not contain them.
for key in (
    "GOOGLE_OAUTH_TOKEN_JSON",
    "GOOGLE_OAUTH_CLIENT_JSON",
    "GOOGLE_SERVICE_ACCOUNT_JSON",
    "GOOGLE_AUTH_MODE",
    "GOOGLE_DRIVE_FOLDER_ID",
    "GOOGLE_SPREADSHEET_ID",
    "GOOGLE_DRIVE_UPLOAD",
):
    value = os.environ.get(key)
    if value:
        data[key] = value
data["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
data["BROWSER_VENDOR"] = data.get("BROWSER_VENDOR") or "nstbrowser"
data["INFISICAL_PROJECT_ID"] = data.get("INFISICAL_PROJECT_ID") or "a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe"
data["INFISICAL_ENV"] = data.get("INFISICAL_ENV") or "dev"
output_path.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
print(f"runtime secret prepared with {len(data)} keys")
PY
secret_string="$(<"$secret_json")"
aws secretsmanager put-secret-value --secret-id "$runtime_secret" --secret-string "$secret_string" >/dev/null

if [[ -n "$output_file" ]]; then
  umask 077
  printf 'GCP_PROFILE_LEASE_TABLE=%q\n' "$table_name" >"$output_file"
  printf 'GCP_ARTIFACT_BUCKET_NAME=%q\n' "$artifact_bucket" >>"$output_file"
  printf 'GCP_RUNTIME_SECRET_NAME=%q\n' "$runtime_secret" >>"$output_file"
fi
printf 'AWS backend ready: table=%s bucket=%s secret=%s\n' "$table_name" "$artifact_bucket" "$runtime_secret"

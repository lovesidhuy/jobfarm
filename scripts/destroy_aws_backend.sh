#!/usr/bin/env bash
# Permanently remove the disposable AWS backend used by a GCP bot farm.
# The shared Terraform state bucket is deliberately not part of this command:
# it is the control plane needed to create the next farm.
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

confirmation="${GCP_CONFIRM_FULL_DESTROY:-${AWS_CONFIRM_FULL_DESTROY:-}}"
if [ "$confirmation" != "$GCP_RESOURCE_PREFIX" ]; then
  echo "Refusing permanent AWS backend deletion. Set GCP_CONFIRM_FULL_DESTROY=$GCP_RESOURCE_PREFIX explicitly." >&2
  exit 2
fi

for tool in aws terraform python3; do
  command -v "$tool" >/dev/null || { echo "$tool is required" >&2; exit 1; }
done

tmp_vars="$(mktemp)"
tmp_versions="$(mktemp)"
tmp_delete="$(mktemp)"
trap 'rm -f "$tmp_vars" "$tmp_versions" "$tmp_delete"' EXIT
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

if ! terraform -chdir=terraform/persistent state list | grep -qx 'aws_s3_bucket.artifacts'; then
  echo "No AWS backend state exists for $GCP_RESOURCE_PREFIX; nothing to remove."
  exit 0
fi

artifact_bucket="$(terraform -chdir=terraform/persistent output -raw artifact_bucket_name)"
runtime_secret="$(terraform -chdir=terraform/persistent output -raw runtime_secret_name 2>/dev/null || true)"
case "$artifact_bucket" in
  "${GCP_RESOURCE_PREFIX}"-artifacts-*) ;;
  *)
    echo "Refusing to empty unexpected artifact bucket: $artifact_bucket" >&2
    exit 2
    ;;
esac

empty_versioned_bucket() {
  local count
  echo "Permanently deleting objects from artifact bucket: $artifact_bucket"
  while :; do
    aws s3api list-object-versions --bucket "$artifact_bucket" --output json >"$tmp_versions"
    count="$(python3 - "$tmp_versions" "$tmp_delete" <<'PY'
import json
import sys

source, destination = sys.argv[1:]
payload = json.load(open(source, encoding="utf-8"))
objects = [
    {"Key": item["Key"], "VersionId": item["VersionId"]}
    for field in ("Versions", "DeleteMarkers")
    for item in payload.get(field, [])
    if item.get("Key") is not None and item.get("VersionId") is not None
]
with open(destination, "w", encoding="utf-8") as handle:
    json.dump({"Objects": objects, "Quiet": True}, handle)
print(len(objects))
PY
    )"
    [ "$count" = "0" ] && break
    aws s3api delete-objects --bucket "$artifact_bucket" --delete "file://$tmp_delete" >/dev/null
  done
}

empty_versioned_bucket
terraform -chdir=terraform/persistent destroy -auto-approve -lock-timeout=300s -var-file="$tmp_vars"

# Terraform honours the resource's 30-day recovery window. A *full* destroy
# is intentionally stronger: restore if necessary, then delete immediately.
if [ -n "$runtime_secret" ] && aws secretsmanager describe-secret --secret-id "$runtime_secret" >/dev/null 2>&1; then
  aws secretsmanager restore-secret --secret-id "$runtime_secret" >/dev/null 2>&1 || true
  aws secretsmanager delete-secret --secret-id "$runtime_secret" --force-delete-without-recovery >/dev/null 2>&1 || true
fi

echo "AWS backend permanently removed for $GCP_RESOURCE_PREFIX. Terraform state bucket retained for future farms."

#!/usr/bin/env bash
# Deploy the GCP compute layer while keeping S3/DynamoDB/Secrets Manager on AWS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
source "$ROOT/scripts/cloud_environment.sh"
load_cloud_environment gcp
cloud_env_validate gcp

: "${GCP_PROJECT_ID:=$(gcloud config get-value project 2>/dev/null || true)}"
: "${GCP_REGION:=us-west1}"
: "${GCP_ZONE:=us-west1-a}"
: "${GCP_VM_NAME:=jobbots-gcp-worker}"
: "${GCP_RESOURCE_PREFIX:=${RESOURCE_PREFIX:-jobbots-production-13}}"
: "${GCP_MACHINE_TYPE:=e2-standard-4}"
: "${GCP_BOOT_DISK_SIZE_GB:=64}"
: "${GCP_GOLDEN_IMAGE_FAMILY:=jobbots-gcp-golden}"
: "${GCP_GOLDEN_IMAGE_PROJECT:=}"
: "${AWS_DEFAULT_REGION:=us-west-2}"
: "${TF_STATE_BUCKET:=jobbots-tfstate-bucket}"
: "${GCP_TF_STATE_KEY:=${GCP_RESOURCE_PREFIX}/gcp-worker.tfstate}"
: "${GCP_ARTIFACT_PREFIX:=${ARTIFACT_PREFIX:-gcp/${GCP_RESOURCE_PREFIX}}}"
: "${GCP_PROFILE_LEASE_TABLE:=${PROFILE_LEASE_TABLE_NAME:-${GCP_RESOURCE_PREFIX}-profile-leases}}"
: "${GCP_RUNTIME_SECRET_NAME:=${RUNTIME_SECRET_NAME:-${GCP_RESOURCE_PREFIX}/runtime}}"
: "${TARGET_ENVIRONMENT:=canary}"
: "${DEPLOYMENT_TIER:=$TARGET_ENVIRONMENT}"
: "${GCP_AUTO_PROVISION_AWS_BACKEND:=1}"
: "${BUILD_GOLDEN_IMAGE:=0}"
: "${SKIP_GCP_LIFECYCLE:=0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --build-golden) BUILD_GOLDEN_IMAGE=1; shift ;;
    --skip-lifecycle) SKIP_GCP_LIFECYCLE=1; shift ;;
    --help|-h)
      echo "Usage: $0 [--build-golden] [--skip-lifecycle]"
      exit 0
      ;;
    *) echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v terraform >/dev/null || { echo "terraform is required" >&2; exit 1; }
command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 1; }
[ -n "$GCP_PROJECT_ID" ] || { echo "GCP_PROJECT_ID is required" >&2; exit 1; }
[ -n "${TF_VM_ADMIN_PASSWORD:-}" ] || { echo "TF_VM_ADMIN_PASSWORD is required" >&2; exit 1; }

# Production boots from the project-owned golden family (NST/Playwright stack).
# Only fall back to public Ubuntu when an operator explicitly opts out.
if [ "$BUILD_GOLDEN_IMAGE" = "1" ]; then
  GCP_GOLDEN_IMAGE_FAMILY="${GCP_GOLDEN_IMAGE_FAMILY:-jobbots-gcp-golden}"
  GCP_PROJECT_ID="$GCP_PROJECT_ID" GCP_ZONE="$GCP_ZONE" \
    TARGET_ENVIRONMENT="$TARGET_ENVIRONMENT" RESOURCE_PREFIX="$GCP_RESOURCE_PREFIX" \
    GCP_GOLDEN_IMAGE_FAMILY="$GCP_GOLDEN_IMAGE_FAMILY" \
    bash scripts/build_gcp_golden.sh
  GCP_GOLDEN_IMAGE_PROJECT="$GCP_PROJECT_ID"
fi
if [ -z "${GCP_GOLDEN_IMAGE_PROJECT:-}" ]; then
  if [ "$GCP_GOLDEN_IMAGE_FAMILY" = "ubuntu-2404-lts-amd64" ] || [ "$GCP_GOLDEN_IMAGE_FAMILY" = "ubuntu-2204-lts" ]; then
    GCP_GOLDEN_IMAGE_PROJECT="ubuntu-os-cloud"
  else
    GCP_GOLDEN_IMAGE_PROJECT="$GCP_PROJECT_ID"
  fi
fi

tmp_vars="$(mktemp)"
tmp_plan="$(mktemp)"
tmp_backend_env="$(mktemp)"
trap 'rm -f "$tmp_vars" "$tmp_plan" "$tmp_backend_env"' EXIT
umask 077

if [ "$GCP_AUTO_PROVISION_AWS_BACKEND" = "1" ]; then
  AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
    GCP_RESOURCE_PREFIX="$GCP_RESOURCE_PREFIX" \
    GCP_TARGET_ENVIRONMENT="$TARGET_ENVIRONMENT" \
    TF_STATE_BUCKET="$TF_STATE_BUCKET" \
    bash scripts/ensure_aws_backend.sh --output-file "$tmp_backend_env"
  # The helper writes only Terraform output names, not secret values.
  source "$tmp_backend_env"
fi

# First deployment has no artifact bucket yet. Resolve it only *after* the
# backend helper has had a chance to create the disposable AWS resources.
if [ -z "${GCP_ARTIFACT_BUCKET_NAME:-}" ]; then
  command -v aws >/dev/null || { echo "aws is required to resolve the AWS artifact bucket" >&2; exit 1; }
  GCP_ARTIFACT_BUCKET_NAME="$(aws s3api list-buckets \
    --query "Buckets[?starts_with(Name, \`${GCP_RESOURCE_PREFIX}-artifacts-\`)].Name | [0]" \
    --output text 2>/dev/null || true)"
fi
[ -n "${GCP_ARTIFACT_BUCKET_NAME:-}" ] && [ "$GCP_ARTIFACT_BUCKET_NAME" != "None" ] || {
  echo "GCP_ARTIFACT_BUCKET_NAME is required (the AWS S3 artifact bucket)" >&2; exit 1;
}

cat >"$tmp_vars" <<EOF
gcp_project_id        = "$GCP_PROJECT_ID"
gcp_region            = "$GCP_REGION"
gcp_zone              = "$GCP_ZONE"
environment           = "$TARGET_ENVIRONMENT"
deployment_tier       = "$DEPLOYMENT_TIER"
resource_prefix       = "$GCP_RESOURCE_PREFIX"
vm_name               = "$GCP_VM_NAME"
machine_type          = "$GCP_MACHINE_TYPE"
boot_disk_size_gb     = $GCP_BOOT_DISK_SIZE_GB
golden_image_family   = "$GCP_GOLDEN_IMAGE_FAMILY"
golden_image_project  = "$GCP_GOLDEN_IMAGE_PROJECT"
vm_admin_password     = "$TF_VM_ADMIN_PASSWORD"
allowed_rdp_ip_v4     = "${ALLOWED_RDP_IP_V4:-}"
allowed_rdp_ip_v6     = "${ALLOWED_RDP_IP_V6:-}"
aws_region            = "$AWS_DEFAULT_REGION"
aws_access_key_id     = "${AWS_ACCESS_KEY_ID:-}"
aws_secret_access_key = "${AWS_SECRET_ACCESS_KEY:-}"
profile_lease_table_name = "$GCP_PROFILE_LEASE_TABLE"
artifact_bucket_name  = "$GCP_ARTIFACT_BUCKET_NAME"
artifact_prefix       = "$GCP_ARTIFACT_PREFIX"
runtime_secret_name   = "$GCP_RUNTIME_SECRET_NAME"
EOF

terraform -chdir=terraform/gcp init -reconfigure -upgrade \
  -backend-config="bucket=$TF_STATE_BUCKET" \
  -backend-config="region=$AWS_DEFAULT_REGION" \
  -backend-config="key=$GCP_TF_STATE_KEY"
terraform -chdir=terraform/gcp validate

# Recover firewall resources created out-of-band while fixing the deployer
# IAM path. Import is a no-op when Terraform already owns them.
for firewall in allow_egress allow_iap_ssh; do
  firewall_name="${GCP_RESOURCE_PREFIX}-${firewall//_/-}"
  if gcloud compute firewall-rules describe "$firewall_name" \
      --project="$GCP_PROJECT_ID" >/dev/null 2>&1; then
    terraform -chdir=terraform/gcp import \
      -var-file="$tmp_vars" \
      "google_compute_firewall.$firewall" "$firewall_name" \
      >/dev/null 2>&1 || true
  fi
done

terraform -chdir=terraform/gcp plan -var-file="$tmp_vars" -out="$tmp_plan"
terraform -chdir=terraform/gcp apply "$tmp_plan"

echo "GCP infrastructure is ready: $GCP_VM_NAME"
terraform -chdir=terraform/gcp output

if [ "$SKIP_GCP_LIFECYCLE" != "1" ]; then
  CLOUD_PROVIDER=gcp GCP_PROJECT_ID="$GCP_PROJECT_ID" GCP_REGION="$GCP_REGION" \
    GCP_ZONE="$GCP_ZONE" GCP_VM_NAME="$GCP_VM_NAME" \
    GCP_RESOURCE_PREFIX="$GCP_RESOURCE_PREFIX" START_BOTS=1 \
    bash scripts/gcp_lifecycle.sh bootstrap
fi

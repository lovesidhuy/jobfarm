#!/usr/bin/env bash
# Build the reusable GCP worker image. This is intentionally explicit because
# Packer creates billable compute resources and should not run during ordinary
# deploys.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/cloud_environment.sh"
load_cloud_environment gcp

: "${GCP_PROJECT_ID:=$(gcloud config get-value project 2>/dev/null || true)}"
: "${GCP_ZONE:=us-west1-a}"
: "${GCP_GOLDEN_IMAGE_FAMILY:=jobbots-gcp-golden}"
: "${GCP_GOLDEN_IMAGE_NAME:=}"
: "${TARGET_ENVIRONMENT:=canary}"
: "${RESOURCE_PREFIX:=jobbots-canary}"

command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 1; }
command -v packer >/dev/null || { echo "packer is required" >&2; exit 1; }
[ -n "$GCP_PROJECT_ID" ] || { echo "GCP_PROJECT_ID is required" >&2; exit 1; }

export PKR_VAR_gcp_project_id="$GCP_PROJECT_ID"
export PKR_VAR_gcp_zone="$GCP_ZONE"
export PKR_VAR_environment="$TARGET_ENVIRONMENT"
export PKR_VAR_deployment_tier="$TARGET_ENVIRONMENT"
export PKR_VAR_resource_prefix="$RESOURCE_PREFIX"
export PKR_VAR_image_name="$GCP_GOLDEN_IMAGE_NAME"
export PKR_VAR_image_family="$GCP_GOLDEN_IMAGE_FAMILY"

packer init packer/jobbots-golden-gcp.pkr.hcl
packer validate packer/jobbots-golden-gcp.pkr.hcl
packer build packer/jobbots-golden-gcp.pkr.hcl

echo "GCP golden image family: $GCP_GOLDEN_IMAGE_FAMILY"
echo "Set golden_image_family=$GCP_GOLDEN_IMAGE_FAMILY and golden_image_project=$GCP_PROJECT_ID in the GCP tfvars."

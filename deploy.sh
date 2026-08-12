#!/usr/bin/env bash

# Unified Deployment Bridge Script for Job Automation Bots
# Supports AWS EC2 VM deployment and GCP Compute Engine VM deployment.
# Integrates Infisical CLI, AWS CLI, GCP CLI, GitHub CLI, and Travis CLI.
# AWS remains the active default provider.

set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
source "$ROOT/scripts/cloud_environment.sh"

PROVIDER="${CLOUD_PROVIDER:-aws}"

# Parse command-line flags
while [[ $# -gt 0 ]]; do
    case "$1" in
        --provider|-p)
            PROVIDER="$2"
            shift 2
            ;;
        --provider=*)
            PROVIDER="${1#*=}"
            shift 1
            ;;
        --help|-h)
            echo "Usage: ./deploy.sh [OPTIONS]"
            echo ""
            echo "Options:"
            echo "  --provider, -p <aws|gcp>   Specify cloud provider for VM hosting (default: aws)"
            echo "  --help, -h                 Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  CLOUD_PROVIDER             Set to 'aws' or 'gcp' (default: aws)"
            echo "  TF_VM_ADMIN_PASSWORD        Admin password for VM xrdp login"
            echo "  GCP_PROJECT_ID             (Required for GCP) GCP Project ID"
            echo ""
            echo "Wired CLI Tools:"
            echo "  aws, gcloud, infisical, gh, travis"
            exit 0
            ;;
        *)
            echo "Unknown argument: $1"
            echo "Use ./deploy.sh --help for usage details."
            exit 1
            ;;
    esac
done

PROVIDER=$(echo "$PROVIDER" | tr '[:upper:]' '[:lower:]')
load_cloud_environment "$PROVIDER"
cloud_env_validate "$PROVIDER"

case "$PROVIDER" in
    aws)
        echo "🌐 Routing deployment to AWS EC2 provider..."
        chmod +x ./deploy_aws.sh
        exec ./deploy_aws.sh "$@"
        ;;
    gcp)
        echo "🌐 Routing deployment to GCP Compute Engine provider..."
        chmod +x ./deploy_gcp.sh
        exec ./deploy_gcp.sh "$@"
        ;;
    *)
        echo "Error: Invalid cloud provider '$PROVIDER'. Supported providers are 'aws' and 'gcp'."
        exit 1
        ;;
esac

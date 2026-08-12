#!/usr/bin/env bash
# Unified CLI Tools Integrator (AWS, GCP, Infisical, GitHub CLI, Travis CLI)
# Wiring operator CLIs into the job automation monorepo workflow.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/cloud_environment.sh"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

print_status() { echo -e "${GREEN}[INFO]${NC} $1"; }
print_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
print_error() { echo -e "${RED}[ERROR]${NC} $1"; }
print_step() { echo -e "${BLUE}[STEP]${NC} $1"; }

cmd_check() {
    print_step "Checking local CLI tools installation & authentication..."
    echo ""

    # 1. AWS CLI
    if command -v aws >/dev/null 2>&1; then
        local aws_identity
        aws_identity=$(aws sts get-caller-identity --query "Arn" --output text 2>/dev/null || true)
        if [ -n "$aws_identity" ]; then
            print_status "AWS CLI:        Installed & Authenticated ($aws_identity)"
        else
            print_warning "AWS CLI:        Installed (Not Authenticated or Credentials Expired)"
        fi
    else
        print_error "AWS CLI:        Not Installed"
    fi

    # 2. GCP CLI (gcloud)
    if command -v gcloud >/dev/null 2>&1; then
        local gcp_account gcp_proj
        gcp_account=$(gcloud config get-value account 2>/dev/null || true)
        gcp_proj=$(gcloud config get-value project 2>/dev/null || true)
        if [ -n "$gcp_account" ]; then
            print_status "GCP CLI:        Installed & Authenticated ($gcp_account, project: ${gcp_proj:-none})"
        else
            print_warning "GCP CLI:        Installed (Not Authenticated — run 'gcloud auth login')"
        fi
    else
        print_error "GCP CLI:        Not Installed"
    fi

    # 3. Infisical CLI
    if command -v infisical >/dev/null 2>&1; then
        local infisical_who
        infisical_who=$(infisical whoami --plain 2>/dev/null || echo "Logged In")
        print_status "Infisical CLI:  Installed ($infisical_who)"
    else
        print_error "Infisical CLI:  Not Installed"
    fi

    # 4. GitHub CLI (gh)
    if command -v gh >/dev/null 2>&1; then
        if gh auth status >/dev/null 2>&1; then
            local gh_user
            gh_user=$(gh api user --jq '.login' 2>/dev/null || echo "Logged In")
            print_status "GitHub CLI:     Installed & Authenticated ($gh_user)"
        else
            print_warning "GitHub CLI:     Installed (Not Authenticated — run 'gh auth login')"
        fi
    else
        print_error "GitHub CLI:     Not Installed"
    fi

    # 5. Travis CLI (travis)
    if command -v travis >/dev/null 2>&1; then
        local travis_who
        travis_who=$(travis whoami 2>/dev/null || true)
        if [ -n "$travis_who" ]; then
            print_status "Travis CLI:     Installed & Authenticated ($travis_who)"
        else
            print_warning "Travis CLI:     Installed (Not Authenticated — run 'travis login')"
        fi
    else
        print_error "Travis CLI:     Not Installed"
    fi
    echo ""
}

cmd_load_secrets() {
    print_step "Resolving runtime secrets from Infisical CLI..."
    if ! command -v infisical >/dev/null 2>&1; then
        print_error "Infisical CLI is not installed."
        return 1
    fi

    print_status "Fetching deployment values from Infisical without evaluating raw shell output..."
    load_cloud_environment "${CLOUD_PROVIDER:-gcp}"
    print_status "Loaded Infisical deployment values into this process."
}

cmd_sync() {
    local provider="${1:-${CLOUD_PROVIDER:-gcp}}"
    provider="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')"
    load_cloud_environment "$provider"
    cloud_env_validate "$provider"
    case "$provider" in
        gcp)
            print_step "Synchronizing the GCP worker from the tracked release..."
            bash scripts/gcp_lifecycle.sh sync
            ;;
        aws)
            print_step "Synchronizing the AWS worker from the tracked release..."
            CLOUD_PROVIDER=aws bash scripts/lifecycle.sh sync
            ;;
        all)
            print_step "Synchronizing configured AWS and GCP workers..."
            CLOUD_PROVIDER=aws bash scripts/lifecycle.sh sync
            CLOUD_PROVIDER=gcp bash scripts/gcp_lifecycle.sh sync
            ;;
        *)
            print_error "Unsupported sync provider: $provider"
            return 2
            ;;
    esac
}

cmd_power() {
    local action="${1:-status}"
    case "$action" in
        on|off|status|health|preflight|verify-logins|recover|sync|soft-destroy|destroy|full-destroy)
            exec bash "$ROOT/jobbots" "$action"
            ;;
        *)
            print_error "Usage: $0 power on|off|status|health|preflight|verify-logins|recover|sync|soft-destroy|destroy|full-destroy"
            return 2
            ;;
    esac
}

cmd_gh_runs() {
    print_step "Checking recent GitHub Actions workflow runs via GitHub CLI..."
    if ! command -v gh >/dev/null 2>&1; then
        print_error "GitHub CLI (gh) is not installed."
        return 1
    fi

    gh run list --limit 5
}

cmd_travis_status() {
    print_step "Checking Travis CI build status via Travis CLI..."
    if ! command -v travis >/dev/null 2>&1; then
        print_error "Travis CLI is not installed."
        return 1
    fi

    travis status || travis history --limit 5
}

cmd_status() {
    cmd_check

    print_step "=== Multi-Cloud VM Worker Status ==="

    # Check AWS
    if command -v aws >/dev/null 2>&1; then
        echo "--- AWS EC2 Workers ---"
        aws ec2 describe-instances \
            --filters "Name=tag:Project,Values=jobbots" \
            --query "Reservations[*].Instances[*].{ID:InstanceId,Name:Tags[?Key=='Name'].Value|[0],State:State.Name,IP:PublicIpAddress}" \
            --output table 2>/dev/null || echo "No AWS instances found or AWS CLI error."
    fi

    # Check GCP
    if command -v gcloud >/dev/null 2>&1; then
        echo "--- GCP Compute Engine Workers ---"
        gcloud compute instances list --filter="labels.project=jobbots" \
            --format="table(name,zone,status,externalIp())" 2>/dev/null || echo "No GCP instances found or GCP CLI error."
    fi
}

cmd_deploy() {
    local provider="${1:-aws}"
    print_step "Deploying VM worker (Provider: $provider) using wired CLI credentials..."

    ./deploy.sh --provider="$provider"
}

case "${1:-check}" in
    check) cmd_check ;;
    secrets|load-secrets) cmd_load_secrets ;;
    gh|github) cmd_gh_runs ;;
    travis) cmd_travis_status ;;
    sync) cmd_sync "${2:-gcp}" ;;
    power) cmd_power "${2:-status}" ;;
    status) cmd_status ;;
    deploy) cmd_deploy "${2:-aws}" ;;
    *)
        echo "Usage: $0 check|secrets|gh|travis|sync|power|status|deploy [aws|gcp|all]"
        exit 1
        ;;
esac

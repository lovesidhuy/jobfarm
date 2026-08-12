# GCP VM Hosting + AWS Backend Hybrid Architecture & CLI Integrations

This document describes how the job automation bots infrastructure supports hosting compute virtual machines (VMs) on Google Cloud Platform (GCP) while continuing to run persistent storage and backend services on Amazon Web Services (AWS), fully integrated with operator CLI tools (`aws`, `gcloud`, `infisical`, `gh`, `travis`).

---

## Overview

```
 ┌─────────────────────────────────────────────────────────────┐
 │                      GCP (Compute Layer)                    │
 │                                                             │
 │  ┌───────────────────────────────────────────────────────┐  │
 │  │ Google Compute Engine (Ubuntu 24.04 LTS)              │  │
 │  │  - Desktop GUI (xrdp + XFCE)                          │  │
 │  │  - Playwright / Selenium / Chrome CDP                 │  │
 │  │  - Bot application runners                            │  │
 │  └──────────────────────────┬────────────────────────────┘  │
 └─────────────────────────────┼───────────────────────────────┘
                               │
                               │ Runtime AWS Credentials / Config
                               ▼
 ┌─────────────────────────────────────────────────────────────┐
 │                     AWS (Backend Layer)                     │
 │                                                             │
 │  - Amazon S3: Artifact storage & run logs                   │
 │  - Amazon DynamoDB: Profile lease table                     │
 │  - AWS Secrets Manager: Runtime credentials & secrets       │
 │  - AWS Lambda: Artifact creation completion triggers        │
 └─────────────────────────────────────────────────────────────┘
```

---

## Wired Operator CLI Tools

All 5 operator CLIs are wired into the deployment & operational toolchain:

| CLI Tool | Primary Role in Monorepo | Integration Point |
| :--- | :--- | :--- |
| **AWS CLI** (`aws`) | AWS EC2 VM management, S3 artifacts, Secrets Manager, DynamoDB | `deploy_aws.sh`, `schedule_vm_stop.sh`, `scripts/lifecycle.sh` |
| **GCP CLI** (`gcloud`) | GCP Compute Engine management, VM stop control, project resolution | `deploy_gcp.sh`, `schedule_vm_stop.sh`, `scripts/cli_tools.sh` |
| **Infisical CLI** (`infisical`) | Auto-resolution of deployment passwords & runtime API secrets | `deploy.sh`, `scripts/verify_infisical_secrets.sh`, `scripts/cli_tools.sh` |
| **GitHub CLI** (`gh`) | GitHub Actions workflow inspection (`gh run list`) & remote triggers | `scripts/cli_tools.sh`, `.github/workflows/` |
| **Travis CLI** (`travis`) | Travis CI build status (`travis status`) & build controls | `scripts/cli_tools.sh`, `.travis.yml`, `scripts/travis_deploy.sh` |

---

## Deployment & Usage

### 1. Default Deployment (AWS EC2)
AWS remains the default target provider for normal operations.

```bash
# Deploys VM on AWS EC2
./deploy.sh
# or
./deploy_aws.sh
```

### 2. Testing & Deploying on GCP Compute Engine
To deploy and test VM hosting on GCP:

```bash
# Deploys VM on GCP Compute Engine (Infisical automatically supplies missing secrets)
./deploy.sh --provider=gcp
# or
CLOUD_PROVIDER=gcp ./deploy.sh
```

The first deployment performs Terraform apply, SSH readiness, host
provisioning, dependency installation, and service start. After that, normal
code updates run a remote GitHub pull on the VM and leave running services
untouched.

```bash
# One-time reusable image build (explicit because it incurs GCP build cost)
GCP_PROJECT_ID=my-project ./scripts/build_gcp_golden.sh

# Deploy using the golden image and sync/start the worker
GCP_PROJECT_ID=my-project GCP_GOLDEN_IMAGE_FAMILY=jobbots-gcp-golden \
  GCP_GOLDEN_IMAGE_PROJECT=my-project ./deploy_gcp.sh

# Ongoing operations: code pull does not restart bots
./jobbots status
./jobbots sync
./jobbots on
./jobbots off
./jobbots health
./jobbots preflight   # NST profile + known quota readiness; does not open a browser
# Actual portal login validation opens profiles and therefore spends NST quota.
NST_LOGIN_CHECK_CONFIRM=jobbots-production-13 ./jobbots verify-logins
# Explicit service recovery, only when you choose to interrupt/restart them
./jobbots recover
./jobbots soft-destroy # graceful stop + power off; all resources remain reusable
# Delete GCP compute/network only; the AWS backend remains for the next farm.
GCP_CONFIRM_DESTROY=jobbots-production-13 ./jobbots destroy
# Permanently delete GCP compute plus the disposable AWS backend/artifacts.
# The shared Terraform state bucket stays so a later ON remains automatic.
GCP_CONFIRM_FULL_DESTROY=jobbots-production-13 ./jobbots full-destroy
```

`sync` remains a remote GitHub pull only: it never restarts a running bot.
GitHub Actions, Travis, and the local wrapper all call the same lifecycle
controller. GitHub's `full-destroy` action requires the exact prefix in its
confirmation field; Travis requires `GCP_CONFIRM_FULL_DESTROY` (or
`FULL_DESTROY_CONFIRM` for the AWS controller).

### 3. Checking Integrated CLI Status
Run the unified CLI integrator tool:

```bash
# Check status & auth of aws, gcloud, infisical, gh, travis
./scripts/cli_tools.sh check

# Load secrets directly from Infisical into shell
./scripts/cli_tools.sh secrets

# Inspect multi-cloud worker status across AWS and GCP
./scripts/cli_tools.sh status

# Inspect GitHub Actions workflow runs
./scripts/cli_tools.sh gh

# Inspect Travis CI build status
./scripts/cli_tools.sh travis
```

---

## Management & Stopping

To prevent unnecessary compute charges on either cloud, use the unified `schedule_vm_stop.sh` script:

```bash
# Schedule AWS VM to stop in 6 hours (default)
./schedule_vm_stop.sh 6

# Schedule GCP VM to stop in 6 hours
./schedule_vm_stop.sh 6 schedule --provider gcp
# or
CLOUD_PROVIDER=gcp ./schedule_vm_stop.sh 6

# Cancel pending scheduled stop
./schedule_vm_stop.sh 6 cancel --provider gcp
```

---

## Infrastructure Configuration Files

- **GCP Terraform**: [`terraform/gcp/`](file://./terraform/gcp/)
- **AWS Terraform**: [`terraform/`](file://./terraform/)
- **AWS Persistent Backend**: [`terraform/persistent/`](file://./terraform/persistent/)
- **CLI Tools Integrator**: [`scripts/cli_tools.sh`](file://./scripts/cli_tools.sh)
- **GCP Deployment Script**: [`deploy_gcp.sh`](file://./deploy_gcp.sh)
- **Unified Deployment Bridge**: [`deploy.sh`](file://./deploy.sh)
- **Unified Stop Scheduler**: [`schedule_vm_stop.sh`](file://./schedule_vm_stop.sh)

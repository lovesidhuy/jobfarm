# Cloud Farm Deployment (AWS & GCP)

JobFarm includes full Infrastructure-as-Code to deploy automated worker fleets on cloud virtual machines.

## Architecture
- **IaC**: Terraform in `infra/terraform/` (AWS EC2 Spot instances / GCP Preemptible VMs).
- **Golden Image**: Packer templates in `infra/packer/` for pre-baked Chromium + Python environments.
- **Secret Management**: Infisical integration for dynamic credential retrieval.
- **Monitoring**: Datadog telemetry, Sentry error tracking, and Telegram alerts.

## Deployment Command
```bash
./deploy.sh --provider=aws --region=us-west-2
```

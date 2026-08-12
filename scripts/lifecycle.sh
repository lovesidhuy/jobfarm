#!/usr/bin/env bash
# Ephemeral worker lifecycle for production (die / born / sleep / awake / sync).
# Used by humans and Travis (travis_deploy.sh delegates here).
#
# Usage:
#   bash scripts/lifecycle.sh run|sync|die|born|sleep|awake|status [instance-id]
#
# Env (same as Travis):
#   RESOURCE_PREFIX, AWS_REGION, TF_STATE_BUCKET, ARTIFACT_PREFIX, ...
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/cloud_environment.sh"

: "${RESOURCE_PREFIX:=jobbots-production-13}"
: "${AWS_REGION:=us-west-2}"
: "${AWS_DEFAULT_REGION:=$AWS_REGION}"
: "${TF_STATE_BUCKET:=jobbots-tfstate-bucket}"
: "${TARGET_ENVIRONMENT:=production}"
: "${AVAILABILITY_ZONE:=us-west-2a}"
: "${VM_NAME:=${RESOURCE_PREFIX}-worker}"
: "${ARTIFACT_PREFIX:=production/deploy}"
: "${NSTBROWSER_ACTIVE_SLOT:=2}"
: "${CLOUD_PROVIDER:=aws}"
load_cloud_environment "$CLOUD_PROVIDER"
# Persistent EBS size (GB). EBS cannot shrink in place — must replace volume to reduce.
: "${PERSISTENT_VOLUME_SIZE_GB:=20}"

action="${1:-status}"
override_id="${2:-}"

# Keep one CLI surface, but route GCP before evaluating AWS-only Terraform,
# EC2, EBS, or SSM helpers below.
if [ "$(printf '%s' "$CLOUD_PROVIDER" | tr '[:upper:]' '[:lower:]')" = "gcp" ]; then
  exec bash "$ROOT/scripts/gcp_lifecycle.sh" "$@"
fi

export TF_VAR_deployment_tier="$TARGET_ENVIRONMENT"
export TF_VAR_resource_prefix="$RESOURCE_PREFIX"
export TF_VAR_aws_region="$AWS_REGION"
export TF_VAR_availability_zone="$AVAILABILITY_ZONE"
export TF_VAR_environment="$TARGET_ENVIRONMENT"
export TF_VAR_volume_size_gb="$PERSISTENT_VOLUME_SIZE_GB"
export AWS_REGION AWS_DEFAULT_REGION AVAILABILITY_ZONE RESOURCE_PREFIX TARGET_ENVIRONMENT VM_NAME ARTIFACT_PREFIX PERSISTENT_VOLUME_SIZE_GB

find_running_worker() {
  aws ec2 describe-instances \
    --filters "Name=tag:ResourcePrefix,Values=$RESOURCE_PREFIX" \
              "Name=tag:Ephemeral,Values=true" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo None
}

find_any_worker() {
  aws ec2 describe-instances \
    --filters "Name=tag:ResourcePrefix,Values=$RESOURCE_PREFIX" \
              "Name=tag:Ephemeral,Values=true" \
              "Name=instance-state-name,Values=pending,running,stopping,stopped" \
    --query 'Reservations[0].Instances[0].{Id:InstanceId,State:State.Name,Ip:PublicIpAddress}' \
    --output json 2>/dev/null || echo '{}'
}

find_stopped_worker() {
  aws ec2 describe-instances \
    --filters "Name=tag:ResourcePrefix,Values=$RESOURCE_PREFIX" \
              "Name=tag:Ephemeral,Values=true" \
              "Name=instance-state-name,Values=stopped,stopping" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || echo None
}

resolve_ami() {
  local golden stock
  golden=$(aws ec2 describe-images --owners self \
    --filters "Name=name,Values=${RESOURCE_PREFIX}-linux-golden-*,jobbots-linux-golden-*" \
    --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text 2>/dev/null || echo None)
  if [ -n "$golden" ] && [ "$golden" != "None" ]; then
    echo "$golden|0"
    return
  fi
  stock=$(aws ec2 describe-images --owners 099720109477 \
    --filters "Name=name,Values=ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*" \
              "Name=virtualization-type,Values=hvm" \
              "Name=architecture,Values=x86_64" \
              "Name=state,Values=available" \
    --query "sort_by(Images, &CreationDate)[-1].ImageId" --output text)
  echo "${stock}|1"
}

worker_needs_bootstrap() {
  local id="$1"
  local out
  out="$(bash .github/scripts/ssm-run.sh "$id" \
    'if [ -x /opt/jobbots/venv/bin/python ] && systemctl list-unit-files 2>/dev/null | grep -q "^jobbots-nstbrowser.service"; then echo ready; else echo bare; fi' \
    30 2>/dev/null || true)"
  if printf '%s\n' "$out" | grep -qE '^ready$'; then
    return 1
  fi
  return 0
}

cmd_status() {
  echo "=== lifecycle status ($RESOURCE_PREFIX) ==="
  echo "PERSISTENT_VOLUME_SIZE_GB=$PERSISTENT_VOLUME_SIZE_GB"
  find_any_worker | python3 -m json.tool 2>/dev/null || find_any_worker
  local vol
  vol=$(aws ec2 describe-volumes \
    --filters "Name=tag:ResourcePrefix,Values=$RESOURCE_PREFIX" "Name=tag:Persistent,Values=true" \
    --query 'Volumes[0].{Id:VolumeId,Size:Size,State:State}' --output json 2>/dev/null || echo '{}')
  echo "Persistent EBS: $vol"
  local id
  id="$(find_running_worker)"
  if [ -n "$id" ] && [ "$id" != "None" ]; then
    echo "SSM:"
    aws ssm describe-instance-information \
      --filters "Key=InstanceIds,Values=$id" \
      --query 'InstanceInformationList[0].{Ping:PingStatus,Platform:PlatformName}' --output json 2>/dev/null || true
    echo "Production light:"
    ssm_status="for u in jobbots-resume-workflow.service jobbots-application.service jobbots-supervisor.timer jobbots-discover-glassdoor.timer jobbots-discover-linkedin.timer jobbots-discover-ats.timer jobbots-discover-jobbank.timer; do printf '%s=' \"\$u\"; systemctl is-active \"\$u\" || true; done"
    bash .github/scripts/ssm-run.sh "$id" "$ssm_status" 30 || true
  fi
}

cmd_die() {
  echo "💀 DIE — destroy ephemeral worker (persistent data/S3/secrets kept)"
  terraform -chdir=terraform init -reconfigure \
    -backend-config="bucket=$TF_STATE_BUCKET" \
    -backend-config="region=$AWS_REGION" \
    -backend-config="key=${RESOURCE_PREFIX}/worker.tfstate"
  terraform -chdir=terraform destroy -auto-approve -lock-timeout=300s
  echo "Worker destroyed. Persistent volume + secrets remain."
}

cmd_born() {
  echo "🐣 BORN — provision worker AMI + bootstrap if stock Ubuntu"
  # Ensure persistent root exists
  terraform -chdir=terraform/persistent init -reconfigure \
    -backend-config="bucket=$TF_STATE_BUCKET" \
    -backend-config="region=$AWS_REGION" \
    -backend-config="key=${RESOURCE_PREFIX}/persistent.tfstate"
  terraform -chdir=terraform/persistent apply -auto-approve -lock-timeout=300s

  local ami_info ami bootstrap_stock
  ami_info="$(resolve_ami)"
  ami="${ami_info%%|*}"
  bootstrap_stock="${ami_info##*|}"
  echo "AMI=$ami bootstrap_stock=$bootstrap_stock"
  bash .github/scripts/write-worker-tfvars.sh "$ami"

  terraform -chdir=terraform init -reconfigure \
    -backend-config="bucket=$TF_STATE_BUCKET" \
    -backend-config="region=$AWS_REGION" \
    -backend-config="key=${RESOURCE_PREFIX}/worker.tfstate"
  terraform -chdir=terraform apply -auto-approve -lock-timeout=300s

  local vm_id
  vm_id="$(terraform -chdir=terraform output -raw vm_id)"
  echo "Worker VM: $vm_id"

  # Wait SSM
  local st="None"
  for i in $(seq 1 40); do
    st=$(aws ssm describe-instance-information \
      --filters "Key=InstanceIds,Values=$vm_id" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo None)
    echo "SSM: $st ($i/40)"
    [ "$st" = "Online" ] && break
    sleep 10
  done
  [ "$st" = "Online" ] || { echo "SSM not online" >&2; exit 1; }

  if [ "$bootstrap_stock" = "1" ] || worker_needs_bootstrap "$vm_id"; then
    echo "Stock/bare worker → bootstrap_stock_worker"
    START_BOTS="${START_BOTS:-0}" NSTBROWSER_ACTIVE_SLOT="$NSTBROWSER_ACTIVE_SLOT" \
      bash scripts/bootstrap_stock_worker.sh "$vm_id"
  else
    echo "Golden image ready — sync current release and lifecycle state"
    START_BOTS="${START_BOTS:-0}" NSTBROWSER_ACTIVE_SLOT="$NSTBROWSER_ACTIVE_SLOT" \
      bash scripts/bootstrap_stock_worker.sh "$vm_id"
  fi
  echo "BORN complete: $vm_id"
}

cmd_sleep() {
  echo "😴 SLEEP — stop instance (EBS persists; compute billing stops)"
  local id="${override_id:-$(find_running_worker)}"
  if [ -z "$id" ] || [ "$id" = "None" ]; then
    # also try stopped/pending lookup
    id=$(aws ec2 describe-instances \
      --filters "Name=tag:ResourcePrefix,Values=$RESOURCE_PREFIX" \
                "Name=tag:Ephemeral,Values=true" \
                "Name=instance-state-name,Values=running,pending" \
      --query 'Reservations[0].Instances[0].InstanceId' --output text)
  fi
  if [ -z "$id" ] || [ "$id" = "None" ]; then
    echo "No running worker to sleep"
    exit 0
  fi
  aws ec2 stop-instances --instance-ids "$id" >/dev/null
  echo "Stopping $id"
  aws ec2 wait instance-stopped --instance-ids "$id" || true
  echo "SLEEP complete: $id"
}

cmd_soft_destroy() {
  echo "SOFT DESTROY — pause compute while keeping the worker and backend reusable"
  cmd_sleep
}

cmd_full_destroy() {
  local confirmation="${FULL_DESTROY_CONFIRM:-${GCP_CONFIRM_FULL_DESTROY:-}}"
  if [ "$confirmation" != "$RESOURCE_PREFIX" ]; then
    echo "Refusing full destroy. Set FULL_DESTROY_CONFIRM=$RESOURCE_PREFIX explicitly." >&2
    exit 2
  fi
  cmd_die
  GCP_CONFIRM_FULL_DESTROY="$confirmation" \
    GCP_RESOURCE_PREFIX="$RESOURCE_PREFIX" \
    GCP_TARGET_ENVIRONMENT="$TARGET_ENVIRONMENT" \
    AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
    AWS_REGION="$AWS_REGION" \
    TF_STATE_BUCKET="$TF_STATE_BUCKET" \
    bash "$ROOT/scripts/destroy_aws_backend.sh"
  echo "FULL DESTROY complete: disposable worker and backend are gone."
}

cmd_preflight() {
  local id="${override_id:-$(find_running_worker)}"
  [ -n "$id" ] && [ "$id" != "None" ] || { echo "No running worker for preflight" >&2; exit 1; }
  bash .github/scripts/ssm-run.sh "$id" \
    'set -euo pipefail; set -a; source /etc/jobbots/runtime.conf; source /etc/jobbots/secrets.env; source /etc/jobbots/runtime-prod-overrides.conf 2>/dev/null || true; set +a; export NSTBROWSER_ACTIVE_SLOT="${NSTBROWSER_ACTIVE_SLOT:-2}"; /opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/verify_linux_vm_runtime.py --bot indeed_it --bot indeed_general --bot glassdoor_it --bot workopolis_it --bot linkedin_general --bot jobbank_it --report-json; cd /opt/jobbots/app && /opt/jobbots/venv/bin/python -m jobbots.app.cli farm-check --live' \
    120
}

cmd_verify_logins() {
  local id="${override_id:-$(find_running_worker)}"
  if [ "${NST_LOGIN_CHECK_CONFIRM:-}" != "$RESOURCE_PREFIX" ]; then
    echo "Refusing quota-consuming login check. Set NST_LOGIN_CHECK_CONFIRM=$RESOURCE_PREFIX explicitly." >&2
    exit 2
  fi
  [ -n "$id" ] && [ "$id" != "None" ] || { echo "No running worker for login verification" >&2; exit 1; }
  bash .github/scripts/ssm-run.sh "$id" \
    'set -euo pipefail; set -a; source /etc/jobbots/runtime.conf; source /etc/jobbots/secrets.env; set +a; exec /opt/jobbots/venv/bin/python -c "import json, sys; from core.session_check import run_preflight_checks; result = run_preflight_checks([\"indeed_it\", \"indeed_general\", \"glassdoor_it\", \"workopolis_it\", \"linkedin_general\", \"jobbank_it\"]); print(json.dumps(result, sort_keys=True)); sys.exit(0 if result.get(\"mongodb\") and result.get(\"nstbrowser_api\") and all(result.get(\"bots\", {}).values()) else 1)"' \
    600
}

cmd_health() {
  local id="${override_id:-$(find_running_worker)}"
  [ -n "$id" ] && [ "$id" != "None" ] || { echo "No running worker for health check" >&2; exit 1; }
  bash .github/scripts/ssm-run.sh "$id" \
    'for unit in jobbots-mongodb.service jobbots-nstbrowser.service jobbots-application.service jobbots-application-general.service; do systemctl is-active --quiet "$unit" || exit 1; done' \
    60
}

cmd_recover() {
  local id="${override_id:-$(find_running_worker)}"
  [ -n "$id" ] && [ "$id" != "None" ] || { echo "No running worker for recovery" >&2; exit 1; }
  bash .github/scripts/ssm-run.sh "$id" \
    'sudo systemctl restart jobbots-mongodb.service jobbots-nstbrowser.service jobbots-application.service jobbots-application-general.service' \
    120
}

cmd_awake() {
  echo "⏰ AWAKE — start stopped worker + ensure stack/code"
  local id="${override_id}"
  if [ -z "$id" ]; then
    id=$(aws ec2 describe-instances \
      --filters "Name=tag:ResourcePrefix,Values=$RESOURCE_PREFIX" \
                "Name=tag:Ephemeral,Values=true" \
                "Name=instance-state-name,Values=stopped,stopping" \
      --query 'Reservations[0].Instances[0].InstanceId' --output text)
  fi
  if [ -z "$id" ] || [ "$id" = "None" ]; then
    # already running?
    id="$(find_running_worker)"
  fi
  if [ -z "$id" ] || [ "$id" = "None" ]; then
    echo "No worker found — use born" >&2
    exit 1
  fi
  local state
  state=$(aws ec2 describe-instances --instance-ids "$id" --query 'Reservations[0].Instances[0].State.Name' --output text)
  if [ "$state" = "stopped" ] || [ "$state" = "stopping" ]; then
    aws ec2 start-instances --instance-ids "$id" >/dev/null
    aws ec2 wait instance-running --instance-ids "$id"
  fi
  local st="None"
  for i in $(seq 1 40); do
    st=$(aws ssm describe-instance-information \
      --filters "Key=InstanceIds,Values=$id" \
      --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo None)
    echo "SSM: $st ($i/40)"
    [ "$st" = "Online" ] && break
    sleep 10
  done
  [ "$st" = "Online" ] || { echo "SSM not online after awake" >&2; exit 1; }

  if worker_needs_bootstrap "$id"; then
    START_BOTS="${START_BOTS:-0}" NSTBROWSER_ACTIVE_SLOT="$NSTBROWSER_ACTIVE_SLOT" \
      bash scripts/bootstrap_stock_worker.sh "$id"
  fi
  echo "AWAKE complete: $id"
}

cmd_run() {
  echo "💡 RUN — ensure worker, sync release, preflight, then start production cycle"
  local id
  id="$(find_running_worker)"
  if [ -z "$id" ] || [ "$id" = "None" ]; then
    local stopped
    stopped="$(find_stopped_worker)"
    if [ -n "$stopped" ] && [ "$stopped" != "None" ]; then
      override_id="$stopped"
      START_BOTS=0 cmd_awake
      # A woken worker keeps its volume, but every production batch still
      # receives the checked-out release before jobs resume.
      START_BOTS=1 cmd_sync
    else
      # A fresh worker bootstraps the checked-out release and starts only after
      # its embedded preflight succeeds.
      START_BOTS=1 cmd_born
    fi
  else
    # Running workers follow the same release → preflight → start path. This
    # makes RUN the single production switch, not a stale-service resume.
    START_BOTS=1 cmd_sync
  fi
}

cmd_sync() {
  echo "⚡ SYNC — code + secrets + restart bots on running worker"
  local id="${override_id:-$(find_running_worker)}"
  if [ -z "$id" ] || [ "$id" = "None" ]; then
    echo "No running worker — awake or born first" >&2
    exit 1
  fi
  if worker_needs_bootstrap "$id"; then
    START_BOTS="${START_BOTS:-1}" NSTBROWSER_ACTIVE_SLOT="$NSTBROWSER_ACTIVE_SLOT" \
      bash scripts/bootstrap_stock_worker.sh "$id"
  else
    # Reuse bootstrap with START_BOTS for secrets+code+services without full provision
    FORCE_PROVISION=0 START_BOTS="${START_BOTS:-1}" NSTBROWSER_ACTIVE_SLOT="$NSTBROWSER_ACTIVE_SLOT" \
      bash scripts/bootstrap_stock_worker.sh "$id"
  fi
  echo "SYNC complete: $id"
}

case "$action" in
  die|destroy) cmd_die ;;
  full-destroy|full_destroy) cmd_full_destroy ;;
  born|create|provision) cmd_born ;;
  sleep|stop) cmd_sleep ;;
  soft-destroy|soft_destroy) cmd_soft_destroy ;;
  awake|wake) cmd_awake ;;
  run|start|go) cmd_run ;;
  sync|fast-sync|deploy-sync) cmd_sync ;;
  status) cmd_status ;;
  preflight) cmd_preflight ;;
  verify-logins|verify_logins) cmd_verify_logins ;;
  health) cmd_health ;;
  recover) cmd_recover ;;
  *)
    echo "Usage: $0 run|sync|die|full-destroy|born|sleep|soft-destroy|awake|status|preflight|verify-logins|health|recover [instance-id]" >&2
    exit 2
    ;;
esac

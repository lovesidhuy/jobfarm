#!/usr/bin/env bash
# Lifecycle controller for the GCP compute / AWS backend hybrid.
# Normal code updates are pulled on the VM from GitHub; initial provisioning
# still uses gcloud transfer and keeps persistent services on AWS.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/cloud_environment.sh"
load_cloud_environment gcp

: "${GCP_PROJECT_ID:=$(gcloud config get-value project 2>/dev/null || true)}"
: "${GCP_REGION:=us-west1}"
: "${GCP_ZONE:=us-west1-a}"
: "${GCP_VM_NAME:=jobbots-gcp-worker}"
: "${GCP_RESOURCE_PREFIX:=${RESOURCE_PREFIX:-jobbots-gcp}}"
: "${TF_STATE_BUCKET:=jobbots-tfstate-bucket}"
: "${AWS_DEFAULT_REGION:=us-west-2}"
: "${GCP_TF_STATE_KEY:=${GCP_RESOURCE_PREFIX}/gcp-worker.tfstate}"
: "${AWS_PERSISTENT_STATE_KEY:=${GCP_RESOURCE_PREFIX}/persistent.tfstate}"
: "${GCP_TARGET_ENVIRONMENT:=${TARGET_ENVIRONMENT:-production}}"
: "${START_BOTS:=1}"
: "${GCP_HEALTH_TIMEOUT_SECONDS:=120}"
: "${GCP_RECOVERY_ATTEMPTS:=2}"
: "${GITHUB_REPOSITORY:=jobfarm/jobfarm}"
: "${GITHUB_BRANCH:=main}"

LIFECYCLE_LOCK_DIR="${JOBBOTS_LIFECYCLE_LOCK_DIR:-${TMPDIR:-/tmp}/jobbots-gcp-lifecycle.lock}"
LIFECYCLE_LOCK_HELD=0

acquire_lifecycle_lock() {
  [ "${JOBBOTS_LIFECYCLE_LOCK_HELD:-0}" = "1" ] && return 0
  if mkdir "$LIFECYCLE_LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$LIFECYCLE_LOCK_DIR/pid"
    export JOBBOTS_LIFECYCLE_LOCK_HELD=1
    LIFECYCLE_LOCK_HELD=1
    trap 'if [ "$LIFECYCLE_LOCK_HELD" = "1" ]; then rm -f "$LIFECYCLE_LOCK_DIR/pid"; rmdir "$LIFECYCLE_LOCK_DIR" 2>/dev/null || true; fi' EXIT
    return 0
  fi

  local owner="unknown"
  [ -f "$LIFECYCLE_LOCK_DIR/pid" ] && owner="$(cat "$LIFECYCLE_LOCK_DIR/pid" 2>/dev/null || true)"
  if [ -n "$owner" ] && ! kill -0 "$owner" 2>/dev/null; then
    rm -f "$LIFECYCLE_LOCK_DIR/pid"
    rmdir "$LIFECYCLE_LOCK_DIR" 2>/dev/null || true
    acquire_lifecycle_lock
    return
  fi
  echo "Another GCP lifecycle operation is already running (pid=$owner)" >&2
  exit 3
}

export CLOUD_PROVIDER=gcp

require_tools() {
  command -v gcloud >/dev/null || { echo "gcloud is required" >&2; exit 1; }
  command -v tar >/dev/null || { echo "tar is required" >&2; exit 1; }
  [ -n "$GCP_PROJECT_ID" ] || { echo "GCP_PROJECT_ID is required" >&2; exit 1; }
}

instance_state() {
  gcloud compute instances describe "$GCP_VM_NAME" \
    --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" \
    --format='value(status)' 2>/dev/null || true
}

wait_for_ssh() {
  for i in $(seq 1 36); do
    if gcloud compute ssh "$GCP_VM_NAME" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" \
      --tunnel-through-iap --quiet --command='true' >/dev/null 2>&1; then
      return 0
    fi
    echo "Waiting for GCP SSH ($i/36)…"
    sleep 10
  done
  echo "GCP VM is not reachable over SSH" >&2
  return 1
}

remote() {
  gcloud compute ssh "$GCP_VM_NAME" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" \
    --tunnel-through-iap --quiet --command="$1"
}

health_check_remote() {
  remote 'set -u
services="jobbots-mongodb.service jobbots-nstbrowser.service jobbots-application.service jobbots-application-general.service"
failed=0
for service in $services; do
  state="$(systemctl is-active "$service" 2>/dev/null || true)"
  printf "%s=%s\n" "$service" "$state"
  [ "$state" = active ] || failed=1
done
exit "$failed"'
}

wait_for_healthy_services() {
  local deadline=$(( $(date +%s) + GCP_HEALTH_TIMEOUT_SECONDS ))
  local health_output=""
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if health_output="$(health_check_remote 2>&1)"; then
      echo "GCP worker health: healthy"
      printf '%s\n' "$health_output"
      return 0
    fi
    sleep 5
  done
  echo "GCP worker health check timed out:" >&2
  printf '%s\n' "$health_output" >&2
  return 1
}

wait_for_startup_remote() {
  local deadline=$(( $(date +%s) + ${GCP_STARTUP_TIMEOUT_SECONDS:-900} ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if remote 'test -f /var/lib/jobbots/gcp-startup.ready && test -f /etc/jobbots/runtime.conf'; then
      echo "GCP startup script: ready"
      return 0
    fi
    echo "Waiting for GCP startup script to finish…"
    sleep 10
  done
  echo "GCP startup script did not become ready" >&2
  return 1
}

recover_services() {
  local attempt
  for attempt in $(seq 1 "$GCP_RECOVERY_ATTEMPTS"); do
    echo "GCP worker health failed; recovery attempt $attempt/$GCP_RECOVERY_ATTEMPTS"
    remote 'sudo systemctl restart jobbots-mongodb.service jobbots-nstbrowser.service jobbots-application.service jobbots-application-general.service 2>/dev/null || true'
    if wait_for_healthy_services; then
      return 0
    fi
  done
  echo "GCP worker did not become healthy after recovery attempts" >&2
  return 1
}

ensure_healthy() {
  wait_for_healthy_services
}

wait_for_nst_api() {
  local attempt
  for attempt in $(seq 1 30); do
    # NST returns 404 on the root route even when the API is healthy; test
    # socket/HTTP readiness without treating that expected status as failure.
    if remote 'curl -sS --max-time 2 -o /dev/null -w "%{http_code}" http://127.0.0.1:8848/ 2>/dev/null | grep -Eq "^[1-4][0-9][0-9]$"'; then
      echo "NSTbrowser API: ready"
      return 0
    fi
    sleep 2
  done
  echo "NSTbrowser API did not become ready" >&2
  return 1
}

stop_farm_remote() {
  # Stop work cleanly before powering the VM down. The VM stop is still
  # attempted if an individual unit no longer exists on an older image.
  remote 'sudo systemctl stop \
    jobbots-artifact-sync.timer jobbots-supervisor.timer \
    jobbots-discover-ats.timer jobbots-discover-glassdoor.timer \
    jobbots-discover-indeed-general.timer jobbots-discover-jobbank.timer \
    jobbots-discover-linkedin.timer jobbots-discover-linkedin-general.timer \
    jobbots-application.service jobbots-application-general.service \
    jobbots-supervisor.service jobbots-resume-workflow.service 2>/dev/null || true'
}

preflight_remote() {
  remote 'set -euo pipefail
    exec sudo bash -c '\''set -a; source /etc/jobbots/runtime.conf; source /etc/jobbots/secrets.env; source /etc/jobbots/runtime-prod-overrides.conf 2>/dev/null || true; set +a; export NSTBROWSER_ACTIVE_SLOT="${NSTBROWSER_ACTIVE_SLOT:-2}"; sudo -E -u ubuntu /opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/verify_linux_vm_runtime.py --bot indeed_it --bot indeed_general --bot glassdoor_it --bot workopolis_it --bot linkedin_general --bot jobbank_it --report-json; cd /opt/jobbots/app && sudo -E -u ubuntu /opt/jobbots/venv/bin/python -m jobbots.app.cli farm-check --live'\'''
}

preflight_before_start() {
  wait_for_nst_api || {
    echo "NST readiness failed; pausing bot services without destroying the worker." >&2
    stop_farm_remote || true
    return 1
  }
  if preflight_remote; then
    return 0
  fi
  echo "NST readiness failed; pausing bot services without destroying the worker." >&2
  stop_farm_remote || true
  return 1
}

verify_logins_remote() {
  remote 'set -euo pipefail
    exec sudo bash -c '\''set -a; source /etc/jobbots/runtime.conf; source /etc/jobbots/secrets.env; set +a; exec sudo -u ubuntu /opt/jobbots/venv/bin/python -c "import json, sys; from core.session_check import run_preflight_checks; result = run_preflight_checks([\\"indeed_it\\", \\"indeed_general\\", \\"glassdoor_it\\", \\"workopolis_it\\", \\"linkedin_general\\"]); print(json.dumps(result, sort_keys=True)); sys.exit(0 if result.get(\\"mongodb\\") and result.get(\\"nstbrowser_api\\") and all(result.get(\\"bots\\", {}).values()) else 1)"'\'''
}

resolve_github_token() {
  if [ -n "${GITHUB_TOKEN:-}" ]; then
    printf '%s' "$GITHUB_TOKEN"
    return 0
  fi
  if [ -n "${GH_TOKEN:-}" ]; then
    printf '%s' "$GH_TOKEN"
    return 0
  fi
  if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
    gh auth token
    return 0
  fi
  echo "A GitHub token is required for remote code pull (set GITHUB_TOKEN or authenticate gh)" >&2
  return 1
}

pull_code_remote() {
  local token remote_script remote_script_file remote_script_name remote_script_path
  token="$(resolve_github_token)"
  [ -n "$token" ] || { echo "GitHub token is empty" >&2; return 1; }
  remote_script="$(cat <<'REMOTE_PULL'
set -euo pipefail
app_dir=/opt/jobbots/app
install -d -o ubuntu -g ubuntu -m 0755 "$app_dir"
cd "$app_dir"
if [ ! -d .git ]; then
  git init -q
  git remote add origin "https://github.com/${repository}.git"
elif ! git remote get-url origin >/dev/null 2>&1; then
  git remote add origin "https://github.com/${repository}.git"
else
  git remote set-url origin "https://github.com/${repository}.git"
fi
auth_origin="https://x-access-token:${git_token}@github.com/${repository}.git"
trap 'git remote set-url origin "https://github.com/${repository}.git" 2>/dev/null || true' EXIT
git remote set-url origin "$auth_origin"
git fetch --prune origin "$branch"
# The initial ON path deploys a git archive, so this directory contains a
# complete *untracked* release when live pull-sync is first used.  A normal
# checkout refuses to overwrite it.  This is a dedicated, disposable code
# checkout (runtime state lives under /srv and /var/lib), so reconcile it to
# the selected remote revision without restarting workers.
had_git_head=0
git rev-parse --verify -q HEAD >/dev/null && had_git_head=1
git reset --hard "origin/$branch"
if [ "$had_git_head" = "1" ]; then
  # This is now a normal checkout. Keep untracked runtime state (especially
  # generated Node dependencies) intact; reset already reconciles every
  # tracked source file and avoids disrupting an in-flight worker.
  :
else
  # The first pull reconciles an archive release, whose source files are all
  # untracked and must yield to the checked-out repository.
  git clean -ffd
fi
git checkout -B "$branch" "origin/$branch"
git config --local core.filemode false
git remote set-url origin "https://github.com/${repository}.git"
# Refresh worker-side helpers and timers from the pulled checkout without
# restarting application/discovery workers. Python processes already running
# finish their current jobs; the next process sees the new code immediately.
if [ -d /opt/jobbots/app/packer/linux/bin ]; then
  sudo install -d -m 0755 /opt/jobbots/bin
  sudo install -m 0755 /opt/jobbots/app/packer/linux/bin/* /opt/jobbots/bin/
fi
if [ -d /opt/jobbots/app/packer/linux/systemd ]; then
  sudo install -m 0644 /opt/jobbots/app/packer/linux/systemd/* /etc/systemd/system/
  sudo install -m 0644 /opt/jobbots/app/packer/linux/runtime-prod-overrides.conf /etc/jobbots/runtime-prod-overrides.conf 2>/dev/null || true
  sudo systemctl daemon-reload
  sudo systemctl enable jobbots-artifact-sync.timer jobbots-report.timer 2>/dev/null || true
  sudo systemctl restart jobbots-artifact-sync.timer jobbots-report.timer 2>/dev/null || true
fi
printf 'code_pull_ok branch=%s commit=%s\n' "$branch" "$(git rev-parse --short HEAD)"
REMOTE_PULL
  )"
  remote_script_file="$(mktemp "${TMPDIR:-/tmp}/jobbots-pull-code.XXXXXX")"
  # Never reuse a fixed /tmp name on the worker. A previous interrupted run
  # may have left a root-owned file behind, which would make a harmless push
  # sync fail before it can clean up.
  remote_script_name="jobbots-pull-code-$(basename "$remote_script_file").sh"
  remote_script_path="/tmp/$remote_script_name"
  {
    printf 'git_token=%q\n' "$token"
    printf 'repository=%q\n' "$GITHUB_REPOSITORY"
    printf 'branch=%q\n' "$GITHUB_BRANCH"
    printf '%s\n' "$remote_script"
  } >"$remote_script_file"
  gcloud compute scp "$remote_script_file" \
    "$GCP_VM_NAME:$remote_script_path" \
    --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" \
    --tunnel-through-iap --quiet
  rm -f "$remote_script_file"
  gcloud compute ssh "$GCP_VM_NAME" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" \
    --tunnel-through-iap --quiet \
    --command="set -e; sudo chown ubuntu:ubuntu '$remote_script_path'; sudo chmod 0700 '$remote_script_path'; sudo -u ubuntu bash '$remote_script_path'; sudo rm -f '$remote_script_path'"
}

package_release() {
  local out="$1"
  mkdir -p "$out/provision/bin" "$out/provision/systemd"
  cp -a packer/linux/bin/. "$out/provision/bin/"
  cp -a packer/linux/systemd/. "$out/provision/systemd/"
  cp packer/linux/runtime-prod-overrides.conf "$out/provision/runtime-prod-overrides.conf"
  cp requirements.txt "$out/provision/requirements.txt"
  cp packer/scripts/provision_linux.sh "$out/provision/provision_linux.sh"
  cat >"$out/provision/run_provision.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
install -d /tmp/bin /tmp/systemd
cp -a /tmp/jobbots-bootstrap/bin/. /tmp/bin/
cp -a /tmp/jobbots-bootstrap/systemd/. /tmp/systemd/
cp /tmp/jobbots-bootstrap/requirements.txt /tmp/requirements.txt
cp /tmp/jobbots-bootstrap/runtime-prod-overrides.conf /tmp/runtime-prod-overrides.conf
bash /tmp/jobbots-bootstrap/provision_linux.sh
EOF
  chmod +x "$out/provision/run_provision.sh" "$out/provision/provision_linux.sh"
  tar -czf "$out/provision.tgz" -C "$out/provision" .

  # Build from Git's object database rather than the checkout filesystem. This
  # remains reliable when stale tracked browser-cache paths are absent on the
  # runner. Secrets are not tracked in this repository; runtime secrets are
  # loaded separately by jobbots-load-secrets.service.
  git archive --format=tar.gz --output="$out/app.tgz" HEAD
  # Read the complete listing so grep does not close the pipe early and make
  # tar report a misleading broken-pipe failure.
  tar -tzf "$out/app.tgz" | grep -x 'automation_monorepo/scripts/verify_linux_vm_runtime.py' >/dev/null
}

sync_runtime_secret_remote() {
  local tmp secret_id remote_tmp
  secret_id="${JOBBOTS_RUNTIME_SECRET:-${GCP_RUNTIME_SECRET_NAME:-}}"
  : "${secret_id:?GCP_RUNTIME_SECRET_NAME is required}"
  tmp="$(mktemp)"
  aws secretsmanager get-secret-value \
    --secret-id "$secret_id" \
    --query SecretString --output text >"$tmp"
  # Never reuse a fixed file in /tmp.  A prior interrupted deployment can
  # leave it root-owned, which prevents the non-root SSH/scp user from
  # refreshing runtime secrets in GitHub Actions.
  remote_tmp="/tmp/jobbots-runtime-secret-${RANDOM}-${RANDOM}.json"
  gcloud compute scp "$tmp" \
    "$GCP_VM_NAME:$remote_tmp" \
    --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" \
    --tunnel-through-iap --quiet
  remote "sudo install -m 0600 -o root -g root '$remote_tmp' /etc/jobbots/runtime-secret.json; sudo rm -f '$remote_tmp'"
  # Reload only the generated secrets environment. Existing workers are not
  # restarted; new timer/worker processes inherit the refreshed values.
  remote 'sudo systemctl restart jobbots-load-secrets.service 2>/dev/null || true'
  echo "GCP runtime secret synchronized through the AWS-backed runner."
}

refresh_runtime_secret_source() {
  # Infisical is the editable source of runtime credentials while AWS Secrets
  # Manager is the worker-delivery store.  Keep them converged on every live
  # sync so adding a provider key never requires a manual VM or AWS edit.
  AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
    GCP_RESOURCE_PREFIX="$GCP_RESOURCE_PREFIX" \
    GCP_TARGET_ENVIRONMENT="$GCP_TARGET_ENVIRONMENT" \
    TF_STATE_BUCKET="$TF_STATE_BUCKET" \
    bash "$ROOT/scripts/ensure_aws_backend.sh" >/dev/null
}

sync_release() {
  local tmp
  tmp="$(mktemp -d)"
  trap 'if [ -n "${tmp:-}" ]; then rm -rf "$tmp"; fi' RETURN
  package_release "$tmp"
  gcloud compute scp "$tmp/provision.tgz" "$tmp/app.tgz" \
    "$GCP_VM_NAME:/tmp/" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" \
    --tunnel-through-iap --quiet
  remote 'set -e
    sudo install -d -m 0755 /tmp/jobbots-bootstrap
    sudo tar -xzf /tmp/provision.tgz -C /tmp/jobbots-bootstrap
    if [ ! -x /opt/jobbots/venv/bin/python ] || ! systemctl list-unit-files | grep -q "^jobbots-nstbrowser.service"; then
      sudo bash /tmp/jobbots-bootstrap/run_provision.sh
    fi
    sudo systemctl stop jobbots-application.service jobbots-application-general.service jobbots-supervisor.service 2>/dev/null || true
    sudo install -d -o ubuntu -g ubuntu -m 0755 /opt/jobbots/app
    sudo tar -xzf /tmp/app.tgz -C /opt/jobbots/app
    sudo chown -R ubuntu:ubuntu /opt/jobbots/app
    sudo /opt/jobbots/venv/bin/python -m pip install -q -r /opt/jobbots/app/requirements.txt
    if [ -f /opt/jobbots/app/legacy/linkedin-ai-auto-apply-source/package-lock.json ]; then
      sudo -u ubuntu npm ci --omit=dev --prefix /opt/jobbots/app/legacy/linkedin-ai-auto-apply-source || true
    fi
    sudo install -d -m 0755 /opt/jobbots/bin
    sudo install -m 0755 /opt/jobbots/app/packer/linux/bin/* /opt/jobbots/bin/
    sudo install -m 0644 /opt/jobbots/app/packer/linux/systemd/* /etc/systemd/system/
    sudo install -m 0644 /opt/jobbots/app/packer/linux/runtime-prod-overrides.conf /etc/jobbots/runtime-prod-overrides.conf
    sudo systemctl daemon-reload
    sudo systemctl restart jobbots-load-secrets.service || true
    sudo systemctl restart jobbots-mongodb.service jobbots-nstbrowser.service jobbots-resume-workflow.service || true
    sudo systemctl restart jobbots-application.service jobbots-application-general.service
    sudo systemctl restart jobbots-supervisor.timer jobbots-discover-linkedin-general.timer jobbots-discover-indeed-general.timer jobbots-discover-ats.timer jobbots-discover-glassdoor.timer jobbots-discover-jobbank.timer || true
    sudo install -d -m 0755 /var/lib/jobbots
    echo "gcp_sync_ok $(date -u +%Y-%m-%dT%H:%M:%SZ)" | sudo tee /var/lib/jobbots/gcp-sync.status >/dev/null'
  echo "GCP release sync complete: $GCP_VM_NAME"
}

cmd_status() {
  printf 'provider=gcp project=%s zone=%s vm=%s state=%s\n' \
    "$GCP_PROJECT_ID" "$GCP_ZONE" "$GCP_VM_NAME" "$(instance_state)"
  if [ "$(instance_state)" = "RUNNING" ]; then
    health_check_remote || true
  fi
}

cmd_run() {
  local state
  state="$(instance_state)"
  if [ "$state" = "TERMINATED" ]; then
    gcloud compute instances start "$GCP_VM_NAME" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" --quiet
    wait_for_ssh
  elif [ "$state" != "RUNNING" ]; then
    echo "GCP VM is not available (state=${state:-missing}); provisioning it now"
    bash "$ROOT/deploy_gcp.sh"
    return 0
  else
    wait_for_ssh
  fi
  refresh_runtime_secret_source
  if ! remote 'test -f /var/lib/jobbots/gcp-startup.ready && test -f /etc/jobbots/runtime.conf'; then
    echo "GCP startup marker missing; reconciling instance metadata and rerunning startup script."
    TARGET_ENVIRONMENT=production DEPLOYMENT_TIER=production SKIP_GCP_LIFECYCLE=1 \
      bash "$ROOT/deploy_gcp.sh" --skip-lifecycle
    wait_for_ssh
    remote 'sudo google_metadata_script_runner startup'
  fi
  wait_for_startup_remote
  if ! remote 'test -f /etc/systemd/system/jobbots-persistent-volume.service'; then
    echo "GCP storage dependency unit missing; installing the self-healing local unit."
    remote 'set -e
      sudo install -d -m 0755 /srv/jobbots/mongodb /srv/jobbots/application /srv/jobbots/nstbrowser
      sudo chown ubuntu:ubuntu /srv/jobbots/application /srv/jobbots/nstbrowser
      sudo tee /usr/local/sbin/jobbots-prepare-local-volume >/dev/null <<"EOF"
#!/usr/bin/env bash
set -euo pipefail
install -d -m 0750 /srv/jobbots/mongodb
install -d -o ubuntu -g ubuntu -m 0750 /srv/jobbots/application /srv/jobbots/nstbrowser
EOF
      sudo chmod 0755 /usr/local/sbin/jobbots-prepare-local-volume
      sudo tee /etc/systemd/system/jobbots-persistent-volume.service >/dev/null <<"EOF"
[Unit]
Description=Prepare ephemeral GCP worker storage
Before=docker.service
After=local-fs.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/jobbots-prepare-local-volume
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
      sudo systemctl daemon-reload
      sudo systemctl enable --now jobbots-persistent-volume.service'
  fi
  # The workflow checkout is the authoritative release for an explicit ON
  # operation. Transfer it directly so startup never depends on a GitHub
  # credential being installed on the worker. The non-interruptive push/sync
  # path still uses pull_code_remote for live code updates.
  echo "Bootstrapping the checked-out production release archive."
  sync_runtime_secret_remote
  sync_release
  preflight_before_start
  ensure_healthy
}

cmd_sync() {
  [ "$(instance_state)" = "RUNNING" ] || { echo "GCP VM must be RUNNING for sync" >&2; exit 1; }
  wait_for_ssh
  refresh_runtime_secret_source
  sync_runtime_secret_remote
  pull_code_remote
  ensure_healthy
}

cmd_bootstrap() {
  [ "$(instance_state)" = "RUNNING" ] || { echo "GCP VM must be RUNNING for bootstrap" >&2; exit 1; }
  wait_for_ssh
  # A newly-created GCP VM may not have AWS credentials in its startup
  # metadata. Transfer the AWS-backed runtime secret before provisioning
  # services, so the first load-secrets run can succeed without credentials
  # living on the worker.
  sync_runtime_secret_remote
  sync_release
  preflight_before_start
  ensure_healthy
}

cmd_awake() {
  [ "$(instance_state)" = "TERMINATED" ] && gcloud compute instances start "$GCP_VM_NAME" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" --quiet
  wait_for_ssh
  echo "GCP VM awake: $GCP_VM_NAME (services remain unchanged; use sync or run to deploy/start)"
}

cmd_sleep() {
  [ "$(instance_state)" = "RUNNING" ] || { echo "GCP VM is not running"; return 0; }
  gcloud compute instances stop "$GCP_VM_NAME" --project="$GCP_PROJECT_ID" --zone="$GCP_ZONE" --quiet
}

cmd_soft_destroy() {
  if [ "$(instance_state)" = "RUNNING" ]; then
    wait_for_ssh
    stop_farm_remote || true
  fi
  cmd_sleep
  echo "Soft destroy complete: compute is off; VM disk, GCP network, and AWS backend remain reusable."
}

destroy_gcp_compute() {
  terraform -chdir=terraform/gcp init -reconfigure -upgrade=false \
    -backend-config="bucket=$TF_STATE_BUCKET" \
    -backend-config="region=$AWS_DEFAULT_REGION" \
    -backend-config="key=$GCP_TF_STATE_KEY"
  terraform -chdir=terraform/gcp destroy -auto-approve -lock-timeout=300s \
    -var="gcp_project_id=$GCP_PROJECT_ID" \
    -var="gcp_region=$GCP_REGION" \
    -var="gcp_zone=$GCP_ZONE" \
    -var="environment=$GCP_TARGET_ENVIRONMENT" \
    -var="deployment_tier=$GCP_TARGET_ENVIRONMENT" \
    -var="resource_prefix=$GCP_RESOURCE_PREFIX" \
    -var="vm_name=$GCP_VM_NAME"
}

cmd_destroy_compute() {
  if [ "${GCP_CONFIRM_DESTROY:-}" != "$GCP_RESOURCE_PREFIX" ]; then
    echo "Refusing GCP destroy. Set GCP_CONFIRM_DESTROY=$GCP_RESOURCE_PREFIX explicitly." >&2
    exit 2
  fi
  destroy_gcp_compute
  echo "Compute destroy complete: AWS backend remains reusable."
}

cmd_full_destroy() {
  if [ "${GCP_CONFIRM_FULL_DESTROY:-}" != "$GCP_RESOURCE_PREFIX" ]; then
    echo "Refusing full destroy. Set GCP_CONFIRM_FULL_DESTROY=$GCP_RESOURCE_PREFIX explicitly." >&2
    exit 2
  fi
  cmd_soft_destroy
  destroy_gcp_compute
  GCP_CONFIRM_FULL_DESTROY="$GCP_CONFIRM_FULL_DESTROY" \
    GCP_RESOURCE_PREFIX="$GCP_RESOURCE_PREFIX" \
    GCP_TARGET_ENVIRONMENT="$GCP_TARGET_ENVIRONMENT" \
    AWS_PERSISTENT_STATE_KEY="$AWS_PERSISTENT_STATE_KEY" \
    AWS_DEFAULT_REGION="$AWS_DEFAULT_REGION" \
    AWS_REGION="$AWS_DEFAULT_REGION" \
    TF_STATE_BUCKET="$TF_STATE_BUCKET" \
    bash "$ROOT/scripts/destroy_aws_backend.sh"
  echo "Full destroy complete: compute and disposable AWS backend are gone."
}

require_tools
action="${1:-status}"
case "$action" in
  run|start|go|sync|deploy-sync|bootstrap|awake|wake|sleep|stop|soft-destroy|soft_destroy|die|destroy|full-destroy|full_destroy|born|create|provision|recover|preflight|verify-logins|verify_logins)
    acquire_lifecycle_lock
    ;;
esac
case "$action" in
  status) cmd_status ;;
  born|create|provision) bash "$ROOT/deploy_gcp.sh" ;;
  bootstrap) cmd_bootstrap ;;
  run|start|go) cmd_run ;;
  sync|deploy-sync) cmd_sync ;;
  awake|wake) cmd_awake ;;
  sleep|stop) cmd_sleep ;;
  soft-destroy|soft_destroy) cmd_soft_destroy ;;
  die|destroy) cmd_destroy_compute ;;
  full-destroy|full_destroy) cmd_full_destroy ;;
  recover)
    [ "$(instance_state)" = "RUNNING" ] || { echo "GCP VM must be RUNNING for recovery" >&2; exit 1; }
    wait_for_ssh
    recover_services
    ;;
  health)
    if [ "$(instance_state)" != "RUNNING" ]; then
      echo "GCP worker is not running; health check unavailable" >&2
      exit 1
    fi
    wait_for_ssh
    ensure_healthy
    ;;
  preflight)
    if [ "$(instance_state)" != "RUNNING" ]; then
      echo "GCP worker is not running; NST preflight unavailable" >&2
      exit 1
    fi
    wait_for_ssh
    preflight_remote
    ;;
  verify-logins|verify_logins)
    if [ "${NST_LOGIN_CHECK_CONFIRM:-}" != "$GCP_RESOURCE_PREFIX" ]; then
      echo "Refusing quota-consuming login check. Set NST_LOGIN_CHECK_CONFIRM=$GCP_RESOURCE_PREFIX explicitly." >&2
      exit 2
    fi
    if [ "$(instance_state)" != "RUNNING" ]; then
      echo "GCP worker is not running; login verification unavailable" >&2
      exit 1
    fi
    wait_for_ssh
    verify_logins_remote
    ;;
  *) echo "Usage: $0 run|sync|bootstrap|awake|sleep|soft-destroy|destroy|full-destroy|status|health|preflight|verify-logins|recover" >&2; exit 2 ;;
esac

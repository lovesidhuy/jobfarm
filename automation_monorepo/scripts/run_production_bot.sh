#!/usr/bin/env bash
set -uo pipefail

# Load configuration and secrets
if [ -r /etc/jobbots/runtime.conf ]; then
  source /etc/jobbots/runtime.conf
fi
if [ -r /etc/jobbots/secrets.env ]; then
  # Remove shell formatting (quotes) if they are in the secrets file
  # since we might want to read them directly or export them.
  # Using source works since secrets.env is already formatted as KEY='val'.
  source /etc/jobbots/secrets.env
fi

# Resolve/generate run ID
run_id="${JOBBOTS_RUN_ID:-}"
if [ -z "$run_id" ]; then
  if [ -f /etc/machine-id ]; then
    run_id="$(cat /etc/machine-id)"
  else
    run_id="run-$(date +%s)"
  fi
fi
export JOBBOTS_RUN_ID="$run_id"
export FORCE_RUN=1
export NODE_OPTIONS="--max-old-space-size=4096"

bot_list="${JOBBOTS_ONLY_BOTS:-indeed_it indeed_general glassdoor_it workopolis_it}"
stop_on_failure="${JOBBOTS_STOP_ON_FAILURE:-1}"

echo "=== Starting production bot cycle: ${bot_list} (sequential) (Run ID: $run_id) ==="
status="failed"
log_dir="/srv/jobbots/application/logs/production"
log_file="${log_dir}/it-bots-${run_id}-$(date -u +%Y%m%dT%H%M%SZ).log"
install -d -o ubuntu -g ubuntu -m 0750 "$log_dir"
install -d -o ubuntu -g ubuntu -m 0750 /srv/jobbots/browser_profiles
install -d -o ubuntu -g ubuntu -m 0750 /opt/jobbots/app/automation_monorepo/data/supervisor

# Run one NST Browser profile at a time. Lower-tier NST plans can reject
# concurrent launches with "exceeded plan limits", so production serializes bots.
set +e
(
  cd /opt/jobbots/app/automation_monorepo || exit 1

  run_bot() {
    bot_name="$1"
    tmp_log="/tmp/${bot_name}.log"
    echo "--- Starting ${bot_name} ---"
    xvfb-run -a --server-args="-screen 0 1280x1024x24" /opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/supervisor.py --only "$bot_name" --once --include-not-ok >"$tmp_log" 2>&1
    bot_exit=$?
    echo "=== ${bot_name} output ==="
    cat "$tmp_log"
    rm -f "$tmp_log"
    echo "--- Finished ${bot_name} with exit code ${bot_exit} ---"
    return "$bot_exit"
  }

  failed=0
  for bot_name in $bot_list; do
    run_bot "$bot_name"
    bot_status=$?
    if [ $bot_status -ne 0 ]; then
      failed=1
      if [ "$stop_on_failure" != "0" ]; then
        echo "Stopping production bot cycle after ${bot_name} failed (JOBBOTS_STOP_ON_FAILURE=${stop_on_failure})."
        break
      fi
    fi
  done

  # Return non-zero if any of the runs failed
  if [ $failed -ne 0 ]; then
    exit 1
  fi
  exit 0
) 2>&1 | tee "$log_file"
exit_code=$?
set -e

if [ $exit_code -eq 0 ]; then
  echo "Production bot cycle completed successfully."
  status="success"
else
  echo "Production bot cycle failed with exit code $exit_code."
  status="failed"
fi

# Upload completion marker to S3
/opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/upload_completion_marker.py "$status" "$run_id"

exit $exit_code

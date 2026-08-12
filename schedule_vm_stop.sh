#!/usr/bin/env bash
# Schedule the VM (AWS EC2 or GCP Compute Engine) to stop after N hours.
#
# Stopping: stops compute billing, KEEPS all disk data/files/configs intact.
# When you restart the VM later, everything is exactly as you left it.
#
# Usage:
#   ./schedule_vm_stop.sh 6                      # stop AWS VM in 6 hours (default)
#   ./schedule_vm_stop.sh 6 cancel               # cancel pending stop schedule
#   ./schedule_vm_stop.sh 6 schedule --provider gcp  # stop GCP VM in 6 hours
#   CLOUD_PROVIDER=gcp ./schedule_vm_stop.sh 6   # stop GCP VM via env var
#
# This runs in the background on YOUR Mac, so don't close your Mac/laptop.

set -euo pipefail

HOURS="${1:-6}"
ACTION="${2:-schedule}"
PROVIDER="${CLOUD_PROVIDER:-aws}"

# Parse optional --provider argument if supplied in positional params
shift 2 2>/dev/null || shift 1 2>/dev/null || true
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
        *)
            shift 1
            ;;
    esac
done

PROVIDER=$(echo "$PROVIDER" | tr '[:upper:]' '[:lower:]')
VM="${GCP_VM_NAME:-jobbots-dev-vm}"
PIDFILE="/tmp/vm_stop_${PROVIDER}.pid"
LOGFILE="/tmp/vm_stop_${PROVIDER}.log"

if [[ "$ACTION" == "cancel" ]]; then
    if [[ -f "$PIDFILE" ]]; then
        PID=$(cat "$PIDFILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID" && echo "Cancelled scheduled stopping for $PROVIDER VM (pid $PID)"
            rm -f "$PIDFILE"
        else
            echo "No active scheduler for $PROVIDER (pid $PID dead). Cleaning up."
            rm -f "$PIDFILE"
        fi
    else
        echo "No scheduled stop for $PROVIDER to cancel."
    fi
    exit 0
fi

# Cancel any prior schedule for this provider
if [[ -f "$PIDFILE" ]]; then
    OLD=$(cat "$PIDFILE")
    if kill -0 "$OLD" 2>/dev/null; then
        kill "$OLD" 2>/dev/null && echo "Cancelled previous scheduler for $PROVIDER (pid $OLD)"
    fi
    rm -f "$PIDFILE"
fi

# Support fractional hours (e.g., 6.5)
SECS=$(awk -v h="$HOURS" 'BEGIN { printf "%d", h * 3600 }')
MINS=$(awk -v h="$HOURS" 'BEGIN { printf "%d", h * 60 }')
FIRES_AT=$(date -v +"${MINS}M" "+%Y-%m-%d %H:%M:%S" 2>/dev/null || date -d "+$MINS minutes" "+%Y-%m-%d %H:%M:%S")

INSTANCE_ID=""
ZONE=""
PROJECT="${GCP_PROJECT_ID:-}"

if [[ "$PROVIDER" == "aws" ]]; then
    echo "Querying AWS EC2 for Instance ID of VM named '$VM'..."
    INSTANCE_ID=$(aws ec2 describe-instances \
        --filters "Name=tag:Name,Values=$VM" "Name=instance-state-name,Values=running" \
        --query "Reservations[*].Instances[*].InstanceId" \
        --output text || true)

    if [[ -z "$INSTANCE_ID" ]]; then
        echo "Error: No running AWS EC2 instance with name tag '$VM' found."
        exit 1
    fi
elif [[ "$PROVIDER" == "gcp" ]]; then
    echo "Querying GCP Compute Engine for VM named '$VM'..."
    if [[ -z "$PROJECT" ]]; then
        PROJECT=$(gcloud config get-value project 2>/dev/null || true)
    fi
    
    VM_INFO=$(gcloud compute instances list --filter="name=$VM AND status=RUNNING" --format="csv[no-heading](name,zone)" 2>/dev/null || true)
    if [[ -n "$VM_INFO" ]]; then
        VM_NAME=$(echo "$VM_INFO" | cut -d',' -f1)
        ZONE=$(echo "$VM_INFO" | cut -d',' -f2)
        INSTANCE_ID="$VM_NAME"
    fi

    if [[ -z "$INSTANCE_ID" ]]; then
        echo "Error: No running GCP Compute Engine instance named '$VM' found."
        exit 1
    fi
else
    echo "Error: Unsupported provider '$PROVIDER'. Use 'aws' or 'gcp'."
    exit 1
fi

echo "============================================="
echo " VM Stop Scheduler ($PROVIDER)"
echo "============================================="
echo "Provider:     $PROVIDER"
echo "VM Name:      $VM"
echo "Instance ID:  $INSTANCE_ID"
if [[ -n "$ZONE" ]]; then
echo "GCP Zone:     $ZONE"
fi
echo "Sleeping for: ${HOURS}h (${SECS}s)"
echo "Will stop at: $FIRES_AT"
echo ""
echo "What happens:"
echo "  ✓ All disk data preserved"
echo "  🛑 Compute billing STOPS"
echo ""
echo "To cancel:  ./schedule_vm_stop.sh $HOURS cancel --provider $PROVIDER"
echo "Logs:       tail -f $LOGFILE"
echo ""

# Spawn background process
(
    echo "[$(date)] Sleeping ${SECS}s until $PROVIDER VM stops at $FIRES_AT" > "$LOGFILE"
    sleep "$SECS"
    if [[ "$PROVIDER" == "aws" ]]; then
        echo "[$(date)] Triggering aws ec2 stop-instances..." >> "$LOGFILE"
        aws ec2 stop-instances --instance-ids "$INSTANCE_ID" >> "$LOGFILE" 2>&1
    elif [[ "$PROVIDER" == "gcp" ]]; then
        echo "[$(date)] Triggering gcloud compute instances stop..." >> "$LOGFILE"
        gcloud compute instances stop "$INSTANCE_ID" --zone="$ZONE" ${PROJECT:+--project="$PROJECT"} >> "$LOGFILE" 2>&1
    fi
    echo "[$(date)] Stop command executed. Compute billing stopped." >> "$LOGFILE"
    rm -f "$PIDFILE"
) &

STOP_PID=$!
echo "$STOP_PID" > "$PIDFILE"
disown "$STOP_PID"

echo "Scheduler running (pid $STOP_PID). You can close this terminal."

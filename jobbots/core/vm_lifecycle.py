"""VM Lifecycle Automation for AWS EC2 instance.

Allows the orchestrator to automatically stop the VM instance after completing
daily application cycles to reduce hosting costs.
"""

from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT

# Add project root to path
base_dir = _MONOREPO_ROOT
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from jobbots.core.secret_manager import get_secret
from jobbots.core.alerts import send_telegram_alert

# Prefer production ephemeral worker tags; fall back to legacy name.
RESOURCE_PREFIX = os.getenv("JOBBOTS_RESOURCE_PREFIX") or os.getenv("RESOURCE_PREFIX") or "jobbots-production-13"
VM_NAME = os.getenv("JOBBOTS_VM_NAME") or f"{RESOURCE_PREFIX}-worker"


def get_instance_id_boto3() -> str | None:
    """Resolve instance ID using boto3 (ResourcePrefix ephemeral, then Name tag)."""
    try:
        import boto3
        region = get_secret("AWS_DEFAULT_REGION", "us-west-2")
        ec2 = boto3.client("ec2", region_name=region)
        # Production path: ephemeral worker for resource prefix
        response = ec2.describe_instances(
            Filters=[
                {"Name": "tag:ResourcePrefix", "Values": [RESOURCE_PREFIX]},
                {"Name": "tag:Ephemeral", "Values": ["true"]},
                {"Name": "instance-state-name", "Values": ["running", "stopped", "stopping"]},
            ]
        )
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                return instance.get("InstanceId")
        # Legacy name tag
        response = ec2.describe_instances(
            Filters=[
                {"Name": "tag:Name", "Values": [VM_NAME, "jobbots-dev-vm"]},
                {"Name": "instance-state-name", "Values": ["running", "stopped", "stopping"]},
            ]
        )
        for reservation in response.get("Reservations", []):
            for instance in reservation.get("Instances", []):
                return instance.get("InstanceId")
    except Exception as e:
        print(f"[VMLifecycle] boto3 describe-instances failed: {e}")
    return None


def get_instance_id_cli() -> str | None:
    """Resolve instance ID using the AWS CLI."""
    try:
        region = get_secret("AWS_DEFAULT_REGION", "us-west-2")
        for filters in (
            [
                f"Name=tag:ResourcePrefix,Values={RESOURCE_PREFIX}",
                "Name=tag:Ephemeral,Values=true",
            ],
            [f"Name=tag:Name,Values={VM_NAME}"],
        ):
            cmd = [
                "aws", "ec2", "describe-instances",
                "--region", region,
                "--filters", *filters,
                "--query", "Reservations[0].Instances[0].InstanceId",
                "--output", "text",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if res.returncode == 0 and res.stdout.strip() and res.stdout.strip() != "None":
                return res.stdout.strip()
    except Exception as e:
        print(f"[VMLifecycle] AWS CLI describe-instances failed: {e}")
    return None


def resolve_instance_id() -> str | None:
    """Resolve the EC2 instance ID for the production ephemeral worker."""
    instance_id = get_instance_id_boto3()
    if instance_id:
        return instance_id
    return get_instance_id_cli()


def stop_vm() -> bool:
    """Trigger AWS EC2 stop-instances for the VM."""
    print(f"[VMLifecycle] Attempting to stop VM instance '{VM_NAME}'...")
    
    # 1. Resolve instance ID
    instance_id = resolve_instance_id()
    if not instance_id:
        print(f"[VMLifecycle] Error: Could not resolve instance ID for VM '{VM_NAME}'.")
        return False
        
    print(f"[VMLifecycle] Resolved VM Instance ID: {instance_id}")
    
    # Send alert before shutting down
    send_telegram_alert(
        f"🔌 Triggering VM stop sequence for {VM_NAME} ({instance_id}) to stop compute billing...",
        bot_name="system",
        alert_type="vm_shutdown",
        force=True
    )
    
    # Try boto3 first
    try:
        import boto3
        region = get_secret("AWS_DEFAULT_REGION", "us-west-2")
        ec2 = boto3.client("ec2", region_name=region)
        ec2.stop_instances(InstanceIds=[instance_id])
        print(f"[VMLifecycle] boto3 successfully triggered stop-instances for {instance_id}")
        return True
    except Exception as e:
        print(f"[VMLifecycle] boto3 stop-instances failed: {e}. Trying CLI fallback...")

    # Try AWS CLI
    try:
        region = get_secret("AWS_DEFAULT_REGION", "us-west-2")
        cmd = ["aws", "ec2", "stop-instances", "--region", region, "--instance-ids", instance_id]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0:
            print(f"[VMLifecycle] AWS CLI successfully triggered stop-instances for {instance_id}")
            return True
        else:
            print(f"[VMLifecycle] AWS CLI stop-instances failed with code {res.returncode}: {res.stderr}")
    except Exception as e:
        print(f"[VMLifecycle] AWS CLI stop-instances invocation failed: {e}")

    # Local VM shutdown commands (as extreme fallback)
    print("[VMLifecycle] AWS commands failed. Note: if running locally, VM stop will be skipped.")
    return False


if __name__ == "__main__":
    stop_vm()

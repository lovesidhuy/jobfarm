#!/usr/bin/env python3
import os
import sys
import json
import hmac
import hashlib
import boto3
from datetime import datetime, timezone

def main():
    if len(sys.argv) < 2:
        print("Usage: upload_completion_marker.py <status> [run_id]")
        sys.exit(1)

    status = sys.argv[1]
    if status not in ("success", "failed"):
        print(f"Invalid status: {status}. Must be 'success' or 'failed'")
        sys.exit(1)

    # Resolve run ID
    if len(sys.argv) >= 3:
        run_id = sys.argv[2]
    else:
        # Fallback to reading machine-id or env
        run_id = os.environ.get("JOBBOTS_RUN_ID")
        if not run_id:
            try:
                with open("/etc/machine-id", "r") as f:
                    run_id = f.read().strip()
            except Exception:
                run_id = "unknown_run"

    # Resolve NSTBROWSER_API_KEY
    nstbrowser_api_key = os.environ.get("NSTBROWSER_API_KEY")
    if not nstbrowser_api_key:
        # Try loading from /etc/jobbots/secrets.env if present
        try:
            with open("/etc/jobbots/secrets.env", "r") as f:
                for line in f:
                    if line.startswith("NSTBROWSER_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        # Strip quotes if any
                        if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
                            val = val[1:-1]
                        nstbrowser_api_key = val
                        break
        except Exception:
            pass

    if not nstbrowser_api_key:
        print("Error: NSTBROWSER_API_KEY not found in environment or /etc/jobbots/secrets.env")
        sys.exit(1)

    bucket = os.environ.get("JOBBOTS_ARTIFACT_BUCKET")
    prefix = os.environ.get("JOBBOTS_ARTIFACT_PREFIX")
    if not bucket or not prefix:
        # Try loading from /etc/jobbots/runtime.conf
        try:
            with open("/etc/jobbots/runtime.conf", "r") as f:
                for line in f:
                    if line.startswith("JOBBOTS_ARTIFACT_BUCKET="):
                        bucket = line.split("=", 1)[1].strip()
                    elif line.startswith("JOBBOTS_ARTIFACT_PREFIX="):
                        prefix = line.split("=", 1)[1].strip()
        except Exception:
            pass

    if not bucket or not prefix:
        print("Error: JOBBOTS_ARTIFACT_BUCKET or JOBBOTS_ARTIFACT_PREFIX not set")
        sys.exit(1)

    timestamp = datetime.now(timezone.utc).isoformat()
    
    # Calculate signature: HMAC-SHA256(key=nstbrowser_api_key, msg="run_id:timestamp:status")
    message = f"{run_id}:{timestamp}:{status}"
    sig = hmac.new(
        nstbrowser_api_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    marker = {
        "run_id": run_id,
        "timestamp": timestamp,
        "status": status,
        "ref": os.environ.get("GITHUB_REF_NAME", "main"),
        "signature": sig
    }

    s3_key = f"{prefix}/completion/marker-{run_id}.json"
    print(f"Uploading completion marker to s3://{bucket}/{s3_key}...")

    s3 = boto3.client("s3")
    try:
        s3.put_object(
            Bucket=bucket,
            Key=s3_key,
            Body=json.dumps(marker, indent=2),
            ContentType="application/json"
        )
        print("Upload successful.")
    except Exception as e:
        print(f"Error uploading to S3: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()

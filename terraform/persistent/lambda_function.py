import os
import json
import hmac
import hashlib
import urllib.request
import urllib.parse
import boto3

s3_client = boto3.client("s3")
secrets_client = boto3.client("secretsmanager")

def get_secret(secret_name):
    try:
        res = secrets_client.get_secret_value(SecretId=secret_name)
        if "SecretString" in res:
            return json.loads(res["SecretString"])
    except Exception as e:
        print(f"Error fetching secret {secret_name}: {e}")
    return {}

def trigger_github_workflow(owner, repo, workflow_id, github_token, ref, inputs):
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches"
    data = json.dumps({
        "ref": ref,
        "inputs": inputs
    }).encode("utf-8")
    
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "AWS-Lambda-Jobbots-Trigger"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            status = response.status
            body = response.read().decode("utf-8")
            print(f"GitHub workflow dispatch triggered successfully. Status: {status}, Response: {body}")
            return True
    except urllib.error.HTTPError as e:
        print(f"HTTP Error triggering GitHub workflow: {e.code} {e.reason}")
        print(e.read().decode("utf-8"))
    except Exception as e:
        print(f"Error triggering GitHub workflow: {e}")
    return False

def lambda_handler(event, context):
    print("Received event:", json.dumps(event))
    
    # Extract bucket and key from the event
    record = event["Records"][0]
    bucket = record["s3"]["bucket"]["name"]
    key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
    
    print(f"Processing marker file from s3://{bucket}/{key}")
    
    if "completion/marker-" not in key:
        print(f"Skipping non-marker key: {key}")
        return {"statusCode": 200, "body": "Not a completion marker"}
    
    # Fetch marker from S3
    try:
        obj = s3_client.get_object(Bucket=bucket, Key=key)
        marker = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception as e:
        print(f"Error reading object from S3: {e}")
        return {"statusCode": 500, "body": "Failed to read completion marker from S3"}
        
    run_id = marker.get("run_id")
    timestamp = marker.get("timestamp")
    status = marker.get("status")
    signature = marker.get("signature")
    ref = marker.get("ref", "main")
    
    if not all([run_id, timestamp, status, signature]):
        print("Error: Missing required fields in marker")
        return {"statusCode": 400, "body": "Invalid completion marker format"}
        
    # Reconstruct the resource prefix from environment variable or key prefix
    resource_prefix = os.environ.get("RESOURCE_PREFIX")
    if not resource_prefix:
        parts = key.split("/")
        if len(parts) < 3:
            print("Error: S3 key structure is not compatible")
            return {"statusCode": 400, "body": "Invalid S3 key structure"}
        resource_prefix = parts[0]
        
    secret_name = f"{resource_prefix}/runtime"
    
    # Fetch secrets
    secrets = get_secret(secret_name)
    nstbrowser_api_key = secrets.get("NSTBROWSER_API_KEY")
    github_token = secrets.get("GITHUB_PAT") or secrets.get("GITHUB_TOKEN")
    
    if not nstbrowser_api_key:
        print(f"Error: NSTBROWSER_API_KEY not found in secret {secret_name}")
        return {"statusCode": 500, "body": "Configuration secret not found"}
        
    if not github_token:
        print(f"Error: GITHUB_TOKEN/GITHUB_PAT not found in secret {secret_name}")
        return {"statusCode": 500, "body": "GitHub token not found"}
        
    # Verify signature
    message = f"{run_id}:{timestamp}:{status}"
    expected_sig = hmac.new(
        nstbrowser_api_key.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    
    if not hmac.compare_digest(signature, expected_sig):
        print("Error: Invalid completion marker signature")
        return {"statusCode": 403, "body": "Invalid signature"}
        
    print("Signature verified successfully.")
    
    # Trigger production-cleanup.yml workflow
    owner = os.environ.get("GITHUB_OWNER", "YOUR_GITHUB_USERNAME")
    repo = os.environ.get("GITHUB_REPO", "jobfarm")
    workflow_id = os.environ.get("GITHUB_CLEANUP_WORKFLOW", "production-cleanup.yml")
    
    inputs = {
        "run_id": run_id,
        "completion_marker_key": key,
        "status": status,
        "bucket": bucket
    }
    
    success = trigger_github_workflow(owner, repo, workflow_id, github_token, ref, inputs)
    if success:
        return {"statusCode": 200, "body": "Successfully triggered cleanup workflow"}
    else:
        return {"statusCode": 500, "body": "Failed to trigger cleanup workflow"}

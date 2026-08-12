#!/usr/bin/env bash
# Bootstrap a stock Ubuntu worker (no golden AMI) then sync app + secrets.
# Safe to re-run: skips apt/docker provision when /opt/jobbots/venv exists.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ "${CLOUD_PROVIDER:-aws}" = "gcp" ]; then
  exec bash "$ROOT/scripts/gcp_lifecycle.sh" sync "${1:-}"
fi

: "${RESOURCE_PREFIX:=jobbots-production-13}"
: "${AWS_REGION:=us-west-2}"
: "${AWS_DEFAULT_REGION:=$AWS_REGION}"
: "${ARTIFACT_PREFIX:=production/deploy}"
: "${INFISICAL_PROJECT_ID:=a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe}"
: "${INFISICAL_ENV:=dev}"
: "${NSTBROWSER_ACTIVE_SLOT:=1}"
: "${START_BOTS:=1}"

vm_id="${1:-}"
if [ -z "$vm_id" ] || [ "$vm_id" = "None" ]; then
  vm_id="$(aws ec2 describe-instances \
    --filters "Name=tag:ResourcePrefix,Values=$RESOURCE_PREFIX" \
              "Name=tag:Ephemeral,Values=true" \
              "Name=instance-state-name,Values=running" \
    --query 'Reservations[0].Instances[0].InstanceId' --output text)"
fi
if [ -z "$vm_id" ] || [ "$vm_id" = "None" ]; then
  echo "No running worker for $RESOURCE_PREFIX" >&2
  exit 1
fi
echo "Worker VM: $vm_id"

artifact_bucket="$(aws s3api list-buckets \
  --query "Buckets[?starts_with(Name, \`${RESOURCE_PREFIX}-artifacts-\`)].Name | [0]" \
  --output text)"
if [ -z "$artifact_bucket" ] || [ "$artifact_bucket" = "None" ]; then
  echo "Artifact bucket not found for $RESOURCE_PREFIX" >&2
  exit 1
fi
echo "Artifact bucket: $artifact_bucket"

ssm() {
  # usage: ssm "cmd" [max_polls]
  bash .github/scripts/ssm-run.sh "$vm_id" "$1" "${2:-60}"
}

# Capture only the remote script stdout body (between === STDOUT === and === STDERR ===)
ssm_out() {
  local full
  full="$(bash .github/scripts/ssm-run.sh "$vm_id" "$1" "${2:-60}" 2>/dev/null || true)"
  printf '%s\n' "$full" | awk '
    /^=== STDOUT ===$/ {p=1; next}
    /^=== STDERR ===$/ {p=0}
    p {print}
  ' | tr -d '\r'
}

# --- 1) Package provision assets + app ---
echo "[1/8] Packaging bootstrap + app tarballs..."
export COPYFILE_DISABLE=1
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/provision/bin" "$tmp/provision/systemd"
cp -a packer/linux/bin/. "$tmp/provision/bin/"
cp -a packer/linux/systemd/. "$tmp/provision/systemd/"
cp packer/linux/runtime-prod-overrides.conf "$tmp/provision/runtime-prod-overrides.conf"
cp requirements.txt "$tmp/provision/requirements.txt"
cp packer/scripts/provision_linux.sh "$tmp/provision/provision_linux.sh"
# provision_linux expects /tmp/bin, /tmp/systemd, /tmp/requirements.txt
cat >"$tmp/provision/run_provision.sh" <<'EOS'
#!/usr/bin/env bash
set -euo pipefail
install -d /tmp/bin /tmp/systemd
cp -a /tmp/jobbots-bootstrap/bin/. /tmp/bin/
cp -a /tmp/jobbots-bootstrap/systemd/. /tmp/systemd/
cp /tmp/jobbots-bootstrap/requirements.txt /tmp/requirements.txt
cp /tmp/jobbots-bootstrap/runtime-prod-overrides.conf /tmp/runtime-prod-overrides.conf 2>/dev/null || true
bash /tmp/jobbots-bootstrap/provision_linux.sh
EOS
chmod +x "$tmp/provision/run_provision.sh" "$tmp/provision/provision_linux.sh"
tar -czf "$tmp/provision.tgz" -C "$tmp/provision" .

# Package only tracked working-tree source.  This preserves local edits to
# tracked release files while preventing backups, browser data, exports, and
# other untracked workstation artifacts from being uploaded to production.
# Exclude heavy/runtime-only trees so packaging stays seconds, not minutes.
# ``git ls-files`` can include a tracked file deleted in the working tree;
# filter those paths so a legitimate local deletion cannot abort a release.
# Portable across macOS bsdtar and GNU tar (no --ignore-failed-read).
git ls-files -z | python3 -c '
import os, sys
for raw in sys.stdin.buffer.read().split(b"\0"):
    if not raw:
        continue
    try:
        path = raw.decode()
    except Exception:
        continue
    if os.path.lexists(path):
        sys.stdout.buffer.write(raw + b"\0")
' | tar --null -czf "$tmp/app.tgz" \
  --exclude='*/node_modules' \
  --exclude='*/.venv' \
  --exclude='*/venv' \
  --exclude='*/__pycache__' \
  --exclude='*/.pytest_cache' \
  --exclude='*/.ruff_cache' \
  --exclude='*/browser_profiles' \
  --exclude='*/chrome-profile*' \
  --exclude='*/logs' \
  --exclude='*/artifacts' \
  --exclude='*/backups' \
  --exclude='*/trial_data' \
  --exclude='*/scratch' \
  --exclude='*/data' \
  --exclude='*/outputs' \
  --exclude='*.env' \
  --exclude='*.env.*' \
  --exclude='*/test_profile' \
  --exclude='*/applied_jobs.csv' \
  --exclude='*.zip' \
  --exclude='*/.git' \
  --files-from=-

# Fail before upload if the release archive does not contain the current
# discovery gate. Canonical path is jobbots/core after Phase-2 refactor;
# keep the legacy monorepo path as a fallback for older checkouts.
planner_ok=0
if tar -xOzf "$tmp/app.tgz" jobbots/core/discovery/planner.py 2>/dev/null \
  | grep -q 'general profile deterministic company-site save'; then
  planner_ok=1
elif tar -xOzf "$tmp/app.tgz" automation_monorepo/core/discovery/planner.py 2>/dev/null \
  | grep -q 'general profile deterministic company-site save'; then
  planner_ok=1
fi
if [ "$planner_ok" != "1" ]; then
  echo "Release archive missing discovery planner gate marker" >&2
  exit 1
fi
# Farm productivity contract must ship (ephemeral rebuilds depend on it).
tar -tzf "$tmp/app.tgz" | grep -q 'jobbots/app/farm_check.py' \
  || { echo "Release archive missing jobbots/app/farm_check.py" >&2; exit 1; }

aws s3 cp "$tmp/provision.tgz" "s3://${artifact_bucket}/${ARTIFACT_PREFIX}/bootstrap/provision.tgz" --only-show-errors
aws s3 cp "$tmp/app.tgz" "s3://${artifact_bucket}/${ARTIFACT_PREFIX}/deploy/travis-update.tar.gz" --only-show-errors

# --- 2) Wait SSM ---
echo "[2/8] Waiting for SSM Online..."
for i in $(seq 1 40); do
  st="$(aws ssm describe-instance-information \
    --filters "Key=InstanceIds,Values=$vm_id" \
    --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || echo None)"
  echo "  SSM: $st ($i/40)"
  [ "$st" = "Online" ] && break
  sleep 10
done
[ "$st" = "Online" ] || { echo "SSM not online" >&2; exit 1; }

# --- 2b) Ensure AWS CLI on stock AMI (IMDS role credentials) ---
echo "[2b/8] Ensuring AWS CLI on worker..."
ssm 'set -eu
if command -v aws >/dev/null 2>&1 || [ -x /usr/local/bin/aws ]; then
  echo aws_present
  exit 0
fi
export DEBIAN_FRONTEND=noninteractive
for i in $(seq 1 30); do
  if ! sudo fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; then
    break
  fi
  echo "Waiting for apt/dpkg lock..."
  sleep 5
done
sudo apt-get update -qq
sudo apt-get install -y -qq unzip curl ca-certificates
cd /tmp
curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o awscliv2.zip
unzip -q awscliv2.zip
sudo ./aws/install
rm -rf aws awscliv2.zip
/usr/local/bin/aws --version
' 120

# --- 3) Provision stack if missing ---
echo "[3/8] Provisioning host stack if needed..."
needs_provision="$(ssm_out 'if [ -x /opt/jobbots/venv/bin/python ] && systemctl list-unit-files 2>/dev/null | grep -q "^jobbots-nstbrowser.service"; then echo no; else echo yes; fi' | grep -E '^(yes|no)$' | head -1 || true)"
if [ -z "$needs_provision" ]; then
  needs_provision=yes
fi
# FORCE_PROVISION=1 always re-runs packer provision_linux
if [ "${FORCE_PROVISION:-0}" = "1" ]; then
  needs_provision=yes
fi
echo "  needs_provision=$needs_provision"
if [ "$needs_provision" = "yes" ]; then
  echo "  Running provision_linux (apt/docker/playwright) — may take 15–30m..."
  # Long SSM: install stack. Use PATH with /usr/local/bin for aws. max_polls=480 ≈ 40m
  ssm "set -eu; export PATH=/usr/local/bin:/usr/bin:\$PATH; tmp=\$(mktemp -d); aws s3 cp 's3://${artifact_bucket}/${ARTIFACT_PREFIX}/bootstrap/provision.tgz' \"\$tmp/p.tgz\" --only-show-errors; sudo rm -rf /tmp/jobbots-bootstrap; sudo mkdir -p /tmp/jobbots-bootstrap; sudo tar -xzf \"\$tmp/p.tgz\" -C /tmp/jobbots-bootstrap; sudo bash /tmp/jobbots-bootstrap/run_provision.sh; rm -rf \"\$tmp\"" 480
else
  echo "  Stack already present — skip full provision"
fi

# --- 4) Runtime secrets from Infisical (+ dual NST slot for prod) ---
echo "[4/8] Writing production runtime secret (Infisical export)..."
secret_id="$(aws secretsmanager list-secrets \
  --filters Key=tag-key,Values=ResourcePrefix Key=tag-value,Values="$RESOURCE_PREFIX" \
  --query 'SecretList[0].ARN' --output text)"
if [ -z "$secret_id" ] || [ "$secret_id" = "None" ]; then
  secret_id="jobbots-production-13/runtime"
fi

# Build runtime secret JSON:
# 1) Infisical CLI export when available (local ops)
# 2) Else Travis/env vars (CI has NST_* / INFISICAL_* / PROXY_* as secure env)
# 3) Merge onto any existing Secrets Manager payload so we don't wipe keys
python3 - "$tmp/secret.json" "$NSTBROWSER_ACTIVE_SLOT" "$INFISICAL_ENV" "$INFISICAL_PROJECT_ID" "$secret_id" <<'PY'
import json, os, subprocess, sys
from pathlib import Path

dst = Path(sys.argv[1])
active_slot = sys.argv[2]
inf_env = sys.argv[3]
inf_proj = sys.argv[4]
secret_name = sys.argv[5]
data: dict = {}
existing_slot2_profile_ids: dict[str, str] = {}

# Prefer existing secret so re-sync is additive
try:
    existing = subprocess.check_output(
        [
            "aws", "secretsmanager", "get-secret-value",
            "--secret-id", secret_name,
            "--query", "SecretString", "--output", "text",
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()
    if existing and existing not in ("None", ""):
        data.update(json.loads(existing))
        # The worker's local NST API is the source of truth after a profile
        # migration. Keep its verified Slot 2 IDs stable across ordinary
        # Infisical/CI exports; an intentional profile rotation must opt in.
        existing_slot2_profile_ids = {
            key: str(value)
            for key, value in data.items()
            if key.startswith("NSTBROWSER_PROFILE_ID_2_") and str(value).strip()
        }
except Exception:
    pass

# Infisical CLI (optional)
if subprocess.call(["bash", "-lc", "command -v infisical >/dev/null"], stdout=subprocess.DEVNULL) == 0 and inf_proj:
    try:
        raw = subprocess.check_output(
            [
                "infisical", "export",
                f"--env={inf_env}",
                f"--projectId={inf_proj}",
                "--format=dotenv",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            data[k] = v
        print("secrets: infisical export ok", len(data))
    except Exception as e:
        print("secrets: infisical export skipped:", e)

# Overlay from process env (Travis secure vars / local export)
ENV_KEYS = [
    "NSTBROWSER_API_KEY", "NSTBROWSER_API_KEY_2",
    "NSTBROWSER_PROFILE_ID_INDEED_IT", "NSTBROWSER_PROFILE_ID_INDEED_GENERAL",
    "NSTBROWSER_PROFILE_ID_GLASSDOOR_IT", "NSTBROWSER_PROFILE_ID_GLASSDOOR_GENERAL",
    "NSTBROWSER_PROFILE_ID_WORKOPOLIS_IT", "NSTBROWSER_PROFILE_ID_WORKOPOLIS_GENERAL",
    "NSTBROWSER_PROFILE_ID_LINKEDIN_IT", "NSTBROWSER_PROFILE_ID_LINKEDIN_GENERAL",
    "NSTBROWSER_PROFILE_ID_LINKEDIN_DISCOVERY",
    "NSTBROWSER_PROFILE_ID_LINKEDIN_DISCOVERY_IT",
    "NSTBROWSER_PROFILE_ID_GOOGLE_IT",
    "NSTBROWSER_PROFILE_ID_JOBBANK_IT",
    "NSTBROWSER_PROFILE_ID_2_INDEED_IT", "NSTBROWSER_PROFILE_ID_2_INDEED_GENERAL",
    "NSTBROWSER_PROFILE_ID_2_GLASSDOOR_IT", "NSTBROWSER_PROFILE_ID_2_GLASSDOOR_GENERAL",
    "NSTBROWSER_PROFILE_ID_2_WORKOPOLIS_IT", "NSTBROWSER_PROFILE_ID_2_WORKOPOLIS_GENERAL",
    "NSTBROWSER_PROFILE_ID_2_LINKEDIN_IT", "NSTBROWSER_PROFILE_ID_2_LINKEDIN_GENERAL",
    "NSTBROWSER_PROFILE_ID_2_LINKEDIN_DISCOVERY",
    "NSTBROWSER_PROFILE_ID_2_LINKEDIN_DISCOVERY_IT",
    "NSTBROWSER_PROFILE_ID_2_GOOGLE_IT",
    "NSTBROWSER_PROFILE_ID_2_JOBBANK_IT",
    "INFISICAL_CLIENT_ID", "INFISICAL_CLIENT_SECRET", "INFISICAL_PROJECT_SLUG", "INFISICAL_ENV",
    "PROXY_URL", "CAPMONSTER_PROXY_URL", "CAPMONSTER_API_KEY",
    # Webshare + Proxy-Cheap (no DataImpulse). JOBSPY_PROXY_DATAIMPULSE is a
    # legacy alias that may still point at Proxy-Cheap in Secrets Manager.
    "WEBSHARE_PROXY_URL", "JOBSPY_PROXY_WEBSHARE",
    "JOBSPY_PROXY_DATAIMPULSE", "DATAIMPULSE_PROXY_URL", "PROXY_CHEAP_URL",
    "JOBSPY_PROXY_MODE", "JOBSPY_SKIP_LOCAL",
    "OPENROUTER_API_KEY", "GITHUB_TOKEN", "DD_API_KEY", "DD_SITE",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SENTRY_DSN",
    "FIRECRAWL_API_KEY", "TAVILY_API_KEY",
]
for k in ENV_KEYS:
    v = os.environ.get(k)
    if v:
        data[k] = v

# Do not silently roll a verified production Profile 2 mapping back to stale
# dashboard/CI values. Set NSTBROWSER_ALLOW_PROFILE_ID_ROTATION=1 only for a
# deliberate NST profile migration, after validating the replacement IDs.
allow_profile_id_rotation = str(
    os.environ.get("NSTBROWSER_ALLOW_PROFILE_ID_ROTATION") or ""
).strip().lower() in {"1", "true", "yes", "on"}
if existing_slot2_profile_ids and not allow_profile_id_rotation:
    for key, value in existing_slot2_profile_ids.items():
        if data.get(key) and str(data[key]).strip() != value:
            print(f"secrets: preserving verified runtime mapping for {key}")
        data[key] = value

# Heal dead Webshare host that returned 407 (old endpoint). Keep user:pass.
_DEAD_WS = "158.140.213.121:7321"
_LIVE_WS = "72.1.132.207:8099"
for _k in ("JOBSPY_PROXY_WEBSHARE", "WEBSHARE_PROXY_URL"):
    _v = str(data.get(_k) or "")
    if _DEAD_WS in _v:
        data[_k] = _v.replace(_DEAD_WS, _LIVE_WS)
        print(f"secrets: healed {_k} host {_DEAD_WS} -> {_LIVE_WS}")

data["BROWSER_VENDOR"] = data.get("BROWSER_VENDOR") or "nstbrowser"
# Always use local docker mongo on the worker (Infisical may hold Atlas placeholders)
data["MONGODB_URI"] = "mongodb://127.0.0.1:27017"
data["NSTBROWSER_ACTIVE_SLOT"] = active_slot or data.get("NSTBROWSER_ACTIVE_SLOT") or "1"
data["NSTBROWSER_FORBID_CREATE"] = data.get("NSTBROWSER_FORBID_CREATE") or "1"
data["DISCOVERY_REFRESH_EMAIL_HISTORY"] = data.get("DISCOVERY_REFRESH_EMAIL_HISTORY") or "1"
data["GOOGLE_AUTH_MODE"] = data.get("GOOGLE_AUTH_MODE") or "oauth"
data["INFISICAL_PROJECT_SLUG"] = (
    data.get("INFISICAL_PROJECT_SLUG")
    or os.environ.get("INFISICAL_PROJECT_SLUG")
    or "mybots-r46g"
)
data["INFISICAL_PROJECT_ID"] = (
    data.get("INFISICAL_PROJECT_ID")
    or os.environ.get("INFISICAL_PROJECT_ID")
    or inf_proj
    or "a2aaccb9-2d1a-4338-b8f5-bae3f42d7dbe"
)
data["INFISICAL_ENV"] = data.get("INFISICAL_ENV") or os.environ.get("INFISICAL_ENV") or inf_env or "dev"

# Preserve both API keys. Never overwrite primary with key2 — docker TOKEN
# and apply_slot_to_env select the right key via NSTBROWSER_ACTIVE_SLOT.
slot = str(data.get("NSTBROWSER_ACTIVE_SLOT", "")).strip()
if data.get("NSTBROWSER_API_KEY") and data.get("NSTBROWSER_API_KEY_2"):
    if data["NSTBROWSER_API_KEY"] == data["NSTBROWSER_API_KEY_2"] and data.get(
        "NSTBROWSER_API_KEY_SLOT1"
    ):
        # Heal earlier mistaken stamp of key2 over primary
        data["NSTBROWSER_API_KEY"] = data["NSTBROWSER_API_KEY_SLOT1"]
elif data.get("NSTBROWSER_API_KEY") and not data.get("NSTBROWSER_API_KEY_SLOT1"):
    data["NSTBROWSER_API_KEY_SLOT1"] = data["NSTBROWSER_API_KEY"]

if not data.get("NSTBROWSER_API_KEY") and data.get("NSTBROWSER_API_KEY_2"):
    data["NSTBROWSER_API_KEY"] = data["NSTBROWSER_API_KEY_2"]

if not data.get("NSTBROWSER_API_KEY"):
    raise SystemExit("NSTBROWSER_API_KEY (or _2) required in env or existing secret")

# Sole production LinkedIn bot is linkedin_general. When only LINKEDIN_IT
# profile IDs exist (legacy IT-only secrets), alias them so preflight + apply
# do not fail after the sole-bot switch.
if not data.get("NSTBROWSER_PROFILE_ID_2_LINKEDIN_GENERAL"):
    data["NSTBROWSER_PROFILE_ID_2_LINKEDIN_GENERAL"] = (
        data.get("NSTBROWSER_PROFILE_ID_2_LINKEDIN_IT")
        or data.get("NSTBROWSER_PROFILE_ID_LINKEDIN_IT")
        or ""
    )
if not data.get("NSTBROWSER_PROFILE_ID_LINKEDIN_GENERAL"):
    data["NSTBROWSER_PROFILE_ID_LINKEDIN_GENERAL"] = (
        data.get("NSTBROWSER_PROFILE_ID_LINKEDIN_IT")
        or data.get("NSTBROWSER_PROFILE_ID_2_LINKEDIN_IT")
        or ""
    )

# Greenhouse and Lever use the authenticated Google session. When a dedicated
# Google profile is absent, reuse the existing LinkedIn IT browser profile
# instead of creating an empty profile or requiring an operator map.
if not data.get("NSTBROWSER_PROFILE_ID_2_GOOGLE_IT"):
    data["NSTBROWSER_PROFILE_ID_2_GOOGLE_IT"] = (
        data.get("NSTBROWSER_PROFILE_ID_2_LINKEDIN_IT")
        or data.get("NSTBROWSER_PROFILE_ID_LINKEDIN_IT")
        or data.get("NSTBROWSER_PROFILE_ID_2_LINKEDIN_GENERAL")
        or ""
    )
if not data.get("NSTBROWSER_PROFILE_ID_GOOGLE_IT"):
    data["NSTBROWSER_PROFILE_ID_GOOGLE_IT"] = (
        data.get("NSTBROWSER_PROFILE_ID_LINKEDIN_IT")
        or data.get("NSTBROWSER_PROFILE_ID_LINKEDIN_GENERAL")
        or data.get("NSTBROWSER_PROFILE_ID_2_LINKEDIN_IT")
        or ""
    )

dst.write_text(json.dumps(data), encoding="utf-8")
print(f"secret keys: {len(data)} active_slot={data['NSTBROWSER_ACTIVE_SLOT']}")
PY

aws secretsmanager put-secret-value --secret-id "$secret_id" --secret-string "file://$tmp/secret.json" >/dev/null
echo "  Secrets updated ($secret_id)"

# --- 5) Sync app code ---
echo "[5/8] Syncing application code..."
ssm "set -eu; export PATH=/usr/local/bin:/usr/bin:\$PATH; sudo systemctl stop jobbots-application jobbots-application-general jobbots-supervisor jobbots-discover-glassdoor jobbots-discover-linkedin jobbots-discover-linkedin-general jobbots-discover-indeed-general jobbots-discover-ats jobbots-discover-jobbank jobbots-resume-workflow jobbots-supervisor.timer jobbots-discover-glassdoor.timer jobbots-discover-linkedin.timer jobbots-discover-linkedin-general.timer jobbots-discover-indeed-general.timer jobbots-discover-ats.timer jobbots-discover-jobbank.timer 2>/dev/null || true; tmp=\$(mktemp -d); aws s3 cp 's3://${artifact_bucket}/${ARTIFACT_PREFIX}/deploy/travis-update.tar.gz' \"\$tmp/app.tar.gz\" --only-show-errors; sudo rm -rf /opt/jobbots/app; sudo install -d -o ubuntu -g ubuntu -m 0755 /opt/jobbots/app; sudo tar -xzf \"\$tmp/app.tar.gz\" -C /opt/jobbots/app; sudo mkdir -p '/opt/jobbots/app/master/it_indeed cwgeopy/Auto_indeed/all resumes' && sudo cp '/opt/jobbots/app/automation_monorepo/all resumes/ls_resume_it.pdf' '/opt/jobbots/app/master/it_indeed cwgeopy/Auto_indeed/all resumes/ls_resume_it.pdf' && sudo cp '/opt/jobbots/app/automation_monorepo/all resumes/cover_ls_it.pdf' '/opt/jobbots/app/master/it_indeed cwgeopy/Auto_indeed/all resumes/cover_ls_it.pdf' 2>/dev/null || true; sudo mkdir -p '/opt/jobbots/app/master/gen_indeed/Auto_indeed/all resumes' && sudo cp '/opt/jobbots/app/automation_monorepo/all resumes/ls_resume_general.pdf' '/opt/jobbots/app/master/gen_indeed/Auto_indeed/all resumes/ls_resume_general.pdf' 2>/dev/null || true; sudo chown -R ubuntu:ubuntu /opt/jobbots/app; rm -rf \"\$tmp\"" 120

echo "[5b/8] Python deps + Playwright Chromium + Infisical project .env..."
# LibreOffice (soffice) is required for resume DOCX→PDF on stock workers.
# Golden images may already have it; apt is idempotent.
ssm "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq libreoffice-writer libreoffice-common 2>/dev/null || true; command -v soffice || command -v libreoffice" 180
ssm "sudo /opt/jobbots/venv/bin/python -m pip install -q -r /opt/jobbots/app/requirements.txt" 120
# Expose unified CLI (jobbots doctor|farm-check|run) after Phase-0/1 package skeleton.
ssm "sudo /opt/jobbots/venv/bin/python -m pip install -q -e /opt/jobbots/app" 120
ssm "sudo /opt/jobbots/venv/bin/python -m playwright install chromium" 120
ssm "sudo -u ubuntu bash -lc 'cd /opt/jobbots/app/resume_workflow && npm ci --omit=dev' || true" 120
# LinkedIn hybrid_runner needs puppeteer/ghost-cursor (tar excludes node_modules).
# Fresh stock images can take >5m for npm; poll longer so SYNC does not die mid-install.
ssm "sudo -u ubuntu bash -lc 'cd /opt/jobbots/app/legacy/linkedin-ai-auto-apply-source && if [ -f package-lock.json ]; then npm ci --omit=dev; else npm install --omit=dev; fi' || true" 180
ssm "cat > /opt/jobbots/app/automation_monorepo/.env <<'ENVEOF'
INFISICAL_PROJECT_ID=${INFISICAL_PROJECT_ID}
INFISICAL_PROJECT_SLUG=mybots-r46g
INFISICAL_ENV=${INFISICAL_ENV}
AUTOMATION_PROFILES_DIR=/srv/jobbots/browser_profiles
BROWSER_VENDOR=nstbrowser
NSTBROWSER_ACTIVE_SLOT=${NSTBROWSER_ACTIVE_SLOT}
NSTBROWSER_FORBID_CREATE=1
DISCOVERY_REFRESH_EMAIL_HISTORY=1
GOOGLE_AUTH_MODE=oauth
TAVILY_ATS_ENABLED=0
GOOGLE_CDP_TAVILY_FALLBACK=0
ENVEOF
sudo chown ubuntu:ubuntu /opt/jobbots/app/automation_monorepo/.env
sudo install -d -o ubuntu -g ubuntu -m 0750 /srv/jobbots/browser_profiles /opt/jobbots/app/automation_monorepo/data/supervisor
sudo install -d -o ubuntu -g ubuntu -m 0755 /var/lib/jobbots
sudo chown -R ubuntu:ubuntu /srv/jobbots/browser_profiles /opt/jobbots/app/automation_monorepo/data /var/lib/jobbots 2>/dev/null || true"

# OAuth/token files for Drive+Sheets (from local monorepo if present)
if [ -f automation_monorepo/token.json ]; then
  echo "[5c/8] Uploading Google OAuth token + SA (encrypted at rest on volume)..."
  # use ssm with base64 to avoid shell escaping issues
  for f in token.json client_secret.json google_service_account.json; do
    if [ -f "automation_monorepo/$f" ]; then
      b64="$(base64 <"automation_monorepo/$f" | tr -d '\n')"
      ssm "echo '$b64' | base64 -d | sudo tee /opt/jobbots/app/automation_monorepo/$f >/dev/null; sudo chown ubuntu:ubuntu /opt/jobbots/app/automation_monorepo/$f; sudo chmod 600 /opt/jobbots/app/automation_monorepo/$f"
    fi
  done
fi

# --- 6) Systemd units + discover wrappers + prod overrides ---
echo "[6/8] Installing bin wrappers, systemd units, prod overrides..."
ssm "set -eu
# Discover wrappers + any other packer bin helpers (every sync — not only first provision)
if [ -d /opt/jobbots/app/packer/linux/bin ]; then
  sudo install -d -m 0755 /opt/jobbots/bin
  sudo install -m 0755 /opt/jobbots/app/packer/linux/bin/* /opt/jobbots/bin/
fi
if [ -f /opt/jobbots/app/packer/linux/runtime-prod-overrides.conf ]; then
  sudo install -m 0644 /opt/jobbots/app/packer/linux/runtime-prod-overrides.conf /etc/jobbots/runtime-prod-overrides.conf
fi
# Units (service + timer)
sudo cp /opt/jobbots/app/packer/linux/systemd/*.service /etc/systemd/system/ 2>/dev/null || true
sudo cp /opt/jobbots/app/packer/linux/systemd/*.timer /etc/systemd/system/ 2>/dev/null || true
# Drop live-only volume drop-ins: prod knobs are in runtime-prod-overrides.conf
# (and units no longer hardcode SERP/batch AI). Stale drop-ins hid deploy drift.
for d in \
  /etc/systemd/system/jobbots-supervisor.service.d \
  /etc/systemd/system/jobbots-discover-indeed-general.service.d \
  /etc/systemd/system/jobbots-application.service.d \
  /etc/systemd/system/jobbots-application-general.service.d \
  /etc/systemd/system/jobbots-nstbrowser.service.d
do
  if [ -d "\$d" ]; then
    sudo rm -f "\$d/discovery-volume.conf" "\$d/slot-override.conf" 2>/dev/null || true
    # remove empty drop-in dirs
    sudo rmdir "\$d" 2>/dev/null || true
  fi
done
sudo systemctl daemon-reload
# Disable legacy simple long-lived discover if previously enabled
sudo systemctl disable --now jobbots-supervisor.service 2>/dev/null || true
sudo systemctl enable \
  jobbots-load-secrets.service \
  jobbots-mongodb.service \
  jobbots-nstbrowser.service \
  jobbots-novnc.service \
  jobbots-telegram.service \
  2>/dev/null || true
sudo systemctl restart jobbots-load-secrets.service
sleep 3
sudo systemctl restart jobbots-mongodb.service jobbots-nstbrowser.service jobbots-novnc.service || true
"

echo "[6b/8] Waiting for Mongo + NST..."
ssm "for i in \$(seq 1 40); do curl -sS -m 2 http://127.0.0.1:8848/ >/dev/null 2>&1 && echo nst_ok && break; sleep 3; done; for i in \$(seq 1 30); do (echo >/dev/tcp/127.0.0.1/27017) >/dev/null 2>&1 && echo mongo_ok && break; sleep 2; done; systemctl is-active jobbots-mongodb.service jobbots-nstbrowser.service || true; sudo docker ps --format '{{.Names}} {{.Status}}' || true"

echo "[6c/8] Ensuring Datadog Agent (DogStatsD :8125) when DD_API_KEY present..."
ssm "set -eu
set -a; . /etc/jobbots/secrets.env; set +a
if [ -z \"\${DD_API_KEY:-}\" ]; then
  echo 'DD_API_KEY unset — skip Datadog agent'
  exit 0
fi
SITE=\"\${DD_SITE:-us5.datadoghq.com}\"
if [ ! -x /opt/datadog-agent/bin/agent/agent ]; then
  echo 'Installing datadog-agent...'
  export DD_API_KEY DD_SITE=\"\$SITE\"
  curl -fsSL https://s3.amazonaws.com/dd-agent/scripts/install_script_agent7.sh -o /tmp/dd_install.sh
  sudo -E bash /tmp/dd_install.sh || true
fi
sudo tee /etc/datadog-agent/datadog.yaml >/dev/null <<EOF
api_key: \${DD_API_KEY}
site: \${SITE}
hostname: jobbots-production-13-worker
tags:
  - env:production
  - service:jobbots
logs_enabled: false
process_config:
  process_collection:
    enabled: false
EOF
sudo chown dd-agent:dd-agent /etc/datadog-agent/datadog.yaml 2>/dev/null || true
sudo chmod 640 /etc/datadog-agent/datadog.yaml 2>/dev/null || true
sudo systemctl enable datadog-agent 2>/dev/null || true
sudo systemctl restart datadog-agent 2>/dev/null || true
sleep 3
systemctl is-active datadog-agent 2>/dev/null || true
ss -ulnp 2>/dev/null | grep 8125 || true
" 180 || echo "  WARNING: Datadog agent ensure failed (non-fatal)" >&2

# --- 7) Preflight ---
# IMPORTANT: code sync (step 5) already stopped apply/discover. If preflight fails
# hard and we exit before step 8, production stays dead for hours. When START_BOTS=1
# we treat preflight as best-effort: warn loudly, still start the factory, and let
# journals prove health. Hard-fail only when operator intentionally left bots off.
echo "[7/8] Runtime preflight..."
preflight_ok=1
# google_it is Playwright ATS (no NST) — do not open/warm an NST profile for it.
# Browser NST profiles on the active slot: Indeed IT+general, Glassdoor IT,
# Workopolis IT, LinkedIn sole, and Job Bank Direct Apply.
if ssm "sudo bash -c 'set -a; source /etc/jobbots/runtime.conf; source /etc/jobbots/secrets.env; source /etc/jobbots/runtime-prod-overrides.conf 2>/dev/null || true; set +a; export NSTBROWSER_ACTIVE_SLOT=${NSTBROWSER_ACTIVE_SLOT:-2}; /opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/verify_linux_vm_runtime.py --bot indeed_it --bot indeed_general --bot glassdoor_it --bot workopolis_it --bot linkedin_general --bot jobbank_it --warm && cd /opt/jobbots/app && /opt/jobbots/venv/bin/python -m jobbots.app.cli farm-check'" 180; then
  echo "  Preflight OK"
else
  preflight_ok=0
  echo "  WARNING: preflight FAILED (see SSM output above)" >&2
  if [ "$START_BOTS" != "1" ]; then
    echo "  START_BOTS=0 — not starting services; fix preflight then re-run with START_BOTS=1" >&2
    exit 1
  fi
  echo "  START_BOTS=1 — continuing to start factory so mid-run sync cannot leave bots dead" >&2
fi

# --- 8) Start production cycle ---
if [ "$START_BOTS" = "1" ]; then
  echo "[8/8] Starting timers + application workers..."
  ssm "set -eu
# Sole LinkedIn discover = linkedin-general (IT+office dual pass). Stop legacy LI-IT timer.
sudo systemctl disable --now jobbots-discover-linkedin.timer 2>/dev/null || true
sudo systemctl enable \\
  jobbots-resume-workflow.service \\
  jobbots-application.service \\
  jobbots-application-general.service \\
  jobbots-supervisor.timer \\
  jobbots-discover-glassdoor.timer \\
  jobbots-discover-linkedin-general.timer \\
  jobbots-discover-indeed-general.timer \\
  jobbots-discover-ats.timer \\
  jobbots-discover-jobbank.timer
sudo systemctl restart jobbots-telegram.service 2>/dev/null || true
sudo systemctl restart jobbots-resume-workflow.service 2>/dev/null || true
sudo systemctl restart jobbots-application.service
sudo systemctl restart jobbots-application-general.service
sudo systemctl restart jobbots-supervisor.timer
sudo systemctl restart jobbots-discover-glassdoor.timer
sudo systemctl restart jobbots-discover-linkedin-general.timer
sudo systemctl restart jobbots-discover-indeed-general.timer
sudo systemctl restart jobbots-discover-ats.timer
sudo systemctl restart jobbots-discover-jobbank.timer
# Kick first discover ticks immediately (oneshot) without waiting OnBootSec
sudo systemctl start --no-block jobbots-supervisor.service || true
sudo systemctl start --no-block jobbots-discover-glassdoor.service || true
sudo systemctl start --no-block jobbots-discover-linkedin-general.service || true
sudo systemctl start --no-block jobbots-discover-indeed-general.service || true
sudo systemctl start --no-block jobbots-discover-ats.service || true
sudo systemctl start --no-block jobbots-discover-jobbank.service || true
sleep 8
echo '=== unit status ==='
systemctl is-active jobbots-application.service jobbots-application-general.service jobbots-supervisor.timer jobbots-discover-glassdoor.timer jobbots-discover-linkedin-general.timer jobbots-discover-indeed-general.timer jobbots-discover-ats.timer jobbots-discover-jobbank.timer jobbots-mongodb.service jobbots-nstbrowser.service || true
systemctl list-timers 'jobbots-*' --no-pager || true
echo '=== wrappers ==='
ls -la /opt/jobbots/bin/jobbots-discover-* 2>/dev/null || true
grep -E 'KEYWORDS|JOBSPY_FULL|EMAIL_REFRESH|GLASSDOOR|APPLY_PORTALS|ACTIVE_SLOT|FRESHNESS|TERM_MEMORY|WORKOPOLIS|GENERAL|LINKEDIN_MAX|BATCH_AI|DEEPSEEK|SERP_CACHE' /etc/jobbots/runtime-prod-overrides.conf 2>/dev/null || true
# Hotpatch sanity (code path must ship with SYNC tarball).
# Canonical core is jobbots/core (Phase-2); keep monorepo shim path as fallback.
CORE=/opt/jobbots/app/jobbots/core
CORE_LEGACY=/opt/jobbots/app/automation_monorepo/core
core_grep() { grep -q "\$1" "\$CORE/\$2" 2>/dev/null || grep -q "\$1" "\$CORE_LEGACY/\$2"; }
grep -q 'linkedin_sole_worker' /opt/jobbots/app/automation_monorepo/scripts/application_worker.py
core_grep 'PROXY_CHEAP_URL' secret_manager.py
grep -q 'proxyFallbackUrl' /opt/jobbots/app/legacy/linkedin-ai-auto-apply-source/hybrid_runner.js
core_grep 'DISCOVERY_BATCH_AI_CHUNK' shared_modules/indeed/gates.py
# Portal productivity (2026-08-04): requeue dead + IT fail-open + general junk reject
core_grep 'form_stalled' job_queue.py
core_grep 'submit clicked but no confirmation' job_queue.py
core_grep 'fail-open: EA IT title signal overrode batch AI' discovery/planner.py
core_grep 'floor retail/clinical/trades title' discovery/_gate_adapter.py
grep -q 'JOBBOTS_REENQUEUE_COOLDOWN_SECONDS' /etc/jobbots/runtime-prod-overrides.conf
grep -qE 'JOBBOTS_APPLICATION_WORKERS=[34]' /etc/jobbots/runtime-prod-overrides.conf
grep -q 'NSTBROWSER_LAUNCH_RETRIES' /etc/jobbots/runtime-prod-overrides.conf
core_grep 'NSTBROWSER_LAUNCH_RETRIES' browser/open_chrome.py || core_grep 'launch_attempts' browser/open_chrome.py
test -f /opt/jobbots/app/jobbots/app/farm_check.py
grep -Eq '^NSTBROWSER_ACTIVE_SLOT=[12]$' /etc/jobbots/runtime-prod-overrides.conf
# Offline productivity contract (no browser) — must pass after every SYNC.
cd /opt/jobbots/app && sudo -u ubuntu /opt/jobbots/venv/bin/python -m jobbots.app.cli farm-check
echo '=== recent logs ==='
sudo journalctl -u jobbots-application -u jobbots-application-general -u jobbots-supervisor -u jobbots-discover-glassdoor -u jobbots-discover-linkedin-general -u jobbots-discover-indeed-general -u jobbots-discover-ats -u jobbots-discover-jobbank -n 50 --no-pager || true
"
  if [ "$preflight_ok" != "1" ]; then
    echo "Bootstrap finished with PREFLIGHT WARNINGS — factory started; check journals" >&2
  fi
else
  echo "[8/8] START_BOTS=0 — production services disabled"
  ssm "sudo systemctl disable --now \\
    jobbots-resume-workflow.service \\
    jobbots-application.service \\
    jobbots-application-general.service \\
    jobbots-supervisor.timer \\
    jobbots-discover-glassdoor.timer \\
    jobbots-discover-linkedin.timer \\
    jobbots-discover-linkedin-general.timer \\
    jobbots-discover-indeed-general.timer \\
    jobbots-discover-jobbank.timer \\
    jobbots-discover-ats.timer \\
    2>/dev/null || true"
fi

echo "Bootstrap complete for $vm_id"

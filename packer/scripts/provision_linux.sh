#!/usr/bin/env bash
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
for i in $(seq 1 30); do
  if ! fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock >/dev/null 2>&1; then
    break
  fi
  echo "Waiting for apt/dpkg lock..."
  sleep 5
done
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl docker.io git jq nodejs npm novnc python3 python3-pip \
  python3-tk python3-venv rsync unzip websockify xfce4 xfce4-terminal xorg xvfb xrdp \
  libreoffice-writer libreoffice-common
# soffice converts tailored DOCX → PDF for Indeed/resume workflow (stock workers).

# Install AWS CLI v2 (idempotent — update if already present)
if ! command -v aws >/dev/null 2>&1 && [ ! -x /usr/local/bin/aws ]; then
  curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
  unzip -q awscliv2.zip
  ./aws/install
  rm -rf aws awscliv2.zip
elif [ -x /usr/local/bin/aws ] || command -v aws >/dev/null 2>&1; then
  # Optionally refresh without failing bootstrap
  curl -sS "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip" || true
  if [ -f awscliv2.zip ]; then
    unzip -q -o awscliv2.zip
    ./aws/install --update || true
    rm -rf aws awscliv2.zip
  fi
fi

# Install Google Chrome (non-interactive; no TTY required for gpg)
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
  | gpg --batch --yes --dearmor -o /usr/share/keyrings/google-chrome.gpg
echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
  > /etc/apt/sources.list.d/google-chrome.list
apt-get update
apt-get install -y google-chrome-stable || {
  echo "WARNING: google-chrome-stable install failed — continuing (Playwright Chromium still available)"
}

systemctl enable docker
install -d -m 0755 /etc/jobbots /opt/jobbots/bin
install -d -o ubuntu -g ubuntu -m 0755 /var/lib/jobbots
install -d -o ubuntu -g ubuntu -m 0750 /srv/jobbots/browser_profiles
install -m 0755 /tmp/bin/* /opt/jobbots/bin/
install -m 0644 /tmp/systemd/* /etc/systemd/system/
# Production env knobs (optional EnvironmentFile=- on units)
if [ -f /tmp/jobbots-bootstrap/runtime-prod-overrides.conf ]; then
  install -m 0644 /tmp/jobbots-bootstrap/runtime-prod-overrides.conf /etc/jobbots/runtime-prod-overrides.conf
elif [ -f /tmp/runtime-prod-overrides.conf ]; then
  install -m 0644 /tmp/runtime-prod-overrides.conf /etc/jobbots/runtime-prod-overrides.conf
fi
install -m 0600 /dev/null /etc/jobbots/secrets.env

python3 -m venv /opt/jobbots/venv
/opt/jobbots/venv/bin/python -m pip install --upgrade pip
/opt/jobbots/venv/bin/python -m pip install -r /tmp/requirements.txt
/opt/jobbots/venv/bin/python -m playwright install-deps
sudo -u ubuntu /opt/jobbots/venv/bin/python -m playwright install

docker pull mongo:8.0
docker pull nstbrowser/browserless:latest

systemctl daemon-reload
systemctl enable \
  jobbots-load-secrets.service \
  jobbots-mongodb.service \
  jobbots-nstbrowser.service \
  jobbots-novnc.service \
  jobbots-artifact-sync.timer \
  jobbots-report.timer \
  jobbots-resume-workflow.service \
  jobbots-supervisor.timer \
  jobbots-discover-glassdoor.timer \
  jobbots-discover-linkedin-general.timer \
  jobbots-discover-indeed-general.timer \
  jobbots-discover-ats.timer \
  jobbots-discover-jobbank.timer \
  jobbots-application.service \
  jobbots-application-general.service \
  2>/dev/null || true
# Sole LinkedIn discover unit is linkedin-general (IT+office dual pass).
# Disable legacy linkedin-it timer if present on older images.
systemctl disable --now jobbots-discover-linkedin.timer 2>/dev/null || true
# Oneshot discover units are pulled by timers — do not enable simple long-lived discover.

# Enable and configure xrdp to use XFCE
systemctl enable xrdp
echo "xfce4-session" > /etc/skel/.xsession
if id -u ubuntu >/dev/null 2>&1; then
  echo "xfce4-session" > /home/ubuntu/.xsession
  chown ubuntu:ubuntu /home/ubuntu/.xsession
fi

# Create desktop shortcuts for XFCE
mkdir -p /etc/skel/Desktop
if id -u ubuntu >/dev/null 2>&1; then
  mkdir -p /home/ubuntu/Desktop
fi

create_launcher() {
  local name="$1"
  local exec_cmd="$2"
  local file_basename="$3"
  
  local skel_file="/etc/skel/Desktop/${file_basename}"
  cat <<EOF > "$skel_file"
[Desktop Entry]
Version=1.0
Type=Application
Name=${name}
Comment=Launch ${name}
Exec=xfce4-terminal --hold -e "${exec_cmd}"
Icon=utilities-terminal
Path=/opt/jobbots/app/automation_monorepo
Terminal=true
StartupNotify=false
EOF
  chmod +x "$skel_file"

  if id -u ubuntu >/dev/null 2>&1; then
    local user_file="/home/ubuntu/Desktop/${file_basename}"
    cp "$skel_file" "$user_file"
    chmod +x "$user_file"
    chown ubuntu:ubuntu "$user_file"
  fi
}

create_launcher "Run Indeed IT Chrome" "/opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/run_indeed_it_chrome.py" "Run_Indeed_IT.desktop"
create_launcher "Run Glassdoor IT Chrome" "/opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/run_glassdoor_it_chrome.py" "Run_Glassdoor_IT.desktop"
create_launcher "Run Workopolis IT Chrome" "/opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/run_workopolis_it_chrome.py" "Run_Workopolis_IT.desktop"
create_launcher "Run Indeed General Chrome" "/opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/scripts/run_indeed_general_chrome.py" "Run_Indeed_General.desktop"
create_launcher "Bot Status" "/opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/bot_manager.py status" "Bot_Status.desktop"
create_launcher "Stop All Bots" "/opt/jobbots/venv/bin/python /opt/jobbots/app/automation_monorepo/bot_manager.py stop-all" "Stop_All_Bots.desktop"

# Ensure /opt/jobbots is owned by the service user
if id -u ubuntu >/dev/null 2>&1; then
  chown -R ubuntu:ubuntu /opt/jobbots
fi

apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/bin /tmp/systemd /tmp/requirements.txt

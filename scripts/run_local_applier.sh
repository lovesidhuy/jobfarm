#!/usr/bin/env bash
# Local Job Applier Launcher
# Runs the application workers locally on your Mac to apply to the enqueued jobs.

set -euo pipefail

cleanup() {
    echo -e "\n[INFO] Terminating all background worker child processes..."
    pkill -P $$ 2>/dev/null || true
    pkill -f "application_worker.py" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Define colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}==================================================${NC}"
echo -e "${GREEN}      Starting Local Application Workers${NC}"
echo -e "${BLUE}==================================================${NC}"

# Check if MongoDB is running
if ! mongosh --eval "db.adminCommand('ping')" --quiet &>/dev/null; then
    echo -e "${YELLOW}[WARNING] MongoDB is not responding. Starting MongoDB community service...${NC}"
    brew services start mongodb-community
    sleep 2
fi

# Ensure Python requirements and Playwright are set up
export PYTHONPATH="./automation_monorepo:${PYTHONPATH:-}"
export MONGODB_URI="mongodb://127.0.0.1:27017"
export JOBBOTS_MONGO_DATABASE="jobbots"
export BROWSER_VENDOR="${BROWSER_VENDOR:-nstbrowser}"
export USE_NSTBROWSER="${USE_NSTBROWSER:-true}"
export NSTBROWSER_ACTIVE_SLOT="${NSTBROWSER_ACTIVE_SLOT:-2}"
export ATS_HEADLESS="${ATS_HEADLESS:-1}"
export HEADLESS="${HEADLESS:-0}"

cd "./automation_monorepo"

echo -e "${GREEN}[INFO] Syncing local NSTBrowser Slot 1 profile IDs...${NC}"
python3 scripts/sync_nst_dual_accounts.py || true

echo -e "${GREEN}[INFO] Refreshing IMAP confirmation emails & deduplicating queue...${NC}"
python3 -c "
try:
    from core.discovery.email_history_refresh import refresh_email_applied_history
    refresh_email_applied_history(days=30)
except Exception as e:
    print(f'IMAP refresh note: {e}')
"
python3 ../scripts/enqueue_local_leads.py

echo -e "${GREEN}[INFO] Querying current queued jobs in local MongoDB...${NC}"
python3 -c "
from core.job_queue import JobQueue
q = JobQueue()
print(f'Total jobs in queue: {q.jobs.count_documents({\"status\": \"queued\"})}')
print(f'Portals breakdown:')
for x in q.jobs.aggregate([{\"\$match\": {\"status\": \"queued\"}}, {\"\$group\": {\"_id\": \"\$portal\", \"count\": {\"\$sum\": 1}}}]):
    print(f'  - {x.get(\"_id\")}: {x.get(\"count\")}')
"

echo -e "${BLUE}--------------------------------------------------${NC}"
echo -e "${GREEN}Starting 5 parallel application workers for: greenhouse, lever, ashby, bamboohr, workopolis, linkedin, indeed${NC}"
echo -e "${BLUE}--------------------------------------------------${NC}"

# Run supervisor.py for apply stage with 5 parallel workers (1 per portal cluster)
python3 supervisor.py \
  --stage apply \
  --workers 5 \
  --portal greenhouse,lever,ashby,bamboohr,workopolis,linkedin,indeed \
  --profile it

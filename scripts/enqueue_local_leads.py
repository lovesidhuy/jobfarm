#!/usr/bin/env python3
"""Local Queue Enqueuer — Insert all approved JSON leads into the local MongoDB job queue."""

import json
import os
import sys
from pathlib import Path

# Add monorepo root to sys.path
_ROOT = Path(__file__).resolve().parents[1] / "automation_monorepo"
sys.path.insert(0, str(_ROOT))

from core.supervisor_runtime import merge_dotenv_into_env
merge_dotenv_into_env(os.environ, _ROOT / ".env", override=False)

# Force local mongodb URI in env if not specified
os.environ["MONGODB_URI"] = os.getenv("MONGODB_URI", "mongodb://127.0.0.1:27017")
os.environ["JOBBOTS_MONGO_DATABASE"] = os.getenv("JOBBOTS_MONGO_DATABASE", "jobbots")

from core.job_queue import JobQueue

def main():
    json_path = Path(__file__).resolve().parents[1] / "data" / "local_approved_queue.json"
    if not json_path.exists():
        print(f"Error: {json_path} not found.")
        return

    print("==================================================")
    print(" Enqueueing Approved Leads into Local MongoDB")
    print("==================================================")

    reset_dead = "--reset-dead" in sys.argv or "--reset-failed" in sys.argv

    with open(json_path, "r", encoding="utf-8") as f:
        leads = json.load(f)

    print(f"Loaded {len(leads)} leads from local queue JSON.")

    if reset_dead:
        reset_cnt = 0
        for l in leads:
            if l.get("status") not in {"applied", "already_applied"}:
                l["status"] = "queued"
                reset_cnt += 1
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2)
        print(f"Reset {reset_cnt} dead/failed jobs back to 'queued' status in JSON file.")

    queue = JobQueue()

    # Clear any existing local queue so we start fresh and don't duplicate
    deleted = queue.jobs.delete_many({})
    print(f"Cleared existing local queue ({deleted.deleted_count} stale jobs removed).")

    from core.shared_modules.company_throttle import check_company_throttle_and_dedupe

    enqueued_count = 0
    skipped_count = 0
    dupe_count = 0
    throttle_count = 0

    for lead in leads:
        if lead.get("status") != "queued":
            skipped_count += 1
            continue

        action, reason = check_company_throttle_and_dedupe(queue, lead)
        if action == "already_applied":
            dupe_count += 1
            continue
        elif action == "skipped":
            throttle_count += 1
            continue

        try:
            # Map metadata fields (apply_type, region, work_mode)
            metadata = {
                "application_method": lead.get("apply_type"),
                "region": lead.get("region"),
                "work_mode": lead.get("work_mode")
            }
            queue.enqueue(
                portal=lead.get("portal"),
                profile="it",
                source_job_id=str(lead.get("job_id")),
                title=lead.get("title"),
                company=lead.get("company"),
                url=lead.get("url"),
                location=lead.get("location", ""),
                description="",
                gate_score=lead.get("gate_score"),
                gate_reason=lead.get("gate_reason"),
                metadata=metadata
            )
            enqueued_count += 1
        except Exception as e:
            print(f"Failed to enqueue {lead.get('title')} at {lead.get('company')}: {e}")

    print("\n==================================================")
    print(f" SUCCESS! Enqueued {enqueued_count} Clean Net-New Leads into local MongoDB.")
    print(f"   - Skipped {dupe_count} Duplicate Applications")
    print(f"   - Skipped {throttle_count} Company Rate Limited Applications")
    print(f"   - Skipped {skipped_count} Non-Queued Leads")
    print(" Local MongoDB Queue is ready to be processed safely!")
    print(" Local MongoDB Queue is ready to be processed!")
    print("==================================================")

if __name__ == "__main__":
    main()

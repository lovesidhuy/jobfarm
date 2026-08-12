#!/usr/bin/env python3
"""LinkedIn & Indeed Dedicated Exporter — Fetch LinkedIn and Indeed leads and append to local queue."""

import json
import csv
import os
import sys
from pathlib import Path
from datetime import datetime

# Add monorepo root to sys.path
_ROOT = Path(__file__).resolve().parents[1] / "automation_monorepo"
sys.path.insert(0, str(_ROOT))

from core.supervisor_runtime import merge_dotenv_into_env
merge_dotenv_into_env(os.environ, _ROOT / ".env", override=False)

from core.discovery import run_discovery

def main():
    out_dir = Path(__file__).resolve().parents[1] / "data"
    out_dir.mkdir(exist_ok=True)

    json_path = out_dir / "local_approved_queue.json"
    csv_path = out_dir / "local_approved_queue.csv"

    # Read existing queue items to avoid duplicates
    existing_items = []
    seen_ids = set()
    if json_path.exists():
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                existing_items = json.load(f)
                for item in existing_items:
                    seen_ids.add(str(item.get("job_id")))
        except Exception:
            existing_items = []

    print("==================================================")
    print(" Running Dedicated LinkedIn & Indeed Local Exporter")
    print(f" Existing Queue Size: {len(existing_items)}")
    print("==================================================")

    # Clear stale telemetry
    telemetry_file = Path("/tmp/discovery_screening_telemetry.jsonl")
    if telemetry_file.exists():
        telemetry_file.unlink()

    # Dedicated sweep for LinkedIn & Indeed
    result = run_discovery(
        profile="it",
        portals=["linkedin", "indeed"],
        dry_run=True,
        max_results_per_term=25,
        freshness_days=7,
        timeout_seconds=600,
        search_terms=[
            "IT Support Analyst", "Help Desk Technician", "Service Desk Analyst",
            "QA Analyst", "QA Engineer", "Systems Administrator", "Desktop Support",
            "Software Developer Intern", "IT Co-op", "Junior Data Analyst"
        ]
    )

    print("\n--------------------------------------------------")
    print(f" Raw Count: {result.get('raw_count')}, Screened: {result.get('screened')}, Passed: {result.get('passed')}")

    new_approved = []
    if telemetry_file.exists():
        with open(telemetry_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line.strip())
                jid = str(data.get("job_id"))
                if data.get("decision_action") in ("APPLY", "VERIFY", "SAVE", "ENQUEUE") and data.get("decision_action") != "REJECT":
                    if jid not in seen_ids:
                        seen_ids.add(jid)
                        new_approved.append({
                            "job_id": jid,
                            "title": data.get("title"),
                            "company": data.get("company"),
                            "location": data.get("location"),
                            "portal": data.get("portal"),
                            "url": data.get("url"),
                            "apply_type": data.get("apply_type"),
                            "region": data.get("region"),
                            "work_mode": data.get("work_mode"),
                            "gate_score": data.get("gate_score"),
                            "gate_reason": data.get("gate_reason"),
                            "status": "queued",
                            "captured_at": datetime.utcnow().isoformat() + "Z"
                        })

    all_items = existing_items + new_approved

    # Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(all_items, f, indent=2)

    # Save CSV
    if all_items:
        headers = list(all_items[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(all_items)

    print("\n==================================================")
    print(f" SUCCESS! Appended {len(new_approved)} LinkedIn & Indeed Approved Leads!")
    print(f" Total Queue Size Now: {len(all_items)} Leads")
    print(f" JSON File: {json_path}")
    print(f" CSV File:  {csv_path}")
    print("==================================================")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Full Local Queue Exporter — Capture approved leads across LinkedIn, Indeed, Workopolis, & ATS Boards into JSON/CSV queues."""

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

    print("==================================================")
    print(" Running Full Local Multi-Portal Queue Exporter")
    print("==================================================")

    # Clear stale telemetry
    telemetry_file = Path("/tmp/discovery_screening_telemetry.jsonl")
    if telemetry_file.exists():
        telemetry_file.unlink()

    # Full sweep including LinkedIn & Indeed (via JobSpy), Workopolis & ATS Board APIs
    result = run_discovery(
        profile="it",
        portals=["linkedin", "indeed", "workopolis", "ats_board_api"],
        dry_run=True,
        max_results_per_term=30,
        freshness_days=7,
        timeout_seconds=45,
    )

    print("\n--------------------------------------------------")
    print(f" Raw Count: {result.get('raw_count')}, Screened: {result.get('screened')}, Passed: {result.get('passed')}")

    approved_queue = []
    if telemetry_file.exists():
        with open(telemetry_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                data = json.loads(line.strip())
                if data.get("decision_action") in ("APPLY", "VERIFY", "SAVE", "ENQUEUE") and data.get("decision_action") != "REJECT":
                    approved_queue.append({
                        "job_id": data.get("job_id"),
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

    # Save JSON
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(approved_queue, f, indent=2)

    # Save CSV
    if approved_queue:
        headers = list(approved_queue[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(approved_queue)

    print("\n==================================================")
    print(f" SUCCESS! Captured {len(approved_queue)} Approved Leads into Local Queue")
    print(f" JSON File: {json_path}")
    print(f" CSV File:  {csv_path}")
    print("==================================================")

if __name__ == "__main__":
    main()

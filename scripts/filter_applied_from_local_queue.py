#!/usr/bin/env python3
"""Local Queue De-duplicator & Historical Filter — Remove already applied jobs from local queue files."""

import json
import csv
import re
import sqlite3
from pathlib import Path

def normalize_str(s: str) -> str:
    if not s:
        return ""
    # Lowercase and remove non-alphanumeric characters
    return re.sub(r'[^a-z0-9]', '', str(s).lower())

def main():
    root = Path(__file__).resolve().parents[1]
    data_dir = root / "data"
    json_path = data_dir / "local_approved_queue.json"
    csv_path = data_dir / "local_approved_queue.csv"

    if not json_path.exists():
        print(f"Error: {json_path} does not exist.")
        return

    print("==================================================")
    print(" Filtering Local Queue Against All Historical Data")
    print("==================================================")

    # Sets to track applied jobs
    applied_urls = set()
    applied_job_ids = set()
    applied_company_title = set()

    def add_applied(url=None, job_id=None, company=None, title=None):
        if url:
            applied_urls.add(str(url).strip().lower().split('?')[0])
        if job_id:
            applied_job_ids.add(str(job_id).strip())
        if company and title:
            key = (normalize_str(company), normalize_str(title))
            if key[0] and key[1]:
                applied_company_title.add(key)

    # 1. Search for all CSV files with 'applied' in path or header
    csv_count = 0
    for csv_file in root.glob("**/*.csv"):
        if "local_approved_queue" in csv_file.name:
            continue
        try:
            with open(csv_file, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    url = row.get("url") or row.get("listing_url") or row.get("job_url") or row.get("link")
                    jid = row.get("job_id") or row.get("id") or row.get("source_job_id")
                    company = row.get("company") or row.get("company_name")
                    title = row.get("title") or row.get("job_title") or row.get("role")
                    status = (row.get("status") or row.get("result") or "").lower()

                    # Count as applied if status is applied/sent/success or if file is applied history
                    if "applied" in csv_file.name or "applied" in status or "sent" in status or status == "":
                        add_applied(url, jid, company, title)
                        csv_count += 1
        except Exception:
            pass

    # 2. Search JSON files for applied history
    json_count = 0
    for json_file in root.glob("**/*.json"):
        if "local_approved_queue" in json_file.name or "node_modules" in str(json_file) or ".antigravity" in str(json_file):
            continue
        try:
            with open(json_file, "r", encoding="utf-8", errors="ignore") as f:
                data = json.load(f)
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if isinstance(item, dict):
                        url = item.get("url") or item.get("listing_url") or item.get("job_url")
                        jid = item.get("job_id") or item.get("id") or item.get("source_job_id")
                        company = item.get("company") or item.get("company_name")
                        title = item.get("title") or item.get("job_title") or item.get("role")
                        status = (item.get("status") or "").lower()

                        if "applied" in status or "sent" in status or "applied" in json_file.name:
                            add_applied(url, jid, company, title)
                            json_count += 1
        except Exception:
            pass

    # 3. Query SQLite databases (e.g. leads.db)
    db_count = 0
    for db_file in root.glob("**/*.db"):
        try:
            conn = sqlite3.connect(str(db_file))
            cursor = conn.cursor()
            tables = [t[0] for t in cursor.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
            for table in tables:
                cols = [c[1] for c in cursor.execute(f"PRAGMA table_info({table});").fetchall()]
                if any(c in cols for c in ["url", "company", "title", "job_id"]):
                    rows = cursor.execute(f"SELECT * FROM {table}").fetchall()
                    for r in rows:
                        row_dict = dict(zip(cols, r))
                        url = row_dict.get("url") or row_dict.get("job_url")
                        jid = row_dict.get("job_id") or row_dict.get("id")
                        company = row_dict.get("company")
                        title = row_dict.get("title") or row_dict.get("role")
                        add_applied(url, jid, company, title)
                        db_count += 1
            conn.close()
        except Exception:
            pass

    print(f" Loaded Historical Applied Database:")
    print(f"   Unique Applied URLs:            {len(applied_urls)}")
    print(f"   Unique Applied Job IDs:         {len(applied_job_ids)}")
    print(f"   Unique Applied Company+Titles:  {len(applied_company_title)}")

    # 4. Load current local approved queue
    with open(json_path, "r", encoding="utf-8") as f:
        current_queue = json.load(f)

    print(f"\n Current Local Queue Size: {len(current_queue)} jobs")

    filtered_queue = []
    removed_items = []

    for job in current_queue:
        url = str(job.get("url") or "").strip().lower().split('?')[0]
        jid = str(job.get("job_id") or "").strip()
        company = job.get("company") or ""
        title = job.get("title") or ""
        comp_title_key = (normalize_str(company), normalize_str(title))

        # Deduplication check
        is_already_applied = False
        reason = ""

        if url and url in applied_urls:
            is_already_applied = True
            reason = f"Matching Applied URL: {url}"
        elif jid and jid in applied_job_ids:
            is_already_applied = True
            reason = f"Matching Applied Job ID: {jid}"
        elif comp_title_key in applied_company_title:
            is_already_applied = True
            reason = f"Matching Company + Title: {company} - {title}"

        if is_already_applied:
            removed_items.append((job, reason))
        else:
            filtered_queue.append(job)

    # 5. Overwrite cleaned local queue files
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(filtered_queue, f, indent=2)

    if filtered_queue:
        headers = list(filtered_queue[0].keys())
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            writer.writerows(filtered_queue)
    else:
        # Empty CSV with header
        if current_queue:
            headers = list(current_queue[0].keys())
            with open(csv_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()

    print("\n==================================================")
    print(f" FILTERING COMPLETE!")
    print(f" Removed Previously Applied Jobs: {len(removed_items)}")
    print(f" Clean Net-New Queue Remaining:   {len(filtered_queue)} jobs")
    print("==================================================")

    if removed_items:
        print("\nSample Removed Duplicate Applications:")
        for job, reason in removed_items[:5]:
            print(f" - [REMOVED] {job.get('title')} at {job.get('company')} ({reason})")

if __name__ == "__main__":
    main()

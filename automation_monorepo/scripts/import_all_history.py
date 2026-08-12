#!/usr/bin/env python3
"""
Find and migrate all local automation job applier data to MongoDB.
Maintains copies of all original data.
"""

import os
import csv
import sys
import sqlite3
import shutil
from datetime import datetime, timezone
from pathlib import Path

# Insert project root to sys.path to import local modules if needed
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
except ImportError:
    print("Error: pymongo not installed. Please use the venv python.")
    sys.exit(1)

# Helper to find all CSV files matching history files
def discover_csv_files(root: Path) -> list[Path]:
    candidates = []
    for path in root.rglob("*.csv"):
        if "all excels" in path.parts:
            # Skip any backup folder paths to avoid duplicate import of backup files
            if "backups" not in path.parts and "migration_backup" not in path.parts:
                candidates.append(path)
    return sorted(candidates)

def infer_platform_status(path: Path) -> tuple[str, str]:
    stem = path.stem.lower()
    
    # Infer status
    status = "unknown"
    for word in ("applied", "failed", "skipped", "saved"):
        if word in stem:
            status = word
            break
            
    # Infer platform
    platform = stem
    for token in (
        "_applications_history",
        "_jobs_history",
        "_history",
        "_applied",
        "_failed",
        "_skipped",
        "_saved",
    ):
        platform = platform.replace(token, "")
    platform = platform.strip("_") or "unknown"
    if platform == "all":
        platform = "linkedin_default"
        
    return platform, status

def migrate_csv_files(client: MongoClient, db_name: str, coll_name: str, csv_files: list[Path]):
    db = client[db_name]
    coll = db[coll_name]
    
    # Create indexes
    coll.create_index([("platform", 1), ("status", 1), ("job_id", 1)], unique=True, name="platform_status_job_id_unique")
    coll.create_index([("platform", 1), ("status", 1), ("updated_at", -1)], name="platform_status_updated_at")
    
    print(f"\n--- Migrating {len(csv_files)} CSV files into {db_name}.{coll_name} ---")
    
    total_inserted = 0
    total_updated = 0
    total_skipped = 0
    
    for path in csv_files:
        platform, status = infer_platform_status(path)
        print(f"Processing: {path.relative_to(PROJECT_ROOT.parent)} -> Platform: {platform}, Status: {status}")
        
        try:
            with path.open("r", encoding="utf-8-sig", newline="", errors="ignore") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                
            file_inserted = 0
            file_updated = 0
            file_skipped = 0
            
            for row in rows:
                # Find job_id
                job_id = None
                for key in ("Job ID", "job_id", "Job Id", "id", "ID"):
                    if row.get(key):
                        job_id = str(row[key]).strip()
                        break
                        
                if not job_id:
                    file_skipped += 1
                    continue
                
                # Normalize keys and values
                cleaned_record = {str(k).strip(): "" if v is None else str(v).strip() for k, v in row.items() if str(k).strip()}
                
                now = datetime.now(timezone.utc)
                doc = {
                    "platform": platform,
                    "status": status,
                    "job_id": job_id,
                    "record": cleaned_record,
                    "source_file": str(path.relative_to(PROJECT_ROOT.parent)),
                    "updated_at": now
                }
                
                try:
                    res = coll.update_one(
                        {"platform": platform, "status": status, "job_id": job_id},
                        {"$set": doc, "$setOnInsert": {"created_at": now}},
                        upsert=True
                    )
                    if res.matched_count > 0:
                        file_updated += 1
                    else:
                        file_inserted += 1
                except PyMongoError as e:
                    print(f"  Failed to upsert job {job_id}: {e}")
                    file_skipped += 1
                    
            print(f"  Result: {len(rows)} rows -> {file_inserted} inserted, {file_updated} updated, {file_skipped} skipped")
            total_inserted += file_inserted
            total_updated += file_updated
            total_skipped += file_skipped
            
        except Exception as e:
            print(f"  Error reading CSV file {path}: {e}")
            
    print(f"CSV Migration complete: {total_inserted} inserted, {total_updated} updated, {total_skipped} skipped.")

def migrate_sqlite_db(client: MongoClient, db_name: str, sqlite_path: Path):
    if not sqlite_path.exists():
        print(f"SQLite DB not found at {sqlite_path}")
        return
        
    print(f"\n--- Migrating SQLite DB {sqlite_path.relative_to(PROJECT_ROOT.parent)} ---")
    
    db = client[db_name]
    
    # 1. Connect to SQLite
    try:
        conn = sqlite3.connect(sqlite_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
    except Exception as e:
        print(f"Failed to connect to SQLite: {e}")
        return
        
    # 2. Migrate Leads
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='leads'")
        if cursor.fetchone():
            cursor.execute("SELECT * FROM leads")
            leads = [dict(row) for row in cursor.fetchall()]
            
            leads_coll = db["lss_leads"]
            leads_coll.create_index([("email", 1), ("role", 1)], unique=True)
            
            inserted = 0
            updated = 0
            
            for lead in leads:
                email = lead.get("email")
                role = lead.get("role")
                if not email or not role:
                    continue
                    
                now = datetime.now(timezone.utc)
                lead["updated_at"] = now
                
                db_lead = lead.copy()
                created_at_val = db_lead.pop("created_at", None) or now
                
                res = leads_coll.update_one(
                    {"email": email.lower().strip(), "role": role.lower().strip()},
                    {"$set": db_lead, "$setOnInsert": {"created_at": created_at_val}},
                    upsert=True
                )
                if res.matched_count > 0:
                    updated += 1
                else:
                    inserted += 1
            print(f"Leads: {len(leads)} rows -> {inserted} inserted, {updated} updated")
        else:
            print("Table 'leads' not found in SQLite.")
    except Exception as e:
        print(f"Error migrating table 'leads': {e}")
        
    # 3. Migrate Evaluations
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='evaluations'")
        if cursor.fetchone():
            cursor.execute("SELECT * FROM evaluations")
            evals = [dict(row) for row in cursor.fetchall()]
            
            evals_coll = db["lss_evaluations"]
            evals_coll.create_index("url", unique=True)
            
            inserted = 0
            updated = 0
            
            for ev in evals:
                url = ev.get("url")
                if not url:
                    continue
                    
                now = datetime.now(timezone.utc)
                ev["updated_at"] = now
                
                res = evals_coll.update_one(
                    {"url": url},
                    {"$set": ev, "$setOnInsert": {"created_at": now}},
                    upsert=True
                )
                if res.matched_count > 0:
                    updated += 1
                else:
                    inserted += 1
            print(f"Evaluations: {len(evals)} rows -> {inserted} inserted, {updated} updated")
        else:
            print("Table 'evaluations' not found in SQLite.")
    except Exception as e:
        print(f"Error migrating table 'evaluations': {e}")
        
    conn.close()

def main():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = PROJECT_ROOT / "data" / f"migration_backup_{timestamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Backup directory created: {backup_dir.relative_to(PROJECT_ROOT.parent)}")
    
    # Locate files
    csv_files = discover_csv_files(PROJECT_ROOT.parent)
    sqlite_path = PROJECT_ROOT.parent / "data" / "lss" / "leads.db"
    
    # Back up CSVs
    print("\n--- Backing up source CSV files ---")
    for csv_file in csv_files:
        rel_path = csv_file.relative_to(PROJECT_ROOT.parent)
        dest_name = str(rel_path).replace("/", "_").replace("\\", "_")
        dest_path = backup_dir / dest_name
        shutil.copy2(csv_file, dest_path)
        print(f"Copied: {rel_path} -> {dest_path.name}")
        
    # Back up SQLite
    if sqlite_path.exists():
        print("\n--- Backing up SQLite DB ---")
        dest_path = backup_dir / "leads.db"
        shutil.copy2(sqlite_path, dest_path)
        print(f"Copied: {sqlite_path.relative_to(PROJECT_ROOT.parent)} -> leads.db")
        
    # Connect to MongoDB
    uri = os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
    db_name = os.getenv("MONGODB_HISTORY_DB") or "auto_job_applier_history"
    coll_name = os.getenv("MONGODB_HISTORY_COLLECTION") or "job_history"
    
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=2500)
        client.admin.command("ping")
        print("\nConnected to MongoDB successfully.")
    except Exception as e:
        print(f"\nError: Could not connect to MongoDB: {e}")
        sys.exit(1)
        
    # Migrate data
    migrate_csv_files(client, db_name, coll_name, csv_files)
    migrate_sqlite_db(client, db_name, sqlite_path)
    
    client.close()
    print("\nAll data migration tasks completed successfully!")

if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Push all local MongoDB data to MongoDB Atlas cloud cluster.

This script imports:
- All per-bot databases (runs, jobs, applications, errors, etc.)
- Job history databases
- System databases

Usage:
    python scripts/push_to_cloud.py [--source SOURCE_DIR] [--target-uri MONGODB_URI]

Example:
    python scripts/push_to_cloud.py --source data/cloud_export/export_20260517_232314
    python scripts/push_to_cloud.py --target-uri mongodb+srv://user:pass@cluster.mongodb.net

Author: Cascade
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError, DuplicateKeyError
    from bson.objectid import ObjectId
except ImportError:
    print("Error: pymongo not installed. Run: pip install pymongo")
    sys.exit(1)

# Import Infisical secret manager
try:
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.secret_manager import get_secret
    _HAVE_INFISICAL = True
except ImportError:
    _HAVE_INFISICAL = False
    get_secret = None


def get_mongo_uri() -> str:
    """Get MongoDB URI from Infisical secret manager, then environment fallback."""
    # Try Infisical first
    if _HAVE_INFISICAL and get_secret:
        try:
            infisical_uri = get_secret("MONGODB_URI", "")
            if infisical_uri and infisical_uri != "mongodb://localhost:27017":
                # Mask and print the source (don't show full URI)
                masked = infisical_uri.split('@')[-1] if '@' in infisical_uri else "cloud"
                print(f"✓ Using MONGODB_URI from Infisical ({masked})")
                return infisical_uri.strip()
        except Exception:
            pass
    
    # Fall back to environment variables
    uri = (
        os.environ.get("MONGODB_URI")
        or os.environ.get("MONGO_URI")
        or "mongodb://localhost:27017"
    )
    source = "environment" if (os.environ.get("MONGODB_URI") or os.environ.get("MONGO_URI")) else "default"
    print(f"⚠ Using MONGODB_URI from {source}")
    return uri.strip()


def connect_to_mongo(uri: str) -> MongoClient:
    """Connect to MongoDB and verify connection."""
    try:
        client = MongoClient(uri, serverSelectionTimeoutMS=5000)
        client.admin.command("ping")
        print(f"✓ Connected to MongoDB: {uri.split('@')[-1] if '@' in uri else uri}")
        return client
    except PyMongoError as e:
        print(f"✗ Failed to connect to MongoDB: {e}")
        sys.exit(1)


def parse_timestamp(ts: Any) -> Any:
    """Convert timestamp strings back to appropriate types."""
    if isinstance(ts, str):
        try:
            # Try parsing ISO format
            from datetime import datetime
            return datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except:
            pass
    return ts


def restore_document(doc: dict) -> dict:
    """Prepare document for MongoDB insertion."""
    # Convert string _id back to ObjectId if it's a valid ObjectId
    if "_id" in doc:
        try:
            if len(str(doc["_id"])) == 24:
                doc["_id"] = ObjectId(doc["_id"])
        except:
            pass  # Keep as string if not valid ObjectId
    
    # Convert timestamp fields
    for key in ["ts", "started_at", "ended_at", "applied_at"]:
        if key in doc:
            doc[key] = parse_timestamp(doc[key])
    
    return doc


def import_collection(
    client: MongoClient,
    db_name: str,
    collection_name: str,
    documents: list[dict],
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Import documents into a collection.
    
    Returns: (total, inserted, skipped_duplicates)
    """
    if not documents:
        return 0, 0, 0

    db = client[db_name]
    collection = db[collection_name]

    inserted = 0
    skipped = 0
    errors = 0

    for doc in documents:
        restored = restore_document(doc.copy())
        
        if dry_run:
            print(f"  [DRY-RUN] Would insert into {collection_name}: {restored.get('_id', 'new')}")
            inserted += 1
            continue

        try:
            collection.insert_one(restored)
            inserted += 1
        except DuplicateKeyError:
            skipped += 1
        except Exception as e:
            print(f"  ✗ Error inserting document: {e}")
            errors += 1

    return len(documents), inserted, skipped


def discover_export_directories(source_dir: Path) -> list[Path]:
    """Find all export directories in the source folder."""
    if source_dir.name.startswith("export_"):
        return [source_dir]
    
    export_dirs = [d for d in source_dir.iterdir() if d.is_dir() and d.name.startswith("export_")]
    return sorted(export_dirs, reverse=True)  # Most recent first


def parse_filename(filename: str) -> tuple[str, str] | None:
    """Parse database and collection name from export filename."""
    # Format: db_name__collection_name__timestamp.json
    if not filename.endswith(".json") or filename.startswith("_"):
        return None
    
    parts = filename.replace(".json", "").split("__")
    if len(parts) >= 3:
        db_name = parts[0]
        collection_name = parts[1]
        return db_name, collection_name
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Push local MongoDB data to MongoDB Atlas cloud cluster"
    )
    parser.add_argument(
        "--source",
        "-s",
        help="Source directory containing exported JSON files (default: data/cloud_export)",
        default="data/cloud_export",
    )
    parser.add_argument(
        "--target-uri",
        "-t",
        help="Target MongoDB Atlas URI (default: from MONGODB_URI env var)",
        default=None,
    )
    parser.add_argument(
        "--dry-run",
        "-d",
        action="store_true",
        help="Show what would be imported without actually importing",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )

    args = parser.parse_args()

    # Get target MongoDB URI (Atlas)
    target_uri = args.target_uri or get_mongo_uri()
    
    # Check if target is Atlas
    if "mongodb+srv" not in target_uri and "atlas" not in target_uri:
        print("⚠ Warning: Target URI doesn't look like MongoDB Atlas. Make sure you're pushing to the cloud.")
        print(f"  Current target: {target_uri[:50]}...")
        response = input("Continue anyway? (yes/no): ")
        if response.lower() != "yes":
            sys.exit(0)

    # Find source export directory
    source_path = Path(args.source)
    if not source_path.exists():
        print(f"✗ Source directory not found: {source_path}")
        sys.exit(1)

    export_dirs = discover_export_directories(source_path)
    if not export_dirs:
        print(f"✗ No export directories found in {source_path}")
        sys.exit(1)

    # Use most recent export
    export_dir = export_dirs[0]
    print(f"\nSource export: {export_dir}")
    print(f"Target: {target_uri.split('@')[-1] if '@' in target_uri else target_uri}")
    print("-" * 60)

    # Find all JSON files
    json_files = [f for f in export_dir.iterdir() if f.suffix == ".json" and not f.name.startswith("_")]
    
    if not json_files:
        print("✗ No JSON files found to import")
        sys.exit(1)

    print(f"\nFound {len(json_files)} collections to import")
    print("-" * 60)

    # Confirmation
    if not args.yes and not args.dry_run:
        response = input(f"\n⚠ This will import data into your Atlas cluster. Continue? (yes/no): ")
        if response.lower() != "yes":
            print("Aborted.")
            sys.exit(0)

    # Connect to Atlas
    client = connect_to_mongo(target_uri)

    # Import all collections
    total_docs = 0
    total_inserted = 0
    total_skipped = 0

    print("\n" + "=" * 60)
    print("IMPORTING DATA")
    print("=" * 60)

    for json_file in sorted(json_files):
        parsed = parse_filename(json_file.name)
        if not parsed:
            continue

        db_name, collection_name = parsed

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                documents = json.load(f)
        except Exception as e:
            print(f"\n✗ Failed to read {json_file.name}: {e}")
            continue

        if not documents:
            continue

        print(f"\n→ {db_name}.{collection_name} ({len(documents)} documents)")

        total, inserted, skipped = import_collection(
            client, db_name, collection_name, documents, args.dry_run
        )

        total_docs += total
        total_inserted += inserted
        total_skipped += skipped

        if inserted > 0 or skipped > 0:
            status = "[DRY-RUN] " if args.dry_run else ""
            print(f"  {status}✓ Inserted: {inserted}, Skipped (duplicates): {skipped}")

    # Summary
    print("\n" + "=" * 60)
    print("IMPORT SUMMARY")
    print("=" * 60)
    print(f"Total documents processed: {total_docs}")
    print(f"Successfully inserted: {total_inserted}")
    print(f"Skipped (duplicates): {total_skipped}")
    if args.dry_run:
        print("\n⚠ This was a DRY RUN. No data was actually imported.")
        print("   Remove --dry-run to perform the actual import.")
    print("=" * 60)

    client.close()
    print("\n✓ Done!")


if __name__ == "__main__":
    main()

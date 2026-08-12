#!/usr/bin/env python3
"""
Pull all data from MongoDB Atlas cloud cluster to local JSON files.

This script exports:
- System database (automation_system_db)
- All per-bot databases (e.g., linkedin_it_db, indeed_general_db)
- Job history database (auto_job_applier_history)

Usage:
    python scripts/pull_cloud_data.py [--uri MONGODB_URI] [--output OUTPUT_DIR]

Example:
    python scripts/pull_cloud_data.py --output data/cloud_export
    python scripts/pull_cloud_data.py --uri mongodb+srv://user:pass@cluster.mongodb.net

Author: Cascade
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

try:
    from pymongo import MongoClient
    from pymongo.errors import PyMongoError
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

# Default databases to export
SYSTEM_DB = "automation_system_db"
JOB_HISTORY_DB = "auto_job_applier_history"
EVENTS_DB = "bot_events"

# Per-bot databases (add more as needed)
BOT_DATABASES = [
    "linkedin_it_db",
    "linkedin_general_db",
    "indeed_it_db",
    "indeed_general_db",
    "glassdoor_it_db",
    "glassdoor_general_db",
]

COLLECTIONS_TO_EXPORT = {
    "runs": ["_id", "bot_id", "mode", "label", "started_at", "ended_at", "status", "error"],
    "jobs": ["_id", "bot_id", "run_id", "source", "job_id", "title", "company", "location", "url"],
    "gate_decisions": ["_id", "bot_id", "run_id", "job_id", "verdict", "score", "reasoning"],
    "applications": ["_id", "bot_id", "run_id", "job_id", "mode", "saved", "applied", "outcome"],
    "questions": ["_id", "bot_id", "run_id", "job_id", "question", "kind", "answer", "accepted"],
    "errors": ["_id", "bot_id", "run_id", "where", "error", "traceback", "screenshot_path"],
}


def get_mongo_uri() -> str:
    """Get MongoDB URI from Infisical secret manager, then environment fallback."""
    # Try Infisical first
    if _HAVE_INFISICAL and get_secret:
        try:
            infisical_uri = get_secret("MONGODB_URI", "")
            if infisical_uri and infisical_uri != "mongodb://localhost:27017":
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


def sanitize_filename(name: str) -> str:
    """Create safe filename from database/collection name."""
    return name.replace("/", "_").replace("\\", "_").replace(":", "_")


def export_collection(
    client: MongoClient,
    db_name: str,
    collection_name: str,
    output_dir: Path,
    limit: int | None = None,
) -> tuple[int, int]:
    """Export a single collection to JSON file.
    
    Returns: (total_docs, exported_docs)
    """
    db = client[db_name]
    collection = db[collection_name]

    # Count total documents
    total = collection.count_documents({})

    if total == 0:
        return 0, 0

    # Query with optional limit
    cursor = collection.find().limit(limit) if limit else collection.find()

    documents = []
    for doc in cursor:
        # Convert ObjectId to string for JSON serialization
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        # Convert datetime objects to ISO strings
        for key, value in doc.items():
            if hasattr(value, "isoformat"):
                doc[key] = value.isoformat()
        documents.append(doc)

    # Write to file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{sanitize_filename(db_name)}__{sanitize_filename(collection_name)}__{timestamp}.json"
    output_path = output_dir / filename

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(documents, f, indent=2, ensure_ascii=False, default=str)

    return total, len(documents)


def export_database(
    client: MongoClient,
    db_name: str,
    output_dir: Path,
    limit: int | None = None,
) -> dict[str, tuple[int, int]]:
    """Export all collections from a database.
    
    Returns: {collection_name: (total, exported)}
    """
    print(f"\n→ Exporting database: {db_name}")

    db = client[db_name]
    collection_names = db.list_collection_names()

    if not collection_names:
        print(f"  ⚠ No collections found in {db_name}")
        return {}

    results = {}
    for collection_name in collection_names:
        try:
            total, exported = export_collection(
                client, db_name, collection_name, output_dir, limit
            )
            results[collection_name] = (total, exported)
            if total > 0:
                print(f"  ✓ {collection_name}: {exported}/{total} documents")
        except Exception as e:
            print(f"  ✗ {collection_name}: {e}")
            results[collection_name] = (0, 0)

    return results


def discover_bot_databases(client: MongoClient) -> list[str]:
    """Discover all databases that match bot naming pattern."""
    all_dbs = client.list_database_names()
    bot_dbs = [db for db in all_dbs if db.endswith("_db") and db not in {"admin", "local", "config"}]
    return bot_dbs


def main():
    parser = argparse.ArgumentParser(
        description="Pull data from MongoDB Atlas cloud cluster to local JSON files"
    )
    parser.add_argument(
        "--uri",
        help="MongoDB connection URI (default: from MONGODB_URI env var)",
        default=None,
    )
    parser.add_argument(
        "--output",
        "-o",
        help="Output directory for exported files (default: data/cloud_export)",
        default="data/cloud_export",
    )
    parser.add_argument(
        "--limit",
        "-l",
        type=int,
        help="Limit number of documents per collection (for testing)",
        default=None,
    )
    parser.add_argument(
        "--bots-only",
        action="store_true",
        help="Export only bot databases, skip system and history",
    )
    parser.add_argument(
        "--system-only",
        action="store_true",
        help="Export only system and history databases",
    )

    args = parser.parse_args()

    # Get MongoDB URI
    uri = args.uri or get_mongo_uri()

    # Create output directory
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    export_root = output_dir / f"export_{timestamp}"
    export_root.mkdir(parents=True, exist_ok=True)

    print(f"Export destination: {export_root}")
    print("-" * 60)

    # Connect to MongoDB
    client = connect_to_mongo(uri)

    # Determine which databases to export
    databases_to_export = []

    if not args.bots_only:
        databases_to_export.extend([SYSTEM_DB, JOB_HISTORY_DB, EVENTS_DB])
        unified_db = (
            os.environ.get("JOBBOTS_MONGO_DATABASE")
            or os.environ.get("MONGODB_DB_NAME")
            or "jobbots"
        ).strip()
        if unified_db not in databases_to_export:
            databases_to_export.append(unified_db)

    if not args.system_only:
        # Discover bot databases dynamically
        discovered_bots = discover_bot_databases(client)
        print(f"\nDiscovered {len(discovered_bots)} bot databases:")
        for db in discovered_bots:
            print(f"  - {db}")
        databases_to_export.extend(discovered_bots)

    # Export all databases
    all_results = {}
    for db_name in databases_to_export:
        try:
            results = export_database(client, db_name, export_root, args.limit)
            all_results[db_name] = results
        except Exception as e:
            print(f"✗ Failed to export {db_name}: {e}")
            all_results[db_name] = {}

    # Generate summary report
    print("\n" + "=" * 60)
    print("EXPORT SUMMARY")
    print("=" * 60)

    total_docs = 0
    total_collections = 0

    for db_name, collections in all_results.items():
        if collections:
            print(f"\n{db_name}:")
            for coll_name, (total, exported) in collections.items():
                if total > 0:
                    print(f"  {coll_name}: {exported} documents")
                    total_docs += exported
                    total_collections += 1

    print(f"\n{'─' * 60}")
    print(f"Total collections exported: {total_collections}")
    print(f"Total documents exported: {total_docs}")
    print(f"Export location: {export_root}")
    print(f"{'=' * 60}")

    # Save metadata file
    metadata = {
        "export_timestamp": timestamp,
        "source_uri": uri.split("@")[-1] if "@" in uri else "local",
        "total_databases": len(all_results),
        "total_collections": total_collections,
        "total_documents": total_docs,
        "databases": {
            db: {coll: total for coll, (total, _) in colls.items()}
            for db, colls in all_results.items()
        },
    }

    metadata_path = export_root / f"_metadata_{timestamp}.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)
    print(f"\n✓ Metadata saved to: {metadata_path}")

    client.close()
    print("✓ Export complete!")


if __name__ == "__main__":
    main()

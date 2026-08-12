#!/usr/bin/env python3
"""Backfill all CSV job history into the MongoDB history collection.

This is safe to run repeatedly. Documents are upserted by
``platform + status + job_id``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.history_store import (  # noqa: E402
    connect_collection,
    discover_history_csvs,
    import_csv_file,
    load_dotenv,
    mongo_config,
    repo_root,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(repo_root()), help="Repository root to scan")
    parser.add_argument("--strict", action="store_true", help="Fail if Mongo is unavailable")
    args = parser.parse_args()

    load_dotenv()
    coll = connect_collection(strict=args.strict)
    uri, db_name, collection = mongo_config()
    print(f"Mongo history target: {db_name}.{collection} ({uri.split('@')[-1] if '@' in uri else uri})")
    if coll is None:
        print("Mongo unavailable; leaving CSV files as source for this run.")
        return 1 if args.strict else 0

    total_files = 0
    total_rows = 0
    for path in discover_history_csvs(Path(args.root)):
        count = import_csv_file(coll, path)
        total_files += 1
        total_rows += count
        print(f"{path}: {count}")

    print(f"Imported/upserted {total_rows} row(s) from {total_files} CSV file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


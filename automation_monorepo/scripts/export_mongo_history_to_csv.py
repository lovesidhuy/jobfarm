#!/usr/bin/env python3
"""Export MongoDB job history back to CSV files for compatibility."""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.history_store import (  # noqa: E402
    connect_collection,
    fieldnames_for,
    load_dotenv,
    monorepo_root,
)


def default_output_path(output_dir: Path, platform: str, status: str) -> Path:
    if status == "saved":
        return output_dir / f"{platform}_saved_jobs_history.csv"
    return output_dir / f"{platform}_{status}_applications_history.csv"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=str(monorepo_root() / "all excels"))
    parser.add_argument("--strict", action="store_true", help="Fail if Mongo is unavailable")
    args = parser.parse_args()

    load_dotenv()
    coll = connect_collection(strict=args.strict)
    if coll is None:
        print("Mongo unavailable; no CSV export written.")
        return 1 if args.strict else 0

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    grouped: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for doc in coll.find({}, {"_id": 0, "platform": 1, "status": 1, "record": 1}).sort(
        [("platform", 1), ("status", 1), ("updated_at", -1)]
    ):
        platform = doc.get("platform") or "unknown"
        status = doc.get("status") or "unknown"
        record = doc.get("record") or {}
        if record:
            grouped[(platform, status)].append(record)

    total = 0
    for (platform, status), records in sorted(grouped.items()):
        path = default_output_path(output_dir, platform, status)
        fieldnames = fieldnames_for(records)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        total += len(records)
        print(f"{path}: {len(records)}")

    print(f"Exported {total} Mongo history row(s) into {len(grouped)} CSV file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

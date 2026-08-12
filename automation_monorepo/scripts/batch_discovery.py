#!/usr/bin/env python3
"""Run Indeed IT discovery in batches of 10 keywords.

This script imports the configured search terms, splits them into chunks
of 10, and invokes supervisor.py sequentially for each batch.
"""

import sys
import subprocess
from pathlib import Path

# Add monorepo root to sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

try:
    from config.it.search import search_terms
except ImportError as exc:
    print(f"Error: Could not import search_terms from config.it.search: {exc}")
    sys.exit(1)

def chunk_list(lst, chunk_size):
    return [lst[i:i + chunk_size] for i in range(0, len(lst), chunk_size)]

def main():
    if not search_terms:
        print("Error: No search terms configured in config/it/search.py.")
        sys.exit(1)

    batches = chunk_list(search_terms, 10)
    total_batches = len(batches)
    print(f"[Batch Discovery] Loaded {len(search_terms)} search terms divided into {total_batches} batches.")

    for idx, batch in enumerate(batches, start=1):
        print("\n" + "=" * 80)
        print(f"[Batch Discovery] Starting Batch {idx}/{total_batches}")
        print(f"Keywords: {', '.join(batch)}")
        print("=" * 80 + "\n")

        # Comma-separated list of keywords for --keyword flag
        keyword_arg = ",".join(batch)

        cmd = [
            sys.executable,
            "supervisor.py",
            "--stage", "discover",
            "--once",
            "--portal", "indeed",
            "--profile", "it",
            "--keyword", keyword_arg
        ]

        try:
            # Inherit current environment (keeps DISCOVERY_ENGINE, etc.)
            result = subprocess.run(cmd, cwd=str(repo_root), check=True)
            print(f"\n[Batch Discovery] Batch {idx}/{total_batches} completed successfully.")
        except subprocess.CalledProcessError as exc:
            print(f"\n[Batch Discovery] Batch {idx}/{total_batches} failed with exit code {exc.returncode}.")
            # Ask user if they want to continue on failure or stop
            print("[Batch Discovery] Stopping execution. You can resume from batch index.")
            sys.exit(exc.returncode)

if __name__ == "__main__":
    main()

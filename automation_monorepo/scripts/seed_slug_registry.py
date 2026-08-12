#!/usr/bin/env python3
"""Bulk-seed the ATS slug registry from static lists.

Accepts company slugs/tokens in nearly any shape and UPSERTs them into the
registry with ``status=active``, ``discovery_source=manual_seed`` and
``last_successful_poll_at=null`` (existing rows are only *touched* —
``last_seen_at`` updates; original provenance is preserved).

Input formats (auto-detected, or force with ``--format``)
---------------------------------------------------------
- **Raw text** — one entry per line; bare slugs, company names, or full
  career URLs (``https://boards.greenhouse.io/acme/jobs/123`` → ``acme``).
  ``#`` comments and blank lines ignored.
- **JSON** — array of strings, or array of objects
  (``{"slug": "acme", "platform": "greenhouse"}`` /
   ``{"company": "Acme", "greenhouse": "acme"}`` shapes tolerated).
- **CSV** — columns auto-detected: ``slug``/``token``/``board_token``,
  optional ``platform``/``ats``, optional ``company``/``name``. With no
  header, first column = slug, optional second = platform.

Platform resolution per row: explicit column/field > URL evidence >
``--platform`` default (required when undetectable).

Usage
-----
  .venv/bin/python scripts/seed_slug_registry.py slugs.txt --platform greenhouse
  .venv/bin/python scripts/seed_slug_registry.py master_list.csv
  .venv/bin/python scripts/seed_slug_registry.py boards.json --dry-run
  cat tokens.txt | .venv/bin/python scripts/seed_slug_registry.py - --platform lever
  .venv/bin/python scripts/seed_slug_registry.py --slugs acme initech --platform lever
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery.ats_slugs import (  # noqa: E402
    SUPPORTED_PLATFORMS,
    clean_slug,
    extract_slugs_from_url,
    platform_for_url,
)
from core.discovery.slug_registry import get_registry  # noqa: E402


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------

_SLUG_COLS = ("slug", "token", "board_token", "board", "handle", "id")
_PLATFORM_COLS = ("platform", "ats", "type", "provider")
_COMPANY_COLS = ("company", "name", "company_name", "org", "organization")


def _iter_text_rows(text: str) -> Iterable[dict[str, Any]]:
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        yield {"raw": s}


def _iter_json_rows(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, dict):
        # {"greenhouse": [...], "lever": [...]} shape
        for key, val in data.items():
            if isinstance(val, list) and key.strip().lower() in SUPPORTED_PLATFORMS:
                for item in val:
                    if isinstance(item, str):
                        yield {"raw": item, "platform": key.strip().lower()}
                    elif isinstance(item, dict):
                        yield {**item, "platform": key.strip().lower()}
        # or a single object
        if any(k in data for k in _SLUG_COLS + _COMPANY_COLS):
            yield data
        return
    if isinstance(data, list):
        for item in data:
            if isinstance(item, str):
                yield {"raw": item}
            elif isinstance(item, dict):
                yield item


def _iter_csv_rows(text: str) -> Iterable[dict[str, Any]]:
    sample = text[:2048]
    has_header = csv.Sniffer().has_header(sample) if sample.strip() else False
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return
    if has_header:
        header = [h.strip().lower() for h in rows[0]]
        for r in rows[1:]:
            yield {header[i]: r[i].strip() for i in range(min(len(header), len(r)))}
    else:
        for r in rows:
            out: dict[str, Any] = {"raw": r[0].strip()}
            if len(r) > 1 and r[1].strip():
                out["platform"] = r[1].strip()
            yield out


def _detect_format(path: Path | None, text: str, forced: str | None) -> str:
    if forced:
        return forced
    if path is not None:
        suf = path.suffix.lower()
        if suf == ".json":
            return "json"
        if suf == ".csv":
            return "csv"
    stripped = text.lstrip()
    if stripped.startswith("[") or stripped.startswith("{"):
        return "json"
    # CSV heuristic: commas in most non-empty lines
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    if lines and sum("," in ln for ln in lines) >= max(1, len(lines) // 2):
        return "csv"
    return "text"


def parse_inputs(text: str, fmt: str) -> list[dict[str, Any]]:
    if fmt == "json":
        try:
            return list(_iter_json_rows(json.loads(text)))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON: {exc}")
    if fmt == "csv":
        return list(_iter_csv_rows(text))
    return list(_iter_text_rows(text))


# ---------------------------------------------------------------------------
# Row normalisation
# ---------------------------------------------------------------------------

def _row_to_slug_platform(row: dict[str, Any], default_platform: str | None) -> tuple[str, str, str]:
    """Return ``(slug, platform, company)``; slug/platform may be ""."""
    company = ""
    for col in _COMPANY_COLS:
        if row.get(col):
            company = str(row[col]).strip()
            break

    platform = ""
    for col in _PLATFORM_COLS:
        if row.get(col):
            platform = str(row[col]).strip().lower()
            break

    candidate = ""
    for col in _SLUG_COLS:
        if row.get(col):
            candidate = str(row[col]).strip()
            break
    if not candidate:
        candidate = str(row.get("raw") or "").strip()
    if not candidate and company:
        candidate = company

    # URL evidence wins for both slug + platform.
    if "://" in candidate or candidate.startswith(("www.", "boards.", "jobs.", "job-boards.")):
        pairs = extract_slugs_from_url(candidate)
        if pairs:
            url_platform, url_slug = pairs[0]
            return url_slug, url_platform, company
        # GH/Lever URL but unparsable — fall through to token cleaning.
        if not platform:
            platform = platform_for_url(candidate) or ""

    slug = clean_slug(candidate)
    if not platform:
        platform = (default_platform or "").strip().lower()
    return slug, platform, company


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="Input file path, or '-' for stdin")
    ap.add_argument("--slugs", nargs="+", default=None, help="Inline slugs instead of a file")
    ap.add_argument("--platform", default=None, choices=list(SUPPORTED_PLATFORMS),
                    help="Default platform when rows don't specify one")
    ap.add_argument("--format", default=None, choices=["text", "json", "csv"], help="Force input format")
    ap.add_argument("--source", default="manual_seed", help="discovery_source tag (default manual_seed)")
    ap.add_argument("--backend", default=None, choices=["auto", "mongo", "json"], help="Registry backend")
    ap.add_argument("--dry-run", action="store_true", help="Parse + report, no writes")
    args = ap.parse_args()

    if args.slugs:
        text = "\n".join(args.slugs)
        path = None
    else:
        if not args.input:
            ap.error("provide an input file, '-' for stdin, or --slugs")
        if args.input == "-":
            text = sys.stdin.read()
            path = None
        else:
            path = Path(args.input)
            if not path.is_file():
                raise SystemExit(f"Input not found: {path}")
            text = path.read_text(encoding="utf-8", errors="replace")

    fmt = _detect_format(path, text, args.format)
    rows = parse_inputs(text, fmt)
    print(f"parsed {len(rows)} row(s) as format={fmt}")

    registry = None if args.dry_run else get_registry(force_backend=args.backend)

    counts = {"inserted": 0, "updated": 0, "invalid": 0, "no_platform": 0}
    invalid_rows: list[str] = []
    for row in rows:
        slug, platform, company = _row_to_slug_platform(row, args.platform)
        if not slug:
            counts["invalid"] += 1
            invalid_rows.append(str(row)[:80])
            continue
        if platform not in SUPPORTED_PLATFORMS:
            counts["no_platform"] += 1
            invalid_rows.append(f"{slug} (platform={platform or '?'})")
            continue
        if args.dry_run:
            counts["inserted"] += 1  # would-be count
            continue
        outcome = registry.upsert_slug(slug, platform, source=args.source, company_name=company or None)
        counts[outcome if outcome in counts else "invalid"] += 1

    label = "DRY-RUN " if args.dry_run else ""
    print(
        f"{label}seed complete: inserted={counts['inserted']} updated={counts['updated']} "
        f"invalid={counts['invalid']} no_platform={counts['no_platform']}"
    )
    if invalid_rows:
        print("rejected rows (first 10):")
        for r in invalid_rows[:10]:
            print(f"  - {r}")
    if not args.dry_run:
        try:
            print(f"registry stats: {registry.stats()}")
        except Exception:
            pass
    return 0 if counts["inserted"] or counts["updated"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

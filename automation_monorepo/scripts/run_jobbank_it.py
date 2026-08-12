#!/usr/bin/env python3
"""Cloud Job Bank production lane for the complete configured IT term set.

Job Bank discovery emits only authenticated Direct Apply postings.  The
application worker reuses the logged-in ``jobbank_it`` NST profile; email
application is retired.
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRAPERS = ROOT / "scrapers"
MONOREPO = ROOT / "automation_monorepo"
sys.path.insert(0, str(SCRAPERS))
sys.path.insert(0, str(MONOREPO))


def _hero_terms() -> list[str]:
    path = MONOREPO / "config" / "it" / "hero_terms.py"
    spec = importlib.util.spec_from_file_location("jobbots_hero_terms", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load configured search terms: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return [str(term).strip() for term in module.HERO_SEARCH_TERMS if str(term).strip()]


def _core_terms() -> list[str]:
    """Smaller productive term set — finishes before location typeahead burns out."""
    path = MONOREPO / "config" / "it" / "hero_terms.py"
    if not path.exists():
        return []
    spec = importlib.util.spec_from_file_location("jobbots_hero_terms_core", path)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    raw = getattr(module, "PORTAL_CORE_TERMS", None) or []
    return [str(term).strip() for term in raw if str(term).strip()]


def _search_terms() -> list[str]:
    override = os.getenv("JOBBOTS_JOBBANK_KEYWORDS", "").strip()
    if override:
        return [
            t.strip().strip('"').strip("'")
            for t in override.split(",")
            if t.strip().strip('"').strip("'")
        ]
    mode = os.getenv("JOBBOTS_JOBBANK_TERM_SET", "core").strip().lower()
    if mode in {"hero", "full", "all"}:
        return _hero_terms()
    core = _core_terms()
    return core or _hero_terms()


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    from jobbank_scraper import run_scraper

    # Default to Metro Vancouver SERP — Canada-wide wastes the cycle on
    # Toronto/ON/etc. rows that location policy correctly rejects.
    location = (
        os.getenv("JOBBOTS_JOBBANK_LOCATION", "Vancouver, BC").strip()
        or "Vancouver, BC"
    )
    max_results = max(1, int(os.getenv("JOBBOTS_JOBBANK_MAX_RESULTS", "35")))
    terms = _search_terms()
    print(f"[Job Bank] Starting {len(terms)} configured IT terms in {location}; max={max_results}/term")

    failures: list[str] = []
    for index, term in enumerate(terms, start=1):
        print(f"[Job Bank] {index}/{len(terms)}: {term}")
        try:
            run_scraper(query=term, location=location, max_results=max_results)
        except Exception as exc:
            failures.append(f"{term}: {exc}")
            print(f"[Job Bank] Term failed, continuing: {term}: {exc}", file=sys.stderr)

    print("[Job Bank] Collection complete; Direct Apply rows were queued for the jobbank_it worker.")

    if failures:
        raise SystemExit(f"Job Bank completed with {len(failures)} term failure(s): {'; '.join(failures[:5])}")


if __name__ == "__main__":
    main()

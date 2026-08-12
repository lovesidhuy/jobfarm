"""Skip term×location cells that produced 0 net-new for N consecutive ticks.

Env:
  DISCOVERY_TERM_MEMORY=1 (default on)
  DISCOVERY_TERM_SKIP_AFTER=2   # zero-new ticks before skip
  DISCOVERY_TERM_SKIP_HOURS=12  # how long a burned cell stays skipped
  DISCOVERY_TERM_MEMORY_PATH     # default artifacts/term_productivity.json
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT

_log = logging.getLogger("discovery.term_memory")


def memory_enabled() -> bool:
    return os.getenv("DISCOVERY_TERM_MEMORY", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _path() -> Path:
    raw = (os.getenv("DISCOVERY_TERM_MEMORY_PATH") or "").strip()
    if raw:
        return Path(raw)
    return _MONOREPO_ROOT / "artifacts" / "term_productivity.json"


def _skip_after() -> int:
    try:
        return max(1, int(os.getenv("DISCOVERY_TERM_SKIP_AFTER", "2") or "2"))
    except ValueError:
        return 2


def _skip_seconds() -> float:
    try:
        h = float(os.getenv("DISCOVERY_TERM_SKIP_HOURS", "12") or "12")
    except ValueError:
        h = 12.0
    return max(1.0, h) * 3600.0


def _load() -> dict:
    p = _path()
    if not p.is_file():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=0, sort_keys=True), encoding="utf-8")


def cell_id(portal: str, term: str, location: str) -> str:
    return f"{(portal or '').lower()}|{(term or '').strip().lower()}|{(location or '').strip().lower()}"


def should_skip(portal: str, term: str, location: str) -> bool:
    if not memory_enabled():
        return False
    data = _load()
    row = data.get(cell_id(portal, term, location)) or {}
    until = float(row.get("skip_until") or 0)
    if until and time.time() < until:
        _log.info("Term memory skip %s / %r @ %r", portal, term, location)
        return True
    return False


def record_outcome(portal: str, term: str, location: str, *, new_count: int) -> None:
    if not memory_enabled():
        return
    data = _load()
    cid = cell_id(portal, term, location)
    row = data.get(cid) or {"zero_streak": 0}
    if int(new_count or 0) > 0:
        row["zero_streak"] = 0
        row.pop("skip_until", None)
        row["last_new"] = time.time()
    else:
        row["zero_streak"] = int(row.get("zero_streak") or 0) + 1
        if row["zero_streak"] >= _skip_after():
            row["skip_until"] = time.time() + _skip_seconds()
            _log.info(
                "Term memory burn %s / %r @ %r (zeros=%d, skip %.0fh)",
                portal, term, location, row["zero_streak"], _skip_seconds() / 3600.0,
            )
    row["last_seen"] = time.time()
    data[cid] = row
    # Prune old keys (>30d)
    cutoff = time.time() - 30 * 86400
    data = {k: v for k, v in data.items() if float((v or {}).get("last_seen") or 0) >= cutoff}
    try:
        _save(data)
    except Exception as exc:
        _log.debug("term memory save failed: %s", exc)

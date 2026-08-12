"""
Per-bot run-state checkpointing.

Lets a bot resume after a crash from where it left off instead of restarting
the entire job-list. State is a small JSON file under `data/state/`:

    {
      "run_id": "...",
      "cursor": {"search_term_idx": 2, "page": 4, "card_idx": 11},
      "queue": ["job_id_a", "job_id_b", ...],
      "last_completed_job_id": "...",
      "updated_at": 1700000000.0
    }

The bot calls `checkpoint.save(...)` after each job processed. The supervisor
calls `checkpoint.load()` after a restart to pick up where it stopped.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Checkpoint:
    bot_id: str
    state_dir: pathlib.Path
    name: str = "run_state"

    def __post_init__(self) -> None:
        self.state_dir = pathlib.Path(self.state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / f"{self.name}.json"

    def save(self, *, run_id: str, cursor: dict[str, Any],
             queue: Optional[list[str]] = None,
             last_completed_job_id: Optional[str] = None,
             extra: Optional[dict[str, Any]] = None) -> None:
        payload = {
            "bot_id": self.bot_id, "run_id": run_id,
            "cursor": cursor, "queue": queue or [],
            "last_completed_job_id": last_completed_job_id,
            "updated_at": time.time(), "extra": extra or {},
        }
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, default=str), encoding="utf-8")
        os.replace(tmp, self.path)

    def load(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def clear(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

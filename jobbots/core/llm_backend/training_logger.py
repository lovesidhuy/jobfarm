"""
Per-bot training-data capture.

Writes two streams to `data/training/`:
    qa-YYYYMMDD.jsonl       every (question, answer, context) the bot saw
    outcomes-YYYYMMDD.jsonl every gate verdict + final apply outcome

Schema is intentionally simple and append-only so it is trivial to ship to a
fine-tuning pipeline later. Each line is a self-contained JSON object.

Example QA line:
    {"ts": 1700000000.0, "bot_id": "linkedin_it", "run_id": "...",
     "job_id": "...", "question": "Years of Python experience?",
     "kind": "numeric", "answer": "5", "source": "ai", "provider": "groq",
     "accepted": true, "context": {"job_title": "...", "company": "..."}}

Example outcome line:
    {"ts": ..., "bot_id": ..., "run_id": ..., "job_id": ...,
     "gate_verdict": "worth_applying", "gate_score": 0.82,
     "applied": true, "saved": false, "mode": "easy"}
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class TrainingLogger:
    bot_id: str
    training_dir: pathlib.Path

    def __post_init__(self) -> None:
        self.training_dir = pathlib.Path(self.training_dir)
        self.training_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, prefix: str) -> pathlib.Path:
        return self.training_dir / f"{prefix}-{time.strftime('%Y%m%d')}.jsonl"

    def _append(self, path: pathlib.Path, payload: dict) -> None:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload, default=str) + os.linesep)
        except OSError:
            pass

    def log_qa(self, *, run_id: str, job_id: str, question: str, kind: str,
               answer: str, source: str, provider: str,
               accepted: Optional[bool], context: Optional[dict[str, Any]] = None) -> None:
        self._append(self._path("qa"), {
            "ts": time.time(), "bot_id": self.bot_id, "run_id": run_id,
            "job_id": job_id, "question": question, "kind": kind,
            "answer": answer, "source": source, "provider": provider,
            "accepted": accepted, "context": context or {},
        })

    def log_outcome(self, *, run_id: str, job_id: str,
                    gate_verdict: Optional[str], gate_score: Optional[float],
                    applied: bool, saved: bool, mode: str,
                    extra: Optional[dict[str, Any]] = None) -> None:
        self._append(self._path("outcomes"), {
            "ts": time.time(), "bot_id": self.bot_id, "run_id": run_id,
            "job_id": job_id, "gate_verdict": gate_verdict,
            "gate_score": gate_score, "applied": applied, "saved": saved,
            "mode": mode, **(extra or {}),
        })

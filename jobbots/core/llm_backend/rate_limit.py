"""
Per-bot, file-backed token bucket. NO process-shared state.

Used to throttle calls to rate-limited providers (e.g. Groq). Each bot keeps its
own bucket file under `data/state/`. A failure or backoff in one bot is
INVISIBLE to other bots because the file is bot-local.

Design:
- Refill: continuously, `rate` tokens per second up to `capacity`.
- `try_acquire(n)`: non-blocking; returns True iff at least n tokens were
  available, decrements bucket, persists.
- `acquire(n, max_wait)`: blocks up to `max_wait` seconds, sleeping between
  checks. Returns True if eventually acquired, False on timeout.
- File lock with `fcntl` to handle the rare case of two run instances of the
  SAME bot starting at once. Cross-bot coordination is intentionally absent.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
from dataclasses import dataclass


try:
    import fcntl  # POSIX
    _HAVE_FCNTL = True
except ImportError:  # Windows fallback: best-effort, no locking
    _HAVE_FCNTL = False


@dataclass
class TokenBucket:
    state_path: pathlib.Path
    rate: float          # tokens/sec
    capacity: float      # max tokens

    def __post_init__(self) -> None:
        self.state_path = pathlib.Path(self.state_path)
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.state_path.exists():
            self._write({"tokens": float(self.capacity), "ts": time.time()})

    def _read(self) -> dict:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, FileNotFoundError):
            return {"tokens": float(self.capacity), "ts": time.time()}

    def _write(self, state: dict) -> None:
        tmp = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def _refill(self, state: dict) -> dict:
        now = time.time()
        elapsed = max(0.0, now - state.get("ts", now))
        tokens = min(self.capacity, state.get("tokens", self.capacity) + elapsed * self.rate)
        return {"tokens": tokens, "ts": now}

    def try_acquire(self, n: float = 1.0) -> bool:
        with self._locked() as f:
            state = self._refill(self._read())
            if state["tokens"] >= n:
                state["tokens"] -= n
                self._write(state)
                return True
            self._write(state)
            return False

    def acquire(self, n: float = 1.0, max_wait: float = 5.0, poll: float = 0.2) -> bool:
        deadline = time.time() + max_wait
        while True:
            if self.try_acquire(n):
                return True
            if time.time() >= deadline:
                return False
            time.sleep(poll)

    def peek(self) -> float:
        return self._refill(self._read())["tokens"]

    # ── internal: very lightweight per-bot file lock ──────────────────────────
    class _Lock:
        def __init__(self, path: pathlib.Path):
            self.path = path.with_suffix(path.suffix + ".lock")
            self.fh = None

        def __enter__(self):
            self.fh = open(self.path, "a+")
            if _HAVE_FCNTL:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
            return self.fh

        def __exit__(self, *exc):
            if self.fh is not None:
                if _HAVE_FCNTL:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
                self.fh.close()

    def _locked(self):
        return self._Lock(self.state_path)

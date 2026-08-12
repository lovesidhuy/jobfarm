"""
Per-bot supervisor: watchdog + restart with exponential backoff.

Wraps the bot's main loop so an uncaught exception or browser death does NOT
end the run. The supervisor:
- catches exceptions from `target()`,
- writes a structured error to disk (and to MongoDB if connected),
- sleeps with exponential backoff,
- restarts `target()`,
- bails out after `max_restarts_per_window` to prevent crash-loops.

Use it like:
    sup = Supervisor(bot_id=cfg.bot_id, snapshots_dir=cfg.snapshots_dir,
                     on_error=store.record_error)
    sup.run(lambda: bot_main(cfg))

`target` is responsible for resuming from `Checkpoint` if needed — the
supervisor only restarts the *process loop*, not the per-job state machine.
"""

from __future__ import annotations

import json
import pathlib
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Supervisor:
    bot_id: str
    snapshots_dir: pathlib.Path
    on_error: Optional[Callable[..., None]] = None  # signature compat with MongoStore.record_error
    max_restarts_per_window: int = 8
    window_seconds: int = 600
    base_backoff: float = 2.0
    max_backoff: float = 60.0
    _restart_times: list[float] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        self.snapshots_dir = pathlib.Path(self.snapshots_dir)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    def run(self, target: Callable[[], None], *, run_id: str = "supervised") -> int:
        attempt = 0
        while True:
            try:
                target()
                return 0
            except KeyboardInterrupt:
                self._snapshot(run_id, "keyboard_interrupt", "")
                return 130
            except Exception as exc:  # noqa: BLE001
                tb = traceback.format_exc()
                self._snapshot(run_id, type(exc).__name__, tb)
                if self.on_error:
                    try:
                        self.on_error(run_id=run_id, where="supervisor",
                                      error=f"{type(exc).__name__}: {exc}",
                                      traceback_str=tb)
                    except Exception:  # noqa: BLE001
                        pass
                if not self._can_restart():
                    return 1
                backoff = min(self.max_backoff, self.base_backoff * (2 ** attempt))
                attempt += 1
                time.sleep(backoff)

    def _can_restart(self) -> bool:
        now = time.time()
        self._restart_times = [t for t in self._restart_times if now - t < self.window_seconds]
        if len(self._restart_times) >= self.max_restarts_per_window:
            return False
        self._restart_times.append(now)
        return True

    def _snapshot(self, run_id: str, kind: str, traceback_str: str) -> None:
        path = self.snapshots_dir / f"crash-{int(time.time())}-{kind}.json"
        try:
            path.write_text(json.dumps({
                "bot_id": self.bot_id, "run_id": run_id,
                "kind": kind, "ts": time.time(),
                "traceback": traceback_str,
            }), encoding="utf-8")
        except OSError:
            pass

"""
Provider fallback chain with a per-bot, file-backed circuit breaker.

NO shared service. Each bot constructs its own ProviderChain at startup and
calls it directly. If Groq is rate-limited or down, the breaker for THIS BOT
opens and the chain falls through to Ollama. Other bots are unaffected.

Usage:
    chain = ProviderChain(
        providers=[
            Provider("groq", call_groq, bucket=groq_bucket),
            Provider("ollama", call_ollama),
        ],
        breaker_dir=cfg.state_dir,
    )
    result = chain.ask(prompt, task_type="gate_decision")

`call_groq` / `call_ollama` are bot-supplied callables of signature:
    fn(prompt: str, task_type: str, **kwargs) -> str

The chain only adds: rate-limit gating, circuit-breaker, retries, structured
logging, and provider tagging on the result.
"""

from __future__ import annotations

import json
import os
import pathlib
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .rate_limit import TokenBucket


ProviderFn = Callable[..., str]


@dataclass
class ProviderResult:
    text: str
    provider: str
    latency_ms: float
    attempts: int
    error: Optional[str] = None


@dataclass
class Provider:
    name: str
    fn: ProviderFn
    bucket: Optional[TokenBucket] = None       # None = no rate limit gating
    cooldown_seconds: float = 30.0             # circuit-breaker open duration on failure
    max_retries: int = 1                       # in-provider retries before giving up


class CircuitBreaker:
    """File-backed open/close breaker, per provider, per bot."""

    def __init__(self, state_dir: pathlib.Path, provider: str):
        self.state_dir = pathlib.Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / f"breaker_{provider}.json"

    def is_open(self) -> bool:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return time.time() < float(data.get("open_until", 0))
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    def open_for(self, seconds: float, reason: str = "") -> None:
        self.path.write_text(
            json.dumps({"open_until": time.time() + seconds, "reason": reason}),
            encoding="utf-8",
        )

    def close(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


@dataclass
class ProviderChain:
    providers: list[Provider]
    breaker_dir: pathlib.Path
    log_path: Optional[pathlib.Path] = None
    _breakers: dict[str, CircuitBreaker] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        self.breaker_dir = pathlib.Path(self.breaker_dir)
        self.breaker_dir.mkdir(parents=True, exist_ok=True)
        self._breakers = {p.name: CircuitBreaker(self.breaker_dir, p.name) for p in self.providers}
        if self.log_path:
            self.log_path = pathlib.Path(self.log_path)
            self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def ask(self, prompt: str, task_type: str = "default", **kwargs: Any) -> ProviderResult:
        last_error: Optional[str] = None
        for provider in self.providers:
            breaker = self._breakers[provider.name]
            if breaker.is_open():
                self._log({"event": "skip_open", "provider": provider.name, "task": task_type})
                continue
            if provider.bucket and not provider.bucket.try_acquire(1.0):
                self._log({"event": "rate_limited", "provider": provider.name, "task": task_type})
                breaker.open_for(min(provider.cooldown_seconds, 5.0), reason="rate_limit_local")
                continue

            attempts = 0
            t0 = time.time()
            while attempts <= provider.max_retries:
                attempts += 1
                try:
                    text = provider.fn(prompt, task_type=task_type, **kwargs)
                    latency_ms = (time.time() - t0) * 1000
                    self._log({
                        "event": "ok", "provider": provider.name, "task": task_type,
                        "attempts": attempts, "latency_ms": round(latency_ms, 1),
                    })
                    breaker.close()
                    return ProviderResult(
                        text=text, provider=provider.name,
                        latency_ms=latency_ms, attempts=attempts,
                    )
                except Exception as exc:  # noqa: BLE001 — provider errors are intentionally broad
                    last_error = f"{type(exc).__name__}: {exc}"
                    self._log({
                        "event": "error", "provider": provider.name, "task": task_type,
                        "attempt": attempts, "error": last_error,
                        "trace": traceback.format_exc(limit=2),
                    })
                    if attempts > provider.max_retries:
                        breaker.open_for(provider.cooldown_seconds, reason=last_error[:200])
                        break
                    time.sleep(0.5 * attempts)

        return ProviderResult(
            text="", provider="none", latency_ms=0.0, attempts=0,
            error=last_error or "all providers unavailable",
        )

    def _log(self, payload: dict) -> None:
        if not self.log_path:
            return
        payload = {"ts": time.time(), **payload}
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + os.linesep)
        except OSError:
            pass

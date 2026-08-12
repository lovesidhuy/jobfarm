# `core/` — vendored library (per-bot copy)

This directory is **vendored** into every bot. Each bot has its own copy.
**Do not** turn this into a shared installable package: a single shared
runtime would couple the bots and a failure in one would affect the others —
the exact thing this architecture exists to prevent.

## What is in here

| File                  | Responsibility                                                          |
|-----------------------|--------------------------------------------------------------------------|
| `config.py`           | Loads `config/bot.yaml` into a typed `BotConfig`.                        |
| `policy.py`           | Pure decision helpers: `should_apply`, `should_save`, `glassdoor_enabled`. |
| `rate_limit.py`       | File-backed token bucket (per-bot, isolated).                            |
| `fallback.py`         | Provider chain (Groq → Ollama) with file-backed circuit breaker.        |
| `ai_client.py`        | Facade the bot calls for AI; wires chain + store + trainer.              |
| `db.py`               | Mongo writer with per-bot DB name; JSONL fallback if Mongo down.         |
| `training_logger.py`  | Append-only QA + outcomes JSONL streams for fine-tuning.                |
| `state.py`            | Run-state checkpoint for crash-resume.                                   |
| `supervisor.py`       | Watchdog/restart with exponential backoff and crash snapshots.           |

## Public API

```python
from core.config import load_bot_config
from core.policy import GateOutcome, should_apply, should_save
from core.rate_limit import TokenBucket
from core.fallback import Provider, ProviderChain
from core.db import MongoStore
from core.training_logger import TrainingLogger
from core.state import Checkpoint
from core.supervisor import Supervisor
from core.ai_client import AIClient
```

## Wiring template (each bot's `runAiBot.py` follows this)

```python
cfg = load_bot_config("config/bot.yaml")
cfg.ensure_dirs()

store = MongoStore(bot_id=cfg.bot_id, uri=cfg.mongodb_uri,
                   database=cfg.mongodb_database,
                   fallback_dir=cfg.data_dir / "db_fallback")

trainer = TrainingLogger(bot_id=cfg.bot_id, training_dir=cfg.training_dir)

groq_bucket = TokenBucket(cfg.state_dir / "groq_bucket.json",
                          rate=0.5, capacity=20)

chain = ProviderChain(
    providers=[
        Provider("groq",   call_groq_chat,   bucket=groq_bucket),
        Provider("ollama", call_ollama_chat),
    ],
    breaker_dir=cfg.state_dir,
    log_path=cfg.logs_dir / "ai.jsonl",
)

ai = AIClient(bot_id=cfg.bot_id, chain=chain, store=store, trainer=trainer)

run_id = store.start_run(mode="full", label="manual")
ckpt = Checkpoint(bot_id=cfg.bot_id, state_dir=cfg.state_dir)

def main():
    # ... existing bot logic ...
    pass

Supervisor(bot_id=cfg.bot_id, snapshots_dir=cfg.snapshots_dir,
           on_error=store.record_error).run(main, run_id=run_id)

store.end_run(run_id, status="ok")
```

## Isolation guarantees (must hold)

- No imports from `modules/` or any bot-specific path.
- All file I/O lives under the bot's `data/` tree.
- Mongo connections are per-bot, per-process; never reused across bots.
- Circuit breakers and token buckets are file-backed per-bot.
- Training data is per-bot; no cross-bot mixing at write time. (Centralized
  analytics later: read-only export is fine, runtime coupling is not.)

## Updating `core/`

1. Edit only the LinkedIn-IT copy at `Auto_job_applier_linkedIn/core/`.
2. Run `bash sync_core.sh` from the workspace root to vendor the changes into
   the other three bots.
3. Bump `CORE_VERSION` in `core/__init__.py` if the public API changed.

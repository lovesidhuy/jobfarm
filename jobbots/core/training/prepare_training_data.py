#!/usr/bin/env python3
"""
Consolidate training data from Indeed-IT and Indeed-General bots into a single
corpus for later fine-tuning / analytics use.

Outputs land in ./training_data_corpus/ with strict per-bot isolation
(subfolders) plus a `combined/` folder for cross-bot merged data.

Per architecture rules: each bot remains self-contained; this script ONLY
READS from each bot dir and writes to a separate top-level corpus directory.
It does not modify bot internals.
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "training_data_corpus"

BOTS = {
    "indeed_it": {
        "dir": _MONOREPO_ROOT,  # automation_monorepo root
        "log": "logs/indeed_it/indeed_it_training_log.jsonl",
        "extra_logs": [
            "logs/indeed_it/indeed_training_log.jsonl",  # legacy name
            "data/training/from_prod/run_latest/repo-artifacts/master/it_indeed cwgeopy/Auto_indeed/logs/indeed_it/indeed_it_training_log.jsonl",
        ],
        "exports": [
            "all excels/indeed_it_applied_history.csv",
            "all excels/indeed_it_failed_history.csv",
        ],
        "training_data_dir": "data/training/it_data",
    },
    "indeed_general": {
        "dir": _MONOREPO_ROOT,
        "log": "logs/indeed_general/indeed_general_training_log.jsonl",
        "extra_logs": [
            "logs/indeed_general/indeed_training_log.jsonl",
        ],
        "exports": [
            "all excels/indeed_general_applied_history.csv",
            "all excels/indeed_general_failed_history.csv",
        ],
        "training_data_dir": "data/training/general_data",
    },
}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    out = []
    with path.open("r", encoding="utf-8") as f:
        for ln, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  [warn] {path.name}:{ln} bad json ({e})")
    return out


def load_events_from_mongo(bot_id: str) -> list[dict[str, Any]]:
    try:
        import os
        from pymongo import MongoClient
        uri = os.getenv("MONGODB_URI") or "mongodb://localhost:27017"
        client = MongoClient(uri, serverSelectionTimeoutMS=2000)
        client.admin.command("ping")
        
        db_name = os.getenv("JOBBOTS_MONGO_DATABASE") or os.getenv("MONGODB_DB_NAME") or "jobbots"
                
        print(f"  [MongoDB] Attempting to load events from db '{db_name}', collection 'training_events'...")
        db = client[db_name]
        collection = db["training_events"]
        docs = list(collection.find({"bot_id": bot_id}, {"_id": 0}))
        print(f"  [MongoDB] Successfully loaded {len(docs)} events from MongoDB.")
        return docs
    except Exception as exc:
        print(f"  [MongoDB] Failed loading from MongoDB: {exc}. Falling back to JSONL files.")
        return []


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def slim_job(job: dict | None) -> dict:
    if not job:
        return {}
    keep = ("job_id", "title", "company", "location", "search_term", "has_easy_apply")
    return {k: job.get(k) for k in keep if k in job}


def build_qa_pairs(events: list[dict], bot_id: str) -> list[dict]:
    """
    Build clean Q/A pairs from a training log.
    Strategy:
      1. Use every `question_answered` event as the canonical pair (final answer).
      2. Index `ai_answer` events by (job_id, question) to enrich provider/elapsed_ms.
      3. Include `ai_answer` events whose question never reached `question_answered`
         (rare; means the form errored after the AI answered).
    """
    ai_idx: dict[tuple[str, str], dict] = {}
    for e in events:
        if e.get("event_type") != "ai_answer":
            continue
        key = (str(e.get("job", {}).get("job_id", "")), e.get("question", "").strip().lower())
        ai_idx[key] = e

    seen: set[tuple[str, str]] = set()
    pairs: list[dict] = []
    for e in events:
        if e.get("event_type") != "question_answered":
            continue
        q = (e.get("question") or "").strip()
        a = e.get("answer")
        if not q:
            continue
        job = e.get("job") or {}
        key = (str(job.get("job_id", "")), q.lower())
        seen.add(key)
        ai = ai_idx.get(key, {})
        pairs.append(
            {
                "bot_id": bot_id,
                "question": q,
                "answer": a,
                "options": e.get("options") or ai.get("options") or [],
                "control_type": e.get("control_type"),
                "decision_source": e.get("decision_source"),
                "ai_provider": ai.get("provider"),
                "ai_elapsed_ms": ai.get("elapsed_ms"),
                "labels": (e.get("dom") or {}).get("labels") or [],
                "hint": e.get("hint") or ai.get("hint") or "",
                "job": slim_job(job),
                "ts": e.get("ts"),
                "source_event": "question_answered",
            }
        )

    # AI-only orphans (answered by AI but the form never confirmed)
    for (jid, ql), ai in ai_idx.items():
        if (jid, ql) in seen:
            continue
        pairs.append(
            {
                "bot_id": bot_id,
                "question": ai.get("question"),
                "answer": ai.get("answer"),
                "options": ai.get("options") or [],
                "control_type": None,
                "decision_source": "ai",
                "ai_provider": ai.get("provider"),
                "ai_elapsed_ms": ai.get("elapsed_ms"),
                "labels": [],
                "hint": ai.get("hint") or "",
                "job": slim_job(ai.get("job")),
                "ts": ai.get("ts"),
                "source_event": "ai_answer_orphan",
            }
        )
    return pairs


def pairs_from_failed_apps(path: Path, bot_id: str) -> list[dict]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for app in data:
        for qa in app.get("questions_answers", []) or []:
            q = (qa.get("question") or "").strip()
            if not q:
                continue
            out.append(
                {
                    "bot_id": bot_id,
                    "question": q,
                    "answer": qa.get("answer"),
                    "options": qa.get("options") or [],
                    "control_type": qa.get("control_type"),
                    "decision_source": qa.get("decision_source"),
                    "ai_provider": qa.get("provider") or None,
                    "ai_elapsed_ms": None,
                    "labels": [],
                    "hint": "",
                    "job": {
                        "job_id": app.get("job_id"),
                        "title": app.get("title"),
                        "company": app.get("company"),
                        "location": app.get("location"),
                    },
                    "ts": qa.get("timestamp"),
                    "source_event": "failed_application_qa",
                    "application_status": app.get("status"),
                    "failure_reason": app.get("failure_reason"),
                }
            )
    return out


def stats(pairs: list[dict]) -> dict:
    by_provider = defaultdict(int)
    by_source = defaultdict(int)
    by_control = defaultdict(int)
    unique_q = set()
    answered = 0
    for p in pairs:
        by_provider[p.get("ai_provider") or "n/a"] += 1
        by_source[p.get("source_event")] += 1
        by_control[p.get("control_type") or "n/a"] += 1
        unique_q.add((p.get("question") or "").strip().lower())
        if p.get("answer") not in (None, ""):
            answered += 1
    return {
        "total_pairs": len(pairs),
        "answered": answered,
        "unique_questions": len(unique_q),
        "by_ai_provider": dict(by_provider),
        "by_source_event": dict(by_source),
        "by_control_type": dict(by_control),
    }


def main() -> None:
    if OUT.exists():
        # Keep idempotent: refresh outputs.
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    overall_stats: dict[str, dict] = {}
    combined: list[dict] = []

    for bot_id, cfg in BOTS.items():
        print(f"\n=== {bot_id} ===")
        bot_dir: Path = cfg["dir"]
        if not bot_dir.exists():
            print(f"  [skip] missing {bot_dir}")
            continue
        bot_out = OUT / bot_id
        bot_out.mkdir(parents=True, exist_ok=True)

        # 1. Load all training log events
        events = load_events_from_mongo(bot_id)
        if not events:
            for rel in [cfg["log"], *cfg["extra_logs"]]:
                p = bot_dir / rel
                evs = load_jsonl(p)
                print(f"  loaded {len(evs):>5} events from {rel}")
                events.extend(evs)

        # 2. Mirror raw events for archival
        write_jsonl(bot_out / "raw_events.jsonl", events)

        # 3. Build Q/A pairs
        pairs = build_qa_pairs(events, bot_id)

        # 4. Pull QA from any pre-existing per-bot training_data dir
        if cfg.get("training_data_dir"):
            tdir = bot_dir / cfg["training_data_dir"]
            if tdir.exists():
                for f in sorted(tdir.glob("*.jsonl")):
                    extra = load_jsonl(f)
                    if extra:
                        print(f"  + {len(extra)} extra rows from {f.name}")
                    for r in extra:
                        r.setdefault("bot_id", bot_id)
                        r.setdefault("source_event", f"file:{f.name}")
                        pairs.append(r)

        # 5. Pull QA from failed applications export
        for rel in cfg.get("exports", []):
            src = bot_dir / rel
            if not src.exists():
                continue
            (bot_out / "exports").mkdir(exist_ok=True)
            shutil.copy2(src, bot_out / "exports" / src.name)
            if src.name == "failed_applications_questions.json":
                more = pairs_from_failed_apps(src, bot_id)
                print(f"  + {len(more)} pairs from failed_applications_questions.json")
                pairs.extend(more)

        # 6. Sort by ts for stability
        pairs.sort(key=lambda r: (r.get("ts") or "", r.get("question") or ""))

        write_jsonl(bot_out / "qa_pairs.jsonl", pairs)
        s = stats(pairs)
        overall_stats[bot_id] = s
        (bot_out / "stats.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
        print(f"  -> {len(pairs)} qa pairs written to {bot_out / 'qa_pairs.jsonl'}")

        combined.extend(pairs)

    # Combined outputs
    comb_dir = OUT / "combined"
    comb_dir.mkdir(parents=True, exist_ok=True)
    combined.sort(key=lambda r: (r.get("bot_id") or "", r.get("ts") or ""))
    write_jsonl(comb_dir / "qa_pairs.jsonl", combined)

    # Dedup by (question lowercased) keeping first answered occurrence per bot
    dedup: dict[tuple[str, str], dict] = {}
    for p in combined:
        q = (p.get("question") or "").strip().lower()
        if not q:
            continue
        key = (p.get("bot_id") or "", q)
        cur = dedup.get(key)
        if cur is None:
            dedup[key] = p
            continue
        # Prefer rows that have a non-empty answer
        if (cur.get("answer") in (None, "")) and (p.get("answer") not in (None, "")):
            dedup[key] = p
    dedup_rows = sorted(dedup.values(), key=lambda r: (r["bot_id"], r["question"].lower()))
    write_jsonl(comb_dir / "qa_dedup.jsonl", dedup_rows)

    overall_stats["_combined"] = stats(combined)
    overall_stats["_combined_dedup"] = stats(dedup_rows)
    (OUT / "stats.json").write_text(json.dumps(overall_stats, indent=2), encoding="utf-8")

    # Human-readable summary
    lines = [
        "# Training Data Corpus",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        "",
        "Built from the Indeed-IT and Indeed-General bot run logs and exports.",
        "Each bot's data stays in its own subfolder (isolation). `combined/`",
        "merges them with a `bot_id` field for cross-bot fine-tuning.",
        "",
        "## Layout",
        "```",
        "training_data_corpus/",
        "  indeed_it/",
        "    qa_pairs.jsonl       # clean Q/A pairs",
        "    raw_events.jsonl     # full training log copy",
        "    stats.json",
        "  indeed_general/",
        "    qa_pairs.jsonl",
        "    raw_events.jsonl",
        "    exports/             # enriched_applied_jobs.* + failed_applications_questions.json",
        "    stats.json",
        "  combined/",
        "    qa_pairs.jsonl       # both bots, sorted, with bot_id",
        "    qa_dedup.jsonl       # deduped per (bot_id, question)",
        "  stats.json",
        "```",
        "",
        "## Stats",
    ]
    for k, v in overall_stats.items():
        lines.append(f"\n### {k}")
        lines.append("```json")
        lines.append(json.dumps(v, indent=2))
        lines.append("```")
    (OUT / "README.md").write_text("\n".join(lines), encoding="utf-8")

    print("\n=== DONE ===")
    print(json.dumps(overall_stats, indent=2))
    print(f"\nCorpus written to: {OUT}")


if __name__ == "__main__":
    main()

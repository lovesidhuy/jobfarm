#!/usr/bin/env python3
"""Mine production answer/training logs into QA banks and prune junk runtime logs.

What this does
--------------
1. Read ATS ``answers.jsonl`` + Indeed training JSONL (local + from_prod pull).
2. Build high-signal Q→A pairs (prefer frequent AI/bank answers; skip misses).
3. Merge into tracked QA banks under ``master/*/data/training/*_qa_bank.json``.
4. Rebuild slim ``training_data_corpus/`` QA exports for analytics.
5. Truncate/delete bulky operational logs that are not training signal.

Does **not** fine-tune an LLM (no GPU job). It *trains* the runtime answer bank
the bots already use (``qa_answer_bank`` / form_answers step 2).
"""
from __future__ import annotations

import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]  # automation/
MONO = ROOT / "automation_monorepo"
IT_BANK = ROOT / "master/it_indeed cwgeopy/Auto_indeed/data/training/it_job_application_qa_bank.json"
GEN_BANK = ROOT / "master/gen_indeed/Auto_indeed/data/training/general_job_application_qa_bank.json"
CORPUS = ROOT / "training_data_corpus"
OUT_LEARNED = MONO / "data" / "training" / "learned_from_prod"


def _normalize(text: str) -> str:
    value = (text or "").lower().replace("\u00a0", " ")
    value = re.sub(r"\([^)]*duplicate[^)]*\)", " ", value)
    value = re.sub(r"\s*\*\s*$", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


# Identity / profile noise — already covered by profile rules; don't bloat bank.
_SKIP_Q_SUBSTR = (
    "first name", "last name", "full name", "email", "phone", "mobile",
    "postal code", "zip code", "postal/zip", "street address", "city",
    "linkedin", "resume", "cv upload", "cover letter upload",
)
_SKIP_SOURCES = {"missed", "profile_first_name", "profile_last_name", "profile_email",
                 "profile_phone", "profile_city", "profile_country", "profile_linkedin",
                 "profile_state", "profile_zip", "profile_street"}
_GOOD_SOURCE_PREFIX = (
    "deepseek", "qa_answer_bank", "ollama", "gemini", "policy_", "safe_rule",
    "ai_", "akash",
)


def _is_good_source(src: str) -> bool:
    s = (src or "").lower()
    if s in _SKIP_SOURCES or s.startswith("profile_"):
        return False
    return any(s.startswith(p) or p in s for p in _GOOD_SOURCE_PREFIX) or s in {
        "form_answers", "hard_policy", "bank",
    }


def _skip_question(q: str) -> bool:
    ql = (q or "").lower()
    if len(ql) < 3:
        return True
    return any(x in ql for x in _SKIP_Q_SUBSTR)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    out = []
    with path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def mine_ats_answers(paths: list[Path]) -> tuple[dict[str, Counter], dict[str, dict]]:
    """normalized_question -> Counter(answer)."""
    votes: dict[str, Counter] = defaultdict(Counter)
    meta: dict[str, dict] = {}
    for path in paths:
        for e in _load_jsonl(path):
            if not e.get("filled"):
                continue
            q = str(e.get("question") or "").strip()
            a = str(e.get("value") or e.get("answer") or "").strip()
            src = str(e.get("source") or "")
            if not q or not a or _skip_question(q) or not _is_good_source(src):
                continue
            if len(a) > 2000:
                a = a[:2000]
            nq = _normalize(q)
            if not nq:
                continue
            votes[nq][a] += 1
            meta[nq] = {"question": q, "last_source": src}
    return votes, meta


def mine_training_log(paths: list[Path]) -> tuple[dict[str, Counter], dict[str, dict]]:
    votes: dict[str, Counter] = defaultdict(Counter)
    meta: dict[str, dict] = {}
    for path in paths:
        for e in _load_jsonl(path):
            et = e.get("event_type") or e.get("event") or ""
            if et not in {"question_answered", "ai_answer"}:
                continue
            context = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            q = str(e.get("question") or context.get("question") or "").strip()
            a = str(
                e.get("answer") or e.get("ai_answer")
                or context.get("answer") or context.get("value") or ""
            ).strip()
            if not q or not a or _skip_question(q):
                continue
            nq = _normalize(q)
            votes[nq][a] += 1
            meta[nq] = {"question": q, "last_source": et}
    return votes, meta


def merge_votes(*parts: tuple[dict[str, Counter], dict[str, dict]]):
    votes: dict[str, Counter] = defaultdict(Counter)
    meta: dict[str, dict] = {}
    for v, m in parts:
        for k, c in v.items():
            votes[k].update(c)
        meta.update(m)
    return votes, meta


def load_bank(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("questions_answered") or [])


def merge_into_bank(existing: list[dict], votes: dict[str, Counter], meta: dict[str, dict],
                    *, min_votes: int = 1, category: str = "LearnedFromProd") -> tuple[list[dict], dict]:
    by_norm = {_normalize(r.get("question", "")): r for r in existing if r.get("question")}
    added = updated = 0
    learned_rows = []
    for nq, counter in votes.items():
        if not counter:
            continue
        answer, count = counter.most_common(1)[0]
        if count < min_votes:
            continue
        question = (meta.get(nq) or {}).get("question") or nq
        learned_rows.append({
            "question": question,
            "answer": answer,
            "votes": count,
            "category": category,
            "variants": len(counter),
        })
        if nq in by_norm:
            old = by_norm[nq]
            # Only overwrite curated Form/Rules when learned answer is stable (>=2 votes)
            cat = (old.get("category") or "").lower()
            if "form" in cat or "rule" in cat or "policy" in cat:
                if count < 2 or _normalize(old.get("answer", "")) == _normalize(answer):
                    continue
            if (old.get("answer") or "").strip() != answer:
                old["answer"] = answer
                old["category"] = old.get("category") or category
                old["learned_votes"] = count
                old["learned_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                updated += 1
        else:
            by_norm[nq] = {
                "question": question,
                "answer": answer,
                "category": category,
                "learned_votes": count,
                "learned_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            }
            added += 1
    rows = sorted(by_norm.values(), key=lambda r: _normalize(r.get("question", "")))
    stats = {"added": added, "updated": updated, "total": len(rows), "learned_candidates": len(learned_rows)}
    return rows, {"stats": stats, "learned": learned_rows}


def write_bank(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "questions_answered": rows,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "mine_prod_training.py",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rebuild_corpus(it_rows: list[dict], gen_rows: list[dict]) -> None:
    CORPUS.mkdir(parents=True, exist_ok=True)
    for bot, rows in (("indeed_it", it_rows), ("indeed_general", gen_rows)):
        d = CORPUS / bot
        d.mkdir(exist_ok=True)
        pairs = [{
            "bot_id": bot,
            "question": r.get("question"),
            "answer": r.get("answer"),
            "category": r.get("category"),
            "learned_votes": r.get("learned_votes"),
            "decision_source": "qa_bank",
        } for r in rows if r.get("question") and r.get("answer")]
        (d / "qa_pairs.jsonl").write_text(
            "\n".join(json.dumps(p, ensure_ascii=False) for p in pairs) + ("\n" if pairs else ""),
            encoding="utf-8",
        )
        (d / "stats.json").write_text(json.dumps({"total_pairs": len(pairs)}, indent=2) + "\n")
    comb = []
    for bot in ("indeed_it", "indeed_general"):
        for line in (CORPUS / bot / "qa_pairs.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                comb.append(json.loads(line))
    # dedup by bot+question
    seen = set()
    dedup = []
    for p in comb:
        k = (p.get("bot_id"), _normalize(p.get("question") or ""))
        if k in seen:
            continue
        seen.add(k)
        dedup.append(p)
    (CORPUS / "combined").mkdir(exist_ok=True)
    (CORPUS / "combined" / "qa_pairs.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in comb) + "\n", encoding="utf-8")
    (CORPUS / "combined" / "qa_dedup.jsonl").write_text(
        "\n".join(json.dumps(p, ensure_ascii=False) for p in dedup) + "\n", encoding="utf-8")
    stats = {
        "indeed_it": len(it_rows),
        "indeed_general": len(gen_rows),
        "combined": len(comb),
        "combined_dedup": len(dedup),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (CORPUS / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")
    print(f"[corpus] IT={len(it_rows)} GEN={len(gen_rows)} combined_dedup={len(dedup)}")


def prune_junk_logs() -> list[str]:
    """Truncate bulky operational logs; keep training jsonl."""
    actions = []
    # Monorepo logs: keep *.jsonl, truncate large log.txt / bot_manager
    log_root = MONO / "logs"
    patterns_truncate = ["**/log.txt", "**/bot_manager/*.log", "**/supervisor/*.log"]
    keep_suffix = (".jsonl",)
    for pat in patterns_truncate:
        for path in log_root.glob(pat):
            if not path.is_file():
                continue
            size = path.stat().st_size
            if size < 100_000:
                continue
            # keep last 100KB for debug tail
            try:
                data = path.read_bytes()
                tail = data[-100_000:] if len(data) > 100_000 else data
                path.write_bytes(
                    b"# truncated by mine_prod_training.py - full history was operational noise\n"
                    + tail
                )
                actions.append(f"truncate {path.relative_to(MONO)} ({size} -> {path.stat().st_size})")
            except OSError as e:
                actions.append(f"skip {path}: {e}")

    # Empty / zero-byte full-production logs under retired
    legacy_logs = ROOT / "legacy/linkedin-ai-auto-apply-source/logs"
    if legacy_logs.is_dir():
        removed = 0
        for path in legacy_logs.rglob("*"):
            if not path.is_file():
                continue
            name = path.name
            # keep training-ish traces under size cap
            if path.suffix in {".png", ".jpg"} and path.stat().st_size > 200_000:
                path.unlink(missing_ok=True)
                removed += 1
                continue
            if path.stat().st_size == 0 and name.startswith("full-production"):
                path.unlink(missing_ok=True)
                removed += 1
        if removed:
            actions.append(f"removed {removed} empty/large junk files under retired/.../logs")

    # Drop duplicate older mongo backup in from_prod (keep newest only)
    from_prod = MONO / "data/training/from_prod/run_latest/application/backups"
    if from_prod.is_dir():
        archives = sorted(from_prod.glob("*/mongodb.archive.gz"))
        if len(archives) > 1:
            for old in archives[:-1]:
                old.unlink(missing_ok=True)
                actions.append(f"removed older mongo archive {old.parent.name}")
    return actions


def main() -> None:
    answer_paths = [
        MONO / "logs/ats_it/answers.jsonl",
        MONO / "data/training/from_prod/run_latest/repo-artifacts/automation_monorepo/logs/ats_it/answers.jsonl",
    ]
    train_paths = [
        MONO / "logs/training/events.jsonl",
        MONO / "logs/indeed_it/indeed_it_training_log.jsonl",
        MONO / "logs/indeed_general/indeed_general_training_log.jsonl",
        MONO / "data/training/from_prod/run_latest/runtime-data/training/events.jsonl",
        MONO / "data/training/from_prod/run_latest/repo-artifacts/master/it_indeed cwgeopy/Auto_indeed/logs/indeed_it/indeed_it_training_log.jsonl",
        # older July path if present
        ROOT / "master/it_indeed cwgeopy/Auto_indeed/logs/indeed_it/indeed_it_training_log.jsonl",
        ROOT / "master/gen_indeed/Auto_indeed/logs/indeed_general/indeed_general_training_log.jsonl",
    ]
    answer_paths = [p for p in answer_paths if p.is_file()]
    train_paths = [p for p in train_paths if p.is_file()]
    print(f"[mine] answer logs: {len(answer_paths)} training logs: {len(train_paths)}")

    votes, meta = merge_votes(mine_ats_answers(answer_paths), mine_training_log(train_paths))
    print(f"[mine] unique learned questions: {len(votes)}")

    # IT bank gets all technical-ish + general common; gen bank gets non-IT-heavy
    it_existing = load_bank(IT_BANK)
    gen_existing = load_bank(GEN_BANK)
    it_rows, it_info = merge_into_bank(it_existing, votes, meta, min_votes=1)
    # General: same votes but prefer CS/office-friendly; still merge all non-dev
    gen_rows, gen_info = merge_into_bank(gen_existing, votes, meta, min_votes=1)

    write_bank(IT_BANK, it_rows)
    write_bank(GEN_BANK, gen_rows)
    print(f"[bank] IT {it_info['stats']}")
    print(f"[bank] GEN {gen_info['stats']}")

    OUT_LEARNED.mkdir(parents=True, exist_ok=True)
    (OUT_LEARNED / "learned_pairs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in it_info["learned"]) + "\n",
        encoding="utf-8",
    )
    (OUT_LEARNED / "summary.json").write_text(
        json.dumps({
            "it": it_info["stats"],
            "general": gen_info["stats"],
            "unique_questions_mined": len(votes),
            "answer_log_files": [str(p) for p in answer_paths],
            "training_log_files": [str(p) for p in train_paths],
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }, indent=2) + "\n",
        encoding="utf-8",
    )

    rebuild_corpus(it_rows, gen_rows)
    actions = prune_junk_logs()
    for a in actions:
        print(f"[prune] {a}")
    print("[done] QA banks updated; corpus rebuilt; junk logs pruned.")


if __name__ == "__main__":
    main()

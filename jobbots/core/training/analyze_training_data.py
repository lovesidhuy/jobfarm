#!/usr/bin/env python3
"""
Read-only analysis of training_data_corpus/.
Writes a single report to training_data_corpus/ANALYSIS.md and prints a summary.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "training_data_corpus"

BOTS = ["indeed_it", "indeed_general"]

BUCKETS = {
    "years_experience": [r"\byears?\b", r"\bhow many\b", r"\bexperience\b"],
    "salary_pay": [r"\bsalary\b", r"\bpay\b", r"\bcompensation\b", r"\bhourly\b", r"\bwage\b", r"\brate\b"],
    "auth_visa": [r"\bauthori[sz]ed\b", r"\bsponsor\b", r"\bvisa\b", r"work permit", r"\beligib"],
    "availability_shift": [r"\bavailable\b", r"\bshift\b", r"\bweekend\b", r"start date", r"\bnotice\b", r"\bovernight\b", r"\bnight\b"],
    "location_commute": [r"\bcommute\b", r"\brelocat", r"on[- ]site", r"\bremote\b", r"\bhybrid\b", r"\btravel\b"],
    "certifications_edu": [r"\bcertif", r"\bdegree\b", r"\bdiploma\b", r"\beducation\b", r"\bdriver'?s? licen[sc]e\b"],
    "demographics_eeo": [r"\bgender\b", r"\brace\b", r"\bveteran\b", r"\bdisabilit", r"\bethnic", r"\bhispanic", r"\bpronoun"],
    "language": [r"\benglish\b", r"\bfrench\b", r"\blanguage\b", r"\bfluent\b", r"\bspeak\b"],
    "criminal_background": [r"\bbackground check\b", r"\bcriminal\b", r"\bfelony\b", r"\bconvict"],
    "tools_tech": [r"\bazure\b", r"\baws\b", r"\bo365\b", r"\bintune\b", r"\bactive directory\b", r"\bpython\b", r"\bjava\b", r"\bsql\b", r"\bservicenow\b", r"\blinux\b", r"\bwindows\b", r"\bvmware\b"],
}


def load_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    with p.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def pct(n: int, d: int) -> str:
    return f"{(100*n/d):5.1f}%" if d else "  n/a"


def bucketize(q: str) -> list[str]:
    ql = (q or "").lower()
    hits = []
    for name, pats in BUCKETS.items():
        if any(re.search(p, ql) for p in pats):
            hits.append(name)
    return hits or ["other"]


def analyse_bot(bot: str) -> dict:
    pairs = load_jsonl(CORPUS / bot / "qa_pairs.jsonl")
    events = load_jsonl(CORPUS / bot / "raw_events.jsonl")

    # ---- Q/A quality ----
    n = len(pairs)
    answered = sum(1 for p in pairs if p.get("answer") not in (None, ""))
    by_control = Counter()
    by_control_unanswered = Counter()
    by_decision = Counter()
    by_provider = Counter()
    by_source = Counter()
    latencies = defaultdict(list)
    orphan_qs = []
    failed_app_qs = []

    for p in pairs:
        ct = p.get("control_type") or "n/a"
        by_control[ct] += 1
        if p.get("answer") in (None, ""):
            by_control_unanswered[ct] += 1
        by_decision[p.get("decision_source") or "n/a"] += 1
        by_provider[p.get("ai_provider") or "n/a"] += 1
        by_source[p.get("source_event") or "n/a"] += 1
        if p.get("ai_elapsed_ms") is not None and p.get("ai_provider"):
            latencies[p["ai_provider"]].append(p["ai_elapsed_ms"])
        if p.get("source_event") == "ai_answer_orphan":
            orphan_qs.append(p)
        if p.get("source_event") == "failed_application_qa":
            failed_app_qs.append(p)

    latency_stats = {
        prov: {
            "count": len(v),
            "mean_ms": round(mean(v)),
            "median_ms": round(median(v)),
            "p95_ms": round(sorted(v)[int(0.95 * (len(v) - 1))]) if v else 0,
            "max_ms": max(v),
        }
        for prov, v in latencies.items()
    }

    # ---- Taxonomy ----
    bucket_counts = Counter()
    bucket_examples: dict[str, list[str]] = defaultdict(list)
    bucket_unique_qs: dict[str, set] = defaultdict(set)
    for p in pairs:
        q = (p.get("question") or "").strip()
        if not q:
            continue
        for b in bucketize(q):
            bucket_counts[b] += 1
            bucket_unique_qs[b].add(q.lower())
            if len(bucket_examples[b]) < 3 and q.lower() not in [e.lower() for e in bucket_examples[b]]:
                bucket_examples[b].append(q)

    # ---- Funnel from raw events ----
    ev_counts = Counter(e.get("event_type") for e in events)

    # ---- Failure breakdown ----
    failure_reasons = Counter(p.get("failure_reason") for p in failed_app_qs if p.get("failure_reason"))

    # ---- Per-search-term ----
    search_term_pairs = Counter()
    search_term_unanswered = Counter()
    for p in pairs:
        st = (p.get("job") or {}).get("search_term") or "n/a"
        search_term_pairs[st] += 1
        if p.get("answer") in (None, ""):
            search_term_unanswered[st] += 1

    return {
        "bot": bot,
        "n_pairs": n,
        "n_answered": answered,
        "n_unanswered": n - answered,
        "by_control": dict(by_control),
        "by_control_unanswered": dict(by_control_unanswered),
        "by_decision_source": dict(by_decision),
        "by_ai_provider": dict(by_provider),
        "by_source_event": dict(by_source),
        "latency_by_provider": latency_stats,
        "orphans": orphan_qs,
        "failed_app_pairs": failed_app_qs,
        "failure_reasons": dict(failure_reasons),
        "buckets": {
            b: {
                "count": bucket_counts[b],
                "unique": len(bucket_unique_qs[b]),
                "examples": bucket_examples[b],
            }
            for b in sorted(bucket_counts, key=lambda x: -bucket_counts[x])
        },
        "event_counts": dict(ev_counts),
        "search_term_top_pairs": search_term_pairs.most_common(15),
        "search_term_top_unanswered": search_term_unanswered.most_common(15),
    }


def cross_bot(bot_reports: dict[str, dict]) -> dict:
    pairs_by_bot = {b: load_jsonl(CORPUS / b / "qa_pairs.jsonl") for b in BOTS}
    qsets = {b: {(p.get("question") or "").strip().lower() for p in pairs_by_bot[b] if p.get("question")} for b in BOTS}
    overlap = qsets[BOTS[0]] & qsets[BOTS[1]]
    only_it = qsets["indeed_it"] - qsets["indeed_general"]
    only_gen = qsets["indeed_general"] - qsets["indeed_it"]

    # answer divergence on overlap
    def latest_answer_map(rows):
        m = {}
        for p in sorted(rows, key=lambda r: r.get("ts") or ""):
            q = (p.get("question") or "").strip().lower()
            a = p.get("answer")
            if q and a not in (None, ""):
                m[q] = a
        return m

    am_it = latest_answer_map(pairs_by_bot["indeed_it"])
    am_gen = latest_answer_map(pairs_by_bot["indeed_general"])
    divergent = []
    for q in sorted(overlap):
        a1 = am_it.get(q)
        a2 = am_gen.get(q)
        if a1 and a2 and a1.strip().lower() != a2.strip().lower():
            divergent.append({"question": q, "indeed_it": a1, "indeed_general": a2})

    return {
        "overlap_count": len(overlap),
        "only_indeed_it_count": len(only_it),
        "only_indeed_general_count": len(only_gen),
        "divergent_answers": divergent[:30],
        "divergent_total": len(divergent),
    }


def render(reports: dict[str, dict], cross: dict) -> str:
    L = []
    L.append("# Training Data Corpus — Analysis Report\n")
    L.append("Read-only run over `training_data_corpus/`. Per-bot first, then cross-bot.\n")

    for bot in BOTS:
        r = reports[bot]
        L.append(f"\n## {bot}\n")
        L.append(f"- pairs: **{r['n_pairs']}**, answered: **{r['n_answered']}** ({pct(r['n_answered'], r['n_pairs'])}), unanswered: {r['n_unanswered']}")
        L.append(f"- decision source: `{r['by_decision_source']}`")
        L.append(f"- AI provider mix: `{r['by_ai_provider']}`")
        L.append(f"- source events: `{r['by_source_event']}`")
        L.append("")
        L.append("### Control-type breakdown")
        L.append("| control_type | total | unanswered | unanswered % |")
        L.append("|---|---:|---:|---:|")
        for ct, total in sorted(r["by_control"].items(), key=lambda x: -x[1]):
            un = r["by_control_unanswered"].get(ct, 0)
            L.append(f"| {ct} | {total} | {un} | {pct(un, total)} |")

        if r["latency_by_provider"]:
            L.append("\n### AI latency (ms)")
            L.append("| provider | n | mean | median | p95 | max |")
            L.append("|---|---:|---:|---:|---:|---:|")
            for prov, s in r["latency_by_provider"].items():
                L.append(f"| {prov} | {s['count']} | {s['mean_ms']} | {s['median_ms']} | {s['p95_ms']} | {s['max_ms']} |")

        L.append("\n### Question taxonomy")
        L.append("| bucket | rows | unique Qs | example |")
        L.append("|---|---:|---:|---|")
        for name, info in r["buckets"].items():
            ex = info["examples"][0] if info["examples"] else ""
            ex = ex.replace("|", "\\|")[:90]
            L.append(f"| {name} | {info['count']} | {info['unique']} | {ex} |")

        L.append("\n### Funnel (raw events)")
        funnel_keys = [
            "session_started", "job_seen", "job_gate_approved", "job_skipped",
            "job_detail_loaded", "smartapply_started", "smartapply_finished",
            "question_detected", "question_answered", "question_skipped",
            "question_answer_failed", "question_unresolved",
            "ai_answer", "captcha_detected", "indeed_save_succeeded",
            "indeed_save_failed", "job_record_saved", "job_applied",
            "job_apply_failed", "submit_unconfirmed",
        ]
        L.append("| event | count |")
        L.append("|---|---:|")
        for k in funnel_keys:
            if k in r["event_counts"]:
                L.append(f"| {k} | {r['event_counts'][k]} |")

        ec = r["event_counts"]
        seen = ec.get("job_seen", 0)
        approved = ec.get("job_gate_approved", 0)
        applied = ec.get("job_applied", 0)
        qd = ec.get("question_detected", 0)
        qa = ec.get("question_answered", 0)
        qu = ec.get("question_unresolved", 0)
        sa_s = ec.get("smartapply_started", 0)
        sa_f = ec.get("smartapply_finished", 0)
        L.append("\n### Funnel ratios")
        L.append(f"- gate approval: {approved}/{seen} = {pct(approved, seen)}")
        L.append(f"- applied / approved: {applied}/{approved} = {pct(applied, approved)}")
        L.append(f"- applied / seen: {applied}/{seen} = {pct(applied, seen)}")
        L.append(f"- answered / detected: {qa}/{qd} = {pct(qa, qd)}")
        L.append(f"- unresolved / detected: {qu}/{qd} = {pct(qu, qd)}")
        L.append(f"- smartapply finish: {sa_f}/{sa_s} = {pct(sa_f, sa_s)}")
        if "indeed_save_succeeded" in ec or "indeed_save_failed" in ec:
            ok = ec.get("indeed_save_succeeded", 0)
            bad = ec.get("indeed_save_failed", 0)
            L.append(f"- indeed save success: {ok}/{ok+bad} = {pct(ok, ok+bad)}")

        if r["failure_reasons"]:
            L.append("\n### Failure reasons (from failed_applications_questions.json)")
            for k, v in sorted(r["failure_reasons"].items(), key=lambda x: -x[1]):
                L.append(f"- {k}: {v}")

        if r["orphans"]:
            L.append("\n### AI-answered but never confirmed by form (orphans)")
            for o in r["orphans"][:10]:
                q = (o.get("question") or "")[:120]
                a = (str(o.get("answer") or ""))[:80]
                L.append(f"- `{q}` → `{a}` ({(o.get('job') or {}).get('title','')})")
            if len(r["orphans"]) > 10:
                L.append(f"- … +{len(r['orphans']) - 10} more")

        L.append("\n### Top search terms by question volume")
        for st, c in r["search_term_top_pairs"]:
            L.append(f"- {st}: {c}")
        if any(c for _, c in r["search_term_top_unanswered"]):
            L.append("\n### Top search terms by *unanswered* questions")
            for st, c in r["search_term_top_unanswered"]:
                if c:
                    L.append(f"- {st}: {c}")

    # cross-bot
    L.append("\n## Cross-bot\n")
    L.append(f"- overlap (same question text in both bots): **{cross['overlap_count']}**")
    L.append(f"- only in indeed_it: {cross['only_indeed_it_count']}")
    L.append(f"- only in indeed_general: {cross['only_indeed_general_count']}")
    L.append(f"- divergent answers on overlap: **{cross['divergent_total']}**")
    if cross["divergent_answers"]:
        L.append("\n### Sample divergent answers (same Q, different A)")
        L.append("| question | indeed_it | indeed_general |")
        L.append("|---|---|---|")
        for d in cross["divergent_answers"][:20]:
            q = d["question"][:80].replace("|", "\\|")
            a1 = str(d["indeed_it"])[:60].replace("|", "\\|")
            a2 = str(d["indeed_general"])[:60].replace("|", "\\|")
            L.append(f"| {q} | {a1} | {a2} |")

    return "\n".join(L) + "\n"


def main():
    reports = {b: analyse_bot(b) for b in BOTS}
    cross = cross_bot(reports)
    md = render(reports, cross)
    out = CORPUS / "ANALYSIS.md"
    out.write_text(md, encoding="utf-8")
    # also dump raw
    (CORPUS / "analysis.json").write_text(
        json.dumps({"per_bot": reports, "cross_bot": cross}, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"wrote {out}")
    print(f"wrote {CORPUS / 'analysis.json'}")


if __name__ == "__main__":
    main()

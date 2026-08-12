#!/usr/bin/env python3
"""Snapshot LinkedIn queue + tonight's apply outcomes for next-day review.

Writes under artifacts/mac-linkedin/review_<UTC_DATE>/ :
  - summary.json
  - jobs.csv
  - status_counts.json
  - REVIEW.md
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.supervisor_runtime import merge_dotenv_into_env

merge_dotenv_into_env(os.environ, ROOT / ".env", override=False)


def _iso(v):
    if v is None:
        return ""
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def main() -> None:
    from core.job_queue import JobQueue

    q = JobQueue()
    now = datetime.now(timezone.utc)
    day = now.strftime("%Y%m%d")
    out_dir = ROOT / "artifacts" / "mac-linkedin" / f"review_{day}"
    out_dir.mkdir(parents=True, exist_ok=True)

    docs = list(
        q.jobs.find({"portal": "linkedin"}).sort("updated_at", -1)
    )
    status_counts = Counter((d.get("profile") or "?", d.get("status") or "?") for d in docs)
    by_status = Counter(d.get("status") or "?" for d in docs if (d.get("profile") or "") == "it")

    # "Tonight" window: last 18 hours (covers evening→morning review)
    cut = now.timestamp() - 18 * 3600
    recent = []
    for d in docs:
        ua = d.get("updated_at")
        try:
            if hasattr(ua, "timestamp"):
                ts = ua.replace(tzinfo=timezone.utc).timestamp() if ua.tzinfo is None else ua.timestamp()
            else:
                ts = 0
        except Exception:
            ts = 0
        if ts >= cut or (d.get("status") in ("queued", "retry", "leased") and d.get("profile") == "it"):
            recent.append(d)

    rows = []
    for d in docs:
        rows.append(
            {
                "id": d.get("_id"),
                "profile": d.get("profile"),
                "status": d.get("status"),
                "company": d.get("company"),
                "title": d.get("title"),
                "url": d.get("url"),
                "result_url": d.get("result_url"),
                "attempts": d.get("attempts"),
                "last_error": d.get("last_error") or d.get("failure_reason") or "",
                "application_method": (d.get("metadata") or {}).get("application_method", ""),
                "updated_at": _iso(d.get("updated_at")),
                "applied_at": _iso(d.get("applied_at")),
                "discovered_at": _iso(d.get("discovered_at")),
            }
        )

    summary = {
        "generated_at_utc": now.isoformat(),
        "portal": "linkedin",
        "it_status_counts": dict(by_status),
        "status_by_profile": {f"{p}/{s}": n for (p, s), n in status_counts.items()},
        "recent_18h_count": len(recent),
        "recent_applied": [
            {
                "company": d.get("company"),
                "title": d.get("title"),
                "url": d.get("url"),
                "updated_at": _iso(d.get("updated_at")),
                "last_error": d.get("last_error") or "",
            }
            for d in recent
            if d.get("status") == "applied" and d.get("profile") == "it"
        ],
        "recent_failed_or_dead": [
            {
                "company": d.get("company"),
                "title": d.get("title"),
                "status": d.get("status"),
                "last_error": (d.get("last_error") or "")[:200],
                "url": d.get("url"),
                "updated_at": _iso(d.get("updated_at")),
            }
            for d in recent
            if d.get("status") in ("failed", "dead", "retry") and d.get("profile") == "it"
        ],
        "queued_now": [
            {
                "company": d.get("company"),
                "title": d.get("title"),
                "status": d.get("status"),
                "url": d.get("url"),
            }
            for d in docs
            if d.get("profile") == "it" and d.get("status") in ("queued", "retry", "leased")
        ],
        "resume_policy_note": "IT profile should use all resumes/ls_resume_it.pdf (not generic Jane upload).",
        "log_dir": str(ROOT / "artifacts" / "mac-linkedin"),
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "status_counts.json").write_text(
        json.dumps({"it": dict(by_status), "all": summary["status_by_profile"]}, indent=2),
        encoding="utf-8",
    )

    csv_path = out_dir / "jobs.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "id", "profile", "status", "company", "title", "url", "result_url",
                "attempts", "last_error", "application_method", "updated_at", "applied_at", "discovered_at",
            ],
        )
        w.writeheader()
        w.writerows(rows)

    md = []
    md.append(f"# LinkedIn review — {now.strftime('%Y-%m-%d %H:%M UTC')}\n")
    md.append("## IT status counts\n")
    for k, v in sorted(by_status.items()):
        md.append(f"- **{k}**: {v}")
    md.append("\n## Applied (last ~18h)\n")
    if summary["recent_applied"]:
        for a in summary["recent_applied"]:
            md.append(f"- **{a['company']}** — {a['title']}  \n  `{a['url']}`  \n  _{a['updated_at']}_")
    else:
        md.append("_None in window._")
    md.append("\n## Failed / dead / retry (last ~18h)\n")
    for a in summary["recent_failed_or_dead"][:40]:
        md.append(
            f"- **{a['status']}** | {a['company']} — {a['title']}  \n"
            f"  `{a.get('last_error','')}`  \n  `{a['url']}`"
        )
    md.append("\n## Still in queue\n")
    for a in summary["queued_now"]:
        md.append(f"- **{a['status']}** | {a['company']} — {a['title']}")
    md.append("\n## Artifacts\n")
    md.append(f"- CSV: `{csv_path}`")
    md.append(f"- JSON: `{out_dir / 'summary.json'}`")
    md.append(f"- Worker/discovery logs: `artifacts/mac-linkedin/*.log`")
    md.append("\n## Resume wiring\n")
    md.append("- Default IT resume: `profiles/resumes/sample_resume_it.pdf`")
    md.append("- Easy Apply radios prefer `sample_resume_it` over generic `sample_resume` upload.")
    (out_dir / "REVIEW.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    # Pointer for "tomorrow"
    latest = ROOT / "artifacts" / "mac-linkedin" / "LATEST_REVIEW.txt"
    latest.write_text(str(out_dir) + "\n", encoding="utf-8")

    print(f"Wrote review pack → {out_dir}")
    print(f"IT counts: {dict(by_status)}")
    print(f"Recent applied: {len(summary['recent_applied'])} | queue: {len(summary['queued_now'])}")


if __name__ == "__main__":
    main()

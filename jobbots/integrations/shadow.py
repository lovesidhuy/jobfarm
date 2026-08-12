"""Shadow-mode harness (Phases 2–4): the new architecture in comparison-only mode.

Three layers, strictest first:

1. ``fixtures`` — the 43 golden Q&A cases replayed through the frozen chain
   (``jobbots.core.qa.runner``). Any drift = a Q&A behavior change. Stop.
2. ``gate`` — synthetic leads screened through the portal adapters AND through
   the direct frozen gate call; both must produce identical
   (passed, score, reason). Proves adapter wiring routes to the same code.
3. ``live`` (optional, ``--live N``) — N queued jobs sampled read-only from
   Mongo; adapter screening diffed against the direct call. For VM use.

Exit contract: any important difference -> non-zero exit. Never applies,
never writes to the queue.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobbots.paths import MONOREPO_ROOT

# (portal, profile, title, company, location, description, easy_apply)
_GATE_CASES: list[tuple[str, str, str, str, str, str, bool]] = [
    ("indeed", "it", "IT Support Specialist", "Acme", "Vancouver, BC",
     "Help desk and desktop support in Metro Vancouver.", True),
    ("indeed", "it", "Senior QA Manager", "Acme", "Vancouver, BC", "", True),
    ("indeed", "general", "Customer Service Representative", "Acme",
     "Vancouver, BC", "Answer inbound client calls.", True),
    ("indeed", "general", "QA Analyst", "Acme", "Vancouver, BC",
     "Selenium testing", True),
    ("glassdoor", "it", "Help Desk Analyst", "Acme", "Burnaby, BC",
     "IT support role", True),
    ("workopolis", "it", "Systems Administrator", "Acme", "Surrey, BC",
     "Windows and AD administration.", True),
]


def _screen_via_adapter(portal: str, lead_kwargs: dict[str, Any], profile: str):
    from jobbots.integrations.portals.base import JobLead
    from jobbots.integrations.portals.registry import get_adapter

    adapter = get_adapter(portal)
    lead = JobLead(**lead_kwargs)
    return adapter.screen(lead, profile=profile)


def _screen_direct(profile: str, *, title: str, company: str, location: str,
                   description: str, easy_apply: bool):
    from jobbots.core.discovery._gate_adapter import hard_screen_job

    return hard_screen_job(
        title=title, company=company, description=description,
        location=location, easy_apply=easy_apply, profile=profile,
    )


def _compare(portal: str, profile: str, job: dict[str, Any]) -> dict[str, Any]:
    title = str(job.get("title") or "")
    company = str(job.get("company") or "")
    location = str(job.get("location") or "")
    description = str(job.get("description") or "")
    easy = bool(job.get("easy_apply"))

    via = _screen_via_adapter(
        portal,
        {
            "portal": portal,
            "source_job_id": str(job.get("source_job_id") or "shadow"),
            "title": title,
            "company": company,
            "url": str(job.get("url") or "https://example.invalid/job"),
            "location": location,
            "description": description,
            "profile": profile,
            "metadata": {"apply_type": "EASY_APPLY" if easy else "UNKNOWN"},
        },
        profile,
    )
    direct = _screen_direct(
        profile, title=title, company=company, location=location,
        description=description, easy_apply=easy,
    )
    same = (
        via.qualified == bool(direct[0])
        and (via.score or 0) == float(direct[1])
        and via.reason == (direct[2] or "")
    )
    return {
        "portal": portal,
        "profile": profile,
        "title": title,
        "ok": same,
        "adapter": {
            "qualified": via.qualified,
            "score": via.score,
            "reason": via.reason,
            "resume_policy": via.resume_policy,
        },
        "direct": {"passed": bool(direct[0]), "score": direct[1], "reason": direct[2]},
    }


def gate_shadow_checks() -> list[dict[str, Any]]:
    reports = []
    for portal, profile, title, company, location, desc, easy in _GATE_CASES:
        reports.append(
            _compare(
                portal,
                profile,
                {
                    "title": title,
                    "company": company,
                    "location": location,
                    "description": desc,
                    "easy_apply": easy,
                },
            )
        )
    return reports


def live_queue_sample(n: int, *, profile: str | None = None) -> list[dict[str, Any]]:
    """Read-only sample of queued jobs; adapter screening vs direct call."""
    from jobbots.paths import ensure_monorepo_on_path

    ensure_monorepo_on_path()
    from core.job_queue import JobQueue

    queue = JobQueue()
    query: dict[str, Any] = {"status": "queued"}
    if profile:
        query["profile"] = profile.strip().lower()
    cursor = queue.jobs.find(query).limit(max(1, int(n)))
    reports = []
    for doc in cursor:
        metadata = doc.get("metadata") or {}
        reports.append(
            _compare(
                str(doc.get("portal") or "indeed"),
                str(doc.get("profile") or "it"),
                {
                    "source_job_id": doc.get("source_job_id"),
                    "title": doc.get("title"),
                    "company": doc.get("company"),
                    "location": doc.get("location"),
                    "description": doc.get("description"),
                    "url": doc.get("url"),
                    "easy_apply": str(metadata.get("application_method") or "")
                    == "easy_apply",
                },
            )
        )
    return reports


def run_shadow(sample: int = 0, *, profile: str | None = None) -> int:
    from jobbots.core.qa import runner

    reports = runner.replay()
    qa_failed = [r for r in reports if not r["ok"]]

    gate = gate_shadow_checks()
    gate_failed = [g for g in gate if not g["ok"]]

    live: list[dict[str, Any]] = []
    live_failed: list[dict[str, Any]] = []
    if sample > 0:
        live = live_queue_sample(sample, profile=profile)
        live_failed = [l for l in live if not l["ok"]]
        out = MONOREPO_ROOT / "outputs" / "shadow_report.jsonl"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("a", encoding="utf-8") as fh:
            for row in live:
                fh.write(
                    json.dumps(
                        {"ts": datetime.now(timezone.utc).isoformat(), **row},
                        default=str,
                    )
                    + "\n"
                )
        print(f"live shadow report appended: {out}")

    print(
        f"shadow: qa fixtures {len(reports) - len(qa_failed)}/{len(reports)} ok · "
        f"gate wiring {len(gate) - len(gate_failed)}/{len(gate)} ok"
        + (f" · live sample {len(live) - len(live_failed)}/{len(live)} ok" if sample > 0 else "")
    )
    for row in [*qa_failed, *gate_failed, *live_failed]:
        print(f"DRIFT: {json.dumps(row, default=str)[:400]}")
    return 1 if (qa_failed or gate_failed or live_failed) else 0

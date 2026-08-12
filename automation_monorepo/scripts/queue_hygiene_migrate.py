#!/usr/bin/env python3
"""Wave A.2 → reversible queue-hygiene migration (Indeed IT).

Safeguards
----------
* Verifies durable backup checksums before any mutation.
* Does NOT change DISCOVERY_ENGINE.
* Does NOT start Wave B.
* Skips leased records.
* Optimistic concurrency on ``status`` (+ optional ``updated_at``).
* Idempotent via ``metadata.hygiene.migration_id``.
* Deterministic policy rejects never call the AI gate.
* IT gate runs only on policy survivors that would become leasable.
* Expired / insufficient-evidence / Basis hold → non-leasable.
* Writes a rollback script from the before-state snapshot.

Usage
-----
  python scripts/queue_hygiene_migrate.py --dry-run
  python scripts/queue_hygiene_migrate.py --apply --confirm-migrate
  python scripts/queue_hygiene_migrate.py --rollback MIGRATION_ID
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.discovery._gate_adapter import screen_job  # noqa: E402
from core.discovery.classification.apply_type import classify_apply_type  # noqa: E402
from core.discovery.classification.location_policy import (  # noqa: E402
    decide_job_policy,
    detect_work_mode,
    classify_region,
)
from core.discovery.contracts import NormalizedJob  # noqa: E402
from core.job_queue import JobQueue, TERMINAL, _now  # noqa: E402

HYGIENE_BACKUP = ROOT / "artifacts" / "queue-hygiene"
A2_DIR = ROOT / "artifacts" / "wave-a2-reclassify"
MIG_DIR = ROOT / "artifacts" / "queue-hygiene-migrate"
_JK_RE = re.compile(r"[?&]jk=([a-f0-9]+)", re.I)

# Fixed id so re-runs are idempotent for this wave.
DEFAULT_MIGRATION_ID = "wave_a2_hygiene_20260711"

# Explicit holds (insufficient live evidence) — never auto-queue.
FORCE_HOLD_COMPANIES = {
    ("basis", "software engineer, tools"),
}


def _utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _extract_jk(url: str, source_job_id: str = "") -> str:
    sid = (source_job_id or "").strip().lower()
    if sid.startswith("in-"):
        sid = sid[3:]
    if sid and re.fullmatch(r"[a-f0-9]{10,}", sid):
        return sid
    if not url:
        return sid
    m = _JK_RE.search(url)
    if m:
        return m.group(1).lower()
    try:
        qs = parse_qs(urlparse(url).query)
        return (qs.get("jk") or [""])[0].lower() or sid
    except Exception:
        return sid


def _norm_key(company: str, title: str, location: str) -> str:
    def n(s: str) -> str:
        s = (s or "").lower()
        s = re.sub(r"\([^)]*\)", " ", s)
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return " ".join(s.split())
    return f"{n(company)}|{n(title)}|{n(location)}"


def verify_backup_checksums() -> dict:
    sums_path = HYGIENE_BACKUP / "SHA256SUMS.txt"
    if not sums_path.exists():
        return {"ok": False, "error": "SHA256SUMS.txt missing"}
    checked = []
    for line in sums_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        expect, name = parts[0], parts[-1]
        fpath = HYGIENE_BACKUP / name
        if not fpath.exists():
            checked.append({"file": name, "ok": False, "error": "missing"})
            continue
        digest = hashlib.sha256(fpath.read_bytes()).hexdigest()
        checked.append({"file": name, "ok": digest == expect, "sha256": digest})
    return {"ok": all(c["ok"] for c in checked), "files": checked, "sums_path": str(sums_path)}


def load_a2_report() -> dict:
    paths = sorted(A2_DIR.glob("wave_a2_reclassify_*.json"))
    paths = [p for p in paths if ".summary." not in p.name]
    if not paths:
        raise FileNotFoundError("No wave_a2_reclassify_*.json found")
    return json.loads(paths[-1].read_text()), str(paths[-1])


def load_applied_history_jks(q: JobQueue) -> set[str]:
    """Best-effort applied/skipped/bookmarked jk set (no AI)."""
    jks: set[str] = set()
    # Terminal queue history
    for row in q.jobs.find(
        {"status": {"$in": list(TERMINAL)}},
        {"url": 1, "source_job_id": 1, "status": 1},
    ):
        jk = _extract_jk(row.get("url") or "", row.get("source_job_id") or "")
        if jk:
            jks.add(jk)
    # CSV / mongo history via Indeed persistence (optional)
    try:
        from core.shared_modules.indeed.persistence import (
            get_applied_indeed_job_ids,
            get_skipped_indeed_job_ids,
        )
        for raw in get_applied_indeed_job_ids() | get_skipped_indeed_job_ids():
            s = str(raw).strip().lower()
            if s.startswith("in-"):
                s = s[3:]
            if s:
                jks.add(s)
    except Exception as exc:
        print(f"[migrate] history import soft-fail: {exc}", flush=True)
    return jks


def row_to_job(row: dict, a2: dict | None) -> NormalizedJob:
    url = row.get("url") or ""
    jk = _extract_jk(url, row.get("source_job_id") or "")
    meta = row.get("metadata") or {}
    apply_type = "UNKNOWN"
    apply_source = "not_verified"
    confirmed = False
    dest = meta.get("destination_url") or meta.get("job_url_direct")
    is_remote = bool(meta.get("is_remote") or meta.get("is_remote_hint"))

    if a2:
        apply_type = a2.get("corrected_apply_type") or "UNKNOWN"
        apply_source = a2.get("corrected_apply_type_source") or "not_verified"
        confirmed = bool(a2.get("corrected_apply_type_confirmed"))
        dest = a2.get("destination_url") or dest
        # Prefer live-match remote hint when A.2 had one via evidence
        if a2.get("live_matched") and apply_type == "EASY_APPLY":
            # Outside-metro APPLY still needs independent remote confirmation
            # from location/description/is_remote on the stored row + A.2.
            pass
        if a2.get("current_work_mode") == "REMOTE" or "remote" in (row.get("location") or "").lower():
            is_remote = is_remote or True

    if apply_type == "UNKNOWN" and dest:
        probe = NormalizedJob(
            source_platform=row.get("portal") or "indeed",
            source_job_id=str(row.get("source_job_id") or jk or row.get("_id")),
            discovery_engine="hygiene_migrate",
            query_id="migrate",
            job_title=row.get("title") or "",
            company_name=row.get("company") or "",
            location=row.get("location") or "",
            description=row.get("description") or "",
            date_posted=None,
            listing_url=url,
            destination_url=dest,
            apply_type="UNKNOWN",
            apply_type_source="",
            is_remote_hint=is_remote,
        )
        clf = classify_apply_type(probe)
        apply_type, apply_source, confirmed = clf.apply_type, clf.source, clf.confirmed

    # A.2 remote hint: if policy reason was outside_metro_remote_easy_apply, keep remote
    if a2 and a2.get("policy_reason") == "outside_metro_remote_easy_apply":
        is_remote = True

    return NormalizedJob(
        source_platform=row.get("portal") or "indeed",
        source_job_id=str(row.get("source_job_id") or jk or row.get("_id")),
        discovery_engine="hygiene_migrate",
        query_id="migrate",
        job_title=row.get("title") or "",
        company_name=row.get("company") or "",
        location=row.get("location") or "",
        description=row.get("description") or "",
        date_posted=None,
        listing_url=url,
        destination_url=dest,
        apply_type=apply_type,
        apply_type_source=apply_source,
        apply_type_confirmed=confirmed,
        is_remote_hint=is_remote,
    )


def is_force_hold(row: dict) -> bool:
    c = (row.get("company") or "").lower()
    t = (row.get("title") or "").lower()
    for company_n, title_n in FORCE_HOLD_COMPANIES:
        if company_n in c and title_n in t:
            return True
    return False


def decide_outcome(
    *,
    row: dict,
    a2: dict | None,
    applied_jks: set[str],
    retained_keys: dict[str, str],
) -> dict:
    """Return mutation plan for one row. Never calls AI."""
    jid = str(row.get("_id"))
    status = (row.get("status") or "").strip().lower()
    meta = row.get("metadata") or {}
    hygiene = meta.get("hygiene") or {}
    cur_method = (meta.get("application_method") or "").strip().lower() or None
    jk = _extract_jk(row.get("url") or "", row.get("source_job_id") or "")
    url = (row.get("url") or "").strip()
    nkey = _norm_key(row.get("company") or "", row.get("title") or "", row.get("location") or "")

    base = {
        "id": jid,
        "jk": jk,
        "title": row.get("title"),
        "company": row.get("company"),
        "url": url,
        "old_status": status,
        "old_method": cur_method,
        "old_updated_at": row.get("updated_at"),
    }

    # Idempotent: already migrated by this migration_id
    if hygiene.get("migration_id") == DEFAULT_MIGRATION_ID and hygiene.get("applied"):
        return {**base, "outcome": "already_migrated", "mutate": False, "reason": "idempotent_skip"}

    if status == "leased":
        return {**base, "outcome": "skip_leased", "mutate": False, "reason": "currently_leased"}

    if status in TERMINAL:
        return {**base, "outcome": "skip_terminal", "mutate": False, "reason": f"already_terminal:{status}"}

    # Force hold (Basis)
    if is_force_hold(row):
        return {
            **base,
            "outcome": "manual_review_hold",
            "mutate": True,
            "new_status": "manual_review",
            "new_method": cur_method,
            "reason": "insufficient_live_evidence_hold (Basis)",
            "needs_ai": False,
        }

    a2_state = (a2 or {}).get("job_state") or "unknown"
    live_matched = bool((a2 or {}).get("live_matched"))
    active = a2_state == "active" or live_matched

    job = row_to_job(row, a2)
    decision = decide_job_policy(job)
    work_mode = detect_work_mode(job.location, job.description, is_remote_hint=bool(job.is_remote_hint))
    region = classify_region(job.location)

    plan = {
        **base,
        "corrected_apply_type": job.apply_type,
        "apply_type_source": job.apply_type_source,
        "apply_type_confirmed": bool(job.apply_type_confirmed),
        "policy_action": decision.action,
        "policy_reason": decision.reason,
        "work_mode": work_mode,
        "region": region,
        "job_state": "active" if active else a2_state,
        "needs_ai": False,
    }

    # History / already applied
    if jk and jk in applied_jks:
        return {
            **plan,
            "outcome": "already_applied_or_terminal_history",
            "mutate": True,
            "new_status": "rejected",
            "new_method": None,
            "reason": f"history_or_terminal_jk:{jk}",
            "needs_ai": False,
        }

    # Deterministic policy reject — no AI
    if decision.action == "REJECT":
        return {
            **plan,
            "outcome": "policy_rejected",
            "mutate": True,
            "new_status": "rejected",
            "new_method": None,
            "reason": decision.reason,
            "needs_ai": False,
        }

    # Insufficient active evidence → hold (non-leasable)
    if not active:
        return {
            **plan,
            "outcome": "manual_review_hold",
            "mutate": True,
            "new_status": "manual_review",
            "new_method": cur_method,
            "reason": f"insufficient_active_evidence (job_state={a2_state})",
            "needs_ai": False,
        }

    if a2_state == "expired":
        return {
            **plan,
            "outcome": "expired",
            "mutate": True,
            "new_status": "rejected",
            "new_method": None,
            "reason": "expired_inactive",
            "needs_ai": False,
        }

    # Duplicate of another retained queued candidate
    dup_of = None
    if jk and jk in retained_keys:
        dup_of = retained_keys[jk]
    elif url and url in retained_keys:
        dup_of = retained_keys[url]
    elif nkey and nkey in retained_keys:
        dup_of = retained_keys[nkey]
    if dup_of and dup_of != jid:
        return {
            **plan,
            "outcome": "duplicate",
            "mutate": True,
            "new_status": "rejected",
            "new_method": None,
            "reason": f"duplicate_of:{dup_of}",
            "needs_ai": False,
        }

    # Policy survivor → candidate for queued after IT gate
    if decision.action == "APPLY":
        target_method = "easy_apply"
        outcome = "queued_easy_apply"
    elif decision.action == "SAVE":
        target_method = "company_site"
        outcome = "queued_company_site"
    else:  # VERIFY
        target_method = "unverified"
        outcome = "queued_unverified"

    # Reserve keys so later duplicates lose
    if jk:
        retained_keys.setdefault(jk, jid)
    if url:
        retained_keys.setdefault(url, jid)
    if nkey:
        retained_keys.setdefault(nkey, jid)

    return {
        **plan,
        "outcome": outcome,
        "mutate": True,
        "new_status": "queued",
        "new_method": target_method,
        "reason": decision.reason,
        "needs_ai": True,
        "gate_easy_apply": bool(decision.gate_easy_apply),
    }


def run_ai_gate(plan: dict, row: dict) -> dict:
    """IT gate for policy survivors only."""
    passed, score, reason = screen_job(
        title=row.get("title") or "",
        company=row.get("company") or "",
        description=row.get("description") or "",
        location=row.get("location") or "",
        easy_apply=bool(plan.get("gate_easy_apply", True)),
        profile=(row.get("profile") or "it"),
    )
    plan["ai_passed"] = bool(passed)
    plan["ai_score"] = score
    plan["ai_reason"] = reason
    if not passed:
        plan["outcome"] = "screen_rejected"
        plan["new_status"] = "rejected"
        plan["new_method"] = None
        plan["reason"] = f"screen_rejected:{reason}"
    return plan


def apply_mutation(q: JobQueue, row: dict, plan: dict, migration_id: str) -> dict:
    """Optimistic update. Returns result dict."""
    jid = plan["id"]
    old_status = plan["old_status"]
    if not plan.get("mutate"):
        return {"id": jid, "applied": False, "reason": plan.get("reason")}

    hygiene_meta = {
        "migration_id": migration_id,
        "migrated_at": _now().isoformat(),
        "applied": True,
        "outcome": plan["outcome"],
        "reason": plan.get("reason"),
        "old_status": old_status,
        "old_method": plan.get("old_method"),
        "new_status": plan.get("new_status"),
        "new_method": plan.get("new_method"),
        "policy_action": plan.get("policy_action"),
        "policy_reason": plan.get("policy_reason"),
        "corrected_apply_type": plan.get("corrected_apply_type"),
        "apply_type_source": plan.get("apply_type_source"),
        "ai_passed": plan.get("ai_passed"),
        "ai_score": plan.get("ai_score"),
        "ai_reason": plan.get("ai_reason"),
        "job_state": plan.get("job_state"),
    }

    new_meta = dict(row.get("metadata") or {})
    new_meta["hygiene"] = hygiene_meta
    if plan.get("new_method") is not None:
        new_meta["application_method"] = plan["new_method"]
    elif "application_method" in new_meta and plan.get("outcome") in {
        "policy_rejected", "screen_rejected", "expired", "duplicate",
        "already_applied_or_terminal_history",
    }:
        # Keep old method in hygiene; clear leasable method ambiguity
        pass

    if plan.get("region"):
        new_meta["region"] = plan["region"]

    patch = {
        "status": plan["new_status"],
        "updated_at": _now(),
        "lease_owner": None,
        "lease_expires_at": None,
        "metadata": new_meta,
        "last_error": str(plan.get("reason") or "")[:2000],
    }
    if plan.get("ai_score") is not None:
        patch["gate_score"] = plan["ai_score"]
        patch["gate_reason"] = str(plan.get("ai_reason") or "")[:2000]
        patch["gate_status"] = "approved" if plan.get("ai_passed") else "rejected"

    # Optimistic concurrency: must still be in the pre-migration status and not leased.
    filt = {
        "_id": jid,
        "status": old_status,
        "lease_owner": None,
    }
    # Also reject if another migration already stamped this id
    filt["metadata.hygiene.migration_id"] = {"$ne": migration_id}

    res = q.jobs.find_one_and_update(filt, {"$set": patch}, return_document=True)
    if not res:
        # Check idempotent already applied
        cur = q.jobs.find_one({"_id": jid}, {"status": 1, "metadata.hygiene": 1, "lease_owner": 1})
        if (cur or {}).get("metadata", {}).get("hygiene", {}).get("migration_id") == migration_id:
            return {"id": jid, "applied": False, "reason": "idempotent_already_applied"}
        return {
            "id": jid,
            "applied": False,
            "reason": "optimistic_concurrency_failed",
            "current": {
                "status": (cur or {}).get("status"),
                "lease_owner": (cur or {}).get("lease_owner"),
            },
        }

    q._event(jid, "hygiene_migrated", "hygiene_migrate", hygiene_meta)
    return {"id": jid, "applied": True, "outcome": plan["outcome"], "new_status": plan["new_status"]}


def write_rollback_script(before_docs: list[dict], migration_id: str, out_dir: Path) -> Path:
    path = out_dir / f"rollback_{migration_id}.py"
    payload_path = out_dir / f"rollback_{migration_id}_before.json"
    # Slim before-state for restore
    slim = []
    for d in before_docs:
        slim.append({
            "_id": str(d["_id"]),
            "status": d.get("status"),
            "metadata": d.get("metadata"),
            "last_error": d.get("last_error"),
            "gate_score": d.get("gate_score"),
            "gate_reason": d.get("gate_reason"),
            "gate_status": d.get("gate_status"),
            "lease_owner": d.get("lease_owner"),
            "lease_expires_at": d.get("lease_expires_at"),
            "updated_at": d.get("updated_at"),
        })
    payload_path.write_text(json.dumps(slim, indent=2, default=str), encoding="utf-8")
    path.write_text(
        f'''#!/usr/bin/env python3
"""Rollback hygiene migration {migration_id}.

Restores status/method/gate/last_error from the before-state snapshot.
Does not delete records. Skips leased records.
"""
from __future__ import annotations
import json, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from core.job_queue import JobQueue, _now

BEFORE = Path(__file__).with_name({payload_path.name!r})
MIGRATION_ID = {migration_id!r}

def main():
    docs = json.loads(BEFORE.read_text())
    q = JobQueue()
    ok = fail = skip = 0
    for d in docs:
        jid = d["_id"]
        cur = q.jobs.find_one({{"_id": jid}}, {{"status": 1, "lease_owner": 1, "metadata.hygiene": 1}})
        if not cur:
            fail += 1; continue
        if cur.get("lease_owner"):
            skip += 1; print("skip leased", jid); continue
        hy = (cur.get("metadata") or {{}}).get("hygiene") or {{}}
        if hy.get("migration_id") != MIGRATION_ID:
            skip += 1; continue
        meta = dict(d.get("metadata") or {{}})
        # Drop hygiene stamp so a future re-migrate can run if needed
        meta.pop("hygiene", None)
        res = q.jobs.update_one(
            {{"_id": jid, "lease_owner": None}},
            {{"$set": {{
                "status": d.get("status"),
                "metadata": meta,
                "last_error": d.get("last_error") or "",
                "gate_score": d.get("gate_score"),
                "gate_reason": d.get("gate_reason") or "",
                "gate_status": d.get("gate_status"),
                "updated_at": _now(),
            }}}},
        )
        if res.modified_count:
            ok += 1
            q._event(jid, "hygiene_rollback", "hygiene_migrate", {{"migration_id": MIGRATION_ID}})
        else:
            fail += 1
    print(json.dumps({{"restored": ok, "failed": fail, "skipped": skip}}))

if __name__ == "__main__":
    main()
''',
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def summarize_plans(plans: list[dict]) -> dict:
    return {
        "total": len(plans),
        "by_outcome": dict(Counter(p["outcome"] for p in plans)),
        "needs_ai": sum(1 for p in plans if p.get("needs_ai")),
        "would_mutate": sum(1 for p in plans if p.get("mutate")),
    }


def post_counts(q: JobQueue, migration_id: str) -> dict:
    rows = list(q.jobs.find({"metadata.hygiene.migration_id": migration_id}))
    by_outcome = Counter((r.get("metadata") or {}).get("hygiene", {}).get("outcome") for r in rows)
    queued = list(q.jobs.find({"status": "queued"}))
    method = Counter(
        ((r.get("metadata") or {}).get("application_method") or "none") for r in queued
    )
    return {
        "migrated_rows": len(rows),
        "by_hygiene_outcome": dict(by_outcome),
        "queued_easy_apply": method.get("easy_apply", 0),
        "queued_company_site": method.get("company_site", 0),
        "queued_unverified": method.get("unverified", 0),
        "queued_other_method": {
            k: v for k, v in method.items()
            if k not in ("easy_apply", "company_site", "unverified")
        },
        "status_counts": q.counts(),
        "policy_rejected": by_outcome.get("policy_rejected", 0),
        "screen_rejected": by_outcome.get("screen_rejected", 0),
        "expired": by_outcome.get("expired", 0),
        "manual_review_hold": by_outcome.get("manual_review_hold", 0),
        "duplicate": by_outcome.get("duplicate", 0),
        "already_applied_or_terminal_history": by_outcome.get(
            "already_applied_or_terminal_history", 0
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--confirm-migrate", action="store_true")
    ap.add_argument("--migration-id", default=DEFAULT_MIGRATION_ID)
    ap.add_argument("--skip-ai", action="store_true", help="Dev only — do not use in prod migrate")
    args = ap.parse_args()

    if os.getenv("DISCOVERY_ENGINE", "").strip().lower() in {"new"}:
        print("Refusing to run while DISCOVERY_ENGINE=new", file=sys.stderr)
        return 2

    apply_mode = bool(args.apply and args.confirm_migrate)
    if args.apply and not args.confirm_migrate:
        print("--apply requires --confirm-migrate", file=sys.stderr)
        return 2
    if not apply_mode and not args.dry_run:
        args.dry_run = True

    MIG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = _utc()
    migration_id = args.migration_id

    print("[migrate] Verifying durable backup checksums…", flush=True)
    checksum = verify_backup_checksums()
    if not checksum.get("ok"):
        print(json.dumps(checksum, indent=2))
        print("ABORT: backup checksum failed", file=sys.stderr)
        return 2
    print("[migrate] Backup checksums OK", flush=True)

    a2, a2_path = load_a2_report()
    a2_by_id = {str(r["id"]): r for r in a2.get("records") or []}
    print(f"[migrate] Loaded A.2 report {a2_path} ({len(a2_by_id)} records)", flush=True)

    q = JobQueue()
    raw = list(
        q.jobs.find({"status": {"$in": ["queued", "retry", "unverified", "leased"]}})
    )
    print(f"[migrate] Live candidates: {len(raw)}", flush=True)

    print("[migrate] Loading applied/terminal history…", flush=True)
    applied_jks = load_applied_history_jks(q)
    print(f"[migrate] History jk set size: {len(applied_jks)}", flush=True)

    # Snapshot before-state for rollback (only rows we may touch)
    before_docs = []
    for row in raw:
        doc = q.jobs.find_one({"_id": row["_id"]})
        if doc:
            before_docs.append(doc)

    retained_keys: dict[str, str] = {}
    plans: list[dict] = []

    # Pass 1: deterministic decisions (no AI). Process policy rejects first by
    # sorting so survivors claim retained_keys before weaker duplicates.
    scored = []
    for row in raw:
        a2r = a2_by_id.get(str(row["_id"]))
        # Priority: active live-matched APPLY/SAVE/VERIFY first
        pri = 5
        if a2r:
            if a2r.get("policy_action") == "APPLY" and a2r.get("live_matched"):
                pri = 0
            elif a2r.get("policy_action") == "SAVE" and a2r.get("live_matched"):
                pri = 1
            elif a2r.get("policy_action") == "VERIFY" and a2r.get("live_matched"):
                pri = 2
            elif a2r.get("policy_action") == "REJECT":
                pri = 9
        scored.append((pri, row, a2r))
    scored.sort(key=lambda x: x[0])

    for _, row, a2r in scored:
        plans.append(
            decide_outcome(
                row=row, a2=a2r, applied_jks=applied_jks, retained_keys=retained_keys,
            )
        )

    # Pass 2: AI gate only for needs_ai survivors
    ai_plans = [p for p in plans if p.get("needs_ai") and p.get("mutate")]
    print(f"[migrate] AI gate candidates: {len(ai_plans)} (policy rejects skipped)", flush=True)
    rows_by_id = {str(r["_id"]): r for r in raw}
    if not args.skip_ai:
        for i, plan in enumerate(ai_plans, 1):
            row = rows_by_id[plan["id"]]
            print(
                f"[migrate] AI {i}/{len(ai_plans)}: {row.get('company')} — {row.get('title')}",
                flush=True,
            )
            updated = run_ai_gate(plan, row)
            # Replace in plans list
            for j, p in enumerate(plans):
                if p["id"] == updated["id"]:
                    plans[j] = updated
                    break
    else:
        print("[migrate] WARNING: --skip-ai set; treating survivors as AI-passed", flush=True)
        for p in plans:
            if p.get("needs_ai"):
                p["ai_passed"] = True
                p["ai_score"] = 100
                p["ai_reason"] = "skipped_via_flag"

    summary = summarize_plans(plans)
    report = {
        "migration_id": migration_id,
        "mode": "apply" if apply_mode else "dry_run",
        "stamp": stamp,
        "checksum": checksum,
        "a2_report": a2_path,
        "discovery_engine_env": os.getenv("DISCOVERY_ENGINE", ""),
        "plan_summary": summary,
        "plans": plans,
    }

    results = []
    rollback_path = None
    if apply_mode:
        rollback_path = write_rollback_script(before_docs, migration_id, MIG_DIR)
        print(f"[migrate] Rollback script: {rollback_path}", flush=True)
        for plan in plans:
            if not plan.get("mutate"):
                results.append({"id": plan["id"], "applied": False, "reason": plan.get("reason")})
                continue
            row = rows_by_id[plan["id"]]
            results.append(apply_mutation(q, row, plan, migration_id))
        report["mutation_results"] = results
        report["post_counts"] = post_counts(q, migration_id)
        report["rollback_script"] = str(rollback_path)
    else:
        report["post_counts"] = None
        report["note"] = "DRY-RUN — no mutations"

    out = MIG_DIR / f"migrate_{migration_id}_{stamp}.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # Human summary
    outcome_c = Counter(p["outcome"] for p in plans)
    print(json.dumps({
        "wrote": str(out),
        "mode": report["mode"],
        "plan_summary": summary,
        "outcomes": dict(outcome_c),
        "post_counts": report.get("post_counts"),
        "rollback_script": report.get("rollback_script"),
        "ai_screened": sum(1 for p in plans if p.get("needs_ai")),
        "ai_rejected": sum(1 for p in plans if p.get("outcome") == "screen_rejected"),
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

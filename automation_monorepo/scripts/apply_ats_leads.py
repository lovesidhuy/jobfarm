#!/usr/bin/env python3
"""Apply to already-found Greenhouse/Lever leads (no re-discovery).

Reads the Metro Van IT lead list from wave-google-ats artifacts (or --urls /
--json) and submits each Greenhouse/Lever form via ats_apply.

Usage:
  .venv/bin/python scripts/apply_ats_leads.py
  .venv/bin/python scripts/apply_ats_leads.py --dry-run
  .venv/bin/python scripts/apply_ats_leads.py --max 2 --headed
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DEFAULT_JSON = ROOT / "artifacts" / "wave-google-ats" / "metro_van_it_latest.json"
OUT_DIR = ROOT / "artifacts" / "wave-google-ats"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val
    # Same secret path as Indeed (OpenRouter often only in Infisical cache).
    try:
        from core.supervisor_runtime import merge_dotenv_into_env
        merge_dotenv_into_env(os.environ, env_path, override=False)
    except Exception:
        pass
    # load IT IMAP secrets into process env for ATS verification codes
    try:
        from core.secret_manager import get_secret
        for k in (
            "IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT",
            "IMAP_EMAIL", "IMAP_APP_PASSWORD",
            "IMAP_EMAIL_GENERAL", "IMAP_APP_PASSWORD_GENERAL",
        ):
            v = (get_secret(k) or "").strip()
            if v and not (os.environ.get(k) or "").strip():
                os.environ[k] = v
        from core.supervisor_runtime import apply_imap_env_for_profile
        env = dict(os.environ)
        apply_imap_env_for_profile(env, "IT")
        for k in ("IMAP_EMAIL", "IMAP_APP_PASSWORD"):
            if env.get(k):
                os.environ[k] = env[k]
    except Exception:
        pass
    try:
        import core.secret_manager  # noqa: F401
    except Exception:
        pass


def _load_jobs(args) -> list[dict]:
    jobs: list[dict] = []
    if args.urls:
        for u in args.urls:
            jobs.append({"title": "", "company": "", "apply_url": u, "location": ""})
        return jobs
    path = Path(args.json) if args.json else DEFAULT_JSON
    if not path.is_file():
        raise SystemExit(f"Lead file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    for row in data.get("jobs") or data.get("targets") or []:
        url = (row.get("apply_url") or row.get("url") or "").strip()
        if not url:
            continue
        jobs.append(
            {
                "title": row.get("title") or "",
                "company": row.get("company") or "",
                "location": row.get("location") or "",
                "apply_url": url,
            }
        )
    return jobs


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", default=None, help="Lead JSON (default metro_van_it_latest.json)")
    ap.add_argument("--urls", nargs="+", default=None)
    ap.add_argument("--max", type=int, default=0, help="Max jobs to attempt (0=all)")
    ap.add_argument("--dry-run", action="store_true", help="Fill form, do not click submit")
    ap.add_argument("--headed", action="store_true", help="Show browser (default)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Do not skip URLs already submitted/confirmed in prior ats_apply_results",
    )
    ap.add_argument(
        "--include-mongo-dedupe",
        action="store_true",
        help="Also skip GH/Lever URLs marked applied in Mongo application_queue",
    )
    ap.add_argument(
        "--no-email-dedupe",
        action="store_true",
        help="Do not skip leads matching IMAP email_applied_history company/title",
    )
    ap.add_argument(
        "--refresh-imap",
        action="store_true",
        help="Refresh email_applied_history from IMAP before dedupe",
    )
    args = ap.parse_args()

    _load_dotenv()
    os.environ.setdefault("JOB_PROFILE", "IT")
    if args.dry_run:
        os.environ["ATS_DRY_RUN"] = "1"

    jobs = _load_jobs(args)
    skipped_already: list[dict] = []
    if not args.no_dedupe:
        from core.shared_modules.ats_lead_dedupe import filter_fresh_jobs

        jobs, skipped_already, applied = filter_fresh_jobs(
            jobs,
            artifacts_dir=OUT_DIR,
            include_mongo=bool(args.include_mongo_dedupe),
            include_email=not args.no_email_dedupe,
            refresh_imap=bool(args.refresh_imap),
        )
        print(
            f"Dedupe: applied_index={len(applied)} "
            f"fresh={len(jobs)} skipped={len(skipped_already)}",
            flush=True,
        )
        for s in skipped_already[:12]:
            print(
                f"  skip {s.get('dedupe_skip')}: {(s.get('title') or '')[:50]} "
                f"@ {(s.get('company') or '')[:30]} "
                f"{(s.get('canonical_url') or s.get('apply_url') or '')[:90]}",
                flush=True,
            )
    if args.max and args.max > 0:
        jobs = jobs[: args.max]
    if not jobs:
        print("No fresh leads to apply (all deduped or empty).")
        # Still write a small report for ops.
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"ats_apply_results_{stamp}.json"
        payload = {
            "ts": stamp,
            "dry_run": bool(args.dry_run),
            "counts": {
                "attempted": 0,
                "ok": 0,
                "failed": 0,
                "skipped_already": len(skipped_already),
            },
            "skipped_already": skipped_already,
            "results": [],
        }
        out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"wrote {out}")
        return 2

    from playwright.sync_api import sync_playwright
    from core.shared_modules.ats_apply import apply_url, is_greenhouse_or_lever_url

    headless = bool(args.headless) and not args.headed
    results = []
    print(
        f"Applying to {len(jobs)} Greenhouse/Lever lead(s); "
        f"dry_run={args.dry_run} headless={headless}",
        flush=True,
    )
    shot_dir = OUT_DIR / "screenshots"
    shot_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(locale="en-CA", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(20000)
        for i, job in enumerate(jobs, 1):
            url = job["apply_url"]
            title = job.get("title") or url
            company = job.get("company") or ""
            print(f"\n[{i}/{len(jobs)}] {title} @ {company}\n  {url}", flush=True)
            is_valid = is_greenhouse_or_lever_url(url)
            if not is_valid:
                from urllib.parse import urlparse
                host = (urlparse(url).hostname or "").lower()
                if host.startswith("www."):
                    host = host[4:]
                is_valid = host in {"grnh.se", "gh.io"}
            if not is_valid:
                row = {**job, "ok": False, "reason": "not greenhouse/lever"}
                results.append(row)
                print("  skip: not greenhouse/lever", flush=True)
                continue
            t0 = time.time()
            try:
                ok, result_url, reason = apply_url(page, url, title=title, company=company, dry_run=bool(args.dry_run))
            except Exception as exc:
                ok, result_url, reason = False, url, f"{type(exc).__name__}: {exc}"
            elapsed = round(time.time() - t0, 1)
            shot = ""
            if not ok:
                try:
                    safe = "".join(ch if ch.isalnum() else "_" for ch in title)[:40] or f"job{i}"
                    shot_path = shot_dir / f"fail_{safe}_{int(time.time())}.png"
                    page.screenshot(path=str(shot_path), full_page=True)
                    shot = str(shot_path)
                except Exception as exc:
                    shot = f"screenshot_failed:{exc}"
            row = {
                **job,
                "ok": bool(ok),
                "result_url": result_url,
                "reason": reason,
                "elapsed_s": elapsed,
                "screenshot": shot,
            }
            results.append(row)
            print(f"  → ok={ok} ({elapsed}s) reason={reason}", flush=True)
            if shot:
                print(f"  screenshot: {shot}", flush=True)
            time.sleep(0.8)
        browser.close()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"ats_apply_results_{stamp}.json"
    payload = {
        "ts": stamp,
        "dry_run": bool(args.dry_run),
        "counts": {
            "attempted": len(results),
            "ok": sum(1 for r in results if r.get("ok")),
            "failed": sum(1 for r in results if not r.get("ok")),
            "skipped_already": len(skipped_already),
        },
        "skipped_already": [
            {
                "title": s.get("title"),
                "company": s.get("company"),
                "apply_url": s.get("apply_url") or s.get("url"),
                "canonical_url": s.get("canonical_url"),
                "dedupe_skip": s.get("dedupe_skip"),
            }
            for s in skipped_already
        ],
        "results": results,
    }
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n{json.dumps(payload['counts'], indent=2)}")
    print(f"wrote {out}")
    return 0 if payload["counts"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

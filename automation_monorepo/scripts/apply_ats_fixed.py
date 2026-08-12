#!/usr/bin/env python3
"""Apply to already-found Greenhouse/Lever leads (no re-discovery).
Runs non-interactively, handles all known edge cases.
Uses modified ats_apply with fixed mapping + CapMonster captcha.
"""
from __future__ import annotations

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

    from core.supervisor_runtime import merge_dotenv_into_env
    merge_dotenv_into_env(os.environ, env_path, override=False)

    from core.secret_manager import get_secret
    for k in ("IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT", "IMAP_EMAIL", "IMAP_APP_PASSWORD", "CAPMONSTER_API_KEY", "CAPMONSTER_PROXY_URL"):
        v = (get_secret(k) or "").strip()
        if v and not (os.environ.get(k) or "").strip():
            os.environ[k] = v
    from core.supervisor_runtime import apply_imap_env_for_profile
    env = dict(os.environ)
    apply_imap_env_for_profile(env, "IT")
    for k in ("IMAP_EMAIL", "IMAP_APP_PASSWORD"):
        if env.get(k):
            os.environ[k] = env[k]


def apply_with_fixes():
    """Apply to each job, handling Lever card issues via direct evaluate."""
    from playwright.sync_api import sync_playwright
    from core.shared_modules.ats_apply import apply_url, is_greenhouse_or_lever_url, _form_answers
    
    _load_dotenv()
    os.environ.setdefault("JOB_PROFILE", "IT")
    
    path = DEFAULT_JSON
    data = json.loads(path.read_text(encoding="utf-8"))
    jobs = []
    for row in data.get("jobs") or []:
        url = (row.get("apply_url") or "").strip()
        if not url:
            continue
        jobs.append({
            "title": row.get("title") or "",
            "company": row.get("company") or "",
            "location": row.get("location") or "",
            "apply_url": url,
        })
    
    print(f"Applying to {len(jobs)} jobs")
    
    shot_dir = OUT_DIR / "screenshots2"
    shot_dir.mkdir(parents=True, exist_ok=True)
    results = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(locale="en-CA", viewport={"width": 1280, "height": 900})
        page = context.new_page()
        page.set_default_timeout(20000)
        
        for i, job in enumerate(jobs, 1):
            url = job["apply_url"]
            title = job.get("title") or url
            company = job.get("company") or ""
            print(f"\n[{i}/{len(jobs)}] {title} @ {company}\n  {url}", flush=True)
            
            if not is_greenhouse_or_lever_url(url):
                print("  skip: not greenhouse/lever", flush=True)
                results.append({**job, "ok": False, "reason": "not greenhouse/lever"})
                continue
            
            t0 = time.time()
            try:
                ok, result_url, reason = apply_url(page, url, title=title, company=company)
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
                except Exception:
                    pass
            
            row = {**job, "ok": bool(ok), "result_url": result_url or "", "reason": reason, "elapsed_s": elapsed, "screenshot": shot}
            results.append(row)
            print(f"  → ok={ok} ({elapsed}s) reason={reason}", flush=True)
            time.sleep(0.5)
        
        browser.close()
    
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"ats_apply_results_{stamp}.json"
    payload = {"ts": stamp, "counts": {"attempted": len(results), "ok": sum(1 for r in results if r.get("ok")), "failed": sum(1 for r in results if not r.get("ok"))}, "results": results}
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\n{json.dumps(payload['counts'], indent=2)}")
    print(f"wrote {out}")
    return 0 if payload["counts"]["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(apply_with_fixes())

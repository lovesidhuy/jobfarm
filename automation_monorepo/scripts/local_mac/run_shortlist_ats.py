#!/usr/bin/env python3
"""Run shortlisted queue jobs on Mac: submit ATS where possible, record results.

- Input: application_queue_active_and_dead.json (or --queue path)
- Only Greenhouse / Lever / Ashby / BambooHR URLs are applied (Playwright + CapMonster).
- Confirmations written to artifacts/mac-shortlist-run/
- Failures also emit training_capture events + optional DOM screenshot/text.

Usage:
  cd automation_monorepo
  python3 scripts/local_mac/run_shortlist_ats.py
  python3 scripts/local_mac/run_shortlist_ats.py --headed --limit 5
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

DEFAULT_QUEUE = (
    ROOT
    / "outputs"
    / "queue_export_20260806_234716"
    / "application_queue_active_and_dead.json"
)
OUT_ROOT = ROOT / "artifacts" / "mac-shortlist-run"


def _load_dotenv() -> None:
    env_path = ROOT / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
    try:
        from core.supervisor_runtime import merge_dotenv_into_env

        merge_dotenv_into_env(os.environ, env_path, override=False)
    except Exception:
        pass
    try:
        from core.secret_manager import get_secret

        for k in (
            "CAPMONSTER_API_KEY",
            "CAPMONSTER_CLIENT_KEY",
            "CAPMONSTER_PROXY_URL",
            "IMAP_EMAIL_IT",
            "IMAP_APP_PASSWORD_IT",
            "IMAP_EMAIL",
            "IMAP_APP_PASSWORD",
        ):
            v = (get_secret(k) or "").strip()
            if v and not (os.environ.get(k) or "").strip():
                os.environ[k] = v
    except Exception:
        pass


def _enable_capmonster() -> None:
    os.environ.setdefault("USE_CAPMONSTER_CAPTCHA_SOLVER", "1")
    os.environ.setdefault("CAPTCHA_USE_CAPMONSTER", "1")
    os.environ.setdefault("USE_CAPMONSTER", "1")
    os.environ.setdefault("CAPMONSTER_RECAPTCHA_PROXYLESS_FALLBACK", "1")
    os.environ.setdefault("ATS_CAPTCHA_ALLOW_HUMAN_WAIT", "0")
    os.environ.setdefault("DISABLE_GUI_CAPTCHA", "1")
    # No browser residential proxy for public board forms
    for k in (
        "PROXY_URL",
        "PROXY_CHEAP_URL",
        "WEBSHARE_PROXY_URL",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
    ):
        os.environ.pop(k, None)


def _job_url(row: dict) -> str:
    meta = row.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:
            meta = {}
    for key in ("destination_url", "url", "result_url"):
        u = (row.get(key) or meta.get(key) or "").strip()
        if u.startswith("http"):
            return u
    return ""


def _detect_platform(url: str) -> str | None:
    try:
        from core.ats.registry import detect_platform

        return detect_platform(url)
    except Exception:
        host = (urlparse(url).hostname or "").lower()
        if "greenhouse" in host or "gh_jid" in url:
            return "greenhouse"
        if "lever.co" in host:
            return "lever"
        if "ashbyhq.com" in host:
            return "ashby"
        if "bamboohr.com" in host:
            return "bamboohr"
        return None


def _record_training(
    event: str,
    *,
    portal: str,
    profile: str,
    job_id: str,
    url: str,
    result_url: str = "",
    **payload,
) -> None:
    try:
        from core.training_capture import record_training_event

        record_training_event(
            event,
            portal=portal,
            profile=profile or "it",
            job_id=job_id,
            job_url=url,
            result_url=result_url,
            worker="mac-shortlist",
            **payload,
        )
    except Exception as exc:
        print(f"  [training] write failed: {exc}", flush=True)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _safe_shot(page, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path), full_page=True)
    except Exception:
        pass


def _page_snippet(page, limit: int = 4000) -> str:
    try:
        return (page.evaluate(f"() => (document.body?.innerText || '').slice(0, {limit})") or "")
    except Exception:
        return ""


def main() -> int:
    ap = argparse.ArgumentParser(description="Local Mac shortlist ATS apply runner")
    ap.add_argument("--queue", type=Path, default=DEFAULT_QUEUE)
    ap.add_argument("--limit", type=int, default=0, help="Max jobs (0=all)")
    ap.add_argument("--headed", action="store_true", help="Show browser")
    ap.add_argument("--skip-already", action="store_true", default=True)
    ap.add_argument(
        "--status",
        default="queued,dead,manual_review,retry,leased",
        help="Comma statuses to process",
    )
    ap.add_argument(
        "--portals",
        default="greenhouse,lever,ashby,bamboohr,google",
        help="Queue portal labels to include",
    )
    args = ap.parse_args()

    _load_dotenv()
    _enable_capmonster()
    os.environ.setdefault("JOB_PROFILE", "IT")
    os.environ.setdefault("TRAINING_EVENTS_MONGO", "0")  # local JSONL only by default
    train_file = OUT_ROOT / "training_events.jsonl"
    os.environ["JOBBOTS_TRAINING_EVENTS_FILE"] = str(train_file)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_dir = OUT_ROOT / f"run_{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    conf_path = run_dir / "confirmations.jsonl"
    fail_path = run_dir / "failures.jsonl"
    results_csv = run_dir / "results.csv"
    summary_path = run_dir / "summary.json"
    shots = run_dir / "screenshots"
    shots.mkdir(exist_ok=True)

    queue_path = args.queue.expanduser().resolve()
    if not queue_path.is_file():
        print(f"Queue not found: {queue_path}", file=sys.stderr)
        return 2

    rows = json.loads(queue_path.read_text(encoding="utf-8"))
    want_status = {s.strip().lower() for s in args.status.split(",") if s.strip()}
    want_portals = {s.strip().lower() for s in args.portals.split(",") if s.strip()}

    jobs: list[dict] = []
    for r in rows:
        if (r.get("status") or "").lower() not in want_status:
            continue
        if (r.get("portal") or "").lower() not in want_portals:
            continue
        url = _job_url(r)
        if not url:
            continue
        platform = _detect_platform(url)
        if not platform:
            continue
        jobs.append({**r, "_url": url, "_platform": platform})

    # Dedupe by URL
    seen: set[str] = set()
    deduped = []
    for j in jobs:
        u = j["_url"].split("?")[0].rstrip("/")
        if u in seen:
            continue
        seen.add(u)
        deduped.append(j)
    jobs = deduped
    if args.limit and args.limit > 0:
        jobs = jobs[: args.limit]

    cm = bool((os.getenv("CAPMONSTER_API_KEY") or os.getenv("CAPMONSTER_CLIENT_KEY") or "").strip())
    print(f"Shortlist: {len(jobs)} ATS jobs from {queue_path.name}")
    print(f"CapMonster: {'ON' if cm else 'OFF'}")
    print(f"Output: {run_dir}")
    print(f"Training JSONL: {train_file}")

    from core.browser.open_chrome import createBrowserSession
    from core.shared_modules.ats_apply import apply_url

    results: list[dict] = []
    counts = {"applied": 0, "already_applied": 0, "failed": 0, "error": 0, "skipped": 0}

    headless = not args.headed
    os.environ["RUN_IN_BACKGROUND"] = "true" if headless else "false"
    sb = page = context = browser = pw = None
    try:
        sb, page, context, browser, pw = createBrowserSession(bot_name="ats_it")
        page.set_default_timeout(45000)

        for i, job in enumerate(jobs, 1):
            url = job["_url"]
            platform = job["_platform"]
            title = job.get("title") or ""
            company = job.get("company") or ""
            portal = (job.get("portal") or platform).lower()
            profile = (job.get("profile") or "it").lower()
            jid = str(job.get("id") or job.get("_id") or f"local-{i}")
            print(f"\n[{i}/{len(jobs)}] {platform} | {title} @ {company}\n  {url}", flush=True)

            _record_training(
                "application_started",
                portal=portal,
                profile=profile,
                job_id=jid,
                url=url,
                title=title,
                company=company,
                ats_platform=platform,
                source_status=job.get("status"),
            )

            t0 = time.time()
            row_out = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "job_id": jid,
                "portal": portal,
                "ats_platform": platform,
                "title": title,
                "company": company,
                "url": url,
                "source_status": job.get("status"),
            }
            try:
                ok, result_url, reason = apply_url(page, url, title=title, company=company)
                elapsed = round(time.time() - t0, 1)
                reason = reason or ""
                reason_l = reason.lower()
                result_url = result_url or page.url or url
                snippet = _page_snippet(page)

                if ok or "already applied" in reason_l:
                    kind = "already_applied" if "already applied" in reason_l else "applied"
                    counts[kind] += 1
                    row_out.update(
                        {
                            "ok": True,
                            "outcome": kind,
                            "reason": reason,
                            "result_url": result_url,
                            "elapsed_s": elapsed,
                            "confirmation_snippet": snippet[:1500],
                        }
                    )
                    _append_jsonl(conf_path, row_out)
                    _record_training(
                        "application_outcome",
                        portal=portal,
                        profile=profile,
                        job_id=jid,
                        url=url,
                        result_url=result_url,
                        outcome=kind,
                        reason=reason,
                        title=title,
                        company=company,
                        ats_platform=platform,
                        elapsed_s=elapsed,
                        confirmation_snippet=snippet[:800],
                    )
                    print(f"  → {kind}: {reason[:120]}", flush=True)
                else:
                    counts["failed"] += 1
                    shot = shots / f"{i:02d}_{platform}_{jid[:8]}.png"
                    _safe_shot(page, shot)
                    row_out.update(
                        {
                            "ok": False,
                            "outcome": "failed",
                            "reason": reason,
                            "result_url": result_url,
                            "elapsed_s": elapsed,
                            "screenshot": str(shot),
                            "page_snippet": snippet[:2500],
                        }
                    )
                    _append_jsonl(fail_path, row_out)
                    _record_training(
                        "application_outcome",
                        portal=portal,
                        profile=profile,
                        job_id=jid,
                        url=url,
                        result_url=result_url,
                        outcome="failed",
                        reason=reason,
                        title=title,
                        company=company,
                        ats_platform=platform,
                        elapsed_s=elapsed,
                        page_snippet=snippet[:1200],
                        screenshot=str(shot),
                    )
                    # Extra failure detail for form/captcha learning
                    _record_training(
                        "application_failure_detail",
                        portal=portal,
                        profile=profile,
                        job_id=jid,
                        url=url,
                        result_url=result_url,
                        reason=reason,
                        title=title,
                        company=company,
                        ats_platform=platform,
                        page_snippet=snippet[:2000],
                        screenshot=str(shot),
                    )
                    print(f"  → FAILED: {reason[:160]}", flush=True)
            except Exception as exc:
                counts["error"] += 1
                elapsed = round(time.time() - t0, 1)
                tb = traceback.format_exc()
                shot = shots / f"{i:02d}_{platform}_{jid[:8]}_exc.png"
                _safe_shot(page, shot)
                row_out.update(
                    {
                        "ok": False,
                        "outcome": "error",
                        "reason": str(exc),
                        "traceback": tb[-2000:],
                        "elapsed_s": elapsed,
                        "screenshot": str(shot),
                        "page_snippet": _page_snippet(page)[:2500],
                    }
                )
                _append_jsonl(fail_path, row_out)
                _record_training(
                    "application_outcome",
                    portal=portal,
                    profile=profile,
                    job_id=jid,
                    url=url,
                    outcome="error",
                    reason=str(exc),
                    title=title,
                    company=company,
                    ats_platform=platform,
                    traceback=tb[-1500:],
                )
                print(f"  → ERROR: {exc}", flush=True)

            results.append(row_out)
            # Brief pause between boards
            time.sleep(1.2)

    finally:
        for obj in (page, browser, pw):
            try:
                if obj:
                    obj.close() if hasattr(obj, 'close') else obj.stop()
            except Exception:
                pass
        try:
            if sb:
                sb.quit()
        except Exception:
            pass

    # CSV summary
    cols = [
        "ts",
        "outcome",
        "ats_platform",
        "portal",
        "title",
        "company",
        "reason",
        "result_url",
        "url",
        "elapsed_s",
        "job_id",
    ]
    with results_csv.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow({c: r.get(c, "") for c in cols})

    summary = {
        "started": stamp,
        "finished": datetime.now(timezone.utc).isoformat(),
        "queue": str(queue_path),
        "run_dir": str(run_dir),
        "counts": counts,
        "total_attempted": len(results),
        "capmonster": cm,
        "confirmations": str(conf_path),
        "failures": str(fail_path),
        "training_events": str(train_file),
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    # latest pointer
    (OUT_ROOT / "latest_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("\n=== DONE ===")
    print(json.dumps(summary, indent=2))
    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

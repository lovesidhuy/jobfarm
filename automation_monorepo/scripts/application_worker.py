#!/usr/bin/env python3
"""**Phase II** — application / bookmark execution only.

Leases already-approved jobs from the queue (populated by Phase I discovery in
``core/discovery/planner.py``) and executes them:

  * Indeed/Glassdoor Easy Apply  → apply (bot runs in direct-links mode, which
    bypasses the discovery-side title + AI-fit gates).
  * Metro-Van company-site       → bookmark/save only (``JOB_QUEUE_BOOKMARK_ONLY``).

Phase II performs NO primary job-fit, geography, or remote-status screening —
that all happens in Phase I-B (``planner._screen_and_enqueue`` +
``classification/location_policy.py``) before a job is ever queued. The only
checks here are defensive final validations already emitted by the applier
(already-applied, external-apply → bookmark, title mismatch, expired/closed).
Do not add discovery/screening responsibilities to this worker.
"""
from __future__ import annotations
import argparse,json,os,subprocess,sys,tempfile,time,threading
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; REPO=ROOT.parent; sys.path.insert(0,str(ROOT))
import core.secret_manager  # noqa: F401
from core.job_queue import JobQueue,runtime_worker_name

def retryable(reason):
    text=(reason or "").lower()
    permanent=("already applied","external apply","not easy apply","job expired","job closed","title mismatch",
               "easy apply button not found","company-site apply","plain apply / not easy apply","employer website",
               "company website","external apply only","could not locate easy apply","no easy apply",
               "expired or redirected","similar jobs", "jobbank_email_apply_retired")
    return not any(x in text for x in permanent)

# Failures worth another lease (browser/network hiccups, CAPTCHA, etc.).
_TRANSIENT_MARKERS=("captcha","cloudflare","timeout","timed out","navigation","disconnected",
                    "network","stale","rate limit","temporarily","connection reset",
                    "browser closed","target closed","target page","no result","without result",
                    "bot exited","already leased","profile lease",
                    "authwall","login required","sign in","session not authenticated",
                    "authentication required","linkedin authentication",
                    "did not render","empty title",
                    # flaky Easy Apply / form fill — often succeeds on re-try after typeahead/modal race
                    "not submitted","modal disappeared","modal not found","typeahead",
                    "form_stalled","next disabled","false_positive_submit",
                    "no redirect","smartapply failed","smartapply tab did not open",
                    "tab did not open","event loop is closed")

# CAPTCHA / Cloudflare blocks are re-queued at the *end* of the same portal+profile
# queue (not short exponential backoff that can jump ahead of fresher work).
_CAPTCHA_CF_MARKERS=("captcha","cloudflare")

_VERIFY_METHODS=("unverified","verify","unknown")

def is_captcha_cf_reason(reason):
    """True when the failure text indicates CAPTCHA or Cloudflare (incl. mid-canary)."""
    text=(reason or "").lower()
    return any(m in text for m in _CAPTCHA_CF_MARKERS)

def classify_outcome(result,dispatched_method,attempts,max_attempts):
    """Map a bot result to a terminal decision (Phase-II state lockdown).

    Returns ``(action, resolved_method)`` where:
      * ``action`` ∈ {"applied","already_applied","skipped","bookmarked",
        "manual_review","captcha_cf_requeue","retry","dead"}
      * ``resolved_method`` ∈ {"easy_apply","company_site",""} — the *verified*
        apply method to persist ("" = leave the record's method unchanged).

    Honest metrics: ``already_applied`` and ``skipped`` (e.g. cover letter) are
    terminal but must NOT be counted as fresh applications.

    Lease-and-verify (``unverified``) jobs never retry forever: an unresolved
    outcome becomes ``manual_review`` instead of looping until ``dead``.
    CAPTCHA/Cloudflare failures within budget become ``captcha_cf_requeue`` so the
    worker can push them to the back of the same platform queue.
    """
    status=(result.get("status") or "").strip().lower()
    reason=(result.get("reason") or "").strip().lower()
    resolved=(result.get("application_method") or "").strip().lower()
    is_verify=dispatched_method in _VERIFY_METHODS

    # Explicit bot statuses first
    if status in ("already_applied","already-applied"):
        return "already_applied",(resolved or "easy_apply")
    if status in ("skipped","skipped_cover_letter","cover_letter_skipped"):
        return "skipped",(resolved or "easy_apply")
    if status in ("bookmarked","saved") or "company-site bookmarked" in reason or "saved as lead" in reason:
        return "bookmarked",(resolved or "company_site")
    if status in ("manual_review","verification_failed"):
        return "manual_review",resolved

    # Reason-based classification (bots often return status=applied|failed + text)
    if "already applied" in reason or status=="already_applied":
        return "already_applied",(resolved or "easy_apply")
    if (
        "cover letter" in reason
        or "skipped_cover_letter" in reason
        or "skipped due to cover letter" in reason
    ):
        return "skipped",(resolved or "easy_apply")
    if status=="applied":
        return "applied",(resolved or ("easy_apply" if is_verify else "easy_apply"))

    captcha_cf=is_captcha_cf_reason(reason)
    if captcha_cf and attempts<max_attempts:
        return "captcha_cf_requeue",""
    # Workopolis apply-tab flake: worth one more try
    if "no apply flow tab" in reason and attempts<max_attempts:
        return "retry",""
    transient=any(m in reason for m in _TRANSIENT_MARKERS)
    if transient and attempts<max_attempts:
        return "retry",""
    if is_verify:
        return "manual_review",resolved
    if retryable(reason) and attempts<max_attempts:
        return "retry",""
    return "dead",""

def ensure_resume_server_healthy():
    import requests
    resume_server_base = "http://127.0.0.1:3001"
    try:
        r = requests.get(resume_server_base, timeout=2)
        if r.status_code < 500:
            print("[Resume Server Check] Resume server is already healthy.")
            return True
    except Exception:
        pass

    print("[Resume Server Check] Resume server not running on port 3001. Starting it...")
    server_dir = REPO / "resume_workflow"
    if not server_dir.is_dir():
        print(f"[Resume Server Check] Warning: resume_workflow directory not found at {server_dir}")
        return False

    # Start the node server in background
    try:
        subprocess.Popen(
            "node server.js > server.log 2>&1 &",
            shell=True,
            cwd=str(server_dir),
            env=os.environ
        )
    except Exception as exc:
        print(f"[Resume Server Check] Failed to start resume server: {exc}")
        return False

    # Poll status up to 10 seconds
    for attempt in range(1, 11):
        time.sleep(1)
        try:
            r = requests.get(resume_server_base, timeout=2)
            if r.status_code < 500:
                print(f"[Resume Server Check] Resume server started and healthy after {attempt}s.")
                return True
        except Exception:
            pass
    print("[Resume Server Check] Warning: resume server did not become healthy within 10 seconds.")
    return False

def _is_greenhouse_or_lever_url(url: str) -> bool:
    try:
        from core.shared_modules.ats_apply import is_greenhouse_or_lever_url
        return bool(is_greenhouse_or_lever_url(url))
    except Exception:
        u = (url or "").lower()
        return any(h in u for h in (
            "boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co",
            "jobs.ashbyhq.com", "ashbyhq.com", "bamboohr.com",
        ))


def _dispatch_method(job):
    """Resolve the effective apply method for this job, enforcing safeguard #1:
    the lease-and-verify route is allowed ONLY for Metro-Vancouver jobs. If a
    verify job somehow isn't Metro-Van, degrade to bookmark-only (never apply)."""
    metadata=job.get("metadata") or {}
    method=metadata.get("application_method","easy_apply")
    region=(metadata.get("region") or "").strip().upper()
    if method in _VERIFY_METHODS and region and region not in ("METRO_VAN","METRO_VANCOUVER"):
        return "company_site"
    return method


def _is_metro_vancouver_queue_job(job) -> bool:
    """Final no-submit guard for rows queued before the strict geo policy.

    Discovery now rejects every non-Metro job, but a queue can retain rows
    from an earlier run. Never rely on that historical data being clean when
    deciding whether to submit an application.

    Also rejects search-centre false positives where location/metadata say
    Metro Van but the title pins the job to Quebec / Mexico / Toronto-only.
    """
    try:
        from core.discovery.classification.location_policy import (
            REGION_METRO_VAN,
            classify_region,
            title_exclusive_out_of_area,
        )
    except Exception:
        # Missing or unreadable location data is never sufficient to submit.
        return False

    title = str(job.get("title") or "")
    location = str(job.get("location") or "")
    if title_exclusive_out_of_area(title, location=location):
        return False

    metadata = job.get("metadata") or {}
    region = (metadata.get("region") or "").strip().upper()
    if region:
        return region in {"METRO_VAN", "METRO_VANCOUVER"}
    return classify_region(location) == REGION_METRO_VAN

def build_dispatch_env(job,result_path,*,base_env=None):
    """Build the per-job subprocess environment (safeguard #7).

    Returns ``(method, env, job_serializable)``. Verification/bookmark flags are
    always derived from THIS job: any inherited ``JOB_QUEUE_BOOKMARK_*`` /
    ``JOB_QUEUE_VERIFY_APPLY_TYPE`` value is cleared first so a stale flag can never
    leak from the parent environment or a previous dispatch into the next job.

    Wave B.1: ``portal=glassdoor`` never sets bookmark/verify flags (Easy Apply
    only — no company-site / lease-and-verify path).
    """
    method=str(_dispatch_method(job) or "easy_apply").strip().lower()
    portal=(job.get("portal") or "").strip().lower()

    # Sanitize datetime fields for JSON serialization (incl. nested metadata).
    def _jsonable(value):
        if hasattr(value, "isoformat"):
            return value.isoformat()
        if isinstance(value, dict):
            return {k: _jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_jsonable(v) for v in value]
        return value

    job_serializable = _jsonable(dict(job))

    env=dict(os.environ if base_env is None else base_env)
    for _flag in ("JOB_QUEUE_BOOKMARK_FIRST","JOB_QUEUE_BOOKMARK_ONLY","JOB_QUEUE_VERIFY_APPLY_TYPE"):
        env.pop(_flag,None)
    env.update({"JOBBOT_MODE":"apply","JOB_QUEUE_DIRECT_JOB":json.dumps(job_serializable),
      "JOB_QUEUE_RESULT_FILE":str(result_path),"BOT_MAX_RUNTIME_SECONDS":env.get("BOT_MAX_RUNTIME_SECONDS","1800"),
      "SKIP_USER_START": "1"})

    # Glassdoor + Workopolis: Easy/Quick Apply only — never bookmark/verify
    # company-site paths. Phase I policy already rejects non-EA; this is the
    # Phase II hard refuse for any leftover queue rows.
    if portal in {"glassdoor", "workopolis"}:
        return method, env, job_serializable

    if portal == "indeed":
        env["JOB_QUEUE_BOOKMARK_FIRST"] = "1"

    if method=="company_site":
        # ATS leads are real apply pages — submit through the adapter engine.
        # Other company sites remain bookmark-only (no generic site form bot).
        if _is_greenhouse_or_lever_url(job.get("url") or ""):
            env["JOB_QUEUE_BOOKMARK_FIRST"]="1"
            # Do NOT set BOOKMARK_ONLY — allow external ATS submission.
        else:
            # Confirmed non-ATS external application → bookmark/save only.
            env["JOB_QUEUE_BOOKMARK_FIRST"]="1"
            env["JOB_QUEUE_BOOKMARK_ONLY"]="1"
    elif method in _VERIFY_METHODS:
        # Apply type not confirmed at discovery. Verify it without saving first:
        # Easy Apply may proceed; a company-site result is held for the
        # deterministic + batch-AI save decision.
        env["JOB_QUEUE_VERIFY_APPLY_TYPE"]="1"
    return method, env, job_serializable

# Public ATS boards (GH/Lever/Ashby/Bamboo) — Playwright only, no NST, no proxy.
_ATS_PORTALS = frozenset({"greenhouse", "lever", "ashby", "bamboohr", "google", "company_apply"})
# Job Bank email application is retired in favour of authenticated Direct
# Apply. No production portal may route a queue row through SMTP here.
_EMAIL_PORTALS = frozenset()
_JOBBANK_DIRECT_METHODS = frozenset({"direct_apply", "jobbank_direct_apply"})
_PROXY_ENV_KEYS = (
    "PROXY_URL", "PROXY_CHEAP_URL", "CAPMONSTER_PROXY_URL", "WEBSHARE_PROXY_URL",
    "JOBSPY_PROXY_WEBSHARE", "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
    "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy",
)


def _is_ats_portal_or_url(portal: str, url: str = "") -> bool:
    p = (portal or "").strip().lower()
    if p in _ATS_PORTALS:
        return True
    return _is_greenhouse_or_lever_url(url)


def _is_jobbank_direct_apply(portal: str, method: str = "") -> bool:
    return (
        (portal or "").strip().lower() in {"jobbank", "job_bank"}
        and (method or "").strip().lower() in _JOBBANK_DIRECT_METHODS
    )


def _is_email_portal(portal: str, method: str = "") -> bool:
    return (
        (portal or "").strip().lower() in _EMAIL_PORTALS
        and not _is_jobbank_direct_apply(portal, method)
    )


def _strip_proxy_and_nst(env: dict) -> None:
    """ATS apply must not burn NST quota or residential proxies."""
    for k in _PROXY_ENV_KEYS:
        env.pop(k, None)
    env["BROWSER_VENDOR"] = "playwright"
    env["ATS_HEADLESS"] = env.get("ATS_HEADLESS") or "1"
    env["KEEP_BROWSER"] = "0"
    env["NSTBROWSER_KEEP_ALIVE"] = "0"
    env["NSTBROWSER_FORBID_CREATE"] = "1"
    # Clear any stamped profile so open_chrome cannot be pulled in accidentally.
    for k in list(env.keys()):
        if k.startswith("NSTBROWSER_PROFILE") or k in {
            "NSTBROWSER_API_KEY", "NSTBROWSER_API_KEY_2", "NST_API_KEY", "NST_PROFILE_ID",
        }:
            env.pop(k, None)


def _enforce_nst_reuse(env, portal, profile, *, job_url: str = "", method: str = ""):
    """Force forbid-create + require existing per-bot profile id (no new NST profiles).

    Dual-account: prefers slot 2 when primary daily opens are near the soft cap
    (see ``core.browser.nst_accounts``). Always reuses a stamped profile id.

    Greenhouse / Lever / Ashby / BambooHR / Google ATS never use NST — public
    board forms run via headless Playwright (see google_it.py).
    """
    if _is_ats_portal_or_url(portal, job_url):
        _strip_proxy_and_nst(env)
        print(f"[Worker] ATS portal={portal or 'url'} → Playwright only (no NST, no proxy)")
        return
    if _is_email_portal(portal, method):
        _strip_proxy_and_nst(env)
        print(f"[Worker] Email portal={portal} → SMTP only (no NST)")
        return
    from core.browser.nst_profile_safety import (
        env_key_for_bot,
        portal_profile_bot_name,
        require_existing_nst_profile_id,
    )

    env["NSTBROWSER_FORBID_CREATE"] = "1"
    env.pop("NSTBROWSER_ROTATE_PROFILE", None)
    vendor = (env.get("BROWSER_VENDOR") or "nstbrowser").strip().lower()
    if vendor not in {"nstbrowser", "nst"}:
        return
    bot = portal_profile_bot_name(portal, profile)
    try:
        from core.browser.nst_accounts import apply_slot_to_env
        apply_slot_to_env(env, bot)
    except Exception as exc:
        print(f"[Worker] NST dual-account resolve note: {exc}")
    key = env_key_for_bot(bot)
    pid = (env.get("NSTBROWSER_PROFILE_ID") or env.get(key) or "").strip()
    pid = require_existing_nst_profile_id(pid, bot_name=bot, env_key=key)
    env["NSTBROWSER_PROFILE_ID"] = pid
    env.setdefault(key, pid)
    slot = env.get("NSTBROWSER_ACTIVE_SLOT") or env.get("_NST_RESOLVED_SLOT")
    if slot:
        print(f"[Worker] NST slot={slot} bot={bot} profile={pid[:8]}…")


def dispatch(job,result_path,*,keep_browser=False):
    method, env, job_serializable = build_dispatch_env(job,result_path)
    portal=job["portal"]; profile=job["profile"]
    job_url = (job.get("url") or (job.get("metadata") or {}).get("destination_url") or "")
    # Glassdoor + Workopolis: Easy/Quick Apply only (Phase I policy + Phase II).
    portal_l = (portal or "").strip().lower()
    # Indeed General uses the verified static resume.  Resume tailoring is an
    # optional enhancement, and its external LLM workflow can hold a live
    # application at the final review screen; never let it block this queue.
    if portal_l == "indeed" and (profile or "").strip().lower() == "general":
        env["INDEED_SKIP_RESUME_TAILOR"] = "1"
    if portal_l == "glassdoor" and method != "easy_apply":
        return 2, f"glassdoor_non_easy_apply_refused:{method}"
    if portal_l == "workopolis" and method != "easy_apply":
        return 2, f"workopolis_non_easy_apply_refused:{method}"
    direct_jobbank = _is_jobbank_direct_apply(portal_l, method)
    if portal_l in {"jobbank", "job_bank"} and not direct_jobbank:
        return 2, "jobbank_email_apply_retired"
    if direct_jobbank:
        # Direct Apply is an authenticated Job Bank session.  Do not inherit a
        # Playwright/SMTP setting from a previous queue lease: this lane must
        # use the pre-provisioned jobbank_it NST profile.
        env["BROWSER_VENDOR"] = "nstbrowser"
        env["BOT_NAME"] = "jobbank_it"
        env["JOB_PROFILE"] = "IT"
        env["JOB_QUEUE_PORTAL"] = "jobbank"
        env["JOBBOTS_PORTAL"] = "jobbank"
        try:
            from core.secret_manager import align_capmonster_proxy_env
            align_capmonster_proxy_env(env)
        except Exception as exc:
            print(f"[Worker] Job Bank Webshare proxy stamp note: {exc}")
    if _is_ats_portal_or_url(portal_l, job_url) or _is_email_portal(portal_l, method):
        # Never keep NST alive for ATS / email — waste of quota.
        keep_browser = False
        _strip_proxy_and_nst(env)
    elif keep_browser:
        env["KEEP_BROWSER"]="1"
        env["NSTBROWSER_KEEP_ALIVE"]="1"
    else:
        # Hard-off: Infisical/.env keep-alive must not leak into single-job canaries
        # (NST quota — always stop profile when worker is not multi-job).
        env["KEEP_BROWSER"] = "0"
        env["NSTBROWSER_KEEP_ALIVE"] = "0"
    # CF-heavy boards: force Proxy-Cheap for browser + CapMonster (same egress).
    # LinkedIn / ATS stay on Webshare or no proxy.
    if portal_l in {"indeed", "glassdoor", "workopolis"}:
        try:
            from core.secret_manager import stamp_cf_heavy_proxy_env
            from core.browser.nst_profile_safety import portal_profile_bot_name
            bot = portal_profile_bot_name(portal, profile)
            stamp_cf_heavy_proxy_env(env, portal=portal_l, bot_name=bot)
            host = ""
            try:
                from urllib.parse import urlparse
                host = urlparse(env.get("PROXY_URL") or "").hostname or "?"
            except Exception:
                host = "?"
            print(f"[Worker] CF-heavy portal={portal_l} proxy=cheap host={host} (browser+CapMonster matched)")
        except Exception as exc:
            print(f"[Worker] CF-heavy proxy stamp note: {exc}")
    try:
        _enforce_nst_reuse(env, portal, profile, job_url=job_url, method=method)
    except RuntimeError as exc:
        return 2, str(exc)
    if portal in {"indeed", "glassdoor"} and profile == "it":
        ensure_resume_server_healthy()
    if portal == "workopolis" and profile == "it":
        ensure_resume_server_healthy()
    if portal in {"indeed","glassdoor","workopolis"}:
        script=ROOT/"bots"/f"{portal}_{profile}.py"
        if not script.is_file(): return 2,f"missing bot entry point {script}"
        if portal=="indeed":
            direct=result_path.with_suffix(".job.json")
            jk = job["source_job_id"]
            if jk.startswith("in-"):
                jk = jk[3:]
            job_url = (job.get("url") or "").strip()
            if job_url and not job_url.startswith("http"):
                job_url = f"https://ca.indeed.com/{job_url.lstrip('/')}"
            direct.write_text(json.dumps([{"job_id":jk,"jobkey":jk,
              "title":job["title"],"company":job["company"],"url":job_url,"job_link":job_url,
              "description":job.get("description","")}]),encoding="utf-8")
            env["INDEED_DIRECT_LINKS_PATH"]=str(direct); env["INDEED_MAX_APPLICATION_OUTCOMES"]="1"
            # Direct retries must not be suppressed by the discovery queue's
            # handled-state or by a previous application attempt.
            qname = f"indeed-direct-{jk}-{job.get('attempts', 0)}"
            env["INDEED_CRAWLEE_QUEUE_NAME"] = qname
            # General Indeed has its own Crawlee storage tree. Clearing the IT
            # tree for a general job leaves a stale/missing request behind,
            # causing "Enqueued 0" and a false no-outcome failure.
            bot_tree = "gen_indeed" if profile == "general" else "it_indeed cwgeopy"
            qdir = ROOT.parent / "master" / bot_tree / "Auto_indeed" / "data" / "crawlee_storage" / "request_queues" / qname
            if qdir.exists():
                import shutil
                try:
                    shutil.rmtree(qdir)
                    print(f"[Worker] Cleared old Crawlee queue directory: {qname}")
                except Exception as ex:
                    print(f"[Worker] Warning: failed to clear Crawlee queue: {ex}")
        elif portal=="glassdoor":
            # Glassdoor Phase II: same single-outcome contract as Indeed. The bot
            # reads JOB_QUEUE_DIRECT_JOB (already set). Cap outcomes so one lease
            # cannot bleed into the next job's env/session.
            env["GLASSDOOR_MAX_APPLICATION_OUTCOMES"] = "1"
            env["INDEED_MAX_APPLICATION_OUTCOMES"] = "1"  # SmartApply handoff path
            # Prefer English CA host — fr.glassdoor.ca CF rate is worse for automation.
            raw_url = (job.get("url") or "").strip()
            if raw_url:
                fixed = raw_url
                for host in (
                    "https://fr.glassdoor.ca",
                    "http://fr.glassdoor.ca",
                    "https://www.glassdoor.com",
                    "http://www.glassdoor.com",
                    "https://glassdoor.ca",
                    "http://glassdoor.ca",
                ):
                    if fixed.startswith(host):
                        fixed = "https://www.glassdoor.ca" + fixed[len(host):]
                        break
                if fixed != raw_url:
                    print(f"[Worker] Rewrote Glassdoor URL host: {raw_url} → {fixed}")
                    job_serializable = dict(job_serializable)
                    job_serializable["url"] = fixed
                    env["JOB_QUEUE_DIRECT_JOB"] = json.dumps(job_serializable)
            env["GLASSDOOR_BASE_URL"] = "https://www.glassdoor.ca"
        return subprocess.run([sys.executable,"-u",str(script)],cwd=ROOT,env=env).returncode,""
    if portal=="linkedin":
        # One production LinkedIn bot (linkedin_general NST profile).
        # Queue profile may be it|general (discovery gates differ); apply always
        # uses the same logged-in session.
        script=ROOT/"bots"/"linkedin_general.py"
        if not script.is_file(): return 2,f"missing bot entry point {script}"
        env["LINKEDIN_DIRECT_JOB_URL"]=job["url"]
        # Mongo job docs include datetime fields; default=str keeps direct-job JSON valid.
        env["LINKEDIN_DIRECT_JOB_JSON"]=json.dumps(job, default=str)
        # Explicit title/company for hybrid form fill / logging (avoid Target Company placeholders).
        if job.get("title"):
            env["LINKEDIN_JOB_TITLE"] = str(job.get("title") or "")
        if job.get("company"):
            env["LINKEDIN_JOB_COMPANY"] = str(job.get("company") or "")
        env.setdefault("LINKEDIN_USE_EXTENSION","1")
        env.setdefault("LINKEDIN_EXTENSION_SKIP_BACKEND","1")
        # Queue profile (it|general) must win over bot default so IT Easy Apply
        # uses IT answers/resume while office/CS uses general — same NST session.
        q_profile = (profile or job.get("profile") or "it").strip().lower()
        if q_profile not in {"it", "general"}:
            q_profile = "it"
        env["LINKEDIN_JOB_PROFILE"] = q_profile
        env["JOB_QUEUE_PROFILE"] = q_profile
        env["JOB_PROFILE"] = "IT" if q_profile == "it" else "General"
        return subprocess.run([sys.executable,"-u",str(script)],cwd=ROOT,env=env).returncode,""
    # Google CDP / ATS discovery leads, or any queued job whose URL is already ATS.
    if portal in {"google", "greenhouse", "lever", "ashby", "bamboohr"} or _is_greenhouse_or_lever_url(job.get("url") or ""):
        script = ROOT / "bots" / f"google_{profile}.py"
        if not script.is_file():
            script = ROOT / "bots" / "google_it.py"
        if not script.is_file():
            return 2, f"missing bot entry point {script}"
        # Prefer destination_url when present (Google discovery stores ATS URL there).
        meta = job.get("metadata") or {}
        dest = (meta.get("destination_url") or job.get("destination_url") or "").strip()
        if dest and _is_greenhouse_or_lever_url(dest):
            job_serializable = dict(job_serializable)
            job_serializable["url"] = dest
            env["JOB_QUEUE_DIRECT_JOB"] = json.dumps(job_serializable)
        return subprocess.run([sys.executable, "-u", str(script)], cwd=ROOT, env=env).returncode, ""
    # Job Bank Direct Apply is browser-only. Legacy email rows are rejected.
    if direct_jobbank:
        script = ROOT / "bots" / "jobbank_it.py"
        if not script.is_file():
            return 2, f"missing bot entry point {script}"
        env["JOB_QUEUE_DIRECT_JOB"] = json.dumps(job_serializable, default=str)
        env["JOB_QUEUE_RESULT_PATH"] = str(result_path)
        return subprocess.run([sys.executable, "-u", str(script)], cwd=ROOT, env=env).returncode, ""
    return 2,f"unsupported portal {portal}"

def _lease_specific_job(q, worker, job_id):
    import uuid
    from datetime import datetime, timezone
    token = f"{worker}:{uuid.uuid4().hex[:10]}"
    res = q.jobs.update_one(
        {"_id": job_id, "status": {"$in": ["queued", "retry"]}},
        {"$set": {
            "status": "leased",
            "lease_owner": token,
            "lease_expires_at": time.time() + 900,
            "updated_at": datetime.now(timezone.utc)
        }, "$inc": {"attempts": 1}}
    )
    if not res.modified_count:
        return None
    job = q.jobs.find_one({"_id": job_id})
    if not job:
        return None
    job["id"] = job["_id"]
    return job

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--portal",action="append"); ap.add_argument("--profile")
    ap.add_argument("--once",action="store_true"); ap.add_argument("--poll-seconds",type=int,default=15)
    ap.add_argument("--job-id")
    ap.add_argument("--job-ids",help="Comma-separated job IDs processed sequentially in one worker "
                     "(KEEP_BROWSER between jobs so NST stays open until the last).")
    ap.add_argument("--keep-browser",action="store_true",
                    help="Leave NST/browser open after each bot exit (also set via KEEP_BROWSER=1).")
    args=ap.parse_args(); q=JobQueue(); worker=runtime_worker_name("application")
    pending_ids=[]
    if args.job_ids:
        pending_ids=[x.strip() for x in args.job_ids.split(",") if x.strip()]
    elif args.job_id:
        pending_ids=[args.job_id]
    explicit_ids=bool(pending_ids)
    user_keep=args.keep_browser or str(os.environ.get("KEEP_BROWSER") or os.environ.get("NSTBROWSER_KEEP_ALIVE") or "").strip().lower() in {"1","true","yes","on"}
    # LinkedIn production is ONE NST session for IT + office/CS (one account).
    # Claim order: drain profile=it first, then profile=general (one-by-one).
    portals_norm = [str(p).strip().lower() for p in (args.portal or []) if str(p).strip()]
    linkedin_sole_worker = bool(portals_norm) and all(p == "linkedin" for p in portals_norm)
    claim_profile = None if linkedin_sole_worker else args.profile
    if linkedin_sole_worker:
        print(
            f"[Worker] LinkedIn sole NST: claim IT first, then general "
            f"(same account; --profile {args.profile!r} ignored for order)"
        )
    # Secondary NST portals wait until Indeed + LinkedIn active work drains so
    # NST slots aren't burned while primary queues still have jobs.
    _secondary_browser = frozenset({"glassdoor", "workopolis"})
    _primary_browser = frozenset({"indeed", "linkedin"})
    _defer_secondary = str(
        os.environ.get("JOBBOTS_DEFER_GLASSDOOR_WORKOPOLIS")
        or os.environ.get("JOBBOTS_DEFER_SECONDARY_BROWSER")
        or "1"
    ).strip().lower() in {"1", "true", "yes", "on"}
    _is_secondary_only = bool(portals_norm) and all(p in _secondary_browser for p in portals_norm)
    _deferred_logged = False

    def _primary_browser_active_count() -> int:
        try:
            return int(
                q.jobs.count_documents(
                    {
                        "portal": {"$in": list(_primary_browser)},
                        "status": {"$in": ["queued", "leased", "retry"]},
                    }
                )
            )
        except Exception:
            return 0

    while True:
        q.release_expired(); q.heartbeat(
            worker, "application",
            portal=",".join(args.portal or []),
            profile=(claim_profile or args.profile or ""),
            status="idle",
        )
        if _defer_secondary and _is_secondary_only and not pending_ids:
            primary_n = _primary_browser_active_count()
            if primary_n > 0:
                if not _deferred_logged:
                    print(
                        f"[Worker] Deferring glassdoor/workopolis until Indeed+LinkedIn "
                        f"queues drain (primary active={primary_n})"
                    )
                    _deferred_logged = True
                if args.once:
                    return
                time.sleep(max(15, int(args.poll_seconds or 10)))
                continue
            if _deferred_logged:
                print("[Worker] Primary Indeed/LinkedIn drained — starting glassdoor/workopolis")
                _deferred_logged = False
        if pending_ids:
            jid=pending_ids.pop(0)
            job=_lease_specific_job(q,worker,jid)
            if not job:
                print(f"Job {jid} not found in queued/retry status.")
                if pending_ids:
                    continue
                if explicit_ids:
                    return
                if args.once: return
                time.sleep(args.poll_seconds); continue
        else:
            if linkedin_sole_worker:
                # Same NST account: finish IT queue before office/CS (general).
                job = q.claim(worker=worker, portals=args.portal, profile="it")
                if not job:
                    job = q.claim(worker=worker, portals=args.portal, profile="general")
            else:
                job = q.claim(worker=worker, portals=args.portal, profile=claim_profile)
        if not job:
            if args.once or explicit_ids: return
            time.sleep(args.poll_seconds); continue
        q.heartbeat(worker,"application",portal=job["portal"],profile=job["profile"],status="working",current_job_id=job["id"])
        def training_event(event, **details):
            try:
                from core.training_capture import record_training_event
                record_training_event(
                    event, portal=job.get("portal", ""), profile=job.get("profile", ""),
                    job_id=job.get("id", ""), source_job_id=job.get("source_job_id", ""),
                    job_url=job.get("url", ""), title=job.get("title", ""),
                    company=job.get("company", ""), location=job.get("location", ""),
                    worker=worker, attempts=job.get("attempts", 0), **details,
                )
            except Exception:
                pass
        if not _is_metro_vancouver_queue_job(job):
            reason = "outside_metro_vancouver_only: terminal queue safety guard"
            print(f"[Worker] GEO BLOCK: {job.get('title')} @ {job.get('company')} -> {reason}")
            q.skipped(job["id"], job["lease_owner"], job.get("url", ""), reason=reason)
            training_event("application_outcome", outcome="skipped", raw_status="geo_block", reason=reason)
            if args.once or explicit_ids:
                return
            continue
        training_event("application_started", dispatched_method=_dispatch_method(job))
        result_path=Path(tempfile.gettempdir())/f"jobbots-result-{job['id']}-{os.getpid()}.json"; result_path.unlink(missing_ok=True)
        # Keep NST open across sequential allowlist/job-id dispatches or queue claims
        # (unless the user forced keep-browser for the whole run).
        is_nst = (os.environ.get("BROWSER_VENDOR") or "nstbrowser").strip().lower() in {"nstbrowser", "nst"}
        keep_browser=user_keep or is_nst or (explicit_ids and bool(pending_ids))
        try:
            stop_renewal=threading.Event()
            def renew_lease():
                while not stop_renewal.wait(60):
                    if not q.renew(job["id"],job["lease_owner"],900):
                        break
                    q.heartbeat(worker,"application",portal=job["portal"],profile=job["profile"],status="working",current_job_id=job["id"])
            renewal=threading.Thread(target=renew_lease,daemon=True); renewal.start()
            try:
                from core.shared_modules.company_throttle import check_company_throttle_and_dedupe
                block_action, block_reason = check_company_throttle_and_dedupe(q, job)
                if block_action:
                    print(f"[Worker] PRE-DISPATCH BLOCK ({block_action}): {job.get('title')} @ {job.get('company')} -> {block_reason}")
                    if block_action == "already_applied":
                        q.already_applied(job["id"], job["lease_owner"], job.get("url", ""), reason=block_reason)
                    else:
                        q.skipped(job["id"], job["lease_owner"], job.get("url", ""), reason=block_reason)
                    code = 0
                    dispatch_error = block_reason
                    result = {"status": block_action, "reason": block_reason, "result_url": job.get("url", "")}
                else:
                    code,dispatch_error=dispatch(job,result_path,keep_browser=keep_browser)
            finally:
                stop_renewal.set(); renewal.join(timeout=2)
            if result_path.is_file(): result=json.loads(result_path.read_text())
            else: result={"status":"failed","reason":dispatch_error or f"bot exited {code} without result"}
            reason=result.get("reason") or f"exit {code}"
            result_url=result.get("result_url") or job["url"]
            from datetime import datetime, timezone
            dispatched_method=(job.get("metadata") or {}).get("application_method","easy_apply")
            action,resolved_method=classify_outcome(result,dispatched_method,job.get("attempts",0),job.get("max_attempts",3))
            # Safeguard #5: persist the resolved apply method for lease-and-verify
            # jobs (unverified → easy_apply / company_site) BEFORE finishing the lease.
            if resolved_method and resolved_method!=dispatched_method:
                q.set_application_method(job["id"],resolved_method,lease_owner=job["lease_owner"])
            # Safeguard #6: clear terminal outcomes; unverified jobs never retry forever.
            if action=="applied":
                q.complete(job["id"],job["lease_owner"],result_url,reason=reason)
            elif action=="already_applied":
                q.already_applied(job["id"],job["lease_owner"],result_url,reason=reason)
            elif action=="skipped":
                q.skipped(job["id"],job["lease_owner"],result_url,reason=reason)
            elif action=="bookmarked":
                q.bookmarked(job["id"],job["lease_owner"],result_url,reason=reason)
            elif action=="manual_review":
                q.manual_review(job["id"],job["lease_owner"],result_url,reason=reason)
            elif action=="captcha_cf_requeue":
                # Same portal+profile queue; priority bumped so claim order puts this last.
                q.requeue_captcha_cf(job["id"],job["lease_owner"],reason)
            elif action=="retry":
                q.fail(job["id"],job["lease_owner"],reason,retryable=True)
            else:
                q.fail(job["id"],job["lease_owner"],reason,retryable=False)
            # Persist outcome labels for training / dashboards
            q.jobs.update_one(
                {"_id": job["id"]},
                {"$set": {
                    "worker_run_id": worker,
                    "updated_at": datetime.now(timezone.utc),
                    "metadata.last_outcome": action,
                    "metadata.last_outcome_reason": (reason or "")[:500],
                    "metadata.application_method": resolved_method or dispatched_method or "",
                }},
            )
            training_event(
                "application_outcome", outcome=action, raw_status=result.get("status", ""),
                reason=reason, result_url=result_url, resolved_method=resolved_method or dispatched_method,
                exit_code=code, retryable=(action in {"retry", "captcha_cf_requeue"}),
            )
        except BaseException as exc:
            q.fail(job["id"],job["lease_owner"],f"worker crash: {type(exc).__name__}: {exc}",retryable=True)
            training_event("application_worker_error", error=f"{type(exc).__name__}: {exc}")
            if isinstance(exc,(KeyboardInterrupt,SystemExit)): raise
        finally:
            result_path.unlink(missing_ok=True)
            result_path.with_suffix(".job.json").unlink(missing_ok=True)
        if explicit_ids:
            if not pending_ids:
                return
            continue
        if args.once: return
if __name__=="__main__": main()

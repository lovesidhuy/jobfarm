from __future__ import annotations

from ._bootstrap import *  # noqa: F403
from jobbots.core.shared_modules.indeed.search import Indeed404Error
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT

import asyncio

import json
from datetime import datetime

from crawlee.storages import RequestQueue
from crawlee import Request

class CrawleeQueueHelper:
    def __init__(self, queue_name="indeed-jobs-queue"):
        self.queue_name = queue_name
        self.rq = None
        
        # Configure storage path dynamically under repo root
        storage_path = resolve_project_path("data/crawlee_storage")
        os.environ["CRAWLEE_STORAGE_DIR"] = storage_path
        
    def _run_async(self, coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coro)
        else:
            import threading

            result = {}

            def _runner():
                try:
                    result["value"] = asyncio.run(coro)
                except Exception as exc:
                    result["error"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join()
            if "error" in result:
                raise result["error"]
            return result.get("value")
            
    def open(self):
        async def _open():
            self.rq = await RequestQueue.open(name=self.queue_name)
        self._run_async(_open())
        
    def add_job(self, jid, url, payload):
        async def _add():
            req = Request(
                url=url,
                unique_key=jid,
                user_data=payload
            )
            return await self.rq.add_request(req)
        return self._run_async(_add())
        
    def fetch_next(self):
        async def _fetch():
            return await self.rq.fetch_next_request()
        return self._run_async(_fetch())
        
    def mark_handled(self, request):
        async def _mark():
            await self.rq.mark_request_as_handled(request)
        self._run_async(_mark())
        
    def reclaim(self, request):
        async def _reclaim():
            await self.rq.reclaim_request(request)
        self._run_async(_reclaim())

    def is_empty(self) -> bool:
        async def _empty():
            return await self.rq.is_empty()
        return self._run_async(_empty())

    def has_job(self, jid) -> bool:
        if not self.rq:
            return False
        async def _has():
            req = await self.rq.get_request(unique_key=jid)
            return req is not None
        return self._run_async(_has())

def _env_int(name: str, default: int = 0) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        print_lg(f"[Indeed] Ignoring invalid integer for {name}: {raw!r}")
        return default


def _env_csv(name: str) -> list[str]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _process_queue(
    crawlee_queue,
    page,
    sb,
    applied_ids,
    skipped_ids,
    session_seen_ids,
    test_terms,
    original_terms,
    stats_dict,
    run_in_background,
    click_gap,
    max_application_outcomes,
    switch_number=999,
    term_applied=0,
    current_search_term=None,
    easy_apply_only=False,
    skip_easy_apply=False,
) -> dict:
    def buffer(speed: int = 0) -> None:
        """Wait a short random interval based on speed level."""
        import random
        if speed <= 0:
            return
        elif speed < 2:
            time.sleep(random.randint(6, 10) * 0.1)
        elif speed < 3:
            time.sleep(random.uniform(1.0, 1.8))
        else:
            time.sleep(random.uniform(1.8, max(speed, 1.8)))
    global _use_new_resume, _randomly_answered_questions, _current_job_context, _current_job_meta
    
    while True:
        req = crawlee_queue.fetch_next()
        if not req:
            break
            
        jid = req.unique_key
        job = req.user_data
        
        title = job["title"]
        company = job["company"]
        location = job["location"]
        has_easy_apply = job["has_easy_apply"]
        job_href = job["job_href"]
        job_link = job["job_link"]
        card_text = job["card_text"]
        job_search_term = job["search_term"]
        search_url = job["search_url"]
        
        if test_terms and job_search_term not in original_terms:
            print_lg(
                f"  [Indeed] Skipping queued job from non-test term "
                f"{job_search_term!r}: {title}"
            )
            crawlee_queue.mark_handled(req)
            continue
            
        print_lg("")
        pane_url = _search_pane_job_url(search_url, jid)
        _current_job_meta = {
            "job_id": jid,
            "title": title,
            "company": company,
            "location": location,
            "job_link": job_link,
            "job_href": job_href,
            "search_term": job_search_term,
            "search_url": search_url,
            "has_easy_apply": has_easy_apply,
        }
        
        try:
            from modules.heartbeat import send_heartbeat
            bot_name = os.getenv("BOT_NAME") or _bot_name
            send_heartbeat(
                bot_name=bot_name,
                status="running",
                last_activity=f"Processing job {jid} ({title} at {company})"
            )
        except Exception:
            pass
        log_training_event("job_seen", job=_current_job_meta,
                           card_text=card_text,
                           page=page_dom_snapshot(page, limit=20))
        
        reclaimed = False
        try:
            if jid == 'Unknown':
                print_lg(f"  ✗ Skipping '{title}' — no job ID")
                log_training_event("job_skipped", job=_current_job_meta, reason="missing_job_id")
                log_job_status_event("screened_skip", jid, title, company, job_link, reason="missing_job_id")
                stats_dict["skipped_count"] += 1
                continue

            if jid in applied_ids:
                print_lg(f"  ✓ Already applied (ID: {jid})  {title}")
                log_training_event("job_skipped", job=_current_job_meta, reason="already_applied")
                log_job_status_event("already_applied", jid, title, company, job_link, reason="already_applied_ids_check")
                # Phase-II direct queue: this is a terminal applied success, not a skip/no-op.
                stats_dict["applied_count"] = int(stats_dict.get("applied_count", 0) or 0) + 1
                stats_dict["last_reason"] = "Already applied to this job"
                stats_dict["last_result_url"] = job_link
                stats_dict["application_outcomes"] = int(stats_dict.get("application_outcomes", 0) or 0) + 1
                stats_dict["skipped_count"] = int(stats_dict.get("skipped_count", 0) or 0) + 1
                if max_application_outcomes > 0 and stats_dict["application_outcomes"] >= max_application_outcomes:
                    print_lg("  [Indeed] Reached INDEED_MAX_APPLICATION_OUTCOMES — stopping.")
                    return {"stop": True, "term_applied": term_applied}
                continue

            if jid in skipped_ids:
                print_lg(f"  ✓ Already skipped/screened (ID: {jid})  {title}")
                log_training_event("job_skipped", job=_current_job_meta, reason="already_skipped")
                log_job_status_event("screened_skip", jid, title, company, job_link, reason="already_skipped_ids_check")
                stats_dict["skipped_count"] += 1
                continue

            if jid in session_seen_ids:
                print_lg(f"  ↻ Already seen this session (ID: {jid})  {title}")
                log_training_event("job_skipped", job=_current_job_meta,
                                   reason="already_seen_this_session")
                stats_dict["skipped_count"] += 1
                continue
            session_seen_ids.add(jid)

            card_state = _indeed_gui_job_state(None, card_text)
            if card_state == "hidden":
                print_lg(f"  ✗ Skipping hidden/not-interested card: {title}")
                log_training_event("job_skipped", job=_current_job_meta, reason="hidden_card")
                log_job_status_event("screened_skip", jid, title, company, job_link, reason="hidden_card")
                stats_dict["skipped_count"] += 1
                continue
            if card_state:
                state_label = "Already applied" if card_state == "already_applied" else "Already saved"
                print_lg(f"  ✓ {state_label} on Indeed card: {title}")
                _save_applied(
                    jid, title, company, location,
                    f"{state_label} on Indeed card",
                    "Indeed GUI state",
                    state_label + " on Indeed",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    job_link,
                )
                applied_ids.add(jid)
                log_training_event("job_skipped", job=_current_job_meta,
                                   reason=f"{card_state}_card")
                log_job_status_event("already_applied" if card_state == "already_applied" else "screened_skip", jid, title, company, job_link, reason=f"{card_state}_card")
                # Phase II: surface as already_applied terminal via applied_count + reason
                if card_state == "already_applied":
                    stats_dict["applied_count"] = int(stats_dict.get("applied_count", 0) or 0) + 1
                    stats_dict["last_reason"] = "Already applied to this job"
                    stats_dict["last_result_url"] = job_link
                    stats_dict["application_outcomes"] = int(stats_dict.get("application_outcomes", 0) or 0) + 1
                stats_dict["skipped_count"] += 1
                if (
                    max_application_outcomes > 0
                    and int(stats_dict.get("application_outcomes", 0) or 0) >= max_application_outcomes
                ):
                    print_lg("  [Indeed] Reached INDEED_MAX_APPLICATION_OUTCOMES — stopping.")
                    return {"stop": True, "term_applied": term_applied}
                continue

            print_lg(f"  Job: '{title}'  |  '{company}'  |  {location}"
                     + ("  [Easily apply]" if has_easy_apply else ""))

            # Choose correct pre-load reject gate based on profile (skip in direct links mode)
            if current_search_term == "direct_links":
                _pre_reject, _pre_reason = False, ""
            else:
                profile_type = os.environ.get("JOB_PROFILE", "IT").upper()
                if profile_type == "GENERAL":
                    _pre_reject, _pre_reason = _general_local_gate_reject(
                        title, company, location, card_text
                    )
                else:
                    _pre_reject, _pre_reason = _obvious_non_it_reject(
                        title, company, location, card_text, ""
                    )
            if _pre_reject:
                print_lg(f"  ✗ Title hard-reject (pre-load) — {_pre_reason}")
                log_training_event("job_skipped", job=_current_job_meta,
                                   reason=f"title_hard_reject_pre_load: {_pre_reason}")
                _save_skipped(jid, title, company, location, f"title_hard_reject_pre_load: {_pre_reason}", job_link=job_link)
                log_job_status_event("screened_skip", jid, title, company, job_link, reason=f"title_hard_reject_pre_load: {_pre_reason}")
                skipped_ids.add(jid)
                stats_dict["skipped_count"] += 1
                continue

            if skip_easy_apply and has_easy_apply:
                print_lg("  ✗ Skipping Easy Apply job because skip_easy_apply=True.")
                log_training_event("job_skipped", job=_current_job_meta, reason="skip_easy_apply")
                log_job_status_event("screened_skip", jid, title, company, job_link, reason="skip_easy_apply")
                stats_dict["skipped_count"] += 1
                continue

            if easy_apply_only and not has_easy_apply:
                print_lg("  ✗ Skipping non-Easy Apply job because easy_apply_only=True.")
                log_training_event("job_skipped", job=_current_job_meta, reason="easy_apply_only")
                log_job_status_event("screened_skip", jid, title, company, job_link, reason="easy_apply_only")
                stats_dict["skipped_count"] += 1
                continue

            # Fetch description + company info
            desc_nav_error = None
            desc_candidates = [job_link]
            if pane_url and pane_url not in desc_candidates:
                desc_candidates.append(pane_url)
            if job_href and job_href not in desc_candidates:
                desc_candidates.append(job_href)
            for nav_for_desc in desc_candidates:
                print_lg(f"  Opening job detail: {nav_for_desc}")
                try:
                    log_job_status_event("opened", jid, title, company, job_link)
                    _goto_page(page, nav_for_desc, timeout=15000)
                    desc_nav_error = None
                    break
                except Indeed404Error as e404:
                    # Job listing expired / removed — skip cleanly, not a bot failure.
                    print_lg(f"  [404] Job page not found, skipping: {e404}")
                    log_job_status_event("screened_skip", jid, title, company, job_link,
                                        reason="indeed_404_listing_gone")
                    _save_skipped(jid, title, company, location,
                                  "indeed_404_listing_gone", job_link=job_link)
                    skipped_ids.add(jid)
                    stats_dict["skipped_count"] += 1
                    try:
                        _goto_page(page, search_url, timeout=15000)
                    except Exception:
                        pass
                    desc_nav_error = None  # prevent double-handling below
                    break
                except Exception as e:
                    desc_nav_error = e
            if desc_nav_error:
                print_lg(f"  ✗ Could not open job: {desc_nav_error}")
                _save_failed(jid, title, company, job_link, str(desc_nav_error))
                stats_dict["failed_count"] += 1
                try:
                    _goto_page(page, search_url, timeout=15000)
                except Exception:
                    pass
                continue

            # Guard: job was skipped (e.g. 404) — don't fall into apply logic
            if jid in skipped_ids:
                continue

            # Now on job detail page
            check_and_handle_captcha(page, sb, f"job {jid} detail page load",
                                     run_in_background=run_in_background)
            page = try_recover_page(page)

            # Get description
            description = _get_job_description(page)
            if not description:
                print_lg("  ✗ Failed to get job description.")
                _save_failed(jid, title, company, job_link, "Failed to extract job description")
                log_job_status_event("failed", jid, title, company, job_link, reason="failed_description_extraction")
                stats_dict["failed_count"] += 1
                try:
                    _goto_page(page, search_url, timeout=15000)
                except Exception:
                    pass
                continue

            # Extracted structured job data
            try:
                from jobbots.core.shared_modules.indeed.gates import _extract_structured_job_data
                _extract_structured_job_data(jid, description, title, company, location)
                log_job_status_event("extracted", jid, title, company, job_link)
            except Exception as ex_ext:
                print_lg(f"  [Extraction Warning] Failed extracting structured JSON: {ex_ext}")

            # AI screening (bypassed in direct links mode)
            if current_search_term == "direct_links":
                print_lg("  [Indeed] Direct links mode: bypassing AI screening gate.")
                pass_gate, fit_score, reason = True, 10, "Direct links bypass"
            else:
                print_lg("  Screening job with AI gate…")
                pass_gate, fit_score, reason = screen_job_with_ai(
                    title, company, description,
                    location=location,
                    easy_apply=has_easy_apply,
                )
                print_lg(f"    -> Score: {fit_score} | Pass: {pass_gate}")
                print_lg(f"    -> Reason: {reason}")

            if not pass_gate:
                _save_skipped(jid, title, company, location,
                              f"AI Screened (Score: {fit_score}): {reason}", job_link=job_link)
                log_training_event("job_skipped", job=_current_job_meta,
                                   reason=f"ai_gate_reject (Score: {fit_score}): {reason}")
                log_job_status_event("screened_skip", jid, title, company, job_link, reason=f"score_{fit_score}_{reason[:60]}")
                skipped_ids.add(jid)
                stats_dict["skipped_count"] += 1
                try:
                    _goto_page(page, search_url, timeout=15000)
                    buffer(click_gap)
                except Exception:
                    pass
                continue

            from modules.job_queue_bridge import discovery_mode, enqueue_approved_job
            if discovery_mode():
                company_site = not has_easy_apply
                if company_site and not save_company_site_jobs:
                    print_lg("  Company-site job passed screening but saving is disabled.")
                    continue
                queue_id, created = enqueue_approved_job(
                    portal="indeed", profile=(os.getenv("JOB_PROFILE") or "it").lower(),
                    job_id=jid, title=title, company=company, location=location,
                    url=job_link or job_href, description=description,
                    gate_score=fit_score, gate_reason=reason,
                    resume_policy="tailored" if (os.getenv("JOB_PROFILE") or "IT").lower()=="it" else "default",
                    initial_status="queued",
                    application_method="company_site" if company_site else "easy_apply",
                )
                action="Saved company-site lead" if company_site else "Queued approved job"
                print_lg(f"  {action if created else 'Already saved'} #{queue_id}; discovery continues.")
                log_job_status_event("queued", jid, title, company, job_link, reason=f"queue_{queue_id}")
                continue

            # Process job application
            print_lg("  Job passed screening. Starting application flow…")
            log_job_status_event("screened_apply", jid, title, company, job_link)

            experience = _extract_experience(description)
            skills = _extract_skills_ai(description)
            log_job_status_event("apply_started", jid, title, company, job_link)

            # Do NOT tailor resume before bookmark / verify / "worth save" checks.
            # Bookmark-only and verify-external paths must never burn resume generation.
            # Easy Apply submit path tailors inside _apply_to_single_job once it knows
            # it will actually submit through SmartApply.
            os.environ.pop("INDEED_TAILORED_RESUME_PATH", None)
            os.environ["INDEED_JOB_DESCRIPTION_FOR_TAILOR"] = description or ""
            try:
                applied, application_link, reason = _apply_to_single_job(
                    page, sb, jid, title, company, location, job_href, search_url
                )
            finally:
                os.environ.pop("INDEED_TAILORED_RESUME_PATH", None)
                os.environ.pop("INDEED_JOB_DESCRIPTION_FOR_TAILOR", None)

            # Remember the last job's outcome for the direct-links queue result
            # writer (Phase-II terminal-state resolution).
            stats_dict["last_reason"] = reason
            stats_dict["last_result_url"] = application_link or job_link

            if applied:
                date_applied = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                _save_applied(jid, title, company, location,
                              description[:500], experience, skills,
                              date_applied, application_link)
                applied_ids.add(jid)
                # Already-applied / on-Indeed / Greenhouse-Lever ATS success is a
                # real application. Only non-ATS external opens stay "external".
                reason_l = (reason or "").lower()
                link_l = (application_link or "").lower()
                ats_submitted = (
                    "greenhouse" in reason_l
                    or "lever" in reason_l
                    or "greenhouse" in link_l
                    or "lever.co" in link_l
                )
                if (
                    "already applied" in reason_l
                    or SMARTAPPLY_DOMAIN in link_l
                    or "indeed.com" in link_l
                    or ats_submitted
                ):
                    stats_dict["applied_count"] += 1
                else:
                    stats_dict["external_count"] += 1
                    stats_dict["applied_count"] += 1
                term_applied += 1
                print_lg(f"  ✓ Applied: {title}  → {application_link}")
                log_training_event("job_applied", job=_current_job_meta,
                                   application_link=application_link)
                log_job_status_event("submitted", jid, title, company, job_link, reason=application_link)
                stats_dict["application_outcomes"] += 1
                if max_application_outcomes > 0 and stats_dict["application_outcomes"] >= max_application_outcomes:
                    print_lg("  [Indeed] Reached INDEED_MAX_APPLICATION_OUTCOMES — stopping.")
                    return {"stop": True, "term_applied": term_applied}
                if term_applied >= switch_number:
                    print_lg(f"  Reached switch_number ({switch_number}) — moving on.")
                    return {"break_search": True, "term_applied": term_applied}
            else:
                if "external company-site apply" in (reason or "").lower():
                    stats_dict["skipped_count"] += 1
                    print_lg(f"  ✗ Skipped: {title}  — {reason}")
                    log_training_event("job_skipped", job=_current_job_meta,
                                       reason=reason,
                                       application_link=application_link)
                    log_job_status_event("screened_skip", jid, title, company, job_link, reason=reason)
                    stats_dict["application_outcomes"] += 1
                    if max_application_outcomes > 0 and stats_dict["application_outcomes"] >= max_application_outcomes:
                        print_lg("  [Indeed] Reached INDEED_MAX_APPLICATION_OUTCOMES — stopping.")
                        return {"stop": True, "term_applied": term_applied}
                    try:
                        _goto_page(page, search_url, timeout=15000)
                        buffer(click_gap)
                    except Exception:
                        return {"stop": True, "term_applied": term_applied}
                    continue
                if "bookmark" in (reason or "").lower():
                    # Company-site / verify-external lead: saved on Indeed, never
                    # submitted. Terminal "bookmarked" outcome — do NOT count as a
                    # failure (which would make the worker retry it).
                    stats_dict["bookmarked_count"] += 1
                    print_lg(f"  🔖 Bookmarked (saved lead): {title}  — {reason}")
                    log_job_status_event("bookmarked", jid, title, company, job_link, reason=reason)
                    stats_dict["application_outcomes"] += 1
                    if max_application_outcomes > 0 and stats_dict["application_outcomes"] >= max_application_outcomes:
                        print_lg("  [Indeed] Reached INDEED_MAX_APPLICATION_OUTCOMES — stopping.")
                        return {"stop": True, "term_applied": term_applied}
                    try:
                        _goto_page(page, search_url, timeout=15000)
                        buffer(click_gap)
                    except Exception:
                        return {"stop": True, "term_applied": term_applied}
                    continue
                _save_failed(jid, title, company, job_link, reason)
                stats_dict["failed_count"] += 1
                print_lg(f"  ✗ Failed: {title}  — {reason}")
                log_training_event("job_apply_failed", job=_current_job_meta,
                                   reason=reason,
                                   page=page_dom_snapshot(page, limit=50))
                log_job_status_event("failed", jid, title, company, job_link, reason=reason)
                stats_dict["application_outcomes"] += 1
                if max_application_outcomes > 0 and stats_dict["application_outcomes"] >= max_application_outcomes:
                    print_lg("  [Indeed] Reached INDEED_MAX_APPLICATION_OUTCOMES — stopping.")
                    return {"stop": True, "term_applied": term_applied}
                try:
                    _goto_page(page, search_url, timeout=15000)
                    time.sleep(max(0.2, min(float(click_gap or 1), 3.0)))
                except Exception:
                    return {"stop": True, "term_applied": term_applied}

        except Exception as e:
            def _is_recoverable_exception(ex) -> bool:
                err_str = str(ex).lower()
                from playwright.sync_api import Error as PlaywrightError
                if isinstance(ex, (PlaywrightError, TimeoutError, ConnectionError)):
                    return True
                if any(k in err_str for k in ("timeout", "cloudflare", "captcha", "disconnected", "network", "stale", "navigation", "rate limit")):
                    return True
                return False
                
            err_msg = str(e)
            if "closed" in err_msg.lower() or "disconnected" in err_msg.lower() or "target" in err_msg.lower():
                print_lg(f"  [Queue Error] Browser connection closed or disconnected. Reclaiming job {jid} for retry and stopping queue processing.")
                crawlee_queue.reclaim(req)
                stats_dict["last_reason"] = f"Browser connection closed: {e}"
                stats_dict["failed_count"] += 1
                stats_dict["application_outcomes"] += 1
                return {"stop": True, "term_applied": term_applied}
                
            if _is_recoverable_exception(e):
                print_lg(f"  [Queue Error] Recoverable error, reclaiming job {jid} for retry: {e}")
                crawlee_queue.reclaim(req)
                reclaimed = True
                log_job_status_event("blocked" if "captcha" in str(e).lower() else "failed", 
                                     jid, title, company, job_link, reason=str(e))
                stats_dict["last_reason"] = str(e)
                stats_dict["failed_count"] += 1
                stats_dict["application_outcomes"] += 1
            else:
                print_lg(f"  [Queue Error] Non-recoverable error, marking job {jid} as handled: {e}")
                crawlee_queue.mark_handled(req)
                reclaimed = False
                log_job_status_event("failed", jid, title, company, job_link, reason=str(e))
                stats_dict["last_reason"] = str(e)
                stats_dict["failed_count"] += 1
                stats_dict["application_outcomes"] += 1
                if max_application_outcomes > 0 and stats_dict["application_outcomes"] >= max_application_outcomes:
                    print_lg("  [Indeed] Reached INDEED_MAX_APPLICATION_OUTCOMES — stopping.")
                    return {"stop": True, "term_applied": term_applied}
            
            try:
                _goto_page(page, search_url, timeout=15000)
                buffer(click_gap)
            except Exception:
                pass
        finally:
            if not reclaimed:
                crawlee_queue.mark_handled(req)
                
    return {"term_applied": term_applied}


def _extract_job_key_local(url: str) -> str:
    import re
    # Match jk= followed by 16-hex character or letters/numbers
    m = re.search(r'[?&]jk=([a-zA-Z0-9]+)', url)
    if m:
        return m.group(1)
    m2 = re.search(r'/rc/clk\?jk=([a-zA-Z0-9]+)', url)
    if m2:
        return m2.group(1)
    # If the URL is just a hex string of length 16, return it
    val = url.strip()
    if len(val) == 16 and all(c in "0123456789abcdefABCDEF" for c in val):
        return val
    return ""


def _load_direct_links_from_json(json_path: str) -> list[dict]:
    import os
    import json
    from pathlib import Path
    jobs = []
    try:
        resolved_path = Path(json_path)
        if not resolved_path.is_absolute():
            try:
                resolved_path = Path(resolve_project_path(json_path))
            except Exception:
                resolved_path = Path(os.getcwd()) / json_path

        print_lg(f"[Indeed] Reading direct jobs list from {resolved_path}")
        with open(resolved_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    jid = item.get("job_key") or item.get("job_id") or item.get("jk")
                    url = item.get("job_url") or item.get("job_link") or item.get("url") or item.get("link")
                    title = item.get("title") or "Direct Job"
                    company = item.get("company") or "Direct Company"
                    location = item.get("location") or "Direct Location"
                    
                    if not jid and url:
                        jid = _extract_job_key_local(url)
                    
                    if jid:
                        job_link = f"https://ca.indeed.com/viewjob?jk={jid}"
                        jobs.append({
                            "jid": jid,
                            "title": title,
                            "company": company,
                            "location": location,
                            "has_easy_apply": True,
                            "job_href": url or job_link,
                            "job_link": job_link,
                            "card_text": "",
                            "search_term": "direct_links",
                            "search_url": "direct_links",
                        })
                elif isinstance(item, str):
                    val = item.strip()
                    jid = _extract_job_key_local(val)
                    if jid:
                        job_link = f"https://ca.indeed.com/viewjob?jk={jid}"
                        jobs.append({
                            "jid": jid,
                            "title": f"Direct Job {jid}",
                            "company": "Direct Company",
                            "location": "Direct Location",
                            "has_easy_apply": True,
                            "job_href": val if val.startswith("http") else job_link,
                            "job_link": job_link,
                            "card_text": "",
                            "search_term": "direct_links",
                            "search_url": "direct_links",
                        })
        print_lg(f"[Indeed] Successfully loaded {len(jobs)} jobs from {resolved_path}")
    except Exception as e:
        print_lg(f"[Direct Links Error] Failed to load JSON from {json_path}: {e}")
    return jobs


def _tailor_resume_and_set_path(job_title: str, company_name: str, job_description: str, jid: str, page) -> None:
    import requests
    import os
    import time
    from pathlib import Path
    
    os.environ.pop("INDEED_TAILORED_RESUME_PATH", None)
    
    resume_server_base = os.getenv("RESUME_WORKFLOW_URL", "http://127.0.0.1:3001").rstrip("/")

    # The local server can be alive but slow to accept the first request after
    # launch. Give it a real chance so we do not fall back to an old Indeed resume.
    server_ready = False
    last_error = ""
    for attempt in range(1, 11):
        try:
            health_r = requests.get(resume_server_base, timeout=5)
            if health_r.status_code < 500:
                server_ready = True
                break
            last_error = f"HTTP {health_r.status_code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(1)

    if not server_ready:
        print_lg(
            f"  [Resume Tailor] Server on {resume_server_base} is not reachable after retries. "
            f"Skipping resume tailoring. Last error: {last_error}"
        )
        return
        
    print_lg(f"  [Resume Tailor] Requesting tailored resume for '{job_title}' at '{company_name}'...")
    
    payload = {
        "jobTitle": job_title,
        "companyName": company_name,
        "jobDescription": job_description,
        "generateCoverLetter": False
    }
    
    url = f"{resume_server_base}/api/tailor"
    # Tailor can exceed 90s on a loaded 2-vCPU worker (NST + Playwright + apply
    # bot starve the node engine). Make the budget tunable; default raised so
    # one slow run does not silently ship an untailored resume.
    tailor_timeout = int(os.getenv("RESUME_TAILOR_TIMEOUT_SECONDS", "150"))
    try:
        r = requests.post(url, json=payload, timeout=tailor_timeout)
        if r.status_code != 200:
            print_lg(f"  [Resume Tailor Error] Failed to trigger tailoring: HTTP {r.status_code} - {r.text}")
            return
            
        execution_id = r.json().get("executionId")
        if not execution_id:
            print_lg("  [Resume Tailor Error] No executionId returned by backend.")
            return
            
        print_lg(f"  [Resume Tailor] Triggered execution {execution_id}. Polling status...")
        
        status_url = f"{resume_server_base}/api/status/{execution_id}"
        max_attempts = 90  # 3 minutes max
        pdf_ready = False
        
        for attempt in range(1, max_attempts + 1):
            time.sleep(2)
            try:
                status_r = requests.get(status_url, timeout=10)
                if status_r.status_code != 200:
                    continue
                
                status_data = status_r.json()
                status = status_data.get("status")
                
                if status == "success":
                    print_lg("  [Resume Tailor] Success! PDF is ready on backend.")
                    pdf_ready = True
                    break
                elif status == "failed":
                    print_lg(f"  [Resume Tailor Error] Tailoring failed on backend: {status_data.get('error')}")
                    return
            except Exception as e_poll:
                pass
                
        if not pdf_ready:
            print_lg("  [Resume Tailor Error] Timed out waiting for tailored resume.")
            return
            
        # Canonical anchor (Phase 2): resolves to automation_monorepo exactly as
        # the old parent-walk for core/supervised_bots.py did.
        monorepo_root = _MONOREPO_ROOT
        if monorepo_root:

            resumes_dir = monorepo_root / "all resumes"
        else:
            from modules.helpers import resolve_project_path
            resumes_dir = Path(resolve_project_path("all resumes"))
        resumes_dir.mkdir(parents=True, exist_ok=True)
        
        safe_title = "".join(c if c.isalnum() else "_" for c in job_title[:20]).strip("_")
        safe_company = "".join(c if c.isalnum() else "_" for c in company_name[:20]).strip("_")
        file_path = resumes_dir / f"tailored_{safe_company}_{safe_title}_{jid}.pdf"
        
        # Download the PDF from local server endpoint
        print_lg(f"  [Resume Tailor] Downloading tailored PDF from local server endpoint for execution {execution_id}...")
        download_url = f"{resume_server_base}/api/resume/{execution_id}"
        pdf_r = requests.get(download_url, timeout=60)
        if pdf_r.status_code != 200:
            print_lg(f"  [Resume Tailor Error] Failed to download PDF from local server: HTTP {pdf_r.status_code} - {pdf_r.text}")
            return

        content_type = (pdf_r.headers.get("content-type") or "").lower()
        content = pdf_r.content or b""
        if not content.startswith(b"%PDF") or len(content) < 10_000:
            preview = content[:160].decode("utf-8", errors="replace").replace("\n", " ")
            print_lg(
                "  [Resume Tailor Error] Local resume endpoint did not return a real PDF "
                f"(content-type={content_type or 'unknown'}, bytes={len(content)}, preview={preview!r})."
            )
            return

        file_path.write_bytes(pdf_r.content)
        print_lg(f"  [Resume Tailor] Saved tailored resume: {file_path.name} ({len(content)} bytes)")
        os.environ["INDEED_TAILORED_RESUME_PATH"] = str(file_path.resolve())
        
    except Exception as e:
        print_lg(f"  [Resume Tailor Error] Exception during resume tailoring: {e}")


def run_indeed_bot(page, sb=None,
                   username: str = "", password: str = "") -> dict:
    """
    Single-pass entry point.
    page — Playwright Page object (all browser actions)
    sb   — SeleniumBase driver   (CAPTCHA solving only)
    """
    global _use_new_resume, _randomly_answered_questions, _current_job_context, _current_job_meta

    _ensure_dirs()
    _use_new_resume = True
    _randomly_answered_questions = set()
    _current_job_meta = {}

    try:
        from modules.heartbeat import send_heartbeat
        bot_name = os.getenv("BOT_NAME") or _bot_name
        send_heartbeat(bot_name=bot_name, status="starting", last_activity="Initializing Indeed bot")
    except Exception:
        pass

    print_lg("\n" + "=" * 70 + "\n  Indeed Job Applier Bot (Playwright Edition)\n" + "=" * 70)
    print_lg(f"[Indeed] Training log enabled: {training_log_path()}")
    log_training_event(
        "session_started",
        search_terms=list(search_terms),
        search_location=search_location,
        date_posted=_cfg_date_posted,
        indeed_remote_filter=_cfg_indeed_remote_filter,
        run_in_background=run_in_background,
    )

    applied_count  = 0
    failed_count   = 0
    skipped_count  = 0
    external_count = 0
    blacklisted_companies: set = set()

    fromage = _resolve_fromage()
    remote_filter_labels = [
        _REMOTE_WORK_FILTERS[key]["label"]
        for key in _resolve_remote_work_filters()
        if key in _REMOTE_WORK_FILTERS
    ]
    if remote_filter_labels:
        print_lg(f"[Indeed] Remote filter enabled: {', '.join(remote_filter_labels)}")

    # Step 1: Manual login
    login_ok = _wait_for_manual_login(page, sb, timeout_minutes=5)
    # Recover page in case CF bypass during login invalidated the Playwright page
    page = try_recover_page(page)
    if not login_ok and is_cloudflare_challenge(page) and not captcha_allow_gui_fallback and not captcha_allow_manual_fallback:
        print_lg(
            "[Indeed] Stopping before searches because Indeed is still on a "
            "Cloudflare managed challenge with no automatic token path."
        )
        _print_summary(applied_count, failed_count, skipped_count, external_count)
        log_training_event("session_finished", applied=applied_count,
                           failed=failed_count, skipped=skipped_count,
                           external=external_count,
                           stop_reason="cloudflare_automatic_only_block")
        _close_ai_client()
        if os.getenv("JOB_QUEUE_RESULT_FILE", "").strip():
            from modules.queue_result import write_queue_result
            if applied_count > 0:
                write_queue_result("applied", reason="Indeed direct queue job submitted")
            elif failed_count > 0:
                write_queue_result("failed", reason="Indeed direct queue application failed")
            else:
                write_queue_result("failed", reason="Cloudflare managed challenge block during login/initialization")
        return {"applied": applied_count, "failed": failed_count,
                "skipped": skipped_count, "external": external_count}

    # Step 2: User start
    _wait_for_user_start()

    # Step 3: Load applied and skipped IDs (persisted across all locations)
    applied_ids = get_applied_indeed_job_ids()
    skipped_ids = get_skipped_indeed_job_ids()
    session_seen_ids: set[str] = set()
    crawlee_queue_name = (os.getenv("INDEED_CRAWLEE_QUEUE_NAME", "").strip() or f"{_bot_name}-jobs-queue").replace("_", "-")
    crawlee_queue = CrawleeQueueHelper(crawlee_queue_name)
    crawlee_queue.open()

    # Check for direct links mode
    direct_links_path = os.getenv("INDEED_DIRECT_LINKS_PATH", "").strip()
    if direct_links_path:
        print_lg(f"\n{'=' * 70}\n  Indeed Bot — Direct Links Mode\n  File: '{direct_links_path}'\n{'=' * 70}")
        direct_jobs = _load_direct_links_from_json(direct_links_path)
        if not direct_jobs:
            print_lg("[Indeed] No valid direct jobs to process. Exiting.")
            _close_ai_client()
            return {"applied": 0, "failed": 0, "skipped": 0, "external": 0}
            
        added_count = 0
        for job in direct_jobs:
            jid = job["jid"]
            title = job["title"]
            company = job["company"]
            job_link = job["job_link"]
            
            # Direct links mode explicitly targets these specific jobs (e.g. queue retries).
            # Bypassing the historic applied_ids/skipped_ids checks so they can run.
            if jid in session_seen_ids:
                continue
                
            if not crawlee_queue.has_job(jid):
                crawlee_queue.add_job(jid, job_link, job)
                added_count += 1
                
        print_lg(f"[Indeed] Enqueued {added_count} new jobs for direct application.")
        
        max_application_outcomes = _env_int("INDEED_MAX_APPLICATION_OUTCOMES", 0)
        
        stats_dict = {
            "applied_count": applied_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "external_count": external_count,
            "bookmarked_count": 0,
            "application_outcomes": 0,
            "last_reason": "",
            "last_result_url": "",
        }
        
        outcome = _process_queue(
            crawlee_queue=crawlee_queue,
            page=page,
            sb=sb,
            applied_ids=applied_ids,
            skipped_ids=skipped_ids,
            session_seen_ids=session_seen_ids,
            test_terms=[],
            original_terms=[],
            stats_dict=stats_dict,
            run_in_background=run_in_background,
            click_gap=click_gap,
            max_application_outcomes=max_application_outcomes,
            switch_number=9999,
            term_applied=0,
            current_search_term="direct_links",
            easy_apply_only=easy_apply_only,
            skip_easy_apply=skip_easy_apply,
        )
        
        applied_count = stats_dict["applied_count"]
        failed_count = stats_dict["failed_count"]
        skipped_count = stats_dict["skipped_count"]
        external_count = stats_dict["external_count"]
        
        _print_summary(applied_count, failed_count, skipped_count, external_count)
        log_training_event("session_finished", applied=applied_count,
                           failed=failed_count, skipped=skipped_count,
                           external=external_count)
                           
        try:
            from modules.heartbeat import send_heartbeat
            bot_name = os.getenv("BOT_NAME") or _bot_name
            send_heartbeat(bot_name=bot_name, status="finished", last_activity="Run completed successfully")
        except Exception:
            pass
            
        _close_ai_client()
        if os.getenv("JOB_QUEUE_RESULT_FILE", "").strip():
            from modules.queue_result import write_queue_result, resolve_direct_queue_result
            verify_mode = os.getenv("JOB_QUEUE_VERIFY_APPLY_TYPE", "").strip().lower() in {"1","true","yes","on"}
            status, method, reason = resolve_direct_queue_result(stats_dict, verify_mode=verify_mode)
            write_queue_result(
                status,
                result_url=stats_dict.get("last_result_url", ""),
                reason=reason,
                application_method=method,
            )
        return {"applied": applied_count, "failed": failed_count,
                "skipped": skipped_count, "external": external_count}

    # Step 4: Search terms and locations
    test_terms = _env_csv("INDEED_TEST_SEARCH_TERMS")
    test_locations = _env_csv("INDEED_TEST_LOCATIONS")
    original_terms = test_terms or list(search_terms)
    if test_terms:
        print_lg(f"[Indeed] Test search terms override enabled: {original_terms}")
    if test_locations:
        print_lg(f"[Indeed] Test locations override enabled: {test_locations}")
    max_application_outcomes = _env_int("INDEED_MAX_APPLICATION_OUTCOMES", 0)
    if max_application_outcomes > 0:
        print_lg(f"[Indeed] Will stop after {max_application_outcomes} application outcome(s).")
    application_outcomes = 0
    state_terms, resume_location = load_resume_state(_bot_name, original_terms)
    is_resuming = (len(state_terms) != len(original_terms))
    if is_resuming:
        terms = state_terms
    else:
        terms = list(original_terms)
        if randomize_search_order:
            shuffle(terms)

    # Use search_locations list if defined, otherwise fall back to single search_location
    locations = test_locations or (search_locations if search_locations else [search_location])
    if resume_location is not None and not test_locations:
        normalized_resume_location = str(resume_location or "").strip()
        normalized_locations = [str(location or "").strip() for location in locations]
        if normalized_resume_location in normalized_locations:
            locations = locations[normalized_locations.index(normalized_resume_location):]

    # Process leftover queue items first before searching
    if not crawlee_queue.is_empty():
        print_lg("[Indeed] Detected remaining/unhandled jobs from a previous run. Processing them first...")
        stats_dict = {
            "applied_count": applied_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "external_count": external_count,
            "application_outcomes": application_outcomes,
        }
        outcome = _process_queue(
            crawlee_queue=crawlee_queue,
            page=page,
            sb=sb,
            applied_ids=applied_ids,
            skipped_ids=skipped_ids,
            session_seen_ids=session_seen_ids,
            test_terms=test_terms,
            original_terms=original_terms,
            stats_dict=stats_dict,
            run_in_background=run_in_background,
            click_gap=click_gap,
            max_application_outcomes=max_application_outcomes,
            switch_number=9999,
            term_applied=0,
            current_search_term=None,
            easy_apply_only=easy_apply_only,
            skip_easy_apply=skip_easy_apply,
        )
        applied_count = stats_dict["applied_count"]
        failed_count = stats_dict["failed_count"]
        skipped_count = stats_dict["skipped_count"]
        external_count = stats_dict["external_count"]
        application_outcomes = stats_dict["application_outcomes"]
        if outcome.get("stop"):
            _print_summary(applied_count, failed_count, skipped_count, external_count)
            _close_ai_client()
            return {"applied": applied_count, "failed": failed_count,
                    "skipped": skipped_count, "external": external_count}

    # Step 5: Search loop (outer: locations, inner: terms)
    browser_dead = False
    for location_query in locations:
        location_query = str(location_query or "").strip()
        remote_only_search = not location_query

        print_lg(f"\n{'='*70}\n  LOCATION: {location_query or 'Remote only'}\n{'='*70}")

        for term in terms:
            if browser_dead or not is_browser_alive(page):
                print_lg("  [Indeed] Browser/context closed — aborting remaining search terms.")
                browser_dead = True
                break

            print_lg(f"\n{'=' * 70}\n  Indeed: '{term}'  |  Location: '{location_query or 'Remote only'}'\n{'=' * 70}")
            try:
                from modules.heartbeat import send_heartbeat
                bot_name = os.getenv("BOT_NAME") or _bot_name
                send_heartbeat(bot_name=bot_name, status="running", last_activity=f"Searching for '{term}' in '{location_query or 'Remote only'}'")
            except Exception:
                pass

            page_num = 0
            term_applied = 0
            switch_number = 9999  # max applications per search term before moving on
            retry_current_term = False

            while True:
                search_url = _build_search_url(term, location_query, page_num, fromage)
                print_lg(f"  Opening search results page {page_num + 1}…")

                try:
                    _goto_page(page, search_url, timeout=15000)
                except Indeed404Error:
                    # Search URL itself returned 404 (e.g. stale direct_links path).
                    # Recover by going to Indeed home so the session stays alive.
                    print_lg(f"  [404] Search page not found: {search_url} — recovering to Indeed home.")
                    try:
                        _goto_page(page, "https://ca.indeed.com", timeout=15000)
                    except Exception:
                        pass
                except Exception as e:
                    err_str = str(e).lower()
                    if any(k in err_str for k in ("closed", "target", "browser has been")):
                        # Page stale (CF bypass can invalidate it) — recover and retry once
                        page = try_recover_page(page)
                        if not is_browser_alive(page):
                            print_lg("  [Indeed] Browser/context closed — aborting run.")
                            retry_current_term = True
                            browser_dead = True
                            break
                        try:
                            _goto_page(page, search_url, timeout=15000)
                        except Exception as e2:
                            print_lg(f"  Browser error after page recovery: {e2}")
                            retry_current_term = True
                            browser_dead = True
                            break
                    else:
                        print_lg(f"  Browser navigation warning: {e}")
                        navigation_error = e
                        for retry_number, retry_timeout in enumerate((30000, 45000), start=1):
                            if not is_browser_alive(page):
                                page = try_recover_page(page)
                            if not is_browser_alive(page):
                                break
                            try:
                                page.evaluate("window.stop()")
                            except Exception:
                                pass
                            print_lg(
                                f"  [Indeed] Retrying search navigation "
                                f"({retry_number}/2, timeout={retry_timeout // 1000}s)…"
                            )
                            time.sleep(2)
                            try:
                                _goto_page(page, search_url, timeout=retry_timeout)
                                navigation_error = None
                                print_lg("  [Indeed] Search navigation recovered.")
                                break
                            except Exception as retry_error:
                                navigation_error = retry_error
                                print_lg(
                                    f"  [Indeed] Search navigation retry "
                                    f"{retry_number}/2 failed: {retry_error}"
                                )

                        if navigation_error is not None:
                            retry_current_term = True
                            browser_dead = True
                            try:
                                from modules.heartbeat import send_heartbeat
                                bot_name = os.getenv("BOT_NAME") or _bot_name
                                send_heartbeat(
                                    bot_name=bot_name,
                                    status="failed",
                                    last_activity=(
                                        f"Browser navigation failed after retries for '{term}'; "
                                        "exiting for watchdog restart"
                                    ),
                                )
                            except Exception:
                                pass
                            break

                print_lg(f"\n  → Page {page_num + 1}  ({search_url})")
                time.sleep(_T_ACTION)

                # CAPTCHA check on search results page
                check_and_handle_captcha(page, sb, f"search '{term}' page {page_num + 1}",
                                         run_in_background=run_in_background)
                # Recover page if CF bypass during search results page invalidated it
                page = try_recover_page(page)
                if is_cloudflare_challenge(page):
                    print_lg(
                        "  [Indeed] Search page is still blocked by Cloudflare after "
                        "automatic solvers; preserving current term for a clean retry."
                    )
                    retry_current_term = True
                    break

                if _is_no_matching_jobs_page(page):
                    print_lg("  No matching jobs for this search — ignoring Indeed suggestion cards.")
                    break

                # Snapshot all cards (read once before clicking)
                cards = _find_job_cards(page)
                if not cards:
                    print_lg("  No job cards found — moving to next search term.")
                    break

                # Extract all card payloads first
                discovered_payloads = []
                for card in cards:
                    try:
                        if _is_suggested_job_card(card):
                            info = _extract_card_info(card, page)
                            print_lg(f"  ✗ Skipping suggested job card: '{info[1]}'")
                            skipped_count += 1
                            continue
                        info = _extract_card_info(card, page)
                        jid = info[0]
                        if jid == 'Unknown' or not jid:
                            continue

                        # Skip duplicates or already-screened
                        if jid in applied_ids or jid in skipped_ids or jid in session_seen_ids or (crawlee_queue and crawlee_queue.has_job(jid)):
                            session_seen_ids.add(jid)
                            continue

                        try:
                            card_text = " ".join((card.inner_text() or "").split())
                        except Exception:
                            card_text = ""

                        title, company, location, has_easy_apply, job_href = info[1], info[2], info[3], info[4], info[5]
                        job_link, job_href = _preferred_job_urls(jid, job_href)
                        payload = {
                            "jid": jid,
                            "title": title,
                            "company": company,
                            "location": location,
                            "has_easy_apply": has_easy_apply,
                            "job_href": job_href,
                            "job_link": job_link,
                            "card_text": card_text,
                            "search_term": term,
                            "search_url": search_url,
                        }
                        log_job_status_event("discovered", jid, title, company, job_link)
                        discovered_payloads.append(payload)
                    except Exception as e_card:
                        print_lg(f"  [Card Warning] Failed to parse card: {e_card}")
                        continue

                # Batch pre-screening on discovered payloads
                batch_decisions = {}
                if discovered_payloads:
                    print_lg(f"  [Batch AI Gate] Pre-screening {len(discovered_payloads)} new jobs on search page...")
                    try:
                        from jobbots.core.shared_modules.indeed.gates import batch_screen_jobs_with_ai
                        batch_decisions = batch_screen_jobs_with_ai(discovered_payloads)
                    except Exception as e_batch:
                        print_lg(f"  [Batch AI Gate Warning] Batch screening failed: {e_batch}. Falling back to individual check.")

                # Add to queue or discard based on decisions
                for job in discovered_payloads:
                    jid = job["jid"]
                    title = job["title"]
                    company = job["company"]
                    location = job["location"]
                    job_link = job["job_link"]
                    decision_info = batch_decisions.get(jid, {})
                    decision = decision_info.get("decision", "PROCEED")
                    reason = decision_info.get("reason", "Batch screening fallback")

                    if decision == "REJECT":
                        print_lg(f"  ✗ Card batch-rejected: '{title}' at '{company}' — {reason}")
                        _save_skipped(jid, title, company, location, f"Batch Screened Rejected: {reason}", job_link=job_link)
                        log_job_status_event("screened_skip", jid, title, company, job_link, reason=f"batch_reject: {reason[:60]}")
                        skipped_ids.add(jid)
                        skipped_count += 1
                    else:
                        if jid in batch_decisions:
                            print_lg(f"  ✓ Card batch-approved: '{title}' at '{company}' — {reason}")
                        
                        res = crawlee_queue.add_job(jid, job_link, job)
                        was_already_present = False
                        was_already_handled = False
                        if res:
                            was_already_present = getattr(res, "was_already_present", False)
                            was_already_handled = getattr(res, "was_already_handled", False)
                        if was_already_present or was_already_handled:
                            log_job_status_event("duplicate", jid, title, company, job_link, reason="already_present_in_queue")
                        else:
                            log_job_status_event("queued", jid, title, company, job_link)

                # Process all items in Crawlee queue
                stats_dict = {
                    "applied_count": applied_count,
                    "failed_count": failed_count,
                    "skipped_count": skipped_count,
                    "external_count": external_count,
                    "application_outcomes": application_outcomes,
                }
                outcome = _process_queue(
                    crawlee_queue=crawlee_queue,
                    page=page,
                    sb=sb,
                    applied_ids=applied_ids,
                    skipped_ids=skipped_ids,
                    session_seen_ids=session_seen_ids,
                    test_terms=test_terms,
                    original_terms=original_terms,
                    stats_dict=stats_dict,
                    run_in_background=run_in_background,
                    click_gap=click_gap,
                    max_application_outcomes=max_application_outcomes,
                    switch_number=switch_number,
                    term_applied=term_applied,
                    current_search_term=term,
                    easy_apply_only=easy_apply_only,
                    skip_easy_apply=skip_easy_apply,
                )
                applied_count = stats_dict["applied_count"]
                failed_count = stats_dict["failed_count"]
                skipped_count = stats_dict["skipped_count"]
                external_count = stats_dict["external_count"]
                application_outcomes = stats_dict["application_outcomes"]
                term_applied = outcome.get("term_applied", term_applied)
                if outcome.get("stop"):
                    _print_summary(applied_count, failed_count, skipped_count, external_count)
                    _close_ai_client()
                    return {"applied": applied_count, "failed": failed_count,
                            "skipped": skipped_count, "external": external_count}
                if outcome.get("break_search"):
                    break
                # Pagination - always advance to prevent infinite loops
                max_pages = 10  # Safety limit
                if page_num >= max_pages:
                    print_lg(f"  → Reached max pages ({max_pages}) for this term.")
                    break

                try:
                    _goto_page(page, search_url, timeout=15000)
                    time.sleep(max(0.2, min(float(click_gap or 1), 3.0)))
                    check_and_handle_captcha(page, sb, f"pagination check page {page_num + 1}",
                                             run_in_background=run_in_background)
                    page = try_recover_page(page)
                    if is_cloudflare_challenge(page):
                        print_lg(
                            "  [Indeed] Pagination page is still blocked by Cloudflare after "
                            "automatic solvers; preserving current term for a clean retry."
                        )
                        retry_current_term = True
                        break
                except Exception as e:
                    print_lg(f"  [Pagination warning] {e}")

                has_next = _has_next_page(page)
                print_lg(f"  [Pagination] has_next_page={has_next}, current={page_num + 1}")

                if has_next:
                    page_num += 1
                    print_lg(f"  → Advancing to page {page_num + 1}")
                else:
                    print_lg("  → No more pages.")
                    break

            remaining = terms[terms.index(term):] if retry_current_term else terms[terms.index(term) + 1:]
            if remaining:
                save_resume_state(_bot_name, remaining, location_query)
            else:
                clear_resume_state()
            if retry_current_term:
                print_lg(
                    "  [Indeed] Preserved current search term and ending run so "
                    "the watchdog can reconnect the browser."
                )
                browser_dead = True
                break

        if browser_dead:
            break

    _print_summary(applied_count, failed_count, skipped_count, external_count)
    log_training_event("session_finished", applied=applied_count,
                       failed=failed_count, skipped=skipped_count,
                       external=external_count)
    try:
        from modules.heartbeat import send_heartbeat
        bot_name = os.getenv("BOT_NAME") or _bot_name
        send_heartbeat(bot_name=bot_name, status="finished", last_activity="Run completed successfully")
    except Exception:
        pass
    _close_ai_client()
    if os.getenv("JOB_QUEUE_RESULT_FILE", "").strip():
        from modules.queue_result import write_queue_result
        if applied_count > 0:
            write_queue_result("applied", reason="Indeed direct queue job submitted")
        elif failed_count > 0:
            write_queue_result("failed", reason="Indeed direct queue application failed")
        else:
            write_queue_result("failed", reason="Indeed direct queue job produced no application outcome")
    return {"applied": applied_count, "failed": failed_count,
            "skipped": skipped_count, "external": external_count}


# ─────────────────────────────────────────────────────────────────────────────
# Continuous-run loop
# ─────────────────────────────────────────────────────────────────────────────

def run_indeed_loop(page, sb=None) -> None:
    """
    Multi-cycle entry point.
    page — Playwright Page object
    sb   — SeleniumBase driver (CAPTCHA solving)
    """
    global _cfg_date_posted
    _date_options = ["All Dates", "Last 14 days", "Last 7 days", "Last 3 days", "Last 24 hours"]

    _init_ai_client()

    cycle = 1
    while True:
        print_lg(f"\n{'#' * 70}\n  Indeed Bot — Cycle {cycle}  |  {datetime.now()}\n"
                 f"  Date filter: '{_cfg_date_posted or 'Any time'}'\n{'#' * 70}")

        run_indeed_bot(page, sb)

        if not run_non_stop:
            break

        try:
            from config.search import cycle_date_posted
        except ImportError:
            cycle_date_posted = False

        if cycle_date_posted and _date_options:
            cur_idx = _date_options.index(_cfg_date_posted) if _cfg_date_posted in _date_options else 0
            next_idx = (cur_idx + 1) % len(_date_options)
            _cfg_date_posted = _date_options[next_idx]
            print_lg(f"  [Indeed] Cycling date_posted → '{_cfg_date_posted}'")

        print_lg("  [Indeed] Sleeping 10 min before next cycle…")
        time.sleep(300)
        print_lg("  [Indeed] 5 more minutes…")
        time.sleep(300)

        cycle += 1

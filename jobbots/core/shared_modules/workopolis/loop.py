from ._bootstrap import *  # noqa: F403
from jobbots.core.shared_modules.workopolis.persistence import (
    load_resume_state,
    save_resume_state,
    clear_resume_state,
    get_applied_workopolis_job_ids,
    get_skipped_workopolis_job_ids,
    _save_applied,
    _save_skipped,
    _save_failed,
)

def run_workopolis_loop(page, sb=None) -> dict:
    direct_json = os.getenv("JOB_QUEUE_DIRECT_JOB", "").strip()
    if direct_json:
        import json
        from modules.queue_result import write_queue_result
        job=json.loads(direct_json); jid=str(job["source_job_id"])
        try:
            success,result_url,reason=_apply_to_job(page,sb,None,jid,job["title"],job["company"],job.get("location",""),job["url"])
        except Exception as exc:
            success,result_url,reason=False,job["url"],f"{type(exc).__name__}: {exc}"
        reason_l = (reason or "").lower()
        if success:
            status, method = "applied", "easy_apply"
        elif "already applied" in reason_l:
            status, method = "already_applied", "easy_apply"
        elif "cover letter" in reason_l:
            status, method = "skipped", "easy_apply"
        elif "external apply" in reason_l:
            status, method = "bookmarked", "company_site"
        else:
            status, method = "failed", ""
        write_queue_result(status, result_url=result_url, reason=reason, application_method=method)
        return {
            "applied": int(status == "applied"),
            "failed": int(status == "failed"),
            "skipped": int(status in ("skipped", "already_applied", "bookmarked")),
            "external": int(status == "bookmarked"),
        }
    """
    Main Workopolis application loop.
    """
    _ensure_dirs()
    smartapply._use_new_resume = True
    smartapply_impl._use_new_resume = True
    smartapply._randomly_answered_questions = set()
    smartapply_impl._randomly_answered_questions = smartapply._randomly_answered_questions

    # Monkeypatch smartapply saving to ensure logs/excels are saved under Workopolis
    smartapply._save_skipped = _save_skipped
    smartapply_impl._save_skipped = _save_skipped
    smartapply._save_applied = _save_applied
    smartapply_impl._save_applied = _save_applied
    smartapply._save_failed = _save_failed
    smartapply_impl._save_failed = _save_failed

    # Dynamically import Indeed gates
    try:
        from jobbots.core.shared_modules.indeed import gates as indeed_gates
    except Exception:
        indeed_gates = None

    print_lg(
        "\n" + "=" * 70 +
        "\n  Workopolis Job Applier Bot  (SmartApply / Discovery Edition)" +
        "\n" + "=" * 70
    )

    # Open Workopolis homepage
    print_lg("\n[Workopolis] Opening Workopolis homepage...")
    try:
        page.goto(WORKOPOLIS_HOME, timeout=20000)
    except Exception as e:
        print_lg(f"[Workopolis] Could not open homepage: {e}")
    check_and_handle_captcha(page, sb, "Workopolis homepage",
                             run_in_background=run_in_background)
    page = try_recover_page(page)

    # Ensure logged in
    _ensure_workopolis_logged_in(page, sb)

    smartapply._wait_for_user_start()

    # Session init
    original_terms = list(search_terms)
    state_terms, resume_location = load_resume_state(_bot_name, original_terms)
    is_resuming = (len(state_terms) != len(original_terms))
    if is_resuming:
        terms = state_terms
    else:
        terms = list(search_terms)
        if randomize_search_order:
            shuffle(terms)

    locations = search_locations if search_locations else [search_location]
    if resume_location is not None:
        normalized_resume_location = str(resume_location or "").strip()
        normalized_locations = [str(location or "").strip() for location in locations]
        if normalized_resume_location in normalized_locations:
            locations = locations[normalized_locations.index(normalized_resume_location):]

    applied_ids = get_applied_workopolis_job_ids()
    skipped_ids = get_skipped_workopolis_job_ids()
    session_seen_ids: set[str] = set()
    applied_count = failed_count = skipped_count = external_count = 0

    browser_dead = False

    # Outer loop: locations
    for location_query in locations:
        location_query = location_query.strip()
        if not location_query:
            continue

        print_lg(f"\n{'='*70}\n  LOCATION: {location_query}\n{'='*70}")
        log_training_event("workopolis_session_started",
                           search_terms=terms, search_location=location_query)

        # Inner loop: search terms
        for term in terms:
            if browser_dead or not is_browser_alive(page):
                print_lg("  [Workopolis] Browser closed — aborting search terms.")
                browser_dead = True
                break

            print_lg(
                f"\n{'=' * 70}\n"
                f"  Workopolis: '{term}' | Location: '{location_query}'\n"
                f"{'=' * 70}"
            )
            page_num = 0
            term_applied = 0
            seen_job_ids_for_term: set[str] = set()
            seen_page_signatures: set[tuple[str, ...]] = set()
            retry_current_term = False

            # Result pages loop
            while True:
                # Pagination safety max page cap
                max_pages = 10
                if page_num >= max_pages:
                    print_lg(f"  [Workopolis] Reached max pages ({max_pages}) for this term.")
                    break

                if page_num == 0:
                    search_url = _build_search_url(term, location_query, page_num)
                    print_lg(f"  Opening search page {page_num + 1}: {search_url}")

                    try:
                        page.goto(search_url, timeout=25000)
                    except Exception as e:
                        print_lg(f"  [Workopolis] Navigation error: {e}")
                        if not is_browser_alive(page):
                            print_lg("  [Workopolis] Browser closed — aborting run.")
                            browser_dead = True
                            retry_current_term = True
                        break
                else:
                    try:
                        search_url = page.url
                    except Exception:
                        search_url = _build_search_url(term, location_query, page_num)
                    print_lg(f"  Processing clicked search page {page_num + 1}: {search_url}")

                time.sleep(_T_ACTION)
                check_and_handle_captcha(page, sb,
                                         f"Workopolis search '{term}' p{page_num + 1}",
                                         run_in_background=run_in_background)
                page = try_recover_page(page)

                # Ensure logged in check
                _ensure_workopolis_logged_in(page, sb)

                # Apply filters (Distance & Job Type) on the first page
                if page_num == 0:
                    _apply_distance_filter(page, "25 kilometers")
                    
                    # Apply job type if set in config
                    from config.search import job_type as cfg_job_type
                    if cfg_job_type and len(cfg_job_type) > 0:
                        _apply_job_type_filter(page, cfg_job_type[0])

                # Get job cards
                cards = _find_job_cards(page)
                if not cards:
                    print_lg("  [Workopolis] No job cards found on this page. Finished search.")
                    break

                jobs_on_page = []
                for card in cards:
                    try:
                        jobs_on_page.append(_extract_card_info(card))
                    except Exception as e:
                        print_lg(f"  [Workopolis] Could not read card snapshot: {e}")

                print_lg(f"  Found {len(jobs_on_page)} job cards to process.")

                page_signature = tuple(
                    job_id for job_id, *_ in jobs_on_page if job_id
                )
                if page_signature and page_signature in seen_page_signatures:
                    print_lg("  [Workopolis] Repeated result page detected. Moving to next search term.")
                    break
                seen_page_signatures.add(page_signature)

                known_ids = session_seen_ids | applied_ids | skipped_ids
                if page_signature and all(job_id in known_ids for job_id in page_signature):
                    print_lg("  [Workopolis] Page has only already-seen/applied/skipped jobs. Moving to next search term.")
                    break

                # Process a stable page snapshot. Re-find each card only when
                # needed because apply attempts can navigate and stale handles.
                for index, (job_id, title, company, location, has_easy_apply, job_href) in enumerate(jobs_on_page, start=1):
                    if browser_dead or not is_browser_alive(page):
                        browser_dead = True
                        break
                    
                    # Skip duplicate card IDs on the same page
                    if job_id in seen_job_ids_for_term:
                        continue
                    seen_job_ids_for_term.add(job_id)

                    # Check already applied / skipped
                    if job_id in applied_ids:
                        seen_job_ids_for_term.add(job_id)
                        session_seen_ids.add(job_id)
                        continue
                    if job_id in skipped_ids:
                        seen_job_ids_for_term.add(job_id)
                        session_seen_ids.add(job_id)
                        continue
                    if job_id in session_seen_ids:
                        continue
                    session_seen_ids.add(job_id)

                    print_lg(f"\n  [{index}/{len(jobs_on_page)}] Job: {title} | Company: {company} | ID: {job_id}")

                    # ── Pre-click Title/Company hard reject gate using Indeed's gate
                    if indeed_gates:
                        card = _find_card_by_job(page, job_id=job_id, job_href=job_href)
                        try:
                            card_text = " ".join((card.inner_text() or "").split()) if card else ""
                        except Exception:
                            card_text = ""
                        pre_reject, pre_reason = indeed_gates._obvious_non_it_reject(
                            title, company, location, card_text, "", easy_apply=has_easy_apply
                        )
                        if pre_reject:
                            print_lg(f"  ✗ Title hard-reject (pre-load) — {pre_reason}")
                            _save_skipped(job_id, title, company, location, f"bad title: {pre_reason}", job_link=job_href)
                            skipped_ids.add(job_id)
                            skipped_count += 1
                            continue

                    # Bad words filter in Title (fallback local check)
                    if _bad_words and any(w.lower() in title.lower() for w in _bad_words):
                        print_lg("  Title contains bad word. Skipping.")
                        _save_skipped(job_id, title, company, location, "bad title: Title contains bad word", job_link=job_href)
                        skipped_ids.add(job_id)
                        skipped_count += 1
                        continue

                    # If easy apply, process application
                    if has_easy_apply:
                        card = _find_card_by_job(page, job_id=job_id, job_href=job_href)
                        if not card:
                            print_lg("  [Workopolis] Could not re-find job card. Skipping.")
                            _save_failed(job_id, title, company, job_href, "could not re-find card")
                            failed_count += 1
                            continue

                        from modules.job_queue_bridge import discovery_mode, enqueue_approved_job
                        if discovery_mode():
                            # Load and pre-screen through the same application gate without
                            # opening SmartApply. Full description gating remains in apply.py.
                            _click_job_card(page, card)
                            time.sleep(_T_CARD)
                            description = _get_job_description(page)
                            if indeed_gates:
                                approved, queue_gate_reason = indeed_gates._local_easy_apply_gate_should_apply(
                                    title, company, location, card_text, description
                                )
                                if not approved:
                                    print_lg(f"  ✗ Job gate skipped — {queue_gate_reason}")
                                    _save_skipped(job_id, title, company, location,
                                                  f"job_gate_rejected: {queue_gate_reason}", job_link=job_href)
                                    skipped_ids.add(job_id); skipped_count += 1
                                    continue
                            else:
                                queue_gate_reason = "title/easy-apply pre-screen passed"
                            queue_id, created = enqueue_approved_job(
                                portal="workopolis", profile=(os.getenv("JOB_PROFILE") or "it").lower(), job_id=job_id,
                                title=title, company=company, location=location,
                                url=job_href, description=description,
                                gate_reason=queue_gate_reason,
                                resume_policy="tailored" if (os.getenv("JOB_PROFILE") or "IT").lower()=="it" else "default",
                            )
                            print_lg(f"  {'Queued' if created else 'Already queued'} approved job #{queue_id}; discovery continues.")
                            continue
                        
                        # Apply recovery wrapper around apply attempts
                        try:
                            success, app_link, reason = _apply_to_job(page, sb, card, job_id, title, company, location, job_href)
                            page = try_recover_page(page)
                        except Exception as e:
                            err_str = str(e).lower()
                            if any(k in err_str for k in ("page was closed", "target page", "invalid session", "browser has been closed")):
                                print_lg(f"  ✗ Page stale on '{title}' — attempting page recovery…")
                                recovered = try_recover_page(page)
                                from modules.captcha_handler import _is_page_alive
                                if _is_page_alive(recovered):
                                    page = recovered
                                    print_lg("  ↩ Page recovered — skipping this job, continuing…")
                                    _save_failed(job_id, title, company, job_href, f"Page stale: {e}")
                                    failed_count += 1
                                    try:
                                        page.goto(search_url, timeout=25000)
                                        time.sleep(_T_ACTION)
                                    except Exception:
                                        pass
                                    continue
                                else:
                                    print_lg("  Browser is truly closed — aborting.")
                                    browser_dead = True
                                    retry_current_term = True
                                    break
                            print_lg(f"  Error on '{title}': {e}")
                            _save_failed(job_id, title, company, job_href, str(e))
                            failed_count += 1
                            try:
                                page.goto(search_url, timeout=25000)
                                time.sleep(_T_ACTION)
                            except Exception:
                                pass
                            continue

                        if success:
                           applied_count += 1
                           term_applied += 1
                           applied_ids.add(job_id)
                           _save_applied(job_id, title, company, location, "", "", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), app_link)
                           print_lg("  ✓ Applied successfully.")
                        else:
                           reason_lower = (reason or "").lower()
                           is_skip = False
                           skip_reason = reason
                           
                           if "already applied" in reason_lower:
                               is_skip = True
                               skip_reason = "already applied"
                           elif "cover letter" in reason_lower:
                               is_skip = True
                               skip_reason = "cover letter required"
                           elif "external apply" in reason_lower:
                               is_skip = True
                               skip_reason = "external apply"
                           elif "bad title" in reason_lower:
                               is_skip = True
                               skip_reason = reason  # keep detailed gate reason
                           elif "not easy apply" in reason_lower:
                               is_skip = True
                               skip_reason = "not easy apply"
                               
                           if is_skip:
                               skipped_count += 1
                               _save_skipped(job_id, title, company, location, skip_reason, job_link=app_link or job_href)
                               skipped_ids.add(job_id)
                               print_lg(f"  Skipped: {skip_reason}. Marked skipped.")
                           else:
                               failed_count += 1
                               _save_failed(job_id, title, company, app_link or job_href, reason)
                               print_lg(f"  ✗ Apply failed: {reason}")

                        try:
                            page.goto(search_url, timeout=25000)
                            time.sleep(_T_ACTION)
                            check_and_handle_captcha(
                                page, sb,
                                f"Workopolis search restore '{term}' p{page_num + 1}",
                                run_in_background=run_in_background,
                            )
                            page = try_recover_page(page)
                            print_lg("  Restored search page.")
                        except Exception as e:
                            print_lg(f"  [Workopolis] Could not restore search page after apply attempt: {e}")
                            if not is_browser_alive(page):
                                browser_dead = True
                                break
                    else:
                        print_lg("  Not Easy Apply. Skipping.")
                        _save_skipped(job_id, title, company, location, "not easy apply", job_link=job_href)
                        skipped_ids.add(job_id)
                        skipped_count += 1

                # Go to next page
                page_num += 1
                if browser_dead or not _go_to_next_page(page, page_num):
                    print_lg(f"  No more pages found after page {page_num}.")
                    break

            remaining = terms[terms.index(term):] if retry_current_term else terms[terms.index(term) + 1:]
            if remaining:
                save_resume_state(_bot_name, remaining, location_query)
            else:
                clear_resume_state()

            if retry_current_term:
                print_lg("  [Workopolis] Preserved current search term and ending run.")
                break

    summary = {
        "applied": applied_count,
        "failed": failed_count,
        "skipped": skipped_count,
        "external": external_count,
    }
    print_lg(f"\nWorkopolis run summary: {summary}")
    return summary

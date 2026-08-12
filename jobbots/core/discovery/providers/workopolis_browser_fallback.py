"""Workopolis NST Browser fallback — invoked when the HTTP provider fails.

Reuses the card-extraction selector patterns from the existing
``core.shared_jobbots.core.shared_modules.workopolis.search`` module (``_find_job_cards``,
``_extract_card_info``), but runs through NST Browser + Playwright instead
of requiring a logged-in bot session.
"""
from __future__ import annotations

import logging
import os
import random
import time
from urllib.parse import quote_plus

from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest

_log = logging.getLogger("discovery.providers.workopolis_browser")


class WorkopolisBrowserFallback:
    """Workopolis discovery via NST Browser + Playwright.

    This provider is only instantiated by the planner when the HTTP
    provider raises ``WorkopolisHTTPIncomplete``.
    """

    name = "workopolis_browser"
    supported_platforms = ["workopolis"]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        """Open Workopolis search in NST Browser and scrape job cards."""
        profile_id = _resolve_nst_profile(request.profile)
        if not profile_id:
            _log.warning(
                "No NST Browser profile configured for Workopolis fallback. "
                "Set NSTBROWSER_PROFILE_ID_WORKOPOLIS_IT."
            )
            return []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _log.error("Playwright is not installed.")
            return []

        try:
            cdp_url = _start_nst_browser(profile_id)
        except Exception as start_exc:
            _log.error("Failed to start/verify Nstbrowser profile: %s", start_exc)
            return []

        all_jobs: list[RawJob] = []

        try:
            with sync_playwright() as pw:
                _log.info("Connecting to NST Browser for Workopolis: %s", cdp_url)
                browser = pw.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()

                # Cap NST opens: full hero×city matrix burns quota for low yield.
                try:
                    max_queries = max(
                        1, int(os.environ.get("WORKOPOLIS_BROWSER_MAX_QUERIES") or "24")
                    )
                except ValueError:
                    max_queries = 24
                queries_run = 0
                for term in request.search_terms:
                    for location in request.locations:
                        if queries_run >= max_queries:
                            _log.info(
                                "Workopolis browser hit max_queries=%d — stopping",
                                max_queries,
                            )
                            break
                        try:
                            jobs = self._search_and_scrape(
                                page, term, location, request
                            )
                            all_jobs.extend(jobs)
                        except Exception as exc:
                            _log.warning(
                                "Workopolis browser search failed for %r / %r: %s",
                                term, location, exc,
                            )
                        queries_run += 1
                        _jitter(1.0, 2.5)
                    if queries_run >= max_queries:
                        break

                try:
                    page.close()
                except Exception:
                    pass

        except Exception as exc:
            _log.error("Workopolis NST Browser connection failed: %s", exc)

        _log.info("Workopolis browser total raw jobs: %d", len(all_jobs))
        return all_jobs

    def _search_and_scrape(
        self,
        page,
        term: str,
        location: str,
        request: DiscoveryRequest,
    ) -> list[RawJob]:
        """Navigate to search page and extract job cards."""
        remote = request.is_remote_location(location)
        effective_location = "Remote" if remote else location
        url = (
            f"https://www.workopolis.com/search?q={quote_plus(term)}"
            f"&l={quote_plus(effective_location)}&s=d"
        )
        if not remote and request.radius_km > 0:
            url += f"&radius={request.radius_km}"
        _log.info("Workopolis browser navigating: %s", url)

        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        _jitter(2.0, 4.0)

        jobs: list[RawJob] = []
        max_pages = max(1, request.max_results_per_term // 15)

        for page_num in range(max_pages):
            cards = self._extract_cards(page, term)
            if request.easy_apply_only:
                cards = [
                    j for j in cards
                    if (j.easy_apply_evidence or "").strip()
                ]
            jobs.extend(cards)

            if len(jobs) >= request.max_results_per_term:
                break

            # Try to go to next page
            if not self._go_next_page(page, page_num):
                break
            _jitter(1.5, 3.0)

        _log.info(
            "Workopolis browser term=%r location=%r → %d jobs",
            term, location, len(jobs),
        )
        return jobs

    def _extract_cards(self, page, search_term: str) -> list[RawJob]:
        """Extract job cards from the current page using DOM selectors.

        Selector patterns mirror ``core.shared_jobbots.core.shared_modules.workopolis.search``.
        """
        card_selectors = [
            "[data-testid='searchSerpJob']",
            "[data-testid='expandedSearchTitleCard']",
            "div.job_seen_beacon",
            "[data-testid='jobListing']",
            "div[data-jk]",
            "li[data-jk]",
        ]

        elements = []
        for sel in card_selectors:
            try:
                elements = page.query_selector_all(sel)
                if elements:
                    break
            except Exception:
                continue

        jobs: list[RawJob] = []
        for el in elements:
            try:
                raw = self._parse_card_element(el, search_term)
                if raw:
                    jobs.append(raw)
            except Exception as exc:
                _log.debug("Skipping malformed Workopolis card: %s", exc)

        return jobs

    @staticmethod
    def _parse_card_element(el, search_term: str) -> RawJob | None:
        """Parse a Playwright element handle into a RawJob."""
        job_id = el.get_attribute("data-jk") or el.get_attribute("data-jobid") or ""

        # Title
        title_el = el.query_selector(
            "[data-testid='searchSerpJobTitle'], "
            "[data-testid='expandedSearchCardHeader'], "
            "h2, h3, [data-testid='job-title'], "
            "a[data-testid='job-card-title-link']"
        )
        title = title_el.inner_text().strip() if title_el else ""

        # Link
        link_el = (
            el.query_selector("a[href*='/job/']")
            or el.query_selector("a[href*='/joblisting/']")
            or el.query_selector("a[href*='/viewjob/']")
            or el.query_selector("a")
        )
        href = link_el.get_attribute("href") if link_el else ""
        if href and not href.startswith("http"):
            href = f"https://www.workopolis.com{href}"

        # Extract job_id from URL if missing
        if not job_id and href:
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            job_id = (
                qs.get("jk", [""])[0]
                or qs.get("jobId", [""])[0]
                or parsed.path.rstrip("/").split("/")[-1]
            )

        if not job_id:
            job_id = f"work_{abs(hash(href or title)) & 0xffffffff:08x}"

        # Company
        comp_el = el.query_selector(
            "[data-testid='companyName'], "
            "[data-testid='expandedSearchCardCompanyName'], "
            ".companyName, .company"
        )
        company = comp_el.inner_text().strip() if comp_el else ""

        # Location
        loc_el = el.query_selector(
            "[data-testid='searchSerpJobLocation'], "
            "[data-testid='expandedSearchCardJobLocation'], "
            ".companyLocation, .location"
        )
        location = loc_el.inner_text().strip() if loc_el else ""

        # Quick apply badge
        try:
            card_text = el.inner_text().lower()
            has_quick_apply = any(
                badge in card_text
                for badge in ("quick apply", "easy apply", "apply with indeed")
            )
        except Exception:
            has_quick_apply = False

        evidence = "workopolis_quick_apply_badge" if has_quick_apply else ""

        if not title:
            return None

        return RawJob(
            source_platform="workopolis",
            source_job_id=job_id,
            title=title,
            company=company,
            location=location,
            description="",
            listing_url=href or "",
            destination_url=None,
            date_posted=None,
            easy_apply_evidence=evidence,
            raw_extras={"search_term": search_term},
        )

    @staticmethod
    def _go_next_page(page, current_page: int) -> bool:
        """Click the next pagination button."""
        target = current_page + 2  # 1-indexed
        sel = f"[data-testid='paginationBlock{target}']"
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click()
                _jitter(1.5, 3.0)
                return True
        except Exception:
            pass

        # Fallback to generic Next
        for arrow_sel in [
            "[aria-label='Next']",
            "a[aria-label='Next page']",
            "button[aria-label='Next page']",
        ]:
            try:
                el = page.query_selector(arrow_sel)
                if el and el.is_visible():
                    el.click()
                    _jitter(1.5, 3.0)
                    return True
            except Exception:
                continue
        return False


def _jitter(min_s: float = 0.3, max_s: float = 1.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def _resolve_nst_profile(profile: str = "it") -> str:
    suffix = "GENERAL" if profile.strip().lower() == "general" else "IT"
    slot = os.environ.get("NSTBROWSER_ACTIVE_SLOT", "").strip()
    keys = []
    if slot:
        keys.append(f"NSTBROWSER_PROFILE_ID_{slot}_WORKOPOLIS_{suffix}")
    keys.append(f"NSTBROWSER_PROFILE_ID_WORKOPOLIS_{suffix}")
    for key in keys:
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return ""


def _build_nst_cdp_url(profile_id: str) -> str:
    host = os.environ.get("NSTBROWSER_API_HOST", "127.0.0.1").strip()
    port = os.environ.get("NSTBROWSER_API_PORT", "8848").strip()
    api_key = os.environ.get("NSTBROWSER_API_KEY", "").strip()
    return (
        f"ws://{host}:{port}/devtools/browser/{profile_id}"
        f"{'?x-api-key=' + api_key if api_key else ''}"
    )


def _start_nst_browser(profile_id: str) -> str:
    """Start Nstbrowser profile via Local API and return webSocketDebuggerUrl."""
    import requests
    host = os.environ.get("NSTBROWSER_API_HOST", "127.0.0.1").strip()
    port = os.environ.get("NSTBROWSER_API_PORT", "8848").strip()
    api_key = os.environ.get("NSTBROWSER_API_KEY", "").strip()
    
    api_url = f"http://{host}:{port}"
    url = f"{api_url}/api/v2/browsers/{profile_id}"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }
    
    # First check status of running browsers
    try:
        status_resp = requests.get(f"{api_url}/api/v2/browsers", headers=headers, timeout=10)
        if status_resp.ok:
            data = status_resp.json().get("data", [])
            if isinstance(data, list):
                for b in data:
                    if str(b.get("profileId")) == str(profile_id):
                        url_debug = b.get("webSocketDebuggerUrl")
                        if url_debug:
                            return url_debug
    except Exception:
        pass

    # Launch it if not running
    payload = {"headless": False, "autoClose": False}
    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    resp.raise_for_status()
    resp_data = resp.json()
    cdp = resp_data.get("data", {}).get("webSocketDebuggerUrl")
    if not cdp:
        # Fallback to remote debugging port
        port_num = resp_data.get("data", {}).get("remoteDebuggingPort") or resp_data.get("data", {}).get("port")
        if port_num:
            cdp = f"ws://{host}:{port_num}"
    if not cdp:
        raise RuntimeError(f"Nstbrowser started but did not return CDP url: {resp_data}")
    return cdp

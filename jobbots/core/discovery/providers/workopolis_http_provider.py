"""Workopolis HTTP discovery provider — requests + BeautifulSoup (no NST).

Primary lead path for Workopolis. Uses the shared scrape-proxy ladder
(local → webshare → DataImpulse) and browser-like headers so we can scrape
without opening an NST profile.

NST browser fallback is **opt-in only** (``WORKOPOLIS_ALLOW_BROWSER_FALLBACK=1``).
By default, blocked/empty HTTP responses escalate proxies; if all tiers fail
we return whatever jobs we have (or empty) — we do **not** open a browser.

NOTE: Workopolis is an Indeed partner; expect overlap with Indeed. Deduplicator
and Indeed-sync policy handle twins.
"""
from __future__ import annotations

import logging
import os
import random
import re
import time
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.scrape_proxy import build_scrape_proxy_ladder

_log = logging.getLogger("discovery.providers.workopolis_http")

_WORKOPOLIS_SEARCH = "https://www.workopolis.com/search"
_WORKOPOLIS_ORIGIN = "https://www.workopolis.com"

# Rotating modern desktop UAs — Workopolis blocks obvious bare scrapers.
_USER_AGENTS = [
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.5 Safari/605.1.15"
    ),
]


class WorkopolisHTTPIncomplete(Exception):
    """HTTP path exhausted or forced incomplete.

    Planner only invokes NST browser fallback when
    ``WORKOPOLIS_ALLOW_BROWSER_FALLBACK=1``.
    """
    pass


def _browser_headers() -> dict[str, str]:
    ua = random.choice(_USER_AGENTS)
    return {
        "User-Agent": ua,
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;q=0.9,"
            "image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-CA,en-US;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Referer": f"{_WORKOPOLIS_ORIGIN}/",
        "DNT": "1",
        "Connection": "keep-alive",
    }


def _proxies_from_ladder(ladder) -> dict[str, str] | None:
    """Convert JobSpy-style proxy list to requests ``proxies`` dict."""
    urls = ladder.current_proxies()
    if not urls:
        return None
    p = urls[0]
    return {"http": p, "https": p}


class WorkopolisHTTPProvider:
    """Workopolis discovery via HTTP + proxy ladder (no NST)."""

    name = "workopolis_http"
    supported_platforms = ["workopolis"]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        if os.environ.get("FORCE_WORKOPOLIS_FALLBACK") == "1":
            _log.info("FORCE_WORKOPOLIS_FALLBACK=1 — raising WorkopolisHTTPIncomplete")
            raise WorkopolisHTTPIncomplete("Forced browser fallback for testing")

        ladder = build_scrape_proxy_ladder()
        session = requests.Session()
        session.headers.update(_browser_headers())

        # Warm homepage once so cookies land (reduces bare-search 403s).
        self._warm_session(session, ladder)

        all_jobs: list[RawJob] = []
        hard_blocks = 0
        # Cap how long we thrash 403s before browser / giving up. Prod was
        # burning ~15m on 490 consecutive 403 cells with zero jobs.
        try:
            max_hard_blocks = max(
                1, int(os.environ.get("WORKOPOLIS_HTTP_MAX_HARD_BLOCKS") or "4")
            )
        except ValueError:
            max_hard_blocks = 4

        for term in request.search_terms:
            for location in request.locations:
                try:
                    jobs = self._search_term(session, ladder, term, location, request)
                    all_jobs.extend(jobs)
                    ladder.note_success()
                    hard_blocks = 0  # reset streak after a real hit
                    time.sleep(random.uniform(0.4, 1.2))
                except WorkopolisHTTPIncomplete as exc:
                    hard_blocks += 1
                    _log.warning(
                        "Workopolis HTTP hard-block term=%r loc=%r: %s",
                        term, location, exc,
                    )
                    # Escalate proxy and retry this cell once.
                    if ladder.note_failure(str(exc)):
                        try:
                            jobs = self._search_term(
                                session, ladder, term, location, request
                            )
                            all_jobs.extend(jobs)
                            ladder.note_success()
                            hard_blocks = 0
                            continue
                        except Exception as retry_exc:
                            _log.warning("Retry after escalate failed: %s", retry_exc)
                    # Fail fast into NST browser (or empty) instead of N×M 403s.
                    if hard_blocks >= max_hard_blocks:
                        msg = (
                            f"Workopolis HTTP hard-blocked {hard_blocks} cells "
                            f"(cap={max_hard_blocks}); aborting HTTP scrape"
                        )
                        _log.warning(msg)
                        if self._allow_browser_fallback():
                            raise WorkopolisHTTPIncomplete(msg) from exc
                        return all_jobs
                    # Immediate browser handoff when allowed (legacy single-raise).
                    if self._allow_browser_fallback() and hard_blocks >= 2:
                        raise
                except Exception as exc:
                    _log.warning(
                        "Workopolis HTTP failed for %r / %r: %s",
                        term, location, exc,
                    )
                    ladder.note_failure(exc)

        _log.info(
            "Workopolis HTTP total raw jobs: %d (hard_blocks=%d tier=%s)",
            len(all_jobs), hard_blocks, ladder.current_label(),
        )

        # If we got nothing and every path blocked, optionally signal browser.
        if not all_jobs and hard_blocks > 0 and self._allow_browser_fallback():
            raise WorkopolisHTTPIncomplete(
                f"All HTTP tiers blocked ({hard_blocks} hard blocks); "
                "browser fallback allowed"
            )
        return all_jobs

    @staticmethod
    def _allow_browser_fallback() -> bool:
        return str(os.environ.get("WORKOPOLIS_ALLOW_BROWSER_FALLBACK") or "0").strip().lower() in {
            "1", "true", "yes", "on",
        }

    def _warm_session(self, session: requests.Session, ladder) -> None:
        """GET homepage to obtain cookies; try proxy tiers if blocked."""
        for attempt in range(3):
            proxies = _proxies_from_ladder(ladder)
            try:
                resp = session.get(
                    _WORKOPOLIS_ORIGIN + "/",
                    proxies=proxies,
                    timeout=20,
                    allow_redirects=True,
                )
                if resp.status_code in (403, 429):
                    _log.info(
                        "Workopolis warm %s → HTTP %s",
                        ladder.current_label(), resp.status_code,
                    )
                    if not ladder.note_failure(f"warm HTTP {resp.status_code}"):
                        break
                    session.headers.update(_browser_headers())
                    continue
                if resp.ok:
                    _log.info(
                        "Workopolis warm OK tier=%s cookies=%d",
                        ladder.current_label(), len(session.cookies),
                    )
                    ladder.note_success()
                return
            except Exception as exc:
                _log.warning("Workopolis warm failed tier=%s: %s", ladder.current_label(), exc)
                if not ladder.note_failure(exc):
                    return
                session.headers.update(_browser_headers())

    def _search_term(
        self,
        session: requests.Session,
        ladder,
        term: str,
        location: str,
        request: DiscoveryRequest,
    ) -> list[RawJob]:
        jobs: list[RawJob] = []
        max_pages = max(1, request.max_results_per_term // 15)

        for page_num in range(max_pages):
            url = self._build_url(
                term, location, page_num,
                radius_km=request.radius_km,
                remote=request.is_remote_location(location),
            )
            proxies = _proxies_from_ladder(ladder)
            _log.info(
                "Workopolis HTTP [%s]: %s",
                ladder.current_label(), url,
            )

            # Refresh UA occasionally
            if page_num == 0 or random.random() < 0.25:
                session.headers.update(_browser_headers())
            session.headers["Referer"] = (
                f"{_WORKOPOLIS_ORIGIN}/" if page_num == 0 else url
            )
            session.headers["Sec-Fetch-Site"] = "same-origin" if page_num > 0 else "none"

            resp = session.get(url, proxies=proxies, timeout=25, allow_redirects=True)

            if resp.status_code in (403, 429, 503):
                raise WorkopolisHTTPIncomplete(
                    f"HTTP {resp.status_code} from Workopolis"
                )
            resp.raise_for_status()

            text = resp.text or ""
            soup = BeautifulSoup(text, "lxml")
            cards = self._find_cards(soup)

            if not cards and page_num == 0:
                # Soft-block / captcha shell / empty
                if (
                    len(text) < 4000
                    or "captcha" in text.lower()
                    or "access denied" in text.lower()
                    or "just a moment" in text.lower()
                ):
                    raise WorkopolisHTTPIncomplete(
                        "Page looks blocked or JS-shell (no cards)"
                    )
                # Genuine empty result set for this query
                break

            if not cards:
                break

            for card in cards:
                try:
                    raw = self._parse_card(card, term)
                    if not raw:
                        continue
                    if request.easy_apply_only and not (raw.easy_apply_evidence or "").strip():
                        continue
                    jobs.append(raw)
                except Exception as exc:
                    _log.debug("Skipping malformed Workopolis card: %s", exc)

            if not self._has_next_page(soup, page_num):
                break
            time.sleep(random.uniform(0.3, 0.9))

        _log.info(
            "Workopolis HTTP term=%r location=%r → %d jobs (tier=%s)",
            term, location, len(jobs), ladder.current_label(),
        )
        return jobs

    @staticmethod
    def _build_url(
        term: str,
        location: str,
        page_num: int,
        *,
        radius_km: int = 25,
        remote: bool = False,
    ) -> str:
        effective_location = "Remote" if remote else location
        url = (
            f"{_WORKOPOLIS_SEARCH}?q={quote_plus(term)}"
            f"&l={quote_plus(effective_location)}&s=d"
        )
        if not remote and radius_km > 0:
            url += f"&radius={radius_km}"
        if page_num > 0:
            url += f"&start={page_num * 15}"
        return url

    @staticmethod
    def _find_cards(soup: BeautifulSoup) -> list:
        for selector in [
            {"attrs": {"data-testid": "searchSerpJob"}},
            {"attrs": {"data-testid": "expandedSearchTitleCard"}},
            {"class_": "job_seen_beacon"},
            {"attrs": {"data-testid": "jobListing"}},
            {"attrs": {"data-testid": re.compile(r"job", re.I)}},
        ]:
            try:
                cards = soup.find_all("div", **selector)
            except Exception:
                cards = []
            if cards:
                return cards

        cards = soup.find_all(attrs={"data-jk": True})
        if cards:
            return cards

        links = soup.find_all("a", href=re.compile(r"/job/|/joblisting/|/viewjob|jk="))
        if links:
            return [link.parent for link in links if link.parent]

        return []

    @staticmethod
    def _parse_card(card, search_term: str) -> RawJob | None:
        job_id = card.get("data-jk", "") or card.get("data-jobid", "") or ""

        title_el = card.find(
            attrs={"data-testid": re.compile(r"searchSerpJobTitle|expandedSearchCardHeader")}
        ) or card.find(["h2", "h3"])
        title = title_el.get_text(strip=True) if title_el else ""

        link_el = card.find("a", href=re.compile(r"/job/|/joblisting/|/viewjob|jk="))
        href = ""
        if link_el:
            href = link_el.get("href", "") or ""
            if href and not href.startswith("http"):
                href = urljoin(_WORKOPOLIS_ORIGIN, href)

        if not job_id and href:
            parsed = urlparse(href)
            qs = parse_qs(parsed.query)
            job_id = (
                qs.get("jk", [""])[0]
                or qs.get("jobId", [""])[0]
                or parsed.path.rstrip("/").split("/")[-1]
            )

        if not job_id:
            job_id = f"work_{abs(hash(href or title)) & 0xffffffff:08x}"

        comp_el = card.find(
            attrs={"data-testid": re.compile(r"companyName|expandedSearchCardCompanyName")}
        ) or card.find(class_=re.compile(r"companyName|company"))
        company = comp_el.get_text(strip=True) if comp_el else ""

        loc_el = card.find(
            attrs={"data-testid": re.compile(
                r"searchSerpJobLocation|expandedSearchCardJobLocation|company-location"
            )}
        ) or card.find(class_=re.compile(r"companyLocation|location"))
        location = loc_el.get_text(strip=True) if loc_el else ""

        card_text = card.get_text(" ", strip=True).lower()
        has_quick_apply = any(
            badge in card_text
            for badge in ("quick apply", "easy apply", "apply with indeed")
        )
        evidence = "workopolis_quick_apply_badge" if has_quick_apply else ""

        if not title:
            return None

        return RawJob(
            source_platform="workopolis",
            source_job_id=str(job_id),
            title=title,
            company=company,
            location=location,
            description="",
            listing_url=href,
            destination_url=None,
            date_posted=None,
            easy_apply_evidence=evidence,
            raw_extras={"search_term": search_term},
        )

    @staticmethod
    def _has_next_page(soup: BeautifulSoup, current_page: int) -> bool:
        next_num = current_page + 2
        next_btn = soup.find(attrs={"data-testid": f"paginationBlock{next_num}"})
        if next_btn:
            return True
        next_link = soup.find(attrs={"aria-label": re.compile(r"Next|next page", re.I)})
        return next_link is not None

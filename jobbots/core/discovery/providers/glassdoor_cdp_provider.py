"""Glassdoor CDP Discovery Provider — SeleniumBase Pure CDP Mode + Proxy Tunnel.

Bypasses Cloudflare/DataDome using SeleniumBase's Pure CDP Mode, routing through
rotating residential proxies using a background HTTP CONNECT authentication tunnel.
Pagination is done via mathematical URL formatting (appending _IP{page}.htm).
"""
from __future__ import annotations

import base64
import logging
import os
import re
import select
import socket
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlunparse, urlparse, unquote

from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.scrape_proxy import build_scrape_proxy_ladder

_log = logging.getLogger("discovery.providers.glassdoor_cdp")


class ProxyTunnel:
    """Lightweight localhost tunnel to forward authenticated proxy requests.
    
    Avoids Chrome's extension-based proxy authentication errors in headless/CDP modes.
    """
    def __init__(self, upstream_url: str):
        self.upstream_url = upstream_url
        self.local_port = None
        self.server_socket = None
        self._thread = None
        self._stop_event = threading.Event()

    def start(self) -> int:
        parsed = urlparse(self.upstream_url)
        upstream_host = parsed.hostname
        upstream_port = parsed.port or (80 if parsed.scheme == 'http' else 443)
        username = parsed.username
        password = parsed.password

        auth_str = f"{username}:{password}"
        auth_b64 = base64.b64encode(auth_str.encode()).decode()
        auth_header = f"Proxy-Authorization: Basic {auth_b64}"

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind(('127.0.0.1', 0))  # Bind to any free port
        self.local_port = self.server_socket.getsockname()[1]
        self.server_socket.listen(100)

        def handle_client(client_socket):
            try:
                request = b""
                while b"\r\n\r\n" not in request:
                    chunk = client_socket.recv(4096)
                    if not chunk:
                        break
                    request += chunk
                if not request:
                    client_socket.close()
                    return

                first_line = request.split(b"\r\n")[0].decode()
                parts = first_line.split(" ")
                if len(parts) < 2:
                    client_socket.close()
                    return
                method, target = parts[0], parts[1]

                upstream_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                upstream_socket.connect((upstream_host, upstream_port))

                if method == "CONNECT":
                    connect_req = f"CONNECT {target} HTTP/1.1\r\n{auth_header}\r\n\r\n"
                    upstream_socket.sendall(connect_req.encode())
                    
                    resp = b""
                    while b"\r\n\r\n" not in resp:
                        chunk = upstream_socket.recv(4096)
                        if not chunk:
                            break
                        resp += chunk
                    
                    if b"200" not in resp.split(b"\r\n")[0]:
                        client_socket.close()
                        upstream_socket.close()
                        return
                    
                    client_socket.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    # Normal HTTP request
                    headers, body = request.split(b"\r\n\r\n", 1)
                    modified_headers = headers + f"\r\n{auth_header}\r\n\r\n".encode()
                    upstream_socket.sendall(modified_headers + body)

                # Stream bi-directionally
                sockets = [client_socket, upstream_socket]
                while not self._stop_event.is_set():
                    r, w, x = select.select(sockets, [], [], 1.0)
                    if client_socket in r:
                        data = client_socket.recv(4096)
                        if not data:
                            break
                        upstream_socket.sendall(data)
                    if upstream_socket in r:
                        data = upstream_socket.recv(4096)
                        if not data:
                            break
                        client_socket.sendall(data)
            except Exception:
                pass
            finally:
                try: client_socket.close()
                except: pass
                try: upstream_socket.close()
                except: pass

        def run():
            while not self._stop_event.is_set():
                try:
                    self.server_socket.settimeout(1.0)
                    client, addr = self.server_socket.accept()
                    t = threading.Thread(target=handle_client, args=(client,), daemon=True)
                    t.start()
                except socket.timeout:
                    continue
                except Exception:
                    break

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return self.local_port

    def stop(self):
        self._stop_event.set()
        if self.server_socket:
            try:
                self.server_socket.close()
            except Exception:
                pass


def get_page_url(base_url: str, page_num: int) -> str:
    """Return pagination URL for page_num. Page 1 returns the base_url."""
    if page_num <= 1:
        return base_url

    if ".htm" in base_url:
        parts = base_url.split("?", 1)
        path = parts[0]
        query = f"?{parts[1]}" if len(parts) > 1 else ""

        if path.endswith(".htm"):
            new_path = path[:-4] + f"_IP{page_num}.htm"
        else:
            idx = path.rfind(".htm")
            new_path = path[:idx] + f"_IP{page_num}" + path[idx:]
        return new_path + query
    else:
        # Parameter-based pagination
        parsed = urlparse(base_url)
        params = parse_qs(parsed.query)
        params['p'] = [str(page_num)]
        new_query = urlencode(params, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))


def _load_glassdoor_search_urls(profile: str) -> list[str]:
    """Dynamically load search URL templates from profile config if defined."""
    from pathlib import Path
    from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
    import importlib.util

    root = _MONOREPO_ROOT
    config_path = root / "config" / profile.lower() / "glassdoor_search.py"
    if config_path.is_file():
        spec = importlib.util.spec_from_file_location(
            f"config.{profile}.glassdoor_search_custom", str(config_path)
        )
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
            return list(getattr(mod, "glassdoor_search_urls", []))
        except Exception as e:
            _log.warning("Could not load glassdoor_search_urls from %s: %s", config_path, e)
    return []


def _build_search_urls(request: DiscoveryRequest) -> list[tuple[str, str]]:
    """Build (search_term, url) query pairs based on config overrides or fallback."""
    custom_urls = _load_glassdoor_search_urls(request.profile)
    urls = []

    if custom_urls:
        for url_template in custom_urls:
            if "{" in url_template:
                for term in request.search_terms:
                    for location in request.locations:
                        from jobbots.core.discovery.providers.jobspy_provider import normalize_glassdoor_location
                        loc = normalize_glassdoor_location(location)
                        term_slug = term.replace(" ", "+")
                        loc_slug = loc.replace("%20", "+").replace(" ", "+")
                        url = url_template.format(term=term_slug, location=loc_slug, query=term_slug)
                        urls.append((term, url))
            else:
                urls.append(("Custom Query", url_template))
    else:
        # Prefer slug URLs Glassdoor actually geo-scopes (locKeyword alone is ignored).
        from jobbots.core.discovery.providers.jobspy_provider import normalize_glassdoor_location
        from urllib.parse import quote_plus
        # Map freshness_days → Glassdoor fromAge (1=1d, 3=3d, 7=7d, 14=14d, 30=30d).
        # Default 7 so posts 3–4d old (e.g. Human IT) still appear; 3d was too tight.
        fd = request.freshness_days
        if fd is None or fd <= 0:
            from_age = 30  # broadest practical preset when "all dates"
        elif fd <= 1:
            from_age = 1
        elif fd <= 3:
            from_age = 3
        elif fd <= 7:
            from_age = 7
        elif fd <= 14:
            from_age = 14
        else:
            from_age = 30
        for term in request.search_terms:
            for location in request.locations:
                loc = normalize_glassdoor_location(location)
                loc_plain = loc.replace("%20", " ").replace("+", " ").strip()
                term_slug = re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-")
                loc_slug = re.sub(r"[^a-z0-9]+", "-", loc_plain.lower()).strip("-")
                # Force www (not fr.) + radius so SERPs stay local; national
                # Easy Apply dumps (Montreal/Toronto/Cheyenne) were ~90% of raw
                # volume and all rejected as outside_metro.
                url = (
                    f"https://www.glassdoor.ca/Job/{loc_slug}-{term_slug}-jobs-SRCH.htm"
                    f"?sc.keyword={quote_plus(term)}&locKeyword={quote_plus(loc_plain)}"
                    f"&fromAge={from_age}&applicationType=1"
                    f"&radius=25&locT=C"
                )
                urls.append((term, url))
    return urls


def _glassdoor_html_looks_blocked(html_content: str) -> str:
    """Return a short reason when the HTML is a challenge / empty shell, else ''."""
    text = (html_content or "")[:120_000].lower()
    if not text or len(text) < 800:
        return "tiny_html"
    markers = (
        ("cf-browser-verification", "cloudflare"),
        ("cdn-cgi/challenge", "cloudflare"),
        ("just a moment", "cloudflare"),
        ("attention required", "cloudflare"),
        ("captcha", "captcha"),
        ("datadome", "datadome"),
        ("access denied", "access_denied"),
        ("unusual traffic", "bot_check"),
        ("enable javascript", "js_required"),
        ("gd-blocked", "gd_blocked"),
    )
    for needle, label in markers:
        if needle in text:
            return label
    # Real SERPs always mention job listing markers; marketing shells often don't.
    if "joblisting" not in text and "job-listing" not in text and "data-test=" not in text:
        if "glassdoor" in text and "sign in" in text:
            return "signin_shell"
    return ""


def extract_jobs_from_html(html_content: str, search_term: str) -> list[RawJob]:
    """Parse raw HTML with BeautifulSoup and extract matching job list items."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html_content, "lxml")

    cards = []
    for sel in ["li[data-test='jobListing']", "li[data-test='jobListing'][data-jobid]", "[data-test='job-card-wrapper']"]:
        cards = soup.select(sel)
        if cards:
            break

    raw_jobs = []
    for card in cards:
        job_id = card.get("data-jobid") or ""
        title = "Unknown"
        job_href = ""
        title_el = None

        for sel in ["a[data-test='job-title']", "[data-test='jobTitle'] a", "a[data-test='job-link']", "h2 a", "h3 a"]:
            title_el = card.select_one(sel)
            if title_el:
                break
        if title_el:
            title = title_el.get_text(strip=True)
            href = title_el.get("href") or ""
            if href:
                if href.startswith("/"):
                    job_href = f"https://www.glassdoor.ca{href}"
                else:
                    job_href = href

        company = "Unknown"
        for sel in ["[data-test='employer-name']", "[class*='compactEmployerName']", "[class*='EmployerProfile'] span"]:
            comp_el = card.select_one(sel)
            if comp_el:
                company = comp_el.get_text(strip=True)
                # Strip rating at end (e.g. "Google\n4.5")
                company = re.sub(r'\s*\d+\.\d+\s*$', '', company).strip()
                break

        location = "Unknown"
        for sel in ["[data-test='emp-location']", "[class*='location']", "[class*='Location']"]:
            loc_el = card.select_one(sel)
            if loc_el:
                location = loc_el.get_text(strip=True)
                break

        has_easy_apply = False
        # Badge variants Glassdoor uses on CA cards
        for sel in (
            "[aria-label='Easy Apply']",
            "[data-test='easy-apply']",
            "[class*='EasyApply']",
            "[class*='easyApply']",
            "span[class*='easy-apply']",
        ):
            if card.select_one(sel):
                has_easy_apply = True
                break
        full_text = card.get_text(" ", strip=True).lower()
        if not has_easy_apply:
            if "easy apply" in full_text or "easyapply" in full_text.replace(" ", ""):
                has_easy_apply = True
        # Explicit company-site CTA wins over fuzzy text (false EA from nearby cards/SERP noise).
        company_site_markers = (
            "apply on the employer's website",
            "apply on the employer’s website",  # curly apostrophe
            "apply on employer website",
            "apply on company website",
            "employer's site",
            "employer’s site",
        )
        if any(m in full_text for m in company_site_markers):
            has_easy_apply = False

        if not job_id and job_href:
            match = re.search(r'jl=(\d+)', job_href)
            if match:
                job_id = match.group(1)

        if not job_href and job_id:
            job_href = f"https://www.glassdoor.ca/job-listing/-JV.htm?jl={job_id}"

        if not job_id:
            continue

        # Must match apply_type._EASY_APPLY_EVIDENCE_TAGS or Metro EA is rejected
        # as glassdoor_non_easy_apply (UNKNOWN apply type).
        ea_evidence = "glassdoor_easy_apply_badge" if has_easy_apply else ""

        raw_jobs.append(RawJob(
            source_platform="glassdoor",
            source_job_id=job_id,
            title=title,
            company=company,
            location=location,
            description="",
            listing_url=job_href,
            destination_url=None,
            date_posted=None,
            easy_apply_evidence=ea_evidence,
            is_remote=None,
            raw_extras={
                "search_term": search_term,
                "site": "glassdoor",
                "has_easy_apply": has_easy_apply,
                "company_site_cta": (not has_easy_apply and any(m in full_text for m in company_site_markers)),
            }
        ))
    return raw_jobs


class GlassdoorCDPProvider:
    """Glassdoor stealth scraper using SeleniumBase Pure CDP Mode."""

    name = "glassdoor_cdp"
    supported_platforms = ["glassdoor"]

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        """Iterate search queries, load pages, and parse job cards via CDP browser."""
        from seleniumbase import sb_cdp

        ladder = build_scrape_proxy_ladder()
        queries = _build_search_urls(request)
        max_pages = max(1, (request.max_results_per_term + 29) // 30)
        pause = float(os.getenv("GLASSDOOR_REQUEST_PAUSE_SECONDS", "2.0") or 0)

        _log.info(
            "Starting Glassdoor CDP scrape: %d query combinations, max %d pages per query.",
            len(queries), max_pages
        )

        all_jobs: list[RawJob] = []
        # Prefer one browser for the whole run (reuse). Chunking only restarts
        # on hard driver failure. Legacy 20-query restart: GLASSDOOR_CDP_CHUNK=20
        try:
            chunk_size = int(os.getenv("GLASSDOOR_CDP_CHUNK", "0") or "0")
        except ValueError:
            chunk_size = 0
        if chunk_size <= 0:
            query_chunks = [queries]
        else:
            query_chunks = [queries[i : i + chunk_size] for i in range(0, len(queries), chunk_size)]

        for chunk_idx, chunk in enumerate(query_chunks):
            _log.info("Processing query chunk %d/%d (%d queries)", chunk_idx + 1, len(query_chunks), len(chunk))

            proxies = ladder.current_proxies()
            proxy_url = proxies[0] if proxies else None

            tunnel = None
            driver_proxy = None

            if proxy_url:
                parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
                host = parsed.hostname or ""
                port = parsed.port or 80
                user = unquote(parsed.username or "")
                password = unquote(parsed.password or "")
                # SeleniumBase Pure CDP expects ``user:pass@host:port`` (or
                # ``host:port``). A localhost CONNECT tunnel as ``127.0.0.1:N``
                # triggers UnboundLocalError(proxy_string) in seleniumbase's
                # cdp_util on this version — prefer native auth format.
                if user and password and host:
                    driver_proxy = f"{user}:{password}@{host}:{port}"
                    _log.info(
                        "Glassdoor CDP proxy=native-auth host=%s:%s (Chunk %d/%d)",
                        host, port, chunk_idx + 1, len(query_chunks),
                    )
                elif host:
                    driver_proxy = f"{host}:{port}"
                    _log.info(
                        "Glassdoor CDP proxy=host-only %s (Chunk %d/%d)",
                        driver_proxy, chunk_idx + 1, len(query_chunks),
                    )

            driver = None
            try:
                # Initialize Chrome Pure CDP Mode once per chunk (default: whole run)
                _log.info(
                    "Initializing sb_cdp.Chrome with proxy=%s (Chunk %d/%d)",
                    (driver_proxy.split("@")[-1] if driver_proxy and "@" in driver_proxy else driver_proxy),
                    chunk_idx + 1,
                    len(query_chunks),
                )
                driver = sb_cdp.Chrome(headless=True, proxy=driver_proxy)
                
                for term, base_url in chunk:
                    for page_num in range(1, max_pages + 1):
                        target_url = get_page_url(base_url, page_num)
                        _log.info("CDP loading: %s", target_url)

                        try:
                            driver.goto(target_url)

                            # Wait for cards; SPA SERPs often need >2s under proxy.
                            settle = max(3.0, float(os.environ.get("GLASSDOOR_CDP_SETTLE_SECONDS") or "4.5"))
                            time.sleep(settle)

                            html_content = driver.get_page_source()
                            jobs = extract_jobs_from_html(html_content, term)

                            if not jobs:
                                blocked = _glassdoor_html_looks_blocked(html_content)
                                if blocked:
                                    _log.warning(
                                        "Glassdoor empty page looks blocked (%s) for %r — escalating proxy",
                                        blocked, term,
                                    )
                                    if ladder.note_failure(f"glassdoor_empty_blocked:{blocked}"):
                                        # Rebuild driver with next ladder tier on next chunk;
                                        # one retry of this URL after longer wait.
                                        time.sleep(settle + 2.0)
                                        try:
                                            driver.goto(target_url)
                                            time.sleep(settle + 1.5)
                                            jobs = extract_jobs_from_html(driver.get_page_source(), term)
                                        except Exception as retry_exc:
                                            _log.warning("Glassdoor blocked-page retry failed: %s", retry_exc)
                                            jobs = []
                                else:
                                    # Soft empty: one longer settle retry (not always end-of-list).
                                    time.sleep(settle + 1.5)
                                    try:
                                        html_content = driver.get_page_source()
                                        jobs = extract_jobs_from_html(html_content, term)
                                    except Exception:
                                        jobs = []

                            if not jobs:
                                _log.info(
                                    "No job cards found on page %d for term %r. Ending query loop.",
                                    page_num, term,
                                )
                                break

                            all_jobs.extend(jobs)
                            _log.info("Extracted %d jobs from page %d", len(jobs), page_num)

                            if len(jobs) < 15:
                                # Glassdoor page holds 30 jobs; less than 15 means it's likely the last page or limited results
                                _log.info("Low card count (%d) indicating end of list.", len(jobs))
                                break

                        except Exception as e:
                            _log.warning("CDP navigate/parse failed for %s (page %d): %s", target_url, page_num, e)
                            break

                        if pause > 0:
                            time.sleep(pause)

                ladder.note_success()

            except Exception as exc:
                _log.error("Glassdoor CDP Driver failed for chunk %d: %s", chunk_idx + 1, exc)
                # One retry via localhost CONNECT tunnel if native auth blew up.
                if proxy_url and "proxy_string" in str(exc):
                    try:
                        tunnel = ProxyTunnel(proxy_url)
                        tport = tunnel.start()
                        fallback = f"http://127.0.0.1:{tport}"
                        _log.info(
                            "Retrying Glassdoor CDP with CONNECT tunnel %s after: %s",
                            fallback, exc,
                        )
                        driver = sb_cdp.Chrome(headless=True, proxy=fallback)
                        # Minimal smoke: re-run this chunk's queries on tunnel path
                        for term, base_url in chunk:
                            for page_num in range(1, max_pages + 1):
                                target_url = get_page_url(base_url, page_num)
                                try:
                                    driver.goto(target_url)
                                    time.sleep(2.0)
                                    jobs = extract_jobs_from_html(driver.get_page_source(), term)
                                    if not jobs:
                                        break
                                    all_jobs.extend(jobs)
                                    if len(jobs) < 15:
                                        break
                                except Exception as nav_exc:
                                    _log.warning("Tunnel CDP navigate failed: %s", nav_exc)
                                    break
                                if pause > 0:
                                    time.sleep(pause)
                        ladder.note_success()
                        continue
                    except Exception as retry_exc:
                        _log.error("Glassdoor tunnel retry also failed: %s", retry_exc)
                ladder.note_failure(exc)
            finally:
                if driver:
                    try:
                        driver.quit()
                    except Exception:
                        pass
                if tunnel:
                    try:
                        tunnel.stop()
                        _log.info("Stopped local proxy auth tunnel cleanly.")
                    except Exception:
                        pass

        # De-duplicate raw jobs by ID to be defensive
        seen_ids = set()
        unique_jobs = []
        for job in all_jobs:
            if job.source_job_id not in seen_ids:
                seen_ids.add(job.source_job_id)
                unique_jobs.append(job)

        _log.info("Glassdoor CDP total unique raw jobs: %d", len(unique_jobs))
        return unique_jobs


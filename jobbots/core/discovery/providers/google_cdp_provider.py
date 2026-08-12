"""Google CDP discovery — browser replacement for broken JobSpy Google.

JobSpy's HTTP Google scraper gets an enable-JS shell / captcha and returns 0
jobs.  This provider launches Chromium (Playwright) through the same
authenticated proxy tunnel used by Glassdoor CDP, scrapes Google, and on
``/sorry`` / reCAPTCHA:

  1. Tries CapMonster reCAPTCHA (with ``CAPMONSTER_PROXY_URL`` aligned to
     the browser egress proxy)
  2. Escalates the scrape proxy ladder and retries

Modes (``GOOGLE_CDP_MODE`` or constructor ``mode``):
  ``web``  — Google web ``site:boards.greenhouse.io|jobs.lever.co`` search
             (default; yields direct Greenhouse/Lever apply URLs)
  ``jobs`` — Google Jobs widget (``udm=8``); clicks cards and keeps only
             Greenhouse/Lever apply destinations
  ``both`` — run web then jobs
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import quote_plus, unquote, urlparse

from jobbots.core.discovery.contracts import RawJob
from jobbots.core.discovery.providers.base import DiscoveryRequest
from jobbots.core.discovery.providers.glassdoor_cdp_provider import ProxyTunnel
from jobbots.core.discovery.scrape_proxy import ScrapeProxyLadder, build_scrape_proxy_ladder

_log = logging.getLogger("discovery.providers.google_cdp")

_ATS_HOST_RE = re.compile(
    r"(?:^|\.)(?:"
    r"boards\.greenhouse\.io|"
    r"job-boards\.greenhouse\.io|"
    r"greenhouse\.io|"
    r"grnh\.se|"
    r"gh\.io|"
    r"jobs\.lever\.co|"
    r"lever\.co|"
    r"jobs\.ashbyhq\.com|"
    r"ashbyhq\.com|"
    r"bamboohr\.com"
    r")(?:/|$)",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_SORRY_RE = re.compile(
    r"(/sorry|unusual traffic|enablejs|detected unusual)",
    re.IGNORECASE,
)


def is_greenhouse_or_lever(url: str | None) -> bool:
    if not url:
        return False
    try:
        host = (urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return bool(_ATS_HOST_RE.search(host + "/"))
    except Exception:
        return False


def is_supported_ats_url(url: str | None) -> bool:
    """Alias for is_greenhouse_or_lever for platform parity."""
    return is_greenhouse_or_lever(url)


def canonicalize_ats_url(url: str) -> str:
    raw = (url or "").strip()
    raw = raw.replace("&amp;", "&").replace("&amp", "")
    
    # Unwrap Google redirect URLs
    if "google." in raw and "/url?" in raw:
        from urllib.parse import parse_qs, urlparse as _urlparse
        try:
            parsed = parse_qs(_urlparse(raw).query)
            if "q" in parsed:
                raw = parsed["q"][0]
            elif "url" in parsed:
                raw = parsed["url"][0]
        except Exception:
            pass

    if "%23" in raw or "%3A" in raw:
        from urllib.parse import unquote as _unquote
        raw = _unquote(raw)
    raw = raw.split("#", 1)[0].split("&", 1)[0].strip()
    from urllib.parse import urlparse as _urlparse_final
    p = _urlparse_final(raw)
    host = (p.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = (p.path or "").rstrip("/")
    if not host or path in {"", "/"}:
        return ""
    # Require a real job path (not bare boards)
    if host.endswith("greenhouse.io") and "/jobs/" not in path:
        return ""
    if host.endswith("lever.co") and path.count("/") < 2:
        return ""
    if host.endswith("ashbyhq.com") and path.count("/") < 2:
        return ""
    if host.endswith("bamboohr.com") and not any(k in path for k in ("/careers/", "/jobs/")):
        return ""
    return f"https://{host}{path}"


def extract_ats_urls(*blobs: str | None) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for blob in blobs:
        if not blob:
            continue
        candidates = _URL_RE.findall(blob)
        for host in (
            "boards.greenhouse.io",
            "job-boards.greenhouse.io",
            "jobs.lever.co",
            "jobs.ashbyhq.com",
            "bamboohr.com",
        ):
            for m in re.finditer(
                rf"https?%3A%2F%2F[^\"'\s]*{re.escape(host)}[^\"'\s]*",
                blob,
                re.I,
            ):
                candidates.append(unquote(m.group(0)))
        for raw in candidates:
            cleaned = raw.rstrip(").,;]")
            if "google." in (urlparse(cleaned).hostname or ""):
                continue
            if not is_greenhouse_or_lever(cleaned):
                continue
            key = canonicalize_ats_url(cleaned)
            if key and key not in seen:
                seen.add(key)
                found.append(key)
    return found


# Metro Van / Canada-first Google query. Bare ``OR Remote`` pulls US senior SWE.
_METRO_VAN_QUERY_LOCATIONS = (
    '"Vancouver, BC"',
    '"Burnaby, BC"',
    '"Surrey, BC"',
    '"Richmond, BC"',
    '"North Vancouver"',
    '"New Westminster, BC"',
    '"Coquitlam, BC"',
    '"Greater Vancouver"',
    '"Lower Mainland"',
)
_US_NEGATIVES = (
    '-"United States"',
    "-USA",
    '-"San Francisco"',
    '-"New York"',
    '-"Seattle, WA"',
    '-"Vancouver, WA"',
    '-"Austin, TX"',
    '-"Remote, US"',
)
_SENIOR_SWE_TITLE_RE = re.compile(
    r"\b(?:senior|sr\.?|staff|principal|principle|lead|director|head|manager|supervisor|chief|architect|founding|distinguished|executive)\b."
    r"{0,40}\b(?:software|full[- ]?stack|backend|frontend|front[- ]?end|platform|systems?|network|security|qa|data|devops|it|cloud|infrastructure|support)\b|"
    r"\b(?:software|full[- ]?stack|backend|frontend|front[- ]?end|platform|systems?|network|security|qa|data|devops|it|cloud|infrastructure|support)\b."
    r"{0,40}\b(?:senior|sr\.?|staff|principal|principle|lead|director|head|manager|supervisor|chief|architect|founding|distinguished|executive)\b",
    re.IGNORECASE,
)
_SWE_SEARCH_TERM_RE = re.compile(
    r"\b(?:software engineer|software developer|full[- ]?stack|"
    r"backend developer|frontend developer|front[- ]?end developer|"
    r"python developer|react developer|java developer|"
    r"web developer|mobile developer|devops engineer)\b",
    re.IGNORECASE,
)
_IT_ROLE_HINT_RE = re.compile(
    r"\b(?:qa|quality assurance|test(?:er|ing)?|sdet|uat|"
    r"it support|help desk|service desk|desktop support|"
    r"technical support|"
    r"systems?\s+admin(?:istrator)?|sysadmin|"
    r"network|noc|"
    r"data analyst|soc analyst|security analyst|"
    r"support (?:analyst|specialist|technician|engineer))\b",
    re.IGNORECASE,
)
_US_ONLY_GEO_RE = re.compile(
    r"\b(?:united states|,?\s*usa\b|,?\s*u\.s\.a?\b|"
    r"remote\s*[-–]?\s*u\.?s\.?\b|,\s*us\b|"
    r"san francisco|new york(?:\s*city)?|nyc\b|seattle|austin|"
    r"vancouver,\s*wa|remote[- ]only[, ]*\s*us|"
    r"boston|chicago|los angeles|denver|atlanta)\b",
    re.IGNORECASE,
)
_OFF_METRO_GEO_RE = re.compile(
    r"\b(?:"
    r"sydney|melbourne|brisbane|perth|auckland|dublin|"
    r"london|manchester|berlin|paris|amsterdam|"
    r"singapore|hong kong|tokyo|bangalore|bengaluru|hyderabad|"
    r"toronto only|montreal only|ottawa only"
    r")\b",
    re.IGNORECASE,
)
_CANADA_OR_METRO_RE = re.compile(
    r"\b(?:canada|,?\s*bc\b|british columbia|"
    r"vancouver|burnaby|surrey|richmond|coquitlam|"
    r"north vancouver|new westminster|langley|delta|"
    r"lower mainland|greater vancouver|metro vancouver)\b",
    re.IGNORECASE,
)


_ATS_SITE_CLAUSE = (
    "(site:boards.greenhouse.io OR site:job-boards.greenhouse.io "
    "OR site:jobs.lever.co OR site:jobs.ashbyhq.com OR site:bamboohr.com)"
)


def _quote_loc(loc: str) -> str:
    s = (loc or "").strip().strip('"')
    if not s:
        return '"Vancouver, BC"'
    return f'"{s}"'


def build_google_web_ats_query(term: str, location: str | None = None) -> str:
    """Build a Metro-Van / Canada Greenhouse+Lever dork for Google / Tavily.

    Design goals:
      * Always include modern ``job-boards.greenhouse.io`` (many boards moved).
      * Expand one anchor city into a short metro pack (not bare ``OR Remote``).
      * Keep Canada positive + US city negatives (tests lock this contract).
      * One query covers the region so callers need not fan out 8 cities × terms.
    """
    term = (term or "").strip() or "IT Support"
    anchor = _quote_loc(location or "Vancouver, BC")
    # Prefer anchor first, then remaining metro pack (deduped), then Canada.
    loc_parts: list[str] = []
    seen_loc: set[str] = set()
    for part in (anchor, *_METRO_VAN_QUERY_LOCATIONS, '"Canada"'):
        key = part.lower()
        if key in seen_loc:
            continue
        seen_loc.add(key)
        loc_parts.append(part)
        if len(loc_parts) >= 8:  # keep query under ~Google/Tavily soft limits
            break
    if '"Canada"' not in loc_parts and "canada" not in seen_loc:
        loc_parts.append('"Canada"')
    loc_clause = " OR ".join(loc_parts)
    negatives = " ".join(_US_NEGATIVES)
    # Intentionally no bare ``OR Remote`` — that floods US senior SWE.
    return f"{term} {_ATS_SITE_CLAUSE} ({loc_clause}) {negatives}".strip()


def build_ats_query_variants(term: str, location: str | None = None) -> list[str]:
    """High-yield query pack for one term (primary metro dork + Canada remote).

    Used by Tavily/Firecrawl to get more ATS URLs without city×term explosion.
    """
    primary = build_google_web_ats_query(term, location)
    # Explicit Canada-remote GH/Lever postings (still site-restricted).
    remote_ca = (
        f"{(term or '').strip() or 'IT Support'} {_ATS_SITE_CLAUSE} "
        f'("Remote, Canada" OR "Canada Remote" OR "Remote - Canada" '
        f'OR "Remote Canada") {" ".join(_US_NEGATIVES)}'
    )
    # Board host only (some indexes ignore multi-site OR).
    job_boards_only = (
        f'{(term or "").strip() or "IT Support"} '
        f'(site:job-boards.greenhouse.io) '
        f'({_quote_loc(location or "Vancouver, BC")} OR "Burnaby, BC" OR "Canada") '
        f'{" ".join(_US_NEGATIVES[:4])}'
    )
    out: list[str] = []
    for q in (primary, remote_ca, job_boards_only):
        q = " ".join(q.split())
        if q and q not in out:
            out.append(q)
    return out


def serp_passes_metro_van_canada(*, title: str, snippet: str = "") -> bool:
    """Keep SERP hits that look Metro Van / Canada; drop clear US/foreign listings.

    Search dorks already constrain geography. SERP titles/snippets often omit
    the city even for real Vancouver postings — only reject *positive* foreign/US
    signals without a Canada/metro counter-signal. Downstream Phase-I geo policy
    still decides work-mode / metro eligibility.
    """
    text = f"{title or ''} {snippet or ''}".strip()
    if not text:
        # Empty title+snippet: keep URL for downstream (canonicalize already OK).
        return True
    # Explicit non-Metro foreign cities (Sydney, London, Dublin…) — reject even if dork said Van.
    if re.search(
        r"\b(sydney|melbourne|dublin|london|singapore|tokyo|bangalore|bengaluru)\b",
        title or "",
        re.I,
    ):
        return False
    if _OFF_METRO_GEO_RE.search(text) and re.search(
        r"\b(?:in|at|@)\s+(?:sydney|melbourne|dublin|london|singapore|tokyo)\b",
        text,
        re.I,
    ):
        return False
    if _OFF_METRO_GEO_RE.search(text) and not _CANADA_OR_METRO_RE.search(text):
        return False
    if _US_ONLY_GEO_RE.search(text) and not _CANADA_OR_METRO_RE.search(text):
        return False
    # No positive Canada/metro token: still keep. Dork already asked for Van/Canada;
    # rejecting here was discarding most real GH/Lever hits with bare titles.
    return True


_NON_IT_TITLE_RE = re.compile(
    r"\b(?:"
    r"product manager|product owner|program manager|"
    r"account executive|account manager|sales(?:person| rep)?|"
    r"recruiter|talent acquisition|marketing manager|"
    r"content writer|copywriter|brand manager|"
    r"registered nurse|attorney|counsel|pharmacist"
    r")\b",
    re.IGNORECASE,
)


def serp_title_matches_search_intent(*, title: str, search_term: str) -> bool:
    """Drop senior SWE / non-IT noise when the search term is IT/QA/support."""
    title = (title or "").strip()
    term = (search_term or "").strip()
    if not title:
        return True  # unknown; keep for downstream policy
    term_is_swe = bool(_SWE_SEARCH_TERM_RE.search(term)) and not _IT_ROLE_HINT_RE.search(term)
    if term_is_swe:
        return True
    # IT/QA/support searches: reject senior software eng / platform SWE.
    if _SENIOR_SWE_TITLE_RE.search(title):
        return False
    # Also reject generic SWE when term is clearly IT/QA (keep Software Test Engineer).
    if (
        _IT_ROLE_HINT_RE.search(term)
        and not _IT_ROLE_HINT_RE.search(title)
        and re.search(
            r"\b(?:software|full[- ]?stack|backend|frontend)\b.{0,12}"
            r"\b(?:engineer|developer)\b",
            title,
            re.I,
        )
    ):
        return False
    # Reject obvious non-IT disciplines when searching IT/QA/support terms.
    if _IT_ROLE_HINT_RE.search(term) and _NON_IT_TITLE_RE.search(title):
        return False
    return True


def extract_serp_ats_hits(page) -> list[dict[str, str]]:
    """Pull title/snippet/url triples from a Google web SERP (ATS links only)."""
    rows = page.evaluate(
        """() => {
          const out = [];
          const seen = new Set();
          const push = (title, snippet, rawHref) => {
            if (!rawHref) return;
            const href = decodeURIComponent(rawHref);
            if (seen.has(href)) return;
            if (!/boards\\.greenhouse\\.io|job-boards\\.greenhouse\\.io|jobs\\.lever\\.co/i.test(href)) return;
            seen.add(href);
            out.push({
              title: (title || '').trim().slice(0, 200),
              snippet: (snippet || '').trim().slice(0, 400),
              href: href,
            });
          };
          const anchors = document.querySelectorAll('a[href]');
          for (const a of anchors) {
            const rawHref = a.href || a.getAttribute('href') || '';
            if (!/greenhouse\\.io|lever\\.co/i.test(decodeURIComponent(rawHref))) continue;
            const block = a.closest('.g, [data-sokoban-container], div') || a.parentElement;
            const h3 = block ? block.querySelector('h3') : null;
            const sn = block ? block.querySelector('[data-sncf], .VwiC3b, .IsZvec, span[style*="webkit-line"]') : null;
            push(h3 ? h3.innerText : a.innerText, sn ? sn.innerText : (block ? block.innerText.slice(0, 400) : ''), rawHref);
          }
          return out;
        }"""
    ) or []
    cleaned: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in rows:
        apply_url = canonicalize_ats_url(row.get("href") or "")
        if not apply_url or apply_url in seen:
            continue
        seen.add(apply_url)
        cleaned.append({
            "title": (row.get("title") or "").strip(),
            "snippet": (row.get("snippet") or "").strip(),
            "apply_url": apply_url,
        })
    return cleaned


def _truthy(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "y"}


def _page_looks_blocked(page) -> bool:
    try:
        url = (page.url or "").lower()
        if "/sorry" in url or "enablejs" in url:
            return True
        html = page.content()
        head = html[:15000]
        # JS shell with no real results
        if "enablejs" in head.lower() and "AF_initDataCallback" not in html and not extract_ats_urls(html):
            return True
        if "unusual traffic" in head.lower():
            return True
        return False
    except Exception:
        return False


def _extract_sorry_continue_url(page) -> str | None:
    try:
        return page.evaluate(
            """() => {
              const u = new URL(location.href);
              const cont = u.searchParams.get('continue');
              if (cont) return cont;
              const input = document.querySelector('input[name="continue"]');
              if (input && input.value) return input.value;
              const a = document.querySelector('a[href*="continue="]');
              if (a) {
                try { return new URL(a.href).searchParams.get('continue'); } catch (e) {}
              }
              return null;
            }"""
        )
    except Exception:
        return None


def _align_capmonster_proxy(proxy_url: str | None) -> None:
    """Keep CapMonster solve IP aligned with browser egress proxy.

    Busts ``secret_manager`` cache so CapMonster does not keep using the
    stale Infisical/``.env`` ``CAPMONSTER_PROXY_URL`` while the browser is on
    a different scrape-ladder tier.
    """
    if not proxy_url:
        return
    os.environ["CAPMONSTER_PROXY_URL"] = proxy_url
    os.environ["PROXY_URL"] = proxy_url
    try:
        from jobbots.core import secret_manager as sm
        sm._secrets_cache.pop("CAPMONSTER_PROXY_URL", None)
        sm._secrets_cache.pop("PROXY_URL", None)
        if hasattr(sm, "_local_env"):
            sm._local_env["CAPMONSTER_PROXY_URL"] = proxy_url
            sm._local_env["PROXY_URL"] = proxy_url
        from jobbots.core.secret_manager import align_capmonster_proxy_env
        align_capmonster_proxy_env()
    except Exception as exc:
        _log.debug("align_capmonster_proxy_env skipped: %s", exc)


def _sticky_dataimpulse_proxy(proxy_url: str) -> str:
    """Apply a sticky session so browser egress and CapMonster share one IP.

    - DataImpulse: username ``user__session`` / ``user;session``
    - Proxy-Cheap (and similar rotating residential): inject
      ``_session-<id>`` into the password before ``_country-…`` so CapMonster
      and Chromium land on the same exit IP for the captcha lifetime.
    """
    try:
        parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
        host = (parsed.hostname or "").lower()
        user = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if not user:
            return proxy_url

        from urllib.parse import quote

        if "dataimpulse" in host:
            from jobbots.core.evasion._capmonster import _dataimpulse_proxy_username
            sticky_user = _dataimpulse_proxy_username(user, host)
            auth = quote(sticky_user, safe="._~-;")
            if password:
                auth = f"{auth}:{quote(password, safe='')}"
            return (
                f"{parsed.scheme or 'http'}://{auth}@{parsed.hostname}:{parsed.port or 823}"
            )

        # Proxy-Cheap / thehub rotating residential sticky password.
        if "proxy-cheap" in host or "thehub.proxy" in host or os.getenv(
            "GOOGLE_CDP_STICKY_SESSION", ""
        ).strip():
            session = (
                os.getenv("GOOGLE_CDP_STICKY_SESSION", "").strip()
                or os.getenv("CAPMONSTER_DATAIMPULSE_STICKY_SESSION", "").strip()
                or "jobbots-google-ats"
            )
            session = re.sub(r"[^A-Za-z0-9_-]", "", session)[:48] or "jobbots-google"
            if password and f"_session-{session}" not in password and "_session-" not in password:
                # password forms we support:
                #   secret_country-CA  →  secret_session-XYZ_country-CA
                #   secret            →  secret_session-XYZ
                if "_country-" in password:
                    base, country = password.split("_country-", 1)
                    password = f"{base}_session-{session}_country-{country}"
                else:
                    password = f"{password}_session-{session}"
                auth = f"{quote(user, safe='._~-')}:{quote(password, safe='._~-')}"
                return (
                    f"{parsed.scheme or 'http'}://{auth}"
                    f"@{parsed.hostname}:{parsed.port or 8080}"
                )
        return proxy_url
    except Exception as exc:
        _log.debug("sticky proxy rewrite skipped: %s", exc)
        return proxy_url


def _ensure_capmonster_enabled() -> None:
    if not os.getenv("USE_CAPMONSTER_CAPTCHA_SOLVER") and not os.getenv("USE_CAPMONSTER"):
        os.environ.setdefault("USE_CAPMONSTER_CAPTCHA_SOLVER", "1")
        os.environ.setdefault("USE_CAPMONSTER", "1")


def _try_solve_google_captcha(page) -> bool:
    """Solve Google /sorry reCAPTCHA via CapMonster when present."""
    _ensure_capmonster_enabled()
    try:
        from jobbots.core.evasion._capmonster import solve_recaptcha_with_capmonster
        from jobbots.core.evasion._detection import (
            is_recaptcha_challenge,
            is_recaptcha_widget_present,
        )
    except Exception as exc:
        _log.warning("CapMonster imports unavailable: %s", exc)
        return False

    try:
        has_widget = is_recaptcha_widget_present(page) or is_recaptcha_challenge(page)
    except Exception:
        has_widget = True  # /sorry almost always has reCAPTCHA

    if not has_widget and "/sorry" not in (page.url or "").lower():
        return False

    continue_url = _extract_sorry_continue_url(page)
    _log.info(
        "Google captcha detected — CapMonster (url=%s continue=%s)",
        (page.url or "")[:120],
        (continue_url or "")[:120],
    )
    ok = bool(solve_recaptcha_with_capmonster(page))
    if not ok:
        _log.warning("CapMonster did not clear Google captcha")
        return False

    # CapMonster accepts the token; leave /sorry via form navigation.
    try:
        with page.expect_navigation(timeout=20000, wait_until="domcontentloaded"):
            clicked = page.evaluate(
                """() => {
                  const btn = document.querySelector(
                    'input[type="submit"], button[type="submit"], button#submit, input#submit'
                  );
                  if (btn) { btn.click(); return 'btn'; }
                  const form = document.querySelector(
                    'form#captcha-form, form[action*="sorry"], form'
                  );
                  if (form) { form.submit(); return 'form'; }
                  return null;
                }"""
            )
            if not clicked:
                raise RuntimeError("no sorry submit control")
        page.wait_for_timeout(1500)
    except Exception as exc:
        _log.debug("sorry submit+nav failed (%s); trying continue URL", exc)
        if continue_url:
            try:
                page.goto(continue_url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(2500)
            except Exception as exc2:
                _log.warning("Failed to follow sorry continue URL: %s", exc2)

    if "/sorry" in (page.url or "").lower() and continue_url:
        try:
            page.goto(continue_url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
        except Exception as exc:
            _log.warning("Second continue navigation failed: %s", exc)

    still = _page_looks_blocked(page)
    _log.info(
        "CapMonster Google captcha result: cleared=%s url=%s",
        not still,
        (page.url or "")[:160],
    )
    return not still


def _proxy_driver_args(proxy_url: str | None) -> tuple[ProxyTunnel | None, str | None, list[str]]:
    """Return (tunnel, masked_label, chromium launch args)."""
    if not proxy_url:
        return None, "local", []
    parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
    label = f"{parsed.hostname}:{parsed.port}"
    if parsed.username and parsed.password:
        tunnel = ProxyTunnel(proxy_url)
        port = tunnel.start()
        return tunnel, label, [f"--proxy-server=http://127.0.0.1:{port}"]
    hostport = f"{parsed.hostname}:{parsed.port or 80}"
    return None, label, [f"--proxy-server=http://{hostport}"]


def _ats_raw_job(
    *,
    apply_url: str,
    term: str,
    location: str,
    title: str = "",
    company: str = "",
    listing_url: str = "",
    mode: str = "web",
) -> RawJob:
    job_id = canonicalize_ats_url(apply_url)
    return RawJob(
        source_platform="google",
        source_job_id=job_id,
        title=title or "Unknown",
        company=company or "Unknown",
        location=location,
        description="",
        listing_url=listing_url or apply_url,
        destination_url=job_id,
        date_posted=None,
        easy_apply_evidence="",
        is_remote=None,
        raw_extras={
            "search_term": term,
            "site": "google",
            "google_mode": mode,
            "ats_filter": "greenhouse_or_lever",
        },
    )


def _ensure_cdp_page(cdp_http: str) -> None:
    """Create a blank tab if the CDP browser has no page targets."""
    import urllib.request

    try:
        with urllib.request.urlopen(f"{cdp_http.rstrip('/')}/json/list", timeout=3) as resp:
            targets = json.loads(resp.read().decode())
        if any(t.get("type") == "page" for t in targets):
            return
    except Exception as exc:
        _log.debug("CDP list failed: %s", exc)
    try:
        req = urllib.request.Request(
            f"{cdp_http.rstrip('/')}/json/new?about:blank",
            method="PUT",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
        _log.info("Created blank CDP tab on %s", cdp_http)
    except Exception as exc:
        _log.warning("Could not create CDP tab on %s: %s", cdp_http, exc)


def _attach_cdp_page(pw: Any, cdp_url: str):
    """Attach Playwright to an existing Chrome; reuse its context/pages."""
    _ensure_cdp_page(cdp_url)
    browser = pw.chromium.connect_over_cdp(cdp_url)
    if browser.contexts:
        context = browser.contexts[0]
    else:
        context = browser.new_context(
            locale="en-CA",
            viewport={"width": 1280, "height": 900},
        )
    page = context.pages[0] if context.pages else context.new_page()
    return browser, context, page


class GoogleCDPProvider:
    """Google → Greenhouse/Lever discovery via Chromium + proxy + CapMonster."""

    name = "google_cdp"
    supported_platforms = ["google"]

    def __init__(self, mode: str | None = None) -> None:
        raw = (mode or os.getenv("GOOGLE_CDP_MODE") or "web").strip().lower()
        if raw not in {"web", "jobs", "both", "tavily"}:
            raw = "web"
        self.mode = raw
        self.headless = _truthy(os.getenv("GOOGLE_CDP_HEADLESS"), default=True)

    def discover(self, request: DiscoveryRequest) -> list[RawJob]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            _log.error("Playwright is not installed.")
            # Still allow pure Tavily fail-safe without Playwright.
            if self.mode in {"web", "both", "tavily"}:
                return self._tavily_failsafe(request, reason="playwright_missing")
            return []

        # Pure Tavily mode — no browser (CAPTCHA-proof web dork path).
        if self.mode == "tavily":
            return self._tavily_failsafe(request, reason="mode_tavily")

        ladder = build_scrape_proxy_ladder()
        # CapMonster solves via CAPMONSTER_PROXY_URL (DataImpulse). Browser
        # egress MUST match that IP or Google rejects the token after inject.
        # Prefer dataimpulse first; fall back to webshare only if DI missing.
        if ladder.mode == "smart":
            preferred = None
            if ladder.tiers.dataimpulse:
                preferred = "dataimpulse"
            elif ladder.tiers.webshare:
                preferred = "webshare"
            if preferred and ladder.current_label() != preferred:
                try:
                    ladder._set_tier(preferred, reason="google_cdp_match_capmonster_proxy")
                except Exception:
                    ladder.note_soft_block()
                    ladder.note_soft_block()

        all_jobs: list[RawJob] = []
        modes = ["web", "jobs"] if self.mode == "both" else [self.mode]
        browser_web_jobs: list[RawJob] = []
        browser_web_empty = False

        for mode in modes:
            jobs = self._discover_mode(sync_playwright, request, ladder, mode=mode)
            all_jobs.extend(jobs)
            if mode == "web":
                browser_web_jobs = list(jobs)
                browser_web_empty = not jobs

        # Firecrawl then Tavily fail-safe when CDP web is empty or captcha-blocked.
        if self.mode in {"web", "both"}:
            need_api = browser_web_empty or _truthy(
                os.getenv("GOOGLE_CDP_API_FALLBACK_ALWAYS"), default=False
            )
            if need_api and _truthy(
                os.getenv("GOOGLE_CDP_FIRECRAWL_FALLBACK"), default=False
            ):
                fc_jobs = self._firecrawl_failsafe(
                    request,
                    reason="web_empty" if browser_web_empty else "always",
                )
                all_jobs.extend(fc_jobs)
                if fc_jobs:
                    browser_web_empty = False
            if (
                browser_web_empty
                and _truthy(os.getenv("GOOGLE_CDP_TAVILY_FALLBACK"), default=True)
            ) or _truthy(os.getenv("GOOGLE_CDP_TAVILY_ALWAYS"), default=False):
                tavily_jobs = self._tavily_failsafe(
                    request,
                    reason="web_empty" if browser_web_empty else "always",
                )
                all_jobs.extend(tavily_jobs)

        # Dedup by destination URL
        seen: set[str] = set()
        unique: list[RawJob] = []
        for job in all_jobs:
            key = canonicalize_ats_url(job.destination_url or job.listing_url)
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(job)

        _log.info("Google CDP total unique Greenhouse/Lever jobs: %d", len(unique))
        # Flywheel: register board slugs for the ats_board_api direct poller.
        try:
            from jobbots.core.discovery.slug_registry import register_slugs_from_url

            for job in unique:
                register_slugs_from_url(
                    job.destination_url or job.listing_url, source="google_cdp"
                )
        except Exception:
            pass
        return unique

    def _firecrawl_failsafe(
        self, request: DiscoveryRequest, *, reason: str = ""
    ) -> list[RawJob]:
        """CAPTCHA-free ATS web dorks via Firecrawl search."""
        try:
            from jobbots.core.discovery.providers.firecrawl_ats import (
                discover_ats_via_firecrawl,
            )
            from jobbots.core.firecrawl_client import firecrawl_enabled
        except Exception as exc:
            _log.warning("Firecrawl fail-safe import failed: %s", exc)
            return []
        if not firecrawl_enabled():
            _log.info(
                "Firecrawl fail-safe skipped (disabled/missing FIRECRAWL_API_KEY) reason=%s",
                reason,
            )
            return []
        _log.info(
            "Firecrawl fail-safe starting reason=%s terms=%d",
            reason,
            len(request.search_terms or []),
        )
        try:
            jobs = discover_ats_via_firecrawl(request)
        except Exception as exc:
            _log.warning("Firecrawl fail-safe failed: %s", exc)
            return []
        _log.info("Firecrawl fail-safe returned %d jobs (reason=%s)", len(jobs), reason)
        return jobs

    def _tavily_failsafe(
        self, request: DiscoveryRequest, *, reason: str = ""
    ) -> list[RawJob]:
        """High-speed CAPTCHA-free web dork path via Tavily API."""
        try:
            from jobbots.core.discovery.providers.tavily_ats import (
                discover_ats_via_tavily,
                tavily_enabled,
            )
        except Exception as exc:
            _log.warning("Tavily fail-safe import failed: %s", exc)
            return []
        if not tavily_enabled():
            _log.info("Tavily fail-safe skipped (disabled/missing key) reason=%s", reason)
            return []
        _log.info("Tavily fail-safe starting reason=%s terms=%d", reason, len(request.search_terms or []))
        try:
            jobs = discover_ats_via_tavily(request)
        except Exception as exc:
            _log.warning("Tavily fail-safe failed: %s", exc)
            return []
        _log.info("Tavily fail-safe returned %d jobs (reason=%s)", len(jobs), reason)
        return jobs

    def _discover_mode(
        self,
        sync_playwright: Any,
        request: DiscoveryRequest,
        ladder: ScrapeProxyLadder,
        *,
        mode: str,
    ) -> list[RawJob]:
        max_attempts = 3 if ladder.mode == "smart" else 1
        last_jobs: list[RawJob] = []

        for attempt in range(1, max_attempts + 1):
            proxies = ladder.current_proxies()
            proxy_url = proxies[0] if proxies else None
            if proxy_url:
                proxy_url = _sticky_dataimpulse_proxy(proxy_url)
            _align_capmonster_proxy(proxy_url)
            tunnel, label, launch_args = _proxy_driver_args(proxy_url)
            _log.info(
                "Google CDP mode=%s attempt=%d/%d proxy=%s headless=%s",
                mode, attempt, max_attempts, label, self.headless,
            )

            jobs: list[RawJob] = []
            blocked = False
            sb_driver = None
            cdp_url = (
                os.getenv("GOOGLE_CDP_URL")
                or os.getenv("EXISTING_CDP_URL")
                or ""
            ).strip()
            if not cdp_url:
                raw_port = (os.getenv("GOOGLE_CDP_PORT") or os.getenv("EXISTING_CDP_PORT") or "").strip()
                if raw_port.isdigit():
                    cdp_url = f"http://127.0.0.1:{raw_port}"

            if not cdp_url:
                driver_proxy = None
                if proxy_url:
                    parsed = urlparse(proxy_url if "://" in proxy_url else f"http://{proxy_url}")
                    if parsed.username and parsed.password:
                        driver_proxy = f"127.0.0.1:{tunnel.local_port}" if tunnel else None
                    else:
                        driver_proxy = f"{parsed.hostname}:{parsed.port or 80}"
                try:
                    from seleniumbase import sb_cdp
                    sb_driver = sb_cdp.Chrome(headless=self.headless, proxy=driver_proxy)
                    sb_port = sb_driver.get_port()
                    cdp_url = f"http://127.0.0.1:{sb_port}"
                    _log.info("Launched SeleniumBase Pure CDP Chrome for Google stealth search on %s", cdp_url)
                except Exception as exc:
                    _log.warning("Could not launch sb_cdp.Chrome: %s. Falling back to Playwright chromium.", exc)

            try:
                with sync_playwright() as pw:
                    if cdp_url:
                        _log.info("Google CDP attaching to browser: %s", cdp_url)
                        browser, context, page = _attach_cdp_page(pw, cdp_url)
                        owned_browser = False
                    else:
                        try:
                            browser = pw.chromium.launch(
                                channel="chrome",
                                headless=self.headless,
                                args=launch_args,
                            )
                        except Exception:
                            browser = pw.chromium.launch(
                                headless=self.headless,
                                args=launch_args,
                            )
                        context = browser.new_context(
                            locale="en-CA",
                            viewport={"width": 1280, "height": 900},
                            user_agent=(
                                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/126.0.0.0 Safari/537.36"
                            ),
                        )
                        page = context.new_page()
                        owned_browser = True
                    try:
                        if mode == "web":
                            jobs, blocked = self._scrape_web(page, request)
                        else:
                            jobs, blocked = self._scrape_jobs(page, request)
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass
                        if owned_browser and browser:
                            try:
                                browser.close()
                            except Exception:
                                pass
            except Exception as exc:
                _log.warning("Google CDP mode=%s proxy=%s failed: %s", mode, label, exc)
                # Force escalate on Chromium net failures (empty/proxy/tunnel),
                # not only classic HTTP 429 strings.
                escalated = ladder.note_failure(exc)
                if not escalated:
                    escalated = ladder.note_soft_block()
                if not escalated:
                    escalated = ladder.note_failure(
                        RuntimeError(f"google_cdp_nav_error:{type(exc).__name__}")
                    )
                if escalated:
                    continue
                break
            finally:
                if sb_driver:
                    try:
                        sb_driver.quit()
                    except Exception:
                        pass
                if tunnel:
                    try:
                        tunnel.stop()
                    except Exception:
                        pass

            last_jobs = jobs
            if jobs and not blocked:
                ladder.note_success()
                return jobs

            if blocked:
                _log.warning(
                    "Google CDP still blocked after CapMonster (mode=%s proxy=%s) — escalating",
                    mode, label,
                )
                if not ladder.note_soft_block() and not ladder.note_failure(
                    RuntimeError("google_captcha_blocked")
                ):
                    break
                continue

            # empty but not clearly blocked
            if not ladder.note_soft_block():
                break

        return last_jobs

    def _navigate_with_captcha(self, page, url: str) -> bool:
        """Goto URL; if captcha, CapMonster + optional reload. True if usable."""
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2000)
        if not _page_looks_blocked(page):
            return True
        # Warm-CDP / high-volume runs: optional fail-fast (CapMonster rarely
        # clears Google /sorry from an already-flagged session).
        if not _truthy(os.getenv("GOOGLE_CDP_CAPTCHA"), default=True):
            _log.warning("Captcha page and GOOGLE_CDP_CAPTCHA=0 — skipping solve")
            return False
        if _try_solve_google_captcha(page):
            if not _page_looks_blocked(page):
                return True
            # Reload target after clearance cookies
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2000)
            return not _page_looks_blocked(page)
        return False

    def _scrape_web(
        self, page, request: DiscoveryRequest
    ) -> tuple[list[RawJob], bool]:
        jobs: list[RawJob] = []
        any_blocked = False
        # One geo anchor: dork expands metro pack (avoid city fan-out captcha burn).
        locations = list(request.locations or ["Vancouver, BC"])
        anchor = locations[0]
        for loc in locations:
            if "vancouver" in (loc or "").lower() and "wa" not in (loc or "").lower().split(","):
                anchor = loc
                break
        try:
            max_variants = max(1, min(int(os.getenv("GOOGLE_CDP_WEB_VARIANTS", "2") or "2"), 3))
        except ValueError:
            max_variants = 2

        for term in request.search_terms:
            variants = build_ats_query_variants(term, anchor)[:max_variants]
            term_hits = 0
            for qi, q in enumerate(variants):
                if qi > 0 and term_hits >= 3:
                    break  # primary already rich; save captcha risk
                url = (
                    "https://www.google.com/search?"
                    f"q={quote_plus(q)}&hl=en&gl=ca&num="
                    f"{min(int(request.max_results_per_term or 20), 20)}"
                )
                _log.info("Google web ATS: %s", q)
                ok = self._navigate_with_captcha(page, url)
                if not ok:
                    any_blocked = True
                    _log.warning("Blocked on Google web for term=%r", term)
                    continue
                hits = extract_serp_ats_hits(page)
                if not hits:
                    # Fallback if DOM shape drifted — still prefer typed hits later
                    html = page.content()
                    hrefs = page.evaluate(
                        """() => Array.from(document.querySelectorAll('a[href]'))
                            .map(a => a.href)
                            .filter(h => /boards\\.greenhouse\\.io|job-boards\\.greenhouse\\.io|jobs\\.lever\\.co/i.test(h))"""
                    ) or []
                    for apply_url in extract_ats_urls(html, *hrefs):
                        hits.append({"title": "", "snippet": "", "apply_url": apply_url})
                for hit in hits:
                    title = hit.get("title") or ""
                    snippet = hit.get("snippet") or ""
                    apply_url = hit.get("apply_url") or ""
                    if not apply_url:
                        continue
                    if title or snippet:
                        if not serp_passes_metro_van_canada(title=title, snippet=snippet):
                            _log.debug("Skip non-Metro/Canada SERP: %s | %s", title[:80], apply_url)
                            continue
                        if not serp_title_matches_search_intent(title=title, search_term=term):
                            _log.debug("Skip non-IT SERP title: %s | term=%r", title[:80], term)
                            continue
                    jobs.append(
                        _ats_raw_job(
                            apply_url=apply_url,
                            term=term,
                            location=anchor,
                            title=title,
                            listing_url=url,
                            mode="web",
                        )
                    )
                    term_hits += 1
                time.sleep(float(os.getenv("GOOGLE_CDP_PAUSE_SECONDS", "1.2") or 1.2))
        return jobs, any_blocked and not jobs

    def _scrape_jobs(
        self, page, request: DiscoveryRequest
    ) -> tuple[list[RawJob], bool]:
        jobs: list[RawJob] = []
        any_blocked = False
        for term in request.search_terms:
            for location in request.locations:
                q = f"{term} jobs near {location}"
                url = (
                    "https://www.google.com/search?"
                    f"q={quote_plus(q)}&udm=8&hl=en&gl=ca"
                )
                _log.info("Google Jobs: %s", q)
                ok = self._navigate_with_captcha(page, url)
                if not ok:
                    any_blocked = True
                    continue

                # Click through visible job cards and harvest apply destinations.
                cards = page.locator("[data-preview-id]")
                try:
                    page.wait_for_selector("[data-preview-id]", timeout=5000)
                except Exception:
                    pass
                
                if cards.count() == 0:
                    cards = page.locator("div[role='main'] button, div[role='main'] [role='listitem']")
                    try:
                        page.wait_for_selector("div[role='main'] button, div[role='main'] [role='listitem']", timeout=3000)
                    except Exception:
                        pass

                try:
                    cards_count = cards.count()
                    print(f"[{term}] Found {cards_count} job cards on Google Jobs.")
                    n = min(cards_count, int(request.max_results_per_term or 15))
                except Exception as e:
                    print(f"[{term}] Failed to count job cards: {e}")
                    n = 0
                if n == 0:
                    try:
                        screenshot_path = "/Users/Jane/.gemini/antigravity-ide/brain/89e203d6-e388-4e94-900b-5f021e6194ce/google_jobs_zero_cards.png"
                        page.screenshot(path=screenshot_path)
                        _log.warning(f"No cards found! Saved screenshot to {screenshot_path}")
                        html_path = "/Users/Jane/.gemini/antigravity-ide/brain/89e203d6-e388-4e94-900b-5f021e6194ce/google_jobs_zero_cards.html"
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(page.content())
                    except Exception as e:
                        _log.warning(f"Failed to save zero-cards screenshot/HTML: {e}")
                for i in range(n):
                    card_title = "Unknown"
                    try:
                        card_title = cards.nth(i).get_attribute("data-title") or f"Card {i}"
                        print(f"[{term}] Clicking card {i}: {card_title}...")
                        cards.nth(i).click(timeout=3000)
                        page.wait_for_timeout(1000)
                    except Exception as click_err:
                        print(f"[{term}] Failed to click card {i} ({card_title}): {click_err}")
                        continue
                    all_hrefs = page.evaluate(
                        """() => Array.from(document.querySelectorAll('a[href]'))
                            .map(a => ({href: a.href, text: (a.innerText||'').trim().slice(0,80)}))"""
                    ) or []
                    print(f"[{term}] Card {i}: Found {len(all_hrefs)} total a[href] links on page.")
                    
                    import re
                    hrefs = []
                    rx = re.compile(r"utm_campaign=google_jobs_apply|greenhouse|lever\.co", re.I)
                    for item in all_hrefs:
                        h = item.get("href") or ""
                        if rx.search(h):
                            hrefs.append(item)
                    print(f"[{term}] Card {i}: Found {len(hrefs)} matching apply links.")
                    if hrefs:
                        print(f"[{term}] Card {i} sample match: {hrefs[0]}")
                    title = ""
                    try:
                        title = page.locator("h1, h2, [role='heading']").first.inner_text(timeout=400)
                    except Exception:
                        pass
                    for item in hrefs:
                        ats = extract_ats_urls(item.get("href") or "")
                        for apply_url in ats:
                            t = title.strip()
                            if t and not serp_title_matches_search_intent(
                                title=t, search_term=term
                            ):
                                continue
                            if t and not serp_passes_metro_van_canada(
                                title=t, snippet=location or ""
                            ):
                                # Google Jobs often puts city on the card; if title
                                # has no geo, still allow when location search is metro.
                                if not _CANADA_OR_METRO_RE.search(location or ""):
                                    continue
                            jobs.append(
                                _ats_raw_job(
                                    apply_url=apply_url,
                                    term=term,
                                    location=location,
                                    title=t,
                                    listing_url=page.url,
                                    mode="jobs",
                                )
                            )
                # Also harvest whatever is already in the DOM without clicks
                html = page.content()
                for apply_url in extract_ats_urls(html):
                    jobs.append(
                        _ats_raw_job(
                            apply_url=apply_url,
                            term=term,
                            location=location,
                            listing_url=url,
                            mode="jobs",
                        )
                    )
                time.sleep(float(os.getenv("GOOGLE_CDP_PAUSE_SECONDS", "1.2") or 1.2))
        return jobs, any_blocked and not jobs

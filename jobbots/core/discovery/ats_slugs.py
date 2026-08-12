"""ATS slug extraction + cleaning — shared library for the slug flywheel.

Every component that touches a Greenhouse/Lever URL (JobSpy provider, Google
CDP provider, Firecrawl/Tavily harvesters, the seed script, and the footprint
sensor) funnels through here so parsing rules live in exactly one place.

Supported extraction surfaces
------------------------------
- ``boards.greenhouse.io/{slug}/jobs/{id}``        → path slug
- ``job-boards.greenhouse.io/{slug}/jobs/{id}``    → path slug
- ``{slug}.greenhouse.io``                          → subdomain slug (custom embed)
- ``jobs.lever.co/{slug}/{uuid}``                   → path slug
- ``{slug}.lever.co``                               → subdomain slug
- full URLs, query-string-wrapped URLs, redirect params (``?url=``, ``?q=``)
- raw text / HTML snippets (``jobs.lever.co/acme`` buried in markup)
- bare tokens (``acme``, ``Acme Corp`` → ``acmecorp``-style cleaning is left
  to the caller; here we only normalise case/whitespace/charset)

Cleaning rules
--------------
- lowercase, strip whitespace
- strip scheme + host when a full URL is passed
- strip leading ``www.``
- strip trailing paths after the slug segment
- reject anything that isn't ``[a-z0-9][a-z0-9_-]*`` after cleaning

The registry treats ``(platform, slug)`` as the natural unique key.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

# Platforms we know how to poll via public JSON API.
PLATFORM_GREENHOUSE = "greenhouse"
PLATFORM_LEVER = "lever"
PLATFORM_ASHBY = "ashby"
PLATFORM_BAMBOOHR = "bamboohr"
SUPPORTED_PLATFORMS = (PLATFORM_GREENHOUSE, PLATFORM_LEVER, PLATFORM_ASHBY, PLATFORM_BAMBOOHR)

# Valid slug charset (GH tokens are lowercase alnum; Lever/Ashby/BambooHR allow hyphens).
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,80}$")

# Text-snippet patterns (footprint sensor / HTML source scraping).
_LEVER_TEXT_RE = re.compile(r"jobs\.lever\.co/([a-zA-Z0-9_-]{2,60})", re.IGNORECASE)
_GH_TEXT_RE = re.compile(
    r"(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io)/([a-zA-Z0-9_-]{2,60})",
    re.IGNORECASE,
)
_GH_SUBDOMAIN_TEXT_RE = re.compile(
    r"([a-zA-Z0-9][a-zA-Z0-9-]{1,40})\.greenhouse\.io", re.IGNORECASE
)
_ASHBY_TEXT_RE = re.compile(r"jobs\.ashbyhq\.com/([a-zA-Z0-9_-]{2,60})", re.IGNORECASE)
_BAMBOOHR_TEXT_RE = re.compile(r"([a-zA-Z0-9_-]{2,60})\.bamboohr\.com", re.IGNORECASE)

# Hosts that are never slugs (platform infra, not company boards).
_NON_SLUG_HOSTS = frozenset({
    "www", "boards", "job-boards", "jobs", "api", "boards-api",
    "app", "my", "support", "help", "docs", "blog", "status", "careers",
})


def clean_slug(raw: str | None) -> str:
    """Normalise a candidate slug; return ``""`` when unusable.

    Handles bare tokens, full URLs, and URL-wrapped values. Does NOT invent
    slugs from company display names (that mapping is not 1:1 — we only
    accept structural evidence).
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""

    # If it looks like a URL, peel it down to the slug-relevant segment.
    if "://" in s or s.startswith(("www.", "boards.", "jobs.", "job-boards.", "careers.")):
        s = _slug_from_url(s) or ""
    else:
        # Bare token: strip query/fragment noise, keep first path segment.
        s = s.split("?", 1)[0].split("#", 1)[0]
        s = s.strip("/").split("/", 1)[0]

    s = s.strip().lower().strip(".-_")
    if not s or s in _NON_SLUG_HOSTS:
        return ""
    return s if _SLUG_RE.match(s) else ""


def _slug_from_url(url: str) -> str | None:
    """Extract the company slug from a GH/Lever/Ashby/BambooHR URL."""
    try:
        # Unwrap redirect wrappers (?url=, ?q=) up to 2 levels.
        candidate = url.strip()
        for _ in range(2):
            parsed = urlparse(candidate)
            qs = parse_qs(parsed.query)
            nxt = (qs.get("url") or qs.get("q") or [None])[0]
            if nxt and "://" in str(nxt):
                candidate = unquote(str(nxt))
            else:
                break
        parsed = urlparse(candidate if "://" in candidate else f"https://{candidate}")
        host = (parsed.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path_parts = [p for p in (parsed.path or "").split("/") if p]

        # boards.greenhouse.io/{slug}/... | job-boards.greenhouse.io/{slug}/... | jobs.lever.co/{slug}/... | jobs.ashbyhq.com/{slug}/...
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.lever.co", "jobs.ashbyhq.com"}:
            return path_parts[0] if path_parts else None

        # {slug}.bamboohr.com | {slug}.greenhouse.io | {slug}.lever.co | {slug}.ashbyhq.com
        for apex in ("greenhouse.io", "lever.co", "bamboohr.com", "ashbyhq.com"):
            if host.endswith("." + apex):
                sub = host[: -len("." + apex)]
                if sub and sub not in _NON_SLUG_HOSTS:
                    return sub
        return None
    except Exception:
        return None


def platform_for_url(url: str | None) -> str | None:
    """Return ``greenhouse`` | ``lever`` | ``ashby`` | ``bamboohr`` | ``None`` for a URL."""
    if not url:
        return None
    try:
        host = (urlparse(str(url)).hostname or "").lower()
        if not host:
            return None
        if "greenhouse.io" in host:
            return PLATFORM_GREENHOUSE
        if "lever.co" in host:
            return PLATFORM_LEVER
        if "ashbyhq.com" in host:
            return PLATFORM_ASHBY
        if "bamboohr.com" in host:
            return PLATFORM_BAMBOOHR
    except Exception:
        pass
    return None


def _unwrap_redirect(url: str) -> str:
    """Peel redirect wrappers (``?url=`` / ``?q=``) to the innermost target."""
    candidate = url.strip()
    for _ in range(3):
        try:
            parsed = urlparse(candidate)
            qs = parse_qs(parsed.query)
            nxt = (qs.get("url") or qs.get("q") or [None])[0]
            if nxt and "://" in str(nxt):
                candidate = unquote(str(nxt))
                continue
        except Exception:
            break
        break
    return candidate


def extract_slugs_from_url(url: str | None) -> list[tuple[str, str]]:
    """Return ``[(platform, slug)]`` found in a single URL (0 or 1 entries)."""
    if not url:
        return []
    target = _unwrap_redirect(str(url))
    platform = platform_for_url(target)
    slug = _slug_from_url(target)
    if platform and slug:
        slug = clean_slug(slug)
        if slug:
            return [(platform, slug)]
    return []


def extract_slugs_from_text(text: str | None) -> list[tuple[str, str]]:
    """Mine ``(platform, slug)`` pairs from raw text / HTML / snippets."""
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    if not text:
        return found
    blob = unquote(str(text))

    def _add(platform: str, slug: str) -> None:
        slug = clean_slug(slug)
        if slug and (platform, slug) not in seen:
            seen.add((platform, slug))
            found.append((platform, slug))

    for m in _LEVER_TEXT_RE.finditer(blob):
        _add(PLATFORM_LEVER, m.group(1))
    for m in _GH_TEXT_RE.finditer(blob):
        _add(PLATFORM_GREENHOUSE, m.group(1))
    for m in _GH_SUBDOMAIN_TEXT_RE.finditer(blob):
        sub = m.group(1)
        if sub.lower() not in _NON_SLUG_HOSTS:
            _add(PLATFORM_GREENHOUSE, sub)
    for m in _ASHBY_TEXT_RE.finditer(blob):
        _add(PLATFORM_ASHBY, m.group(1))
    for m in _BAMBOOHR_TEXT_RE.finditer(blob):
        _add(PLATFORM_BAMBOOHR, m.group(1))
    return found

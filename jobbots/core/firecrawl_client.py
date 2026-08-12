"""Shared Firecrawl helper for ATS discovery (search) and optional scrape.

**Cloud (default when ``FIRECRAWL_API_KEY=fc-...``):**
  ``https://api.firecrawl.dev`` — student / paid credits.

**Self-host (optional, RAM-heavy):**
  Docker Compose in ``infra/firecrawl`` → ``http://127.0.0.1:3002``
  with ``FIRECRAWL_SELF_HOST=1`` and ``FIRECRAWL_API_KEY=local``.

Credit discipline
-----------------
* ``firecrawl_search`` — only used by ``firecrawl_ats`` (Google CDP fail-safe).
* ``firecrawl_scrape`` / ``firecrawl_markdown`` — **off by default** on cloud
  (``FIRECRAWL_SCRAPE_ENABLED=0``). Enable only when you intentionally need
  page extraction; do not call from every bot module.

All modules must import from here (not raw HTTP).
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

_log = logging.getLogger("core.firecrawl")

_DEFAULT_SELF_HOST = "http://127.0.0.1:3002"
_DEFAULT_CLOUD = "https://api.firecrawl.dev"


def _truthy(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on", "y"}


def _secret(name: str, default: str = "") -> str:
    try:
        from jobbots.core.secret_manager import get_secret

        val = (get_secret(name, default) or default or "").strip()
    except Exception:
        val = (default or "").strip()
    if not val:
        val = (os.getenv(name) or default or "").strip()
    return val


def _looks_like_cloud_key(key: str) -> bool:
    k = (key or "").strip()
    return k.startswith("fc-") and len(k) > 10


def firecrawl_api_key() -> str:
    """API key from Infisical / env. Self-host may use ``local``."""
    key = (
        _secret("FIRECRAWL_API_KEY")
        or os.getenv("FIRECRAWL_KEY")
        or ""
    ).strip()
    return key


def firecrawl_api_base() -> str:
    """Resolve API base: explicit env → cloud if fc- key → optional self-host."""
    base = (
        _secret("FIRECRAWL_API_BASE")
        or _secret("FIRECRAWL_BASE_URL")
        or os.getenv("FIRECRAWL_API_URL")
        or ""
    ).strip().rstrip("/")
    if base:
        # If someone left localhost base but installed a real cloud key, prefer cloud.
        key = firecrawl_api_key()
        if (
            _looks_like_cloud_key(key)
            and ("127.0.0.1" in base or "localhost" in base)
            and not _truthy(os.getenv("FIRECRAWL_SELF_HOST"), default=False)
        ):
            return _DEFAULT_CLOUD
        return base

    key = firecrawl_api_key()
    # Explicit self-host only when asked (RAM-heavy Docker).
    if _truthy(os.getenv("FIRECRAWL_SELF_HOST"), default=False):
        return _DEFAULT_SELF_HOST
    if _looks_like_cloud_key(key):
        return _DEFAULT_CLOUD
    # Legacy: no key → self-host only if SELF_HOST forced; else cloud (will fail auth).
    if _truthy(os.getenv("FIRECRAWL_SELF_HOST"), default=False):
        return _DEFAULT_SELF_HOST
    return _DEFAULT_CLOUD


def firecrawl_enabled() -> bool:
    """True when ATS Firecrawl path may call the API (search).

    Default **off** — the factory runs fully without Firecrawl (JobSpy, Glassdoor
    CDP, Workopolis, public GH/Lever board API). Enable only when credits exist.
    """
    if not _truthy(os.getenv("FIRECRAWL_ATS_ENABLED"), default=False):
        return False
    key = firecrawl_api_key()
    base = firecrawl_api_base()
    if _looks_like_cloud_key(key):
        return True
    # Self-host with auth off
    if "127.0.0.1" in base or "localhost" in base:
        if key in {"", "local"} or key:
            return _truthy(os.getenv("FIRECRAWL_SELF_HOST"), default=False) or key == "local"
    return bool(key) and key != "local"


def firecrawl_scrape_enabled() -> bool:
    """Scrape spends more credits — default off on cloud."""
    if not firecrawl_enabled():
        return False
    # Explicit flag; default off when cloud key, on only for self-host unless set.
    if os.getenv("FIRECRAWL_SCRAPE_ENABLED") is not None:
        return _truthy(os.getenv("FIRECRAWL_SCRAPE_ENABLED"), default=False)
    return is_self_hosted()


def is_self_hosted() -> bool:
    base = firecrawl_api_base()
    return "127.0.0.1" in base or "localhost" in base or "host.docker.internal" in base


def _timeout() -> int:
    try:
        return max(10, int(os.getenv("FIRECRAWL_TIMEOUT_SECONDS", "60") or 60))
    except ValueError:
        return 60


def _post(path: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    key = firecrawl_api_key()
    base = firecrawl_api_base()
    if not key and not is_self_hosted():
        _log.warning("Firecrawl cloud call skipped — no FIRECRAWL_API_KEY")
        return None
    url = f"{base.rstrip('/')}{path}"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Cloud requires Bearer fc-...; self-host with auth off accepts Bearer local.
    auth = key or ("local" if is_self_hosted() else "")
    if auth:
        headers["Authorization"] = f"Bearer {auth}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=_timeout()) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return json.loads(body)
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            detail = ""
        _log.warning("Firecrawl HTTP %s path=%s base=%s %s", exc.code, path, base, detail)
        return None
    except Exception as exc:
        _log.warning("Firecrawl request failed path=%s base=%s: %s", path, base, exc)
        return None


def firecrawl_reachable() -> bool:
    """Quick check whether the configured Firecrawl base responds."""
    if not firecrawl_enabled():
        return False
    # Prefer search-less scrape of example only when scrape enabled; else tiny search.
    if firecrawl_scrape_enabled():
        data = _post(
            "/v1/scrape",
            {"url": "https://example.com", "formats": ["markdown"], "onlyMainContent": True},
        )
        return data is not None
    hits = firecrawl_search(
        'site:boards.greenhouse.io "Vancouver, BC" IT Support',
        limit=1,
    )
    return True  # API answered (even if 0 hits) if no exception path — check via search


def firecrawl_search(
    query: str,
    *,
    limit: int = 10,
    include_domains: list[str] | None = None,
) -> list[dict[str, str]]:
    """Web search via Firecrawl. Returns ``[{title, url, content}, ...]``.

    Intended consumer: ``core.discovery.providers.firecrawl_ats`` only.
    """
    q = (query or "").strip()
    if not q or not firecrawl_enabled():
        return []
    try:
        # Cap lower on cloud to protect student credits.
        hard_cap = 10 if _looks_like_cloud_key(firecrawl_api_key()) else 20
        limit = max(1, min(int(limit), hard_cap))
    except Exception:
        limit = 8
    payload: dict[str, Any] = {"query": q, "limit": limit}
    if include_domains:
        # Cloud /v1/search rejects includeDomains — bake site: into the query only.
        site_bits = " OR ".join(f"site:{d}" for d in include_domains[:4] if d)
        if site_bits and "site:" not in q.lower():
            payload["query"] = f"{q} ({site_bits})"
        elif site_bits and "site:" in q.lower():
            # Query already has site: dorks; do not re-add or send invalid body keys.
            payload["query"] = q
    data = _post("/v1/search", payload)
    if not data:
        return []
    rows = data.get("data") or data.get("results") or data.get("web") or []
    if isinstance(data.get("data"), dict):
        rows = data["data"].get("web") or data["data"].get("results") or []
    out: list[dict[str, str]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = (row.get("url") or row.get("link") or "").strip()
        if not url:
            continue
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        out.append(
            {
                "title": (row.get("title") or meta.get("title") or "").strip(),
                "url": url,
                "content": (
                    row.get("description")
                    or row.get("snippet")
                    or row.get("markdown")
                    or row.get("content")
                    or ""
                ).strip()[:2000],
            }
        )
    _log.info(
        "Firecrawl search q=%r → %d results (base=%s cloud=%s)",
        q[:100],
        len(out),
        firecrawl_api_base(),
        not is_self_hosted(),
    )
    return out


def firecrawl_scrape(url: str, *, formats: list[str] | None = None) -> dict[str, Any] | None:
    """Scrape one URL to markdown/text. Credit-gated; off by default on cloud."""
    target = (url or "").strip()
    if not target:
        return None
    if not firecrawl_scrape_enabled():
        _log.debug("Firecrawl scrape disabled (set FIRECRAWL_SCRAPE_ENABLED=1 to spend credits)")
        return None
    payload = {
        "url": target,
        "formats": formats or ["markdown"],
        "onlyMainContent": True,
    }
    data = _post("/v1/scrape", payload)
    if not data:
        return None
    if isinstance(data.get("data"), dict):
        return data["data"]
    return data


def firecrawl_markdown(url: str) -> str:
    """Convenience: scrape URL and return markdown string (or empty)."""
    data = firecrawl_scrape(url)
    if not data:
        return ""
    md = data.get("markdown") or data.get("content") or ""
    if isinstance(md, str):
        return md
    return str(md or "")

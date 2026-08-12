"""NSTBrowser proxy payload helpers.

NST's profile API expects individual proxy fields.  Passing only ``{"url":
...}`` is accepted by some clients but can fail validation (notably with an
``invalid port number`` response), leaving a profile on the wrong egress.
"""
from __future__ import annotations

from urllib.parse import unquote, urlparse, urlunparse


def nst_proxy_payload(proxy_url: str) -> dict[str, str]:
    """Return a validated NST custom-proxy payload without logging secrets."""
    parsed = urlparse((proxy_url or "").strip())
    if parsed.scheme not in {"http", "https", "socks4", "socks5"}:
        raise ValueError("NST proxy URL must use http, https, socks4, or socks5")
    if not parsed.hostname or parsed.port is None:
        raise ValueError("NST proxy URL must include a host and numeric port")
    if not parsed.username or parsed.password is None:
        raise ValueError("NST proxy URL must include username and password")
    host = parsed.hostname
    port = str(parsed.port)
    username = unquote(parsed.username)
    password = unquote(parsed.password)
    normalized = urlunparse((parsed.scheme, f"{parsed.username}:{parsed.password}@{host}:{port}", "", "", "", ""))
    return {
        "proxySetting": "custom",
        "proxyType": "custom",
        "protocol": parsed.scheme,
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "url": normalized,
        "checker": "nstbrowser",
    }


def safe_proxy_host(proxy_url: str) -> str:
    """Return only host:port for status output."""
    parsed = urlparse(proxy_url or "")
    return f"{parsed.hostname or '?'}:{parsed.port or '?'}"

"""Declarative ATS adapter registry.

Each platform registers its adapter *and* the evidence used to identify it.
This keeps adding an ATS module local to that module instead of growing three
unrelated hard-coded tables (URL patterns, aliases, and page markers).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
from urllib.parse import urlparse

from .base import ATSAdapter


_ADAPTERS: dict[str, type[ATSAdapter]] = {}
_SPECS: dict[str, "PlatformSpec"] = {}


@dataclass(frozen=True)
class PlatformSpec:
    """URL and in-page evidence belonging to one ATS platform.

    Host suffixes match the suffix itself and subdomains, so ``workday.com``
    also matches tenant-hosted pages such as ``wd5.myworkdayjobs.com`` when
    that host suffix is registered explicitly.
    """

    name: str
    host_suffixes: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    dom_markers: tuple[str, ...] = ()

    def matches_host(self, host: str) -> bool:
        normalized = host.lower().removeprefix("www.")
        return any(
            normalized == suffix or normalized.endswith("." + suffix)
            for suffix in self.host_suffixes
        )


def register(
    platform: str,
    adapter_cls: type[ATSAdapter],
    *,
    host_suffixes: Iterable[str] = (),
    aliases: Iterable[str] = (),
    dom_markers: Iterable[str] = (),
) -> None:
    """Register an adapter and optional platform-identification evidence.

    The two positional arguments preserve the original extension API. New
    adapters should provide all three keyword collections, which lets URL and
    embedded-page detection evolve with the adapter in a single registration.
    """
    name = platform.strip().lower()
    if not name:
        raise ValueError("ATS platform name cannot be empty")
    _ADAPTERS[name] = adapter_cls
    _SPECS[name] = PlatformSpec(
        name=name,
        host_suffixes=tuple(
            item.strip().lower().removeprefix("www.")
            for item in host_suffixes if item and item.strip()
        ),
        aliases=tuple(
            item.strip().lower().removeprefix("www.")
            for item in aliases if item and item.strip()
        ),
        dom_markers=tuple(item.strip().lower() for item in dom_markers if item and item.strip()),
    )


def detect_platform(url: str | None) -> str | None:
    """Return the platform name for *url*, or None."""
    if not url:
        return None
    url_l = url.lower()
    if "gh_jid=" in url_l:
        return "greenhouse"
    try:
        host = (urlparse(url).hostname or "").lower()
        host = host.removeprefix("www.")
        for platform, spec in _SPECS.items():
            if host in spec.aliases or spec.matches_host(host):
                return platform
        return None
    except Exception:
        return None


def detect_adapter(url: str | None) -> type[ATSAdapter] | None:
    """Return the adapter class for *url*, or None."""
    platform = detect_platform(url)
    if platform:
        return _ADAPTERS.get(platform)
    return None


def detect_adapter_from_page(page: Any) -> type[ATSAdapter] | None:
    """Detect adapter from an already-loaded page (checks URL + iframes + DOM)."""
    url = getattr(page, "url", "") or ""
    cls = detect_adapter(url)
    if cls:
        return cls

    # Check embedded iframes
    try:
        for fr in getattr(page, "frames", []):
            if fr == getattr(page, "main_frame", None):
                continue
            fr_url = (getattr(fr, "url", "") or "").lower()
            if not fr_url:
                continue
            cls = detect_adapter(fr_url)
            if cls:
                return cls
            # Extra markers for GH embed
            if "/embed/job_app" in fr_url or "boards.greenhouse" in fr_url:
                return _ADAPTERS.get("greenhouse")
    except Exception:
        pass

    # DOM markers fallback
    try:
        html = (page.content() or "")[:12000].lower()
    except Exception:
        return None

    for platform, spec in _SPECS.items():
        if any(marker in html for marker in spec.dom_markers):
            return _ADAPTERS.get(platform)

    return None


def supported_platforms() -> list[str]:
    """Return list of registered platform names."""
    return list(_ADAPTERS.keys())


def is_supported_url(url: str | None) -> bool:
    """Return True if *url* matches any registered ATS platform."""
    return detect_platform(url) is not None


def _auto_register() -> None:
    """Import and register all built-in adapters."""
    from .adapters.greenhouse import GreenhouseAdapter
    from .adapters.lever import LeverAdapter
    from .adapters.ashby import AshbyAdapter
    from .adapters.bamboohr import BambooHRAdapter

    register(
        "greenhouse", GreenhouseAdapter,
        host_suffixes=("greenhouse.io",), aliases=("grnh.se", "gh.io"),
        dom_markers=("boards.greenhouse", "greenhouse", "application--form", 'id="application-form"', "resume-upload-input"),
    )
    register(
        "lever", LeverAdapter, host_suffixes=("lever.co",),
        dom_markers=("lever-application", "posting-application", "jobs.lever.co", 'data-qa="btn-apply"'),
    )
    register(
        "ashby", AshbyAdapter, host_suffixes=("ashbyhq.com",),
        dom_markers=("ashbyhq.com", "ashby-embedded", "ashby-application"),
    )
    register(
        "bamboohr", BambooHRAdapter, host_suffixes=("bamboohr.com",),
        dom_markers=("bamboohr.com", "bamboohr-embedded"),
    )


_auto_register()

"""Smart scrape proxy ladder for JobSpy discovery.

Default order (skip any tier whose env URL is absent):

  1. **local** — no proxy (home / VPS egress)
  2. **webshare** — static residential (Webshare)
  3. **residential** (internal name still ``dataimpulse`` for env compat) —
     rotating residential, which in this project is **Proxy-Cheap**
     (``PROXY_URL`` / ``CAPMONSTER_PROXY_URL``). There is no DataImpulse.

Infisical / env keys (any alias works; first non-empty wins per tier):

  Webshare:     ``JOBSPY_PROXY_WEBSHARE``, ``WEBSHARE_PROXY_URL``
  Proxy-Cheap:  ``PROXY_URL``, ``CAPMONSTER_PROXY_URL``,
                or legacy aliases ``JOBSPY_PROXY_DATAIMPULSE`` /
                ``DATAIMPULSE_PROXY_URL`` (still accepted)

Legacy: ``JOBSPY_PROXY_URLS`` (comma-separated) forces a fixed list and
disables the smart ladder (``JOBSPY_PROXY_MODE=fixed`` implied).

``JOBSPY_PROXY_MODE``:
  ``smart`` (default) — ladder + local↔webshare alternation
  ``local``           — never use a proxy
  ``fixed``           — ``JOBSPY_PROXY_URLS`` / ``PROXY_URL`` only

Cloud knobs:
  ``JOBSPY_SKIP_LOCAL=1`` — start on first available paid tier (AWS egress
  is usually blocked by job boards; local wastes attempts).
  Proxy auth failures (HTTP 407) permanently disable that tier for the
  process so step-down / alternate cannot thrash back to a dead credential.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

_log = logging.getLogger("discovery.scrape_proxy")

_RATE_LIMIT_RE = re.compile(
    r"(429|rate.?limit|too many requests|blocked by|access denied|"
    r"proxy (error|failed|refused|authentication)|"
    r"407|authentication required|"
    r"tunnel connection failed|"
    r"connection reset|max retries exceeded|"
    r"remote.?disconnected|unable to connect to proxy)",
    re.IGNORECASE,
)

_AUTH_FAIL_RE = re.compile(
    r"(407|proxy authentication required|authentication required)",
    re.IGNORECASE,
)

_DATAIMPULSE_HOST_RE = re.compile(r"dataimpulse", re.IGNORECASE)
_TIER_ORDER = ("local", "webshare", "dataimpulse")


@dataclass
class ProxyTier:
    """Resolved URLs for each scrape tier (empty string = unavailable)."""

    local: str = ""  # sentinel; always "available"
    webshare: str = ""
    dataimpulse: str = ""

    def available_names(self) -> list[str]:
        names = ["local"]
        if self.webshare:
            names.append("webshare")
        if self.dataimpulse:
            names.append("dataimpulse")
        return names


@dataclass
class ScrapeProxyLadder:
    """Stateful proxy picker with escalate / alternate / step-down."""

    tiers: ProxyTier
    mode: str = "smart"
    fixed_urls: list[str] = field(default_factory=list)
    # Alternate local↔webshare every N successful queries while healthy.
    alternate_every: int = 3
    # After this many successes on a higher tier, try stepping back toward local.
    step_down_after: int = 8
    # When True, start on first non-local paid tier (cloud workers).
    skip_local: bool = False

    _tier: str = "local"
    _queries: int = 0
    _success_streak: int = 0
    _fail_streak: int = 0
    _prefer_webshare_next: bool = False
    # Tiers that returned hard auth failures (407) — never reuse this process.
    _blacklisted: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.mode = (self.mode or "smart").strip().lower()
        if self.fixed_urls and self.mode == "smart":
            # Explicit list ⇒ fixed rotation, no local-first ladder.
            self.mode = "fixed"
        if self.mode == "local":
            self._tier = "local"
        elif self.mode == "fixed":
            self._tier = "fixed"
        else:
            self._tier = "local"
            if self.skip_local:
                start = self._first_usable_paid_tier()
                if start:
                    self._tier = start
        _log.info(
            "Scrape proxy ladder mode=%s tiers=%s start=%s skip_local=%s alternate_every=%s",
            self.mode,
            self.tiers.available_names(),
            self._tier,
            self.skip_local,
            self.alternate_every,
        )

    def current_label(self) -> str:
        return self._tier

    def current_proxies(self) -> list[str] | None:
        """JobSpy ``proxies=`` argument (``None`` = local egress)."""
        if self.mode == "local":
            return None
        if self.mode == "fixed":
            return list(self.fixed_urls) or None
        if self._tier == "local" or self._tier in self._blacklisted:
            if self._tier in self._blacklisted:
                # Should not happen; recover to a usable tier.
                recovered = self._first_usable_paid_tier() or "local"
                if recovered != self._tier:
                    self._set_tier(recovered, reason="recover_from_blacklist")
                if self._tier == "local":
                    return None
            else:
                return None
        if self._tier == "webshare":
            return [self.tiers.webshare] if self.tiers.webshare else None
        if self._tier == "dataimpulse":
            return [self.tiers.dataimpulse] if self.tiers.dataimpulse else None
        return None

    def note_success(self) -> None:
        self._queries += 1
        self._success_streak += 1
        self._fail_streak = 0
        if self.mode != "smart":
            return
        # Healthy local↔webshare alternation (never onto a blacklisted tier).
        if (
            self.tiers.webshare
            and "webshare" not in self._blacklisted
            and self._tier in {"local", "webshare"}
            and (not self.skip_local or self._tier == "webshare")
        ):
            if self._success_streak >= self.alternate_every:
                self._flip_local_webshare()
                self._success_streak = 0
        # Step down from dataimpulse after a calm streak — skip dead tiers.
        if self._tier == "dataimpulse" and self._success_streak >= self.step_down_after:
            if self.tiers.webshare and "webshare" not in self._blacklisted:
                self._set_tier("webshare", reason="step_down_from_dataimpulse")
            elif not self.skip_local and "local" not in self._blacklisted:
                self._set_tier("local", reason="step_down_from_dataimpulse")
            # else stay on dataimpulse
            self._success_streak = 0

    def note_failure(self, exc: BaseException | str | None = None) -> bool:
        """Record a scrape failure. Returns True if tier escalated."""
        self._queries += 1
        self._fail_streak += 1
        self._success_streak = 0
        if self.mode != "smart":
            return False
        text = str(exc or "")
        if is_proxy_auth_error(text):
            self._blacklist_current(reason=text[:160] or "407")
            return self._escalate(reason=text[:160] or "proxy_auth")
        rateish = bool(_RATE_LIMIT_RE.search(text)) or self._fail_streak >= 2
        if not rateish:
            return False
        return self._escalate(reason=text[:160] or "fail_streak")

    def note_soft_block(self) -> bool:
        """Escalate after empty/blocked-looking responses (JobSpy often doesn't raise)."""
        self._fail_streak += 1
        self._success_streak = 0
        if self.mode != "smart":
            return False
        if self._fail_streak < 2:
            return False
        return self._escalate(reason="consecutive_empty_or_soft_block")

    def _blacklist_current(self, *, reason: str) -> None:
        tier = self._tier
        if tier == "fixed" or tier in self._blacklisted:
            return
        if tier == "local":
            # Local has no credentials; don't permanent-blacklist AWS egress.
            return
        self._blacklisted.add(tier)
        # Clear the URL so available_names / escalate skip it cleanly.
        if tier == "webshare":
            self.tiers.webshare = ""
        elif tier == "dataimpulse":
            self.tiers.dataimpulse = ""
        _log.warning(
            "Scrape proxy tier permanently disabled for this process: %s (%s)",
            tier,
            reason,
        )

    def _tier_usable(self, name: str) -> bool:
        if name in self._blacklisted:
            return False
        if name == "local":
            return not self.skip_local
        if name == "webshare":
            return bool(self.tiers.webshare)
        if name == "dataimpulse":
            return bool(self.tiers.dataimpulse)
        return False

    def _first_usable_paid_tier(self) -> str | None:
        for name in ("webshare", "dataimpulse"):
            if self._tier_usable(name):
                return name
        return None

    def _flip_local_webshare(self) -> None:
        if self._tier == "local" and self._tier_usable("webshare"):
            self._set_tier("webshare", reason="alternate")
        elif self._tier == "webshare" and not self.skip_local:
            self._set_tier("local", reason="alternate")

    def _escalate(self, *, reason: str) -> bool:
        order = list(_TIER_ORDER)
        try:
            idx = order.index(self._tier)
        except ValueError:
            idx = 0
        for name in order[idx + 1 :]:
            if not self._tier_usable(name):
                continue
            self._set_tier(name, reason=f"escalate:{reason}")
            self._fail_streak = 0
            return True
        # If current is blacklisted, jump to any remaining usable tier (wrap).
        for name in order:
            if name == self._tier:
                continue
            if self._tier_usable(name):
                self._set_tier(name, reason=f"escalate_wrap:{reason}")
                self._fail_streak = 0
                return True
        _log.warning(
            "Scrape proxy escalate exhausted (tier=%s blacklisted=%s reason=%s)",
            self._tier,
            sorted(self._blacklisted),
            reason,
        )
        return False

    def _set_tier(self, name: str, *, reason: str) -> None:
        if name == self._tier:
            return
        prev = self._tier
        self._tier = name
        _log.info("Scrape proxy tier %s → %s (%s)", prev, name, reason)


def _env(*names: str) -> str:
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val:
            return val
    return ""


def _truthy(name: str, default: str = "0") -> bool:
    return (os.environ.get(name) or default).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _looks_dataimpulse(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = url.lower()
    return bool(_DATAIMPULSE_HOST_RE.search(host))


def _looks_webshare(url: str) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except Exception:
        host = url.lower()
    return "webshare" in host or "p.webshare.io" in host


def resolve_proxy_tiers() -> ProxyTier:
    """Build tiers from Infisical/env; absent keys are simply skipped.

    Production ladder:
      * webshare — static residential (apply/CapMonster sibling when needed)
      * dataimpulse tier — rotating residential (Proxy-Cheap / DataImpulse)
        used for discovery rate-limit escape only
    """
    webshare = _env("JOBSPY_PROXY_WEBSHARE", "WEBSHARE_PROXY_URL")
    dataimpulse = (
        _env("JOBSPY_PROXY_DATAIMPULSE", "DATAIMPULSE_PROXY_URL")
        or _env("PROXY_CHEAP_URL")
    )

    # Explicit kill-switch for a dead credential without editing Infisical.
    # Applied before generic classification so PROXY_URL still fills a tier.
    if _truthy("JOBSPY_DISABLE_WEBSHARE"):
        webshare = ""
    if _truthy("JOBSPY_DISABLE_DATAIMPULSE"):
        dataimpulse = ""

    # Classify generic PROXY_URL / CAPMONSTER_PROXY_URL into the right tier.
    # Prefer Webshare for sticky; treat Proxy-Cheap / unknown as rotating.
    generic = _env("PROXY_URL", "CAPMONSTER_PROXY_URL")
    if generic:
        if _looks_webshare(generic):
            if not webshare:
                webshare = generic
        elif not dataimpulse and (_looks_dataimpulse(generic) or "proxy-cheap" in generic.lower()):
            dataimpulse = generic
        elif not dataimpulse:
            # Unknown host: treat as rotating residential for discovery.
            if not webshare or generic != webshare:
                dataimpulse = generic

    return ProxyTier(webshare=webshare, dataimpulse=dataimpulse)


def resolve_fixed_proxy_urls() -> list[str]:
    raw = _env("JOBSPY_PROXY_URLS")
    if raw:
        return [p.strip() for p in raw.split(",") if p.strip()]
    # fixed mode without JOBSPY_PROXY_URLS → use best single residential URL
    if (_env("JOBSPY_PROXY_MODE") or "").strip().lower() == "fixed":
        single = (
            _env("JOBSPY_PROXY_DATAIMPULSE", "DATAIMPULSE_PROXY_URL")
            or _env("PROXY_URL", "CAPMONSTER_PROXY_URL")
            or _env("JOBSPY_PROXY_WEBSHARE", "WEBSHARE_PROXY_URL")
        )
        if single:
            return [single]
    return []


def build_scrape_proxy_ladder() -> ScrapeProxyLadder:
    mode = _env("JOBSPY_PROXY_MODE") or "smart"
    fixed = resolve_fixed_proxy_urls()
    alternate = int(_env("JOBSPY_PROXY_ALTERNATE_EVERY") or "3")
    step_down = int(_env("JOBSPY_PROXY_STEP_DOWN_AFTER") or "8")
    return ScrapeProxyLadder(
        tiers=resolve_proxy_tiers(),
        mode=mode,
        fixed_urls=fixed,
        alternate_every=max(1, alternate),
        step_down_after=max(1, step_down),
        skip_local=_truthy("JOBSPY_SKIP_LOCAL"),
    )


def is_rate_limit_error(exc: BaseException | str) -> bool:
    return bool(_RATE_LIMIT_RE.search(str(exc)))


def is_proxy_auth_error(exc: BaseException | str) -> bool:
    return bool(_AUTH_FAIL_RE.search(str(exc)))


def probe_proxy_url(url: str, *, timeout: float = 12.0) -> tuple[bool, str]:
    """HTTP GET through *url* to a cheap IP echo endpoint. Returns (ok, detail)."""
    if not url:
        return False, "empty_proxy_url"
    try:
        import urllib.request

        handler = urllib.request.ProxyHandler({"http": url, "https": url})
        opener = urllib.request.build_opener(handler)
        with opener.open("https://httpbin.org/ip", timeout=timeout) as resp:
            body = resp.read(120).decode("utf-8", "replace")
            return True, f"status={resp.status} body={body[:80]}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

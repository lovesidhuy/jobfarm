from __future__ import annotations

import time
from urllib.parse import urlparse

from jobbots.core.evasion._config import (
    _cap_log,
    _TURNSTILE_BBOX_RETRIES,
    _TURNSTILE_BBOX_WAIT,
    print_lg,
)

# ── Cloudflare page signatures ────────────────────────────────────────────────
_CF_TITLE_KEYWORDS = (
    "just a moment",
    "additional verification required",
    "security check",
    "checking your browser",
    "attention required",
    "verify you're human",
    "verify you are human",
)

_CF_SOURCE_SIGNATURES = (
    "challenges.cloudflare.com",
    "cf-turnstile",
    "cf_chl_opt",
    "verify you are human",
    "troubleshooting cloudflare errors",
)

# Indeed embeds `indeed_cloudflare_static_page` on real challenge pages only.
# Matching the string alone false-positives on normal job pages and triggers
# active solvers that re-create Cloudflare challenges.
_INDEED_CF_ACTIVE_PAGE_TYPES = (
    'page_type:"captcha"',
    "page_type:'captcha'",
    'page_type:"under_attack"',
    "page_type:'under_attack'",
    'page_type:"429"',
    "page_type:'429'",
    'page_type:"1xxx"',
    "page_type:'1xxx'",
    'page_type:"5xx"',
    "page_type:'5xx'",
)

_CF_TURNSTILE_SELECTORS = (
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "iframe[title*='turnstile' i]",
    ".cf-turnstile",
)

# ── reCAPTCHA v2 WIDGET (checkbox) selectors ─────────────────────────────────
_RECAPTCHA_WIDGET_SELECTORS = (
    "iframe[title='reCAPTCHA']",
    "iframe[src*='api2/anchor']",
    "iframe[src*='enterprise/anchor']",
    "#recaptcha-token",
    ".g-recaptcha",
)

# ── reCAPTCHA v2 IMAGE CHALLENGE (bframe) selectors ──────────────────────────
_RECAPTCHA_CHALLENGE_SELECTORS = (
    "iframe[title*='recaptcha challenge']",
    "iframe[src*='recaptcha/api2/bframe']",
    "iframe[src*='recaptcha/enterprise/bframe']",
    "iframe[src*='recaptcha.net/recaptcha']",
    "iframe[src*='google.com/recaptcha/enterprise/bframe']",
)

_RECAPTCHA_SOURCE_SIGNATURES = (
    "g-recaptcha",
    "recaptcha/api",
    "recaptcha__en",
    "window.invisiblerecaptchakey",
    "invisiblerecaptchakey",
    'id="recaptcha-token"',
)


def _page_url(page) -> str:
    try:
        return page.url or ""
    except Exception:
        return ""


def _url_host(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except Exception:
        return ""


def _same_url_family(candidate_url: str, target_url: str) -> bool:
    candidate_host = _url_host(candidate_url)
    target_host    = _url_host(target_url)
    return bool(candidate_host and target_host and candidate_host == target_host)


def _is_page_alive(p) -> bool:
    try:
        p.evaluate("1+1")
        return True
    except Exception:
        return False


def is_browser_alive(page) -> bool:
    """Return True if the Playwright browser/context behind ``page`` still has
    any live pages.  Used by search loops to bail out on browser crash."""
    if page is None:
        return False
    if _is_page_alive(page):
        return True
    try:
        ctx = page.context
    except Exception:
        return False
    try:
        browser = ctx.browser
        if browser is not None and not browser.is_connected():
            return False
    except Exception:
        pass
    try:
        return any(_is_page_alive(p) for p in ctx.pages)
    except Exception:
        return False


def _indeed_submit_button_ready(page) -> bool:
    try:
        return bool(page.evaluate(
            """
            () => {
                const selectors = [
                    "button[data-testid='submit-application-button']",
                    "button[data-testid*='submit']",
                    "button[data-testid*='Submit']",
                    "button[type='submit']",
                    "button[aria-label*='Submit']",
                    "button[aria-label*='submit']",
                ];
                let btn = document.querySelector(selectors.join(","));
                if (!btn) {
                    const buttons = Array.from(document.querySelectorAll("button"));
                    btn = buttons.find((button) => {
                        const text = (button.innerText || button.textContent || "").trim().toLowerCase();
                        return text.includes("submit your application") || text.includes("submit application") || text === "submit";
                    });
                }
                if (!btn) return false;
                const style    = window.getComputedStyle(btn);
                const disabled = Boolean(btn.disabled || btn.getAttribute("aria-disabled") === "true");
                const hidden   = style.display === "none" || style.visibility === "hidden" || style.opacity === "0";
                return !disabled && !hidden;
            }
            """
        ))
    except Exception:
        return False


def _has_recaptcha_response_token(page) -> bool:
    try:
        token = page.evaluate(
            """
            () => {
                const field = document.querySelector(
                    "textarea[name='g-recaptcha-response'], textarea#g-recaptcha-response"
                );
                return field ? field.value : "";
            }
            """
        )
        return bool(token and len(str(token)) > 100)
    except Exception:
        return False


def is_recaptcha_expired(page) -> bool:
    try:
        body = page.query_selector("body")
        text = (body.inner_text() if body else page.content()).lower()
        return (
            "verification challenge expired" in text
            or "check the checkbox again"    in text
            or "verification expired"        in text
            or "expired. check"              in text
        )
    except Exception:
        return False


def is_recaptcha_widget_present(page) -> bool:
    if _has_recaptcha_response_token(page) and not is_recaptcha_expired(page):
        return False
    try:
        for sel in _RECAPTCHA_WIDGET_SELECTORS:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
    except Exception:
        pass
    return False


def is_indeed_waf_ip_block(page) -> bool:
    """True when Indeed shows a hard WAF block (not a solvable Turnstile checkbox)."""
    try:
        title = (page.title() or "").lower()
        if "blocked - indeed" in title:
            return True
        source = (page.content() or "").lower()
        if 'page_type:"waf_block"' in source or "page_type:'waf_block'" in source:
            return True
        if "you have been blocked" in source and "ray id for this request" in source:
            return True
        if "request blocked" in source and "indeed_cloudflare_static_page" in source:
            return True
    except Exception:
        pass
    return False


def _has_visible_turnstile_widget(page) -> bool:
    try:
        for sel in _CF_TURNSTILE_SELECTORS:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
    except Exception:
        pass
    return False


def _indeed_static_cloudflare_challenge(source: str) -> bool:
    lower = (source or "").lower()
    if "indeed_cloudflare_static_page" not in lower:
        return False
    return any(marker in lower for marker in _INDEED_CF_ACTIVE_PAGE_TYPES)


def is_cloudflare_challenge(page) -> bool:
    if is_indeed_waf_ip_block(page):
        return True
    try:
        title = (page.title() or "").lower()
        if any(kw in title for kw in _CF_TITLE_KEYWORDS):
            return True
        if "authenticating" in title:
            return True
        if _has_visible_turnstile_widget(page):
            return True
        source = (page.content() or "").lower()
        if _indeed_static_cloudflare_challenge(source):
            return True
        if "challenges.cloudflare.com" in source and (
            "cf-turnstile" in source or "challenge-platform" in source
        ):
            return True
        if any(sig in source for sig in _CF_SOURCE_SIGNATURES):
            # cf_chl_opt / verify strings appear on real challenge HTML only.
            return True
    except Exception:
        pass
    return False


def is_recaptcha_challenge(page) -> bool:
    try:
        for sel in _RECAPTCHA_CHALLENGE_SELECTORS:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return True
        source = (page.content() or "").lower()
        if any(sig in source for sig in _RECAPTCHA_SOURCE_SIGNATURES):
            for sel in _RECAPTCHA_CHALLENGE_SELECTORS:
                el = page.query_selector(sel)
                if el and el.is_visible():
                    return True
    except Exception:
        pass
    return False


def try_recover_page(page, prefer_page=None, allow_any=True):
    """
    After a SeleniumBase GUI CAPTCHA bypass the original Playwright Page object
    can become stale (Cloudflare destroys the CDP target on redirect).  This
    function returns the freshest live non-blocked page from the same context.
    """
    if _is_page_alive(page):
        if not prefer_page or prefer_page(page):
            return page

    print_lg("[CAPTCHA] ℹ Page stale after CAPTCHA bypass — recovering…")

    deadline                = time.time() + 15.0
    fallback_page           = None
    logged_wait_for_redirect = False
    target_url              = _page_url(page)

    while time.time() < deadline:
        try:
            ctx        = page.context
            live_pages = [p for p in reversed(ctx.pages) if _is_page_alive(p)]
            if not live_pages:
                time.sleep(0.5)
                continue

            if prefer_page:
                filtered_pages = [p for p in live_pages if prefer_page(p)]
                if filtered_pages:
                    live_pages = filtered_pages
                elif not allow_any:
                    time.sleep(0.5)
                    continue

            same_family_pages = [
                p for p in live_pages
                if not target_url or _same_url_family(_page_url(p), target_url)
            ]
            candidates    = same_family_pages or live_pages
            fallback_page = candidates[0]

            for fresh in candidates:
                if is_cloudflare_challenge(fresh):
                    continue
                try:
                    fresh.wait_for_load_state("domcontentloaded", timeout=1500)
                except Exception:
                    pass
                try:
                    fresh_url = fresh.url
                except Exception:
                    fresh_url = "?"
                print_lg(f"[CAPTCHA] ✓ Recovered live Playwright page: {fresh_url}")
                return fresh

            if not logged_wait_for_redirect:
                print_lg("[CAPTCHA] ℹ Recovered page still shows Cloudflare — waiting for redirect…")
                logged_wait_for_redirect = True
        except Exception as ex:
            print_lg(f"[CAPTCHA] Page recovery error: {ex}")
            break

        time.sleep(0.5)

    if fallback_page is not None:
        try:
            fallback_url = fallback_page.url
        except Exception:
            fallback_url = "?"
        print_lg(f"[CAPTCHA] ⚠ Recovery found a live page but it still appears blocked: {fallback_url}")
        return fallback_page

    print_lg("[CAPTCHA] ✗ No live pages in context — using original (may fail).")
    return page


def _finalize_cf_attempt(page, source_label: str) -> bool:
    try:
        start = time.time()
        time.sleep(2)  # _POLL_INTERVAL + 1 = 1 + 1 = 2
        recovered = try_recover_page(page)
        if not is_cloudflare_challenge(recovered):
            _cap_log(f"✓ Cloudflare bypassed via {source_label}.", start)
            time.sleep(1.5)
            try:
                recovered.wait_for_load_state("domcontentloaded", timeout=6000)
            except Exception:
                pass
            return True
    except Exception as e:
        print_lg(f"[CAPTCHA] {source_label} post-check failed: {e}")
    return False


def _get_latest_live_page(page):
    target_url = _page_url(page)
    if _is_page_alive(page):
        return page
    try:
        ctx        = page.context
        live_pages = [p for p in reversed(ctx.pages) if _is_page_alive(p)]
        if target_url:
            for fresh in live_pages:
                if _same_url_family(_page_url(fresh), target_url):
                    return fresh
        if live_pages:
            return live_pages[0]
    except Exception:
        pass
    return try_recover_page(page)


def _wait_for_turnstile_widget(page,
                               retries: int = _TURNSTILE_BBOX_RETRIES,
                               wait: float  = _TURNSTILE_BBOX_WAIT) -> dict | None:
    """
    Wait for the Turnstile iframe to be in the DOM with a valid bounding box.

    Returns the bounding box dict on success, None on timeout.
    The production file used to return False immediately on box=None; this
    retry loop prevents spurious failures when the iframe loads slowly.
    """
    widget_selector = (
        "iframe[src*='challenges.cloudflare.com'], "
        "iframe[src*='turnstile'], "
        "iframe[src*='cloudflare'], "
        "iframe[title*='challenge' i], "
        "iframe[title*='turnstile' i], "
        "iframe[title*='cloudflare' i], "
        "iframe[title*='verify' i], "
        ".cf-turnstile, "
        "[data-sitekey], "
        "[class*='turnstile' i], "
        "[id*='turnstile' i], "
        "[role='checkbox'], "
        "input[type='checkbox']"
    )
    for attempt in range(1, retries + 1):
        try:
            widget_loc = page.locator(widget_selector).first
            if widget_loc.count() == 0:
                _cap_log(f"Turnstile widget not found yet (attempt {attempt}/{retries}).")
                time.sleep(wait)
                continue
            try:
                widget_loc.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass
            box = widget_loc.bounding_box()
            if box:
                return box
            _cap_log(f"Turnstile widget has no bounding box yet (attempt {attempt}/{retries}).")
        except Exception as e:
            _cap_log(f"Turnstile widget wait error (attempt {attempt}/{retries}): {e}")
        time.sleep(wait)
    return None


def _cloudflare_heuristic_checkbox_box(page) -> dict | None:
    """
    Indeed's Cloudflare interstitial can render the visible checkbox without a
    selector Playwright can see. In that case, use the stable page layout:
    the checkbox is just left of the "Verify you are human" label, below the
    "Additional Verification Required" heading.
    """
    try:
        return page.evaluate(
            """
            () => {
              const visible = el => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 &&
                       s.visibility !== 'hidden' && s.display !== 'none';
              };
              const textOf = el => (el.innerText || el.textContent || '').trim();
              const candidates = Array.from(document.querySelectorAll('body *'))
                .filter(visible)
                .map(el => {
                  const rect = el.getBoundingClientRect();
                  return {el, text: textOf(el), rect, area: rect.width * rect.height};
                })
                .filter(c => c.rect.top >= 0 && c.rect.top < window.innerHeight);

              const smallestTextMatch = regex => candidates
                .filter(c => regex.test(c.text))
                .sort((a, b) => a.area - b.area)[0];

              const label = smallestTextMatch(/verify\\s+you\\s+are\\s+human/i);
              if (label) {
                const r = label.rect;
                return {
                  x: Math.max(0, r.left - 48),
                  y: Math.max(0, r.top + (r.height / 2) - 17),
                  width: 34,
                  height: 34,
                  source: 'verify_text'
                };
              }

              const heading = smallestTextMatch(/additional\\s+verification\\s+required/i);
              if (heading) {
                const r = heading.rect;
                const x = (window.innerWidth / 2) - 210;
                const y = r.bottom + 116;
                return {x, y, width: 34, height: 34, source: 'indeed_heading_layout'};
              }

              if (/additional\\s+verification\\s+required/i.test(document.body.innerText || '')) {
                return {
                  x: (window.innerWidth / 2) - 210,
                  y: Math.max(260, window.innerHeight * 0.34),
                  width: 34,
                  height: 34,
                  source: 'indeed_viewport_layout'
                };
              }
              return null;
            }
            """
        )
    except Exception as e:
        _cap_log(f"Cloudflare heuristic box lookup failed: {e}")
        return None

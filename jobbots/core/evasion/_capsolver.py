from __future__ import annotations

import os
import time
import hashlib
import requests
from urllib.parse import unquote, urlparse

from jobbots.core.secret_manager import get_secret
from jobbots.core.utils import print_lg
from jobbots.core.evasion._config import (
    _CAPSOLVER_CREATE_TASK_URL,
    _CAPSOLVER_GET_RESULT_URL,
    _PROJECT_ROOT,
    _cap_log,
    _truthy,
)
from jobbots.core.evasion._capmonster import (
    _extract_recaptcha_params,
    _inject_recaptcha_token,
    _extract_turnstile_params,
    _inject_turnstile_token,
    _extract_hcaptcha_params,
    _inject_hcaptcha_token,
    _get_page_user_agent,
    _get_page_cookies,
    _apply_capmonster_cf_clearance,
)

# Common Cloudflare managed-challenge sitekey used when the widget is not yet
# visible in the DOM (Indeed SmartApply "Additional Verification Required").
_DEFAULT_CF_SITEKEY = "0x4AAAAAAADnBwMwJC38uztB"

_CAPSOLVER_TIMEOUT = 120
_CAPSOLVER_POLL_INTERVAL = 2


def _read_dotenv_value(name: str) -> str:
    try:
        env_path = _PROJECT_ROOT / ".env"
        if not env_path.exists():
            return ""
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key.strip() == name:
                return value.strip().strip('"').strip("'")
    except Exception:
        return ""
    return ""


def _secret_or_env(name: str, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    try:
        value = get_secret(name, "")
        if value:
            return str(value).strip()
    except Exception:
        pass
    return _read_dotenv_value(name) or default


def _capsolver_client_key() -> str:
    """Retrieve active CapSolver API key from env or Infisical."""
    return (
        _secret_or_env("CAPSOLVER_API_KEY")
        or _secret_or_env("CAPSOLVER_CLIENT_KEY")
        or _secret_or_env("capsolver_key")
        or ""
    ).strip()


def _capsolver_proxy_parts() -> dict | None:
    """Parse sticky proxy credentials for CapSolver (same IP as the browser).

    Prefer CAPSOLVER_PROXY_URL when set, otherwise the browser-aligned CapMonster
    proxy (Proxy-Cheap for Indeed/Glassdoor/Workopolis, Webshare elsewhere).
    """
    from jobbots.core.secret_manager import (
        get_browser_proxy_url,
        get_capmonster_proxy_url,
        normalize_proxy_url,
    )

    explicit = (
        _secret_or_env("CAPSOLVER_PROXY_URL")
        or _secret_or_env("CAPMONSTER_PROXY_URL")
    )
    if _truthy(_secret_or_env("BYPASS_PROXY", "0")) and not explicit:
        return None

    proxy_url = explicit
    if not proxy_url:
        try:
            proxy_url = get_capmonster_proxy_url() or get_browser_proxy_url() or ""
        except Exception:
            proxy_url = ""
    if not proxy_url:
        proxy_url = (
            _secret_or_env("WEBSHARE_PROXY_URL")
            or _secret_or_env("PROXY_URL")
            or _secret_or_env("PROXY_CHEAP_URL")
        )
    if not proxy_url:
        return None

    normalized = normalize_proxy_url(proxy_url).rstrip("/")
    try:
        parsed = urlparse(normalized)
        host = parsed.hostname or ""
        port = parsed.port
        user = unquote(parsed.username or "")
        password = unquote(parsed.password or "")
        if host and port:
            return {
                "host": host,
                "port": int(port),
                "user": user,
                "password": password,
                "raw": normalized,
            }
    except Exception:
        return None
    return None


def _capsolver_proxy_str() -> str:
    """Return CapSolver proxy string (prefer host:port:user:pass)."""
    parts = _capsolver_proxy_parts()
    if not parts:
        return ""
    host, port = parts["host"], parts["port"]
    user, password = parts["user"], parts["password"]
    if user and password:
        # CapSolver AntiCloudflareTask docs prefer ip:port:user:pass
        return f"{host}:{port}:{user}:{password}"
    return f"{host}:{port}"


def _capsolver_proxy_task_overlays() -> list[tuple[str, dict]]:
    """
    CapSolver proxy encodings for reCAPTCHA / hCaptcha tasks.

    createTask accepts several formats; some CapSolver worker pools refuse one
    encoding but accept another. CapSolver's reCAPTCHA documentation specifies
    the single canonical ``http:ip:port:user:pass`` proxy field first.
    """
    parts = _capsolver_proxy_parts()
    if not parts:
        return []
    host, port = parts["host"], parts["port"]
    user, password = parts["user"], parts["password"]
    overlays: list[tuple[str, dict]] = []
    if user and password:
        # CapSolver's documented reCAPTCHA form.  Try this before the legacy
        # discrete-field overlay: the latter can create a task but later fail
        # validation for Enterprise widgets.
        overlays.append(
            ("http:ip:port:user:pass", {"proxy": f"http:{host}:{port}:{user}:{password}"})
        )
        overlays.append(
            (
                "fields http",
                {
                    "proxyType": "http",
                    "proxyAddress": host,
                    "proxyPort": port,
                    "proxyLogin": user,
                    "proxyPassword": password,
                },
            )
        )
        overlays.append(
            ("ip:port:user:pass", {"proxy": f"{host}:{port}:{user}:{password}"})
        )
        overlays.append(
            ("http://user:pass@ip:port", {"proxy": f"http://{user}:{password}@{host}:{port}"})
        )
    else:
        overlays.append(("ip:port", {"proxy": f"{host}:{port}"}))
    # Cap hard so a flaky proxy does not burn 3+ minutes of format roulette.
    return overlays[:4]


def _capsolver_anticloudflare_proxy_overlays() -> list[tuple[str, dict]]:
    """
    Proxy encodings for AntiCloudflareTask.

    CapSolver CF docs use bare ``ip:port:user:pass`` as the canonical form.
    Try that first, then http: prefix and discrete fields if workers refuse.
    """
    parts = _capsolver_proxy_parts()
    if not parts:
        return []
    host, port = parts["host"], parts["port"]
    user, password = parts["user"], parts["password"]
    overlays: list[tuple[str, dict]] = []
    if user and password:
        overlays.append(
            ("ip:port:user:pass", {"proxy": f"{host}:{port}:{user}:{password}"})
        )
        overlays.append(
            ("http:ip:port:user:pass", {"proxy": f"http:{host}:{port}:{user}:{password}"})
        )
        overlays.append(
            (
                "fields http",
                {
                    "proxyType": "http",
                    "proxyAddress": host,
                    "proxyPort": port,
                    "proxyLogin": user,
                    "proxyPassword": password,
                },
            )
        )
    else:
        overlays.append(("ip:port", {"proxy": f"{host}:{port}"}))
    return overlays[:3]


def _is_capsolver_proxy_error(error_code: str | None) -> bool:
    code = (error_code or "").upper()
    return "PROXY" in code


def _is_capsolver_unsupported_service(error_code: str | None) -> bool:
    reason = (error_code or "").upper()
    return (
        "DON'T SUPPORT THIS SERVICE" in reason
        or "DO NOT SUPPORT THIS SERVICE" in reason
    )


def _page_html_for_cf(page) -> str:
    """Best-effort HTML snapshot for CapSolver AntiCloudflareTask."""
    try:
        html = page.content()
        if html and len(html) > 200:
            return html
    except Exception:
        pass
    try:
        return page.evaluate("() => document.documentElement.outerHTML") or ""
    except Exception:
        return ""


def _cookies_for_capsolver(cookie_header: str) -> list[dict[str, str]]:
    """CapSolver expects cookies as [{name, value}, ...] — not CapMonster's 'a=b; c=d' string.

    Sending a string yields HTTP 400: ERROR_INVALID_TASK_DATA 'request body must be a json raw'.
    """
    out: list[dict[str, str]] = []
    if not cookie_header or not isinstance(cookie_header, str):
        return out
    for part in cookie_header.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        # CapSolver rejects huge/junk cookie bags; keep only common session markers.
        low = name.lower()
        if low.startswith(("cf_", "__cf", "session", "auth", "jwt", "sid", "token")) or low in {
            "indeed_csfr",
            "indeed_csrf",
            "csrf",
            "jessionid",
            "jsessionid",
            "aws-waf-token",
        } or "clearance" in low or "session" in low:
            out.append({"name": name, "value": value[:4000]})
        if len(out) >= 12:
            break
    return out


def _create_capsolver_task(client_key: str, task: dict) -> str | None:
    """Create a task on CapSolver API and return taskId."""
    _create_capsolver_task.last_error_code = None
    try:
        task_type = task.get("type", "unknown")
        print_lg(f"[CAPTCHA] Creating CapSolver task ({task_type}) for {task.get('websiteURL', '')}")
        # Explicit JSON body — CapSolver is picky about Content-Type / structure.
        payload = {"clientKey": client_key, "task": task}
        response = requests.post(
            _CAPSOLVER_CREATE_TASK_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30,
        )
        try:
            data = response.json()
        except Exception:
            data = {}
        if response.status_code >= 400:
            print_lg(
                f"[CAPTCHA] CapSolver createTask HTTP {response.status_code}: "
                f"{(response.text or '')[:300]}"
            )
            if data.get("errorDescription") or data.get("errorCode"):
                error_code = data.get("errorCode") or data.get("errorDescription")
                desc = (data.get("errorDescription") or "").strip()
                _create_capsolver_task.last_error_code = (
                    f"{error_code}: {desc}"
                    if desc and str(error_code) not in desc
                    else str(error_code or "")
                )
                print_lg(
                    f"[CAPTCHA] CapSolver createTask error: "
                    f"{data.get('errorCode') or ''} {data.get('errorDescription') or ''}"
                )
            return None
        response.raise_for_status()
    except Exception as exc:
        print_lg(f"[CAPTCHA] CapSolver createTask failed: {exc}")
        return None

    if data.get("errorId", 0) != 0:
        error_code = data.get("errorCode") or data.get("errorDescription")
        desc = (data.get("errorDescription") or "").strip()
        _create_capsolver_task.last_error_code = (
            f"{error_code}: {desc}"
            if desc and str(error_code) not in desc
            else str(error_code or "")
        )
        print_lg(f"[CAPTCHA] CapSolver createTask error: {error_code}")
        return None

    task_id = data.get("taskId")
    print_lg(f"[CAPTCHA] CapSolver task created; taskId={task_id}")
    return str(task_id) if task_id else None


_create_capsolver_task.last_error_code = None


def _poll_capsolver_result(client_key: str, task_id: str, timeout: int = _CAPSOLVER_TIMEOUT) -> dict | None:
    """Poll CapSolver for solution until ready or timeout."""
    _poll_capsolver_result.last_error_code = None
    start = time.time()
    deadline = start + timeout
    last_log = 0

    while time.time() < deadline:
        time.sleep(_CAPSOLVER_POLL_INTERVAL)
        try:
            response = requests.post(
                _CAPSOLVER_GET_RESULT_URL,
                json={"clientKey": client_key, "taskId": task_id},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            print_lg(f"[CAPTCHA] CapSolver getTaskResult network error: {exc}")
            continue

        if data.get("errorId", 0) != 0:
            error_code = data.get("errorCode") or data.get("errorDescription")
            _poll_capsolver_result.last_error_code = str(error_code or "")
            print_lg(f"[CAPTCHA] CapSolver getTaskResult error: {error_code}")
            return None

        status = data.get("status")
        if status == "ready":
            solution = data.get("solution") or {}
            print_lg(f"[CAPTCHA] CapSolver task solved in {time.time() - start:.1f}s!")
            return solution

        elapsed = int(time.time() - start)
        if elapsed >= last_log + 10:
            remaining = max(0, int(deadline - time.time()))
            print_lg(f"[CAPTCHA] CapSolver still processing taskId={task_id}... ({remaining}s remaining)")
            last_log = elapsed

    _poll_capsolver_result.last_error_code = "TIMEOUT"
    print_lg(f"[CAPTCHA] CapSolver timed out after {timeout}s for taskId={task_id}")
    return None


_poll_capsolver_result.last_error_code = None


def _is_ip_bound_recaptcha_url(url: str) -> bool:
    """Indeed / Glassdoor / Workopolis bind reCAPTCHA tokens to browser egress IP."""
    host = ""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        host = (url or "").lower()
    markers = (
        "indeed.",
        "smartapply.indeed.",
        "glassdoor.",
        "workopolis.",
        "eluta.",
    )
    return any(m in host for m in markers) or "indeed" in host or "glassdoor" in host


def _recaptcha_url_candidates(website_url: str, page_url: str = "") -> list[str]:
    """
    CapSolver requires the exact page URL for SmartApply's v2 Enterprise
    challenge.  Try the full form URL first; the origin remains a fallback.
    CapSolver docs prefer full URL when origin fails.
    """
    fulls: list[str] = []
    origins: list[str] = []
    for raw in (website_url, page_url):
        u = (raw or "").strip()
        if not u:
            continue
        if u not in fulls:
            fulls.append(u)
        try:
            parsed = urlparse(u)
            if parsed.scheme and parsed.netloc:
                origin = f"{parsed.scheme}://{parsed.netloc}/"
                if origin not in origins:
                    origins.append(origin)
        except Exception:
            pass
    # Full page first: SmartApply binds the challenge metadata to this path.
    out: list[str] = []
    for u in fulls + origins:
        if u not in out:
            out.append(u)
    return out or [website_url]


def _apply_capsolver_recaptcha_solution_cookies(page, solution: dict) -> None:
    """Apply recaptcha-ca-e / recaptcha-ca-t cookies CapSolver may return (CapMonster parity)."""
    if not solution:
        return
    from jobbots.core.evasion._capmonster import _apply_capmonster_cookies

    bag: dict[str, str] = {}
    cookies = solution.get("cookies")
    if isinstance(cookies, dict):
        for k, v in cookies.items():
            if k and v is not None:
                bag[str(k)] = str(v)
    elif isinstance(cookies, list):
        for item in cookies:
            if isinstance(item, dict) and item.get("name"):
                bag[str(item["name"])] = str(item.get("value") or "")
    for key in ("recaptcha-ca-e", "recaptcha-ca-t"):
        val = solution.get(key)
        if val:
            bag[key] = str(val)
    if bag:
        try:
            _apply_capmonster_cookies(page, bag)
        except Exception as exc:
            print_lg(f"[CAPTCHA] CapSolver reCAPTCHA cookie apply failed: {exc}")


def _recaptcha_checkbox_verified(page) -> bool:
    """CapSolver Playwright guide: checkbox complete when border is hidden / aria-checked."""
    try:
        return bool(
            page.evaluate(
                """
                () => {
                    const ta = document.querySelector(
                        "textarea[name='g-recaptcha-response'], textarea#g-recaptcha-response"
                    );
                    if (ta && (ta.value || "").trim().length > 50) {
                        // Token present is necessary but not always sufficient.
                    }
                    const frames = Array.from(
                        document.querySelectorAll("iframe[src*='recaptcha'], iframe[title='reCAPTCHA']")
                    );
                    for (const frame of frames) {
                        try {
                            const doc = frame.contentDocument || frame.contentWindow?.document;
                            if (!doc) continue;
                            const checked = doc.querySelector(
                                ".recaptcha-checkbox-checked, [aria-checked='true']"
                            );
                            if (checked) return true;
                            const border = doc.querySelector(".recaptcha-checkbox-border");
                            if (border) {
                                const display = window.getComputedStyle(border).display;
                                if (display === "none") return true;
                            }
                        } catch (err) {
                            // cross-origin — ignore
                        }
                    }
                    return false;
                }
                """
            )
        )
    except Exception:
        return False


def _wait_for_extension_recaptcha_token(page, timeout: int) -> bool:
    """Allow an installed browser solver to complete the active *visible* v2 widget.

    SmartApply has an unrelated invisible v3 response field as well.  Checking
    only the base ``g-recaptcha-response`` field ensures that a v3 token does
    not falsely clear the visible image gate.
    """
    from jobbots.core.evasion._detection import _indeed_submit_button_ready

    # Never click the checkbox or its Verify button.  Those interactions can
    # open a visual image challenge and leave the application in an ambiguous
    # state.  We only observe a response that is already present on the page.
    print_lg("[CAPTCHA] Waiting for an existing visible reCAPTCHA response; not clicking widget.")

    deadline = time.time() + max(0, int(timeout or 0))
    while time.time() < deadline:
        try:
            if _indeed_submit_button_ready(page):
                print_lg("[CAPTCHA] Submit button is enabled/ready during extension wait — continuing to submit.")
                return True
            token_len = page.evaluate(
                """
                () => {
                    const field = document.querySelector(
                        "textarea[name='g-recaptcha-response'], textarea#g-recaptcha-response"
                    );
                    return field && field.value ? String(field.value).trim().length : 0;
                }
                """
            )
            if int(token_len or 0) > 100:
                print_lg(
                    f"[CAPTCHA] Browser extension supplied visible reCAPTCHA token "
                    f"({token_len} chars); continuing to SmartApply submit."
                )
                return True
        except Exception:
            return False
        time.sleep(1)
    return False


def _finalize_capsolver_recaptcha_token(page, solution: dict) -> bool:
    """
    CapMonster + CapSolver Playwright API path:
      cookies → inject g-recaptcha-response → click Verify → wait clear / submit ready.

    CapSolver Playwright blog injects into #g-recaptcha-response then submits;
    CapMonster also fires grecaptcha callbacks (our _inject_recaptcha_token).
    """
    from jobbots.core.evasion._capmonster import _wait_for_recaptcha_clearance
    from jobbots.core.evasion._detection import (
        _indeed_submit_button_ready,
        is_recaptcha_expired,
    )

    _apply_capsolver_recaptcha_solution_cookies(page, solution)

    token = solution.get("gRecaptchaResponse") or solution.get("token")
    if not token:
        print_lg("[CAPTCHA] CapSolver solution ready but no token found.")
        return False

    # CapSolver Playwright sample: set #g-recaptcha-response (+ our callback invoke).
    if not _inject_recaptcha_token(page, str(token)):
        print_lg("[CAPTCHA] CapSolver reCAPTCHA token injection failed.")
        return False
    print_lg(f"[CAPTCHA] CapSolver reCAPTCHA token injected ({len(str(token))} chars).")

    try:
        if is_recaptcha_expired(page):
            print_lg("[CAPTCHA] reCAPTCHA says verification expired after CapSolver inject.")
            return False
    except Exception:
        pass

    try:
        if _wait_for_recaptcha_clearance(page, timeout=10):
            print_lg("[CAPTCHA] ✓ CapSolver reCAPTCHA token accepted.")
            return True
    except Exception as exc:
        print_lg(f"[CAPTCHA] CapSolver clearance wait failed: {exc}")

    try:
        if _indeed_submit_button_ready(page):
            print_lg(
                "[CAPTCHA] Submit ready after CapSolver token — "
                "continuing SmartApply submit flow."
            )
            return True
    except Exception:
        pass

    if _recaptcha_checkbox_verified(page):
        print_lg("[CAPTCHA] ✓ CapSolver reCAPTCHA checkbox verified after inject.")
        return True

    # CapSolver Playwright guide: set #g-recaptcha-response then submit.
    # Greenhouse/Indeed often leave the image tile UI visible even when the
    # token is already accepted for form POST. Soft-success if token is in DOM.
    try:
        token_len = page.evaluate(
            """
            () => {
              const names = ["g-recaptcha-response", "g-recaptcha-response-100000"];
              let best = 0;
              for (const name of names) {
                const ta = document.querySelector(`textarea[name="${name}"], textarea#${name}`);
                if (ta && ta.value) best = Math.max(best, String(ta.value).length);
              }
              return best;
            }
            """
        )
        if int(token_len or 0) > 100:
            print_lg(
                f"[CAPTCHA] CapSolver token present in DOM ({token_len} chars) — "
                "treating as solved for form submit (UI may still show tiles)."
            )
            return True
    except Exception:
        pass

    print_lg(
        "[CAPTCHA] CapSolver token inject did not land a usable g-recaptcha-response."
    )
    return False


def solve_recaptcha_with_capsolver(page, timeout: int = _CAPSOLVER_TIMEOUT) -> bool:
    """Solve reCAPTCHA v2 / Enterprise using CapSolver — CapMonster-parity flow.

    CapMonster taught us for Indeed apply:
      * full page URL (not just origin)
      * browser User-Agent + session cookies on the task
      * enterprisePayload.s / recaptchaDataSValue when present
      * sticky browser proxy so token IP == Webshare (72.x) / Proxy-Cheap
      * never ProxyLess on Indeed (token IP binding rejects CapSolver's IP)
      * enterprise refuse → standard ReCaptchaV2Task on same proxy
      * inject + verify click + wait for clear / submit ready
    """
    from jobbots.core.evasion._detection import _indeed_submit_button_ready

    if _indeed_submit_button_ready(page):
        print_lg("[CAPTCHA] Submit button already ready/clickable before CapSolver task — continuing to submit.")
        return True

    # When the CapSolver browser extension is installed in the active NST
    # profile, it injects the visible v2 response directly into the page.  Give
    # it a short, explicit window before making a duplicate API task.
    extension_wait = _secret_or_env("CAPSOLVER_EXTENSION_WAIT_SECONDS", "0")
    try:
        extension_wait_seconds = min(max(int(extension_wait or 0), 0), 45)
    except (TypeError, ValueError):
        extension_wait_seconds = 0
    if extension_wait_seconds and _wait_for_extension_recaptcha_token(
        page, extension_wait_seconds
    ):
        return True

    if _indeed_submit_button_ready(page):
        print_lg("[CAPTCHA] Submit button ready after extension check — continuing to submit.")
        return True

    client_key = _capsolver_client_key()
    if not client_key:
        print_lg("[CAPTCHA] CapSolver API key not configured.")
        return False

    params = _extract_recaptcha_params(page)
    website_key = (params.get("websiteKey") or "").strip()
    page_url = ""
    try:
        page_url = (page.url or "").strip()
    except Exception:
        page_url = ""
    website_url = (params.get("websiteURL") or page_url or "").strip()
    if not website_key or not website_url:
        print_lg("[CAPTCHA] CapSolver reCAPTCHA skipped: missing website key or URL.")
        return False

    is_enterprise = bool(params.get("isEnterprise"))
    ip_bound = _is_ip_bound_recaptcha_url(website_url) or _is_ip_bound_recaptcha_url(page_url)
    proxy_overlays = _capsolver_proxy_task_overlays()
    user_agent = ""
    try:
        user_agent = _get_page_user_agent(page) or ""
    except Exception:
        user_agent = ""
    cookies = ""
    try:
        cookies = _get_page_cookies(page) or ""
    except Exception:
        cookies = ""

    data_s = (
        (params.get("recaptchaDataSValue") or "").strip()
        or str((params.get("enterprisePayload") or {}).get("s") or "").strip()
    )
    s_fingerprint = hashlib.sha256(data_s.encode()).hexdigest()[:12] if data_s else "none"
    enterprise_payload = {}
    if isinstance(params.get("enterprisePayload"), dict):
        # CapMonster only needs ``s`` for enterprise; extra keys can confuse CapSolver.
        if data_s:
            enterprise_payload = {"s": data_s}
        elif params["enterprisePayload"].get("s"):
            enterprise_payload = {"s": str(params["enterprisePayload"]["s"])}

    # CapMonster-style logging so farm journals are greppable.
    print_lg(
        "[CAPTCHA] CapSolver reCAPTCHA params: "
        f"type={'enterprise' if is_enterprise else 'standard'}, "
        f"ip_bound={ip_bound}, "
        f"invisible={bool(params.get('isInvisible'))}, "
        f"sitekey={'yes' if website_key else 'no'}, "
        f"data-s={'yes' if data_s else 'no'}, "
        f"cookies={'yes' if cookies else 'no'}, "
        f"ua={'yes' if user_agent else 'no'}, "
        f"proxy_formats={len(proxy_overlays)}, "
        f"url={website_url[:120]}"
    )
    print_lg(
        "[CAPTCHA] CapSolver extracted widget: "
        f"sitekey={website_key[:12]}…, action={str(params.get('pageAction') or '')[:120]!r}, "
        f"enterprise={is_enterprise}, invisible={bool(params.get('isInvisible'))}, "
        f"s_len={len(data_s)}, s_sha256={s_fingerprint}"
    )

    if ip_bound and not proxy_overlays:
        print_lg(
            "[CAPTCHA] CapSolver Indeed/Glassdoor reCAPTCHA requires sticky proxy "
            "(CAPSOLVER_PROXY_URL / CAPMONSTER_PROXY_URL / WEBSHARE = browser IP). "
            "ProxyLess tokens are rejected by Indeed."
        )
        return False

    # Enterprise + sticky proxy often needs 60–120s; CapMonster used long polls too.
    timeout = min(int(timeout or _CAPSOLVER_TIMEOUT), 120)
    per_attempt_timeout = timeout if proxy_overlays else min(timeout, 60)
    url_candidates = _recaptcha_url_candidates(website_url, page_url)

    def _base_task(task_type: str, *, force_standard: bool = False, url: str = "") -> dict:
        """CapSolver ReCaptchaV2 task shape (docs + Playwright blog API sample)."""
        task: dict = {
            "type": task_type,
            "websiteURL": (url or website_url).strip(),
            "websiteKey": website_key,
        }
        if params.get("isInvisible"):
            task["isInvisible"] = True

        # Enterprise fields only on Enterprise task types.
        use_enterprise_fields = (
            not force_standard
            and is_enterprise
            and "Enterprise" in task_type
        )
        # CapSolver enterprisePayload.s can be large; keep under a safe cap.
        s_val = (data_s or "")[:8000]
        if use_enterprise_fields and s_val:
            task["enterprisePayload"] = {"s": s_val}
        elif "Enterprise" not in task_type and s_val:
            # Standard V2: docs support recaptchaDataSValue for the anchor ``s`` param.
            task["recaptchaDataSValue"] = s_val

        # For V2, CapSolver expects pageAction when the current reCAPTCHA
        # anchor exposes its ``sa`` action parameter.  Omitting it produces a
        # task ID, but Indeed's Enterprise task is later rejected as invalid.
        action = str(params.get("pageAction") or "").strip()
        if action:
            task["pageAction"] = action[:512]
        # apiDomain often confuses CapSolver workers — only send google/recaptcha hosts.
        if params.get("apiDomain") and not force_standard:
            ad = str(params["apiDomain"]).strip().lower()
            if ad in {"www.google.com", "www.recaptcha.net", "google.com", "recaptcha.net"}:
                task["apiDomain"] = ad
        if user_agent:
            task["userAgent"] = user_agent[:512]
        # CapSolver cookies MUST be [{name,value}] — CapMonster string form breaks createTask.
        cookie_list = _cookies_for_capsolver(cookies)
        if cookie_list and _truthy(_secret_or_env("CAPSOLVER_RECAPTCHA_SEND_COOKIES", "0")):
            task["cookies"] = cookie_list
        return task

    def _create_and_poll(task: dict, label: str) -> tuple[dict | None, str]:
        print_lg(f"[CAPTCHA] CapSolver reCAPTCHA attempt ({label})")
        task_id = _create_capsolver_task(client_key, task)
        if not task_id:
            err = getattr(_create_capsolver_task, "last_error_code", None) or "create_failed"
            return None, str(err)
        solution = _poll_capsolver_result(client_key, task_id, timeout=per_attempt_timeout)
        if not solution:
            err = getattr(_poll_capsolver_result, "last_error_code", None) or "poll_failed"
            return None, str(err)
        return solution, "ok"

    # Task ladder (CapMonster parity + CapSolver Indeed reality):
    #   1) Enterprise + sticky proxy (if enterprise widget)
    #   2) Standard V2 + sticky proxy (Indeed often only accepts this when enterprise unsupported)
    proxied_ladder: list[tuple[str, bool]] = []
    if is_enterprise:
        proxied_ladder.append(("ReCaptchaV2EnterpriseTask", False))
        proxied_ladder.append(("ReCaptchaV2Task", True))  # force_standard
    else:
        proxied_ladder.append(("ReCaptchaV2Task", False))

    solution = None
    last_reason = ""
    stop_all_proxied = False

    if proxy_overlays:
        print_lg(
            f"[CAPTCHA] CapSolver reCAPTCHA using sticky proxy "
            f"({len(proxy_overlays)} format(s); enterprise={is_enterprise}; "
            f"ip_bound={ip_bound})"
        )
        for task_type, force_standard in proxied_ladder:
            if stop_all_proxied or solution:
                break
            skip_task_type = False
            for url in url_candidates:
                if skip_task_type or solution or stop_all_proxied:
                    break
                url_tag = "full" if (urlparse(url).path or "/").strip("/") else "origin"
                for label, overlay in proxy_overlays:
                    task = _base_task(task_type, force_standard=force_standard, url=url)
                    task.update(overlay)
                    sol, reason = _create_and_poll(task, f"{task_type}/{label}/{url_tag}")
                    if sol:
                        solution = sol
                        break
                    last_reason = reason
                    reason_u = (reason or "").upper()
                    if _is_capsolver_unsupported_service(reason):
                        if "Enterprise" in task_type:
                            print_lg(
                                f"[CAPTCHA] CapSolver refuses {task_type} ({reason}); "
                                "falling back to standard ReCaptchaV2Task + sticky proxy…"
                            )
                            skip_task_type = True
                            break
                        print_lg(
                            f"[CAPTCHA] CapSolver refuses this site/service ({reason}); "
                            "stopping proxied retries."
                        )
                        stop_all_proxied = True
                        break
                    # Malformed body (e.g. bad cookies) — don't burn proxy formats.
                    if "JSON RAW" in reason_u or "MUST BE A JSON" in reason_u:
                        print_lg(
                            f"[CAPTCHA] CapSolver rejected task JSON ({reason}); "
                            "aborting this task type ladder."
                        )
                        stop_all_proxied = True
                        break
                    if "ERROR_INVALID_TASK_DATA" in reason_u and "Enterprise" in task_type:
                        print_lg(
                            f"[CAPTCHA] CapSolver {task_type} invalid ({reason}); "
                            "trying next task type…"
                        )
                        skip_task_type = True
                        break
                    if "ERROR_INVALID_TASK_DATA" in reason_u:
                        # The API accepted the task ID but rejected its data while
                        # solving.  This is not a proxy-encoding failure: retrying
                        # four equivalent proxy strings turns one impossible
                        # SmartApply image challenge into an 8+ minute worker
                        # stall.  Let the queue re-attempt later instead.
                        print_lg(
                            "[CAPTCHA] CapSolver rejected the standard V2 task data; "
                            "stopping format roulette for this job."
                        )
                        stop_all_proxied = True
                        break
                    if _is_capsolver_proxy_error(reason) or reason in {
                        "create_failed",
                        "ERROR_INVALID_TASK_DATA",
                    }:
                        print_lg(
                            f"[CAPTCHA] CapSolver proxy/format '{label}' failed ({reason}); "
                            "trying next encoding…"
                        )
                        continue
                    print_lg(
                        f"[CAPTCHA] CapSolver proxied reCAPTCHA failed ({reason}) via '{label}'"
                    )
                    if "UNSOLVABLE" in reason_u or "TIMEOUT" in reason_u:
                        # Don't burn every encoding/URL on a hard unsolvable sitekey.
                        skip_task_type = True
                        break

    # ProxyLess: OK for Greenhouse/ATS; never for Indeed when sticky proxy exists
    # (CapMonster IP-match rule — Indeed rejects CapSolver-egress tokens).
    force_flag = (_secret_or_env("CAPSOLVER_RECAPTCHA_PROXYLESS_FALLBACK") or "").strip().lower()
    if ip_bound:
        allow_proxyless = force_flag == "force"
        if not allow_proxyless and not solution:
            print_lg(
                "[CAPTCHA] CapSolver skipping ProxyLess on Indeed/Glassdoor "
                "(token must use browser Webshare IP). "
                "Set CAPSOLVER_RECAPTCHA_PROXYLESS_FALLBACK=force to override."
            )
    else:
        allow_proxyless = force_flag not in {"0", "false", "no", "off"}

    if not solution and allow_proxyless:
        print_lg(
            "[CAPTCHA] CapSolver reCAPTCHA falling back to ProxyLess "
            f"(ip_bound={ip_bound}; token IP may not match browser)"
        )
        proxyless_types = (
            ["ReCaptchaV2EnterpriseTaskProxyLess", "ReCaptchaV2TaskProxyLess"]
            if is_enterprise
            else ["ReCaptchaV2TaskProxyLess"]
        )
        for task_type in proxyless_types:
            force_std = task_type == "ReCaptchaV2TaskProxyLess" and is_enterprise
            task = _base_task(task_type, force_standard=force_std, url=url_candidates[0])
            sol, reason = _create_and_poll(task, task_type)
            if sol:
                solution = sol
                break
            last_reason = reason
            print_lg(f"[CAPTCHA] CapSolver reCAPTCHA {task_type} failed ({reason})")
            if _is_capsolver_unsupported_service(reason) and "Enterprise" in task_type:
                continue
            if _is_capsolver_unsupported_service(reason):
                break
    elif not solution:
        print_lg(
            f"[CAPTCHA] CapSolver reCAPTCHA exhausted sticky-proxy attempts "
            f"(last={last_reason or 'none'})"
        )

    if not solution:
        print_lg(
            f"[CAPTCHA] CapSolver reCAPTCHA finished without a usable token "
            f"(last={last_reason or 'none'})"
        )
        return False

    return _finalize_capsolver_recaptcha_token(page, solution)


def solve_turnstile_with_capsolver(page, timeout: int = _CAPSOLVER_TIMEOUT) -> bool:
    """Solve Cloudflare Turnstile challenge using CapSolver API."""
    client_key = _capsolver_client_key()
    if not client_key:
        print_lg("[CAPTCHA] CapSolver API key not configured.")
        return False

    params = _extract_turnstile_params(page)
    website_key = (params.get("websiteKey") or "").strip()
    website_url = (params.get("websiteURL") or page.url or "").strip()
    if not website_url:
        print_lg("[CAPTCHA] CapSolver Turnstile skipped: missing website URL.")
        return False
    if not website_key:
        website_key = _DEFAULT_CF_SITEKEY
        print_lg(
            "[CAPTCHA] CapSolver Turnstile: no sitekey in DOM; "
            f"using default CF sitekey {website_key[:10]}…"
        )

    def _base_task(task_type: str) -> dict:
        task: dict = {
            "type": task_type,
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        metadata = {}
        if params.get("action"):
            metadata["action"] = params["action"]
        if params.get("data"):
            metadata["cdata"] = params["data"]
        if metadata:
            task["metadata"] = metadata
        return task

    def _attempt(task: dict, label: str) -> tuple[bool, str]:
        print_lg(f"[CAPTCHA] CapSolver Turnstile attempt ({label})")
        task_id = _create_capsolver_task(client_key, task)
        if not task_id:
            err = getattr(_create_capsolver_task, "last_error_code", None) or "create_failed"
            return False, str(err)
        solution = _poll_capsolver_result(client_key, task_id, timeout=timeout)
        if not solution:
            err = getattr(_poll_capsolver_result, "last_error_code", None) or "poll_failed"
            return False, str(err)
        token = solution.get("token") or solution.get("gRecaptchaResponse")
        if not token:
            print_lg("[CAPTCHA] CapSolver Turnstile solution ready but no token found.")
            return False, "no_token"
        injected = _inject_turnstile_token(page, token)
        print_lg(f"[CAPTCHA] CapSolver Turnstile token injected (success={injected})")
        return bool(injected), "ok" if injected else "inject_failed"

    # Prefer proxied Turnstile so token IP matches browser (Indeed CF).
    proxy_overlays = _capsolver_proxy_task_overlays()
    if proxy_overlays:
        for label, overlay in proxy_overlays:
            task = _base_task("AntiTurnstileTask")
            task.update(overlay)
            ok, reason = _attempt(task, f"AntiTurnstileTask/{label}")
            if ok:
                return True
            if _is_capsolver_proxy_error(reason) or reason in {
                "create_failed",
                "ERROR_INVALID_TASK_DATA",
            }:
                print_lg(
                    f"[CAPTCHA] CapSolver Turnstile proxy '{label}' failed ({reason}); "
                    "trying next encoding…"
                )
                continue
            print_lg(f"[CAPTCHA] CapSolver Turnstile proxied failed ({reason})")
            break

    ok, reason = _attempt(_base_task("AntiTurnstileTaskProxyLess"), "AntiTurnstileTaskProxyLess")
    if ok:
        return True
    print_lg(f"[CAPTCHA] CapSolver Turnstile ProxyLess failed ({reason})")
    return False


def solve_cloudflare_challenge_with_capsolver(page, timeout: int = _CAPSOLVER_TIMEOUT) -> bool:
    """Solve full-page Cloudflare managed challenge via CapSolver AntiCloudflareTask.

    CapMonster-parity path for Indeed / Glassdoor / Workopolis:
      1. Same sticky/browser proxy as the tab (required for cf_clearance).
      2. AntiCloudflareTask with page HTML + matching User-Agent.
      3. Apply all returned cookies (not only cf_clearance).
      4. Reload and wait for Cloudflare to accept clearance.
    """
    from jobbots.core.evasion._capmonster import (
        _apply_capmonster_cookies,
        _cloudflare_clearance_accept_wait_seconds,
        _wait_for_cloudflare_clearance,
    )
    from jobbots.core.evasion._detection import is_cloudflare_challenge

    client_key = _capsolver_client_key()
    if not client_key:
        print_lg("[CAPTCHA] CapSolver API key not configured.")
        return False

    website_url = ""
    try:
        website_url = (page.url or "").strip()
    except Exception:
        website_url = ""
    if not website_url:
        try:
            website_url = (page.evaluate("() => location.href") or "").strip()
        except Exception:
            website_url = ""
    if not website_url:
        print_lg("[CAPTCHA] CapSolver AntiCloudflare skipped: missing website URL.")
        return False

    proxy_overlays = _capsolver_anticloudflare_proxy_overlays()
    if not proxy_overlays:
        print_lg(
            "[CAPTCHA] CapSolver AntiCloudflare skipped: proxy required "
            "(align CAPSOLVER_PROXY_URL / CAPMONSTER_PROXY_URL with the browser sticky proxy)."
        )
        return False

    user_agent = _get_page_user_agent(page) or ""
    html = _page_html_for_cf(page)
    # CapSolver docs: send HTML for "Just a moment" / 403 challenge pages.
    include_html = bool(html) and (
        "Just a moment" in html
        or "cf-" in html.lower()
        or "challenge" in html.lower()
        or "cloudflare" in html.lower()
        or "Additional Verification" in html
        or len(html) > 500
    )
    timeout = min(int(timeout or _CAPSOLVER_TIMEOUT), 120)
    per_attempt = max(45, min(timeout, 90))

    def _base_cf_task() -> dict:
        task: dict = {
            "type": "AntiCloudflareTask",
            "websiteURL": website_url,
        }
        if user_agent:
            task["userAgent"] = user_agent
        if include_html and html:
            task["html"] = html[:500_000]
        return task

    solution = None
    last_err = ""
    for label, overlay in proxy_overlays:
        task = _base_cf_task()
        task.update(overlay)
        print_lg(
            f"[CAPTCHA] CapSolver AntiCloudflareTask proxy={label} "
            f"html={'yes' if 'html' in task else 'no'} "
            f"ua={'yes' if user_agent else 'no'} url={website_url[:120]}"
        )
        task_id = _create_capsolver_task(client_key, task)
        if not task_id:
            last_err = getattr(_create_capsolver_task, "last_error_code", None) or "create_failed"
            if _is_capsolver_proxy_error(last_err) or last_err in {
                "create_failed",
                "ERROR_INVALID_TASK_DATA",
            }:
                print_lg(
                    f"[CAPTCHA] CapSolver AntiCloudflare proxy '{label}' failed ({last_err}); "
                    "trying next encoding…"
                )
                continue
            print_lg(f"[CAPTCHA] CapSolver AntiCloudflare create failed ({last_err})")
            return False

        solution = _poll_capsolver_result(client_key, task_id, timeout=per_attempt)
        if solution:
            break
        last_err = getattr(_poll_capsolver_result, "last_error_code", None) or "poll_failed"
        if _is_capsolver_proxy_error(last_err):
            print_lg(
                f"[CAPTCHA] CapSolver AntiCloudflare poll proxy error via '{label}' "
                f"({last_err}); trying next encoding…"
            )
            continue
        print_lg(f"[CAPTCHA] CapSolver AntiCloudflare poll failed ({last_err})")
        # Non-proxy failure: still try next encoding once (workers vary).
        if label != proxy_overlays[-1][0]:
            continue
        return False

    if not solution:
        print_lg(
            f"[CAPTCHA] CapSolver AntiCloudflare exhausted proxy formats "
            f"(last={last_err or 'none'})"
        )
        return False

    cookies = solution.get("cookies") or {}
    clearance = ""
    if isinstance(cookies, dict):
        clearance = str(cookies.get("cf_clearance") or "")
    elif isinstance(cookies, list):
        for item in cookies:
            if isinstance(item, dict) and item.get("name") == "cf_clearance":
                clearance = str(item.get("value") or "")
                break
    if not clearance:
        clearance = str(
            solution.get("cf_clearance")
            or solution.get("token")
            or ""
        )
    if not clearance:
        print_lg("[CAPTCHA] CapSolver AntiCloudflare solution missing cf_clearance.")
        return False

    # Apply full cookie bag first (cf_clearance + any companion cookies).
    try:
        _apply_capmonster_cookies(page, cookies)
    except Exception as exc:
        print_lg(f"[CAPTCHA] CapSolver cookie bag apply failed: {exc}")

    applied = _apply_capmonster_cf_clearance(page, str(clearance))
    print_lg(f"[CAPTCHA] CapSolver cf_clearance applied (success={applied})")
    if not applied:
        # Still try reload — some contexts accept the cookie without verification.
        print_lg("[CAPTCHA] CapSolver cf_clearance cookie verification failed; reloading anyway.")

    try:
        page.reload(wait_until="domcontentloaded", timeout=20000)
    except Exception as exc:
        print_lg(f"[CAPTCHA] Reload after CapSolver cf_clearance failed: {exc}")
        try:
            page.goto(website_url, wait_until="domcontentloaded", timeout=20000)
        except Exception as exc2:
            print_lg(f"[CAPTCHA] goto after CapSolver cf_clearance failed: {exc2}")

    accept_wait = _cloudflare_clearance_accept_wait_seconds(default=30)
    print_lg(
        f"[CAPTCHA] Waiting up to {accept_wait}s for Cloudflare to accept CapSolver cf_clearance…"
    )
    if _wait_for_cloudflare_clearance(page, timeout=accept_wait):
        print_lg("[CAPTCHA] ✓ CapSolver cf_clearance accepted.")
        return True

    # Soft success: cookie is set and challenge detector may lag on SPA shells.
    try:
        still_cf = is_cloudflare_challenge(page)
    except Exception:
        still_cf = True
    if not still_cf:
        print_lg("[CAPTCHA] ✓ CapSolver cf_clearance — challenge no longer visible.")
        return True

    print_lg(
        "[CAPTCHA] CapSolver cf_clearance applied but Cloudflare still visible "
        f"(cookie_ok={applied})."
    )
    # Return True when cookie was applied so the caller can re-check / continue
    # SmartApply rather than immediately re-queueing — matches CapMonster soft path.
    return bool(applied)


def solve_hcaptcha_with_capsolver(page, timeout: int = _CAPSOLVER_TIMEOUT) -> bool:
    """Solve hCaptcha challenge using CapSolver API."""
    client_key = _capsolver_client_key()
    if not client_key:
        print_lg("[CAPTCHA] CapSolver API key not configured.")
        return False

    params = _extract_hcaptcha_params(page)
    website_key = (params.get("websiteKey") or "").strip()
    website_url = (params.get("websiteURL") or page.url or "").strip()
    if not website_key or not website_url:
        print_lg("[CAPTCHA] CapSolver hCaptcha skipped: missing website key or URL.")
        return False

    # 0x4... is a Cloudflare Turnstile / Challenge sitekey (often inside challenge iframes)
    if website_key.startswith("0x4") or "challenges.cloudflare.com" in website_url:
        print_lg(f"[CAPTCHA] Sitekey {website_key[:6]}... detected as Cloudflare Turnstile; delegating to Turnstile solver.")
        return solve_turnstile_with_capsolver(page, timeout=timeout)

    proxy_str = _capsolver_proxy_str()

    def _attempt(task_type: str, use_proxy: bool) -> bool:
        task: dict = {
            "type": task_type,
            "websiteURL": website_url,
            "websiteKey": website_key,
            "isInvisible": bool(params.get("isInvisible", False)),
        }
        if params.get("data"):
            task["data"] = params["data"]
        if use_proxy and proxy_str:
            task["proxy"] = proxy_str
        task_id = _create_capsolver_task(client_key, task)
        if not task_id:
            return False
        solution = _poll_capsolver_result(client_key, task_id, timeout=timeout)
        if not solution:
            return False
        token = solution.get("gRecaptchaResponse") or solution.get("token")
        if not token:
            print_lg("[CAPTCHA] CapSolver hCaptcha solution ready but no token found.")
            return False
        injected = _inject_hcaptcha_token(page, token)
        print_lg(f"[CAPTCHA] CapSolver hCaptcha token injected (success={injected})")
        return bool(injected)

    # Prefer proxyless first (more reliable); fall back to proxied task.
    if _attempt("HCaptchaTaskProxyLess", use_proxy=False):
        return True
    if proxy_str:
        print_lg("[CAPTCHA] CapSolver hCaptcha ProxyLess failed; retrying with proxy…")
        return _attempt("HCaptchaTask", use_proxy=True)
    return False

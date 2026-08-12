from __future__ import annotations

import os
import time
import requests
from urllib.parse import urlparse

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


def _capsolver_proxy_str() -> str:
    """Return CapSolver proxy string (prefer host:port:user:pass)."""
    from jobbots.core.secret_manager import normalize_proxy_url
    proxy_url = (
        _secret_or_env("CAPSOLVER_PROXY_URL")
        or _secret_or_env("CAPMONSTER_PROXY_URL")
        or _secret_or_env("WEBSHARE_PROXY_URL")
        or _secret_or_env("PROXY_URL")
    )
    if not proxy_url or _truthy(_secret_or_env("BYPASS_PROXY", "0")):
        return ""
    normalized = normalize_proxy_url(proxy_url).rstrip("/")
    try:
        parsed = urlparse(normalized)
        host = parsed.hostname or ""
        port = parsed.port
        user = parsed.username or ""
        password = parsed.password or ""
        if host and port and user and password:
            # CapSolver AntiCloudflareTask docs prefer ip:port:user:pass
            return f"{host}:{port}:{user}:{password}"
        if host and port:
            return f"{host}:{port}"
    except Exception:
        pass
    return normalized


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


def _create_capsolver_task(client_key: str, task: dict) -> str | None:
    """Create a task on CapSolver API and return taskId."""
    try:
        task_type = task.get("type", "unknown")
        print_lg(f"[CAPTCHA] Creating CapSolver task ({task_type}) for {task.get('websiteURL', '')}")
        response = requests.post(
            _CAPSOLVER_CREATE_TASK_URL,
            json={"clientKey": client_key, "task": task},
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
        print_lg(f"[CAPTCHA] CapSolver createTask error: {error_code}")
        return None

    task_id = data.get("taskId")
    if not task_id:
        print_lg(f"[CAPTCHA] CapSolver createTask returned no taskId: {data}")
        return None

    return str(task_id)


def _poll_capsolver_result(
    client_key: str,
    task_id: str,
    timeout: int = _CAPSOLVER_TIMEOUT,
    poll_interval: int = _CAPSOLVER_POLL_INTERVAL,
) -> dict | None:
    """Poll getTaskResult until task is ready, failed, or timed out."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(poll_interval)
        try:
            response = requests.post(
                _CAPSOLVER_GET_RESULT_URL,
                json={"clientKey": client_key, "taskId": task_id},
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            print_lg(f"[CAPTCHA] CapSolver getTaskResult request warning: {exc}")
            continue

        if data.get("errorId", 0) != 0:
            error_code = data.get("errorCode") or data.get("errorDescription")
            print_lg(f"[CAPTCHA] CapSolver task {task_id} failed: {error_code}")
            return None

        status = data.get("status")
        if status == "ready":
            solution = data.get("solution") or {}
            print_lg(f"[CAPTCHA] CapSolver task {task_id} solved successfully!")
            return solution
        elif status == "processing":
            continue
        elif status == "failed":
            print_lg(f"[CAPTCHA] CapSolver task {task_id} returned failed status")
            return None
        else:
            print_lg(f"[CAPTCHA] CapSolver task {task_id} unknown status: {status}")

    print_lg(f"[CAPTCHA] CapSolver task {task_id} timed out after {timeout}s")
    return None


def solve_recaptcha_with_capsolver(page, timeout: int = _CAPSOLVER_TIMEOUT) -> bool:
    """Solve reCAPTCHA v2 / enterprise challenge using CapSolver API."""
    client_key = _capsolver_client_key()
    if not client_key:
        print_lg("[CAPTCHA] CapSolver API key not configured.")
        return False

    params = _extract_recaptcha_params(page)
    website_key = (params.get("websiteKey") or "").strip()
    website_url = (params.get("websiteURL") or page.url or "").strip()
    if not website_key or not website_url:
        print_lg("[CAPTCHA] CapSolver reCAPTCHA skipped: missing website key or URL.")
        return False

    is_enterprise = bool(params.get("isEnterprise", False))
    proxy_str = _capsolver_proxy_str()

    task_type = (
        "ReCaptchaV2EnterpriseTask"
        if is_enterprise and proxy_str
        else "ReCaptchaV2EnterpriseTaskProxyLess"
        if is_enterprise
        else "ReCaptchaV2Task"
        if proxy_str
        else "ReCaptchaV2TaskProxyLess"
    )

    task: dict = {
        "type": task_type,
        "websiteURL": website_url,
        "websiteKey": website_key,
        "isInvisible": bool(params.get("isInvisible", False)),
    }
    if params.get("enterprisePayload"):
        task["enterprisePayload"] = params["enterprisePayload"]
    if params.get("action"):
        task["pageAction"] = params["action"]
    if proxy_str:
        task["proxy"] = proxy_str

    task_id = _create_capsolver_task(client_key, task)
    if not task_id:
        return False

    solution = _poll_capsolver_result(client_key, task_id, timeout=timeout)
    if not solution:
        return False

    token = solution.get("gRecaptchaResponse") or solution.get("token")
    if not token:
        print_lg("[CAPTCHA] CapSolver reCAPTCHA solution ready but no token found.")
        return False

    injected = _inject_recaptcha_token(page, token)
    print_lg(f"[CAPTCHA] CapSolver reCAPTCHA token injected (success={injected})")
    return bool(injected)


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

    proxy_str = _capsolver_proxy_str()
    task_type = "AntiTurnstileTask" if proxy_str else "AntiTurnstileTaskProxyLess"

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
    if proxy_str:
        task["proxy"] = proxy_str

    task_id = _create_capsolver_task(client_key, task)
    if not task_id:
        return False

    solution = _poll_capsolver_result(client_key, task_id, timeout=timeout)
    if not solution:
        return False

    token = solution.get("token") or solution.get("gRecaptchaResponse")
    if not token:
        print_lg("[CAPTCHA] CapSolver Turnstile solution ready but no token found.")
        return False

    injected = _inject_turnstile_token(page, token)
    print_lg(f"[CAPTCHA] CapSolver Turnstile token injected (success={injected})")
    return bool(injected)


def solve_cloudflare_challenge_with_capsolver(page, timeout: int = _CAPSOLVER_TIMEOUT) -> bool:
    """Solve full-page Cloudflare managed challenge via CapSolver AntiCloudflareTask.

    CapMonster is currently unreachable from the farm; this is the primary
    cf_clearance path for Indeed SmartApply interstitials.
    """
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

    proxy_str = _capsolver_proxy_str()
    if not proxy_str:
        print_lg(
            "[CAPTCHA] CapSolver AntiCloudflare skipped: proxy required "
            "(set CAPSOLVER_PROXY_URL to the same sticky proxy as the browser)."
        )
        return False

    user_agent = _get_page_user_agent(page) or ""
    html = _page_html_for_cf(page)
    task: dict = {
        "type": "AntiCloudflareTask",
        "websiteURL": website_url,
        "proxy": proxy_str,
    }
    if user_agent:
        task["userAgent"] = user_agent
    if html and ("Just a moment" in html or "cf-" in html.lower() or "challenge" in html.lower()):
        task["html"] = html[:500_000]

    print_lg(
        f"[CAPTCHA] CapSolver AntiCloudflareTask proxy={'yes' if proxy_str else 'no'} "
        f"html={'yes' if 'html' in task else 'no'} url={website_url[:120]}"
    )
    task_id = _create_capsolver_task(client_key, task)
    if not task_id:
        return False

    solution = _poll_capsolver_result(client_key, task_id, timeout=timeout)
    if not solution:
        return False

    cookies = solution.get("cookies") or {}
    clearance = (
        cookies.get("cf_clearance")
        or solution.get("cf_clearance")
        or solution.get("token")
        or ""
    )
    if not clearance:
        print_lg("[CAPTCHA] CapSolver AntiCloudflare solution missing cf_clearance.")
        return False

    applied = _apply_capmonster_cf_clearance(page, str(clearance))
    print_lg(f"[CAPTCHA] CapSolver cf_clearance applied (success={applied})")
    if not applied:
        return False

    try:
        page.reload(wait_until="domcontentloaded", timeout=15000)
    except Exception as exc:
        print_lg(f"[CAPTCHA] Reload after CapSolver cf_clearance failed: {exc}")

    # Brief settle for Cloudflare to accept clearance on this proxy/IP.
    time.sleep(2)
    return True


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

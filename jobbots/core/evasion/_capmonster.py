from __future__ import annotations

import os
import time
import requests
import base64
import functools
import uuid
from urllib.parse import unquote, urlparse

from jobbots.core.secret_manager import get_secret
from jobbots.core.evasion._config import (
    _CAPMONSTER_TIMEOUT,
    _CAPMONSTER_TURNSTILE_TIMEOUT,
    _USE_CAPMONSTER,
    _SKIP_TURNSTILE_TOKEN_MODE,
    _CAPMONSTER_CREATE_TASK_URL,
    _CAPMONSTER_GET_RESULT_URL,
    _CAPMONSTER_POLL_INTERVAL,
    _PROJECT_ROOT,
    _cap_log,
    _truthy,
    print_lg,
)

# Avoid circular imports by importing detection helpers inside functions or directly from _detection
# since _detection is independent of _capmonster.
from jobbots.core.evasion._detection import (
    is_cloudflare_challenge,
    is_recaptcha_expired,
    is_recaptcha_challenge,
    is_recaptcha_widget_present,
    _indeed_submit_button_ready,
    _get_latest_live_page,
)

_LAST_RECAPTCHA_UNSOLVABLE: dict[str, float | str] = {}


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


def _capmonster_client_key() -> str:
    enabled = (
        os.getenv("USE_CAPMONSTER_CAPTCHA_SOLVER")
        or os.getenv("CAPTCHA_USE_CAPMONSTER")
        or os.getenv("USE_CAPMONSTER")
    )
    if enabled is None:
        enabled = _USE_CAPMONSTER
    if not _truthy(enabled):
        return ""
    return (
        _secret_or_env("CAPMONSTER_CLIENT_KEY")
        or _secret_or_env("CAPMONSTER_API_KEY")
        or _secret_or_env("capkey")
        or ""
    ).strip()


def _dataimpulse_proxy_username(username: str, host: str) -> str:
    """Return a DataImpulse username with stable session semantics by default."""
    if "dataimpulse" not in host.lower():
        return username

    explicit_session = (
        _secret_or_env("CAPMONSTER_DATAIMPULSE_SESSION_ID", "")
        or _secret_or_env("DATAIMPULSE_SESSION_ID", "")
    ).strip()
    rotate_per_task = _truthy(_secret_or_env("CAPMONSTER_DATAIMPULSE_ROTATE_PER_TASK", "0"))
    if explicit_session:
        session_id = explicit_session
    elif rotate_per_task:
        session_id = f"sessid.{uuid.uuid4().hex[:8]}"
    else:
        session_id = _secret_or_env("CAPMONSTER_DATAIMPULSE_STICKY_SESSION", "jobbots-cf").strip() or "jobbots-cf"

    # DataImpulse accepts either user__session or user;session. Prefer
    # replacing an existing sticky suffix so CAPMONSTER_DATAIMPULSE_SESSION_ID
    # always wins over a baked-in __old segment.
    base = username.split(";", 1)[0]
    if "__" in base:
        base = base.split("__", 1)[0]
    username = f"{base}__{session_id}"

    if rotate_per_task and not explicit_session:
        print_lg(f"[CAPTCHA] DataImpulse proxy rotation: session ID '{session_id}' appended to login.")
    else:
        print_lg(f"[CAPTCHA] DataImpulse sticky session: session ID '{session_id}' appended to login.")
    return username


def _capmonster_proxy_fields(disable: bool = False) -> dict:
    if disable:
        return {}
    from jobbots.core.secret_manager import get_capmonster_proxy_url, normalize_proxy_url
    # Explicit CAPMONSTER_PROXY_URL always wins (tests + intentional overrides).
    # get_capmonster_proxy_url() otherwise picks CF-heavy Proxy-Cheap vs Webshare.
    explicit_capmonster_proxy = _secret_or_env("CAPMONSTER_PROXY_URL", "")
    proxy_url = explicit_capmonster_proxy or get_capmonster_proxy_url()
    if not proxy_url:
        return {}
    if _truthy(_secret_or_env("BYPASS_PROXY", "0")) and not explicit_capmonster_proxy:
        return {}
    proxy_url = normalize_proxy_url(proxy_url)
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname or ""
        port = parsed.port
        if not host or not port:
            print_lg("[CAPTCHA] CapMonster proxy ignored: PROXY_URL missing host/port.")
            return {}
        scheme = (parsed.scheme or "http").lower()
        if scheme not in {"http", "https", "socks4", "socks5"}:
            print_lg(f"[CAPTCHA] CapMonster proxy ignored: unsupported scheme {scheme!r}.")
            return {}
        fields = {
            "proxyType": scheme,
            "proxyAddress": host,
            "proxyPort": int(port),
        }
        if parsed.username:
            username = unquote(parsed.username)
            username = _dataimpulse_proxy_username(username, host)
            fields["proxyLogin"] = username
        if parsed.password:
            fields["proxyPassword"] = unquote(parsed.password)
        return fields
    except Exception as e:
        print_lg(f"[CAPTCHA] CapMonster proxy parse failed: {e}")
        return {}


def _capmonster_task_summary(task: dict) -> str:
    enterprise_payload = task.get("enterprisePayload") or {}
    summary = {
        "type": task.get("type"),
        "websiteURL": task.get("websiteURL"),
        "sitekey": "yes" if task.get("websiteKey") else "no",
        "proxy": "yes" if task.get("proxyAddress") else "no",
        "enterprisePayload": sorted(enterprise_payload.keys()),
        "enterprisePayload.s": "yes" if enterprise_payload.get("s") else "no",
        "data-s": "yes" if task.get("recaptchaDataSValue") else "no",
        "pageAction": task.get("pageAction") or "",
        "apiDomain": task.get("apiDomain") or "",
        "isInvisible": bool(task.get("isInvisible")),
        "cookies": "yes" if task.get("cookies") else "no",
        "userAgent": "yes" if task.get("userAgent") else "no",
    }
    return ", ".join(f"{key}={value}" for key, value in summary.items())


def _capmonster_proxy_log(fields: dict) -> str:
    if not fields:
        return "proxy=no"
    auth = "yes" if fields.get("proxyLogin") else "no"
    return (
        f"proxy=yes type={fields.get('proxyType')} "
        f"host={fields.get('proxyAddress')}:{fields.get('proxyPort')} auth={auth}"
    )


def _masked_proxy_url() -> str:
    proxy_url = (
        _secret_or_env("CAPMONSTER_PROXY_URL", "")
        or _secret_or_env("PROXY_URL", "")
        or ""
    ).strip()
    if not proxy_url:
        return "none"
    try:
        parsed = urlparse(proxy_url)
        if not parsed.hostname:
            return "configured"
        return f"{parsed.scheme or 'http'}://{parsed.hostname}:{parsed.port or ''}".rstrip(":")
    except Exception:
        return "configured"


def _safe_current_url(page) -> str:
    try:
        parsed = urlparse(page.url or "")
        if not parsed.scheme or not parsed.netloc:
            return page.url or ""
        return parsed._replace(query="", fragment="").geturl()
    except Exception:
        return ""


def _challenge_context_from_url(url: str) -> str:
    lower = (url or "").lower()
    if "indeed." in lower and ("/jobs" in lower or "/viewjob" not in lower):
        return "search_page"
    if "glassdoor." in lower and ("job" in lower or "search" in lower):
        return "search_page"
    return "cloudflare_page"


def _log_challenge_diag(diag: dict) -> None:
    ordered_keys = [
        "context",
        "url",
        "browser",
        "challenge_detected",
        "challenge_type",
        "sitekey_present",
        "action_present",
        "data_present",
        "pagedata_present",
        "param_source",
        "browser_proxy",
        "capmonster_mode",
        "capmonster_proxy_used",
        "capmonster_task_created",
        "capmonster_task_id",
        "capmonster_result",
        "token_received",
        "cf_clearance_received",
        "fallback_cookie_mode_skipped",
        "resolved_after_capmonster",
        "resolved_after_reload",
        "final_status",
    ]
    lines = ["[CHALLENGE_DIAG]"]
    for key in ordered_keys:
        if key not in diag:
            continue
        value = diag.get(key)
        if isinstance(value, bool):
            value = str(value).lower()
        elif value is None:
            value = ""
        lines.append(f"{key}={value}")
    print_lg("\n".join(lines))


def get_last_turnstile_challenge_diag() -> dict:
    diag = getattr(solve_turnstile_with_capmonster, "last_diag", None)
    return dict(diag) if isinstance(diag, dict) else {}


def update_last_turnstile_challenge_diag(**updates) -> None:
    diag = get_last_turnstile_challenge_diag()
    if not diag:
        return
    diag.update(updates)
    solve_turnstile_with_capmonster.last_diag = diag
    _log_challenge_diag(diag)


def _turnstile_no_proxy_enabled() -> bool:
    explicit = (
        _secret_or_env("CAPMONSTER_TURNSTILE_NO_PROXY", "")
        or _secret_or_env("CAPTCHA_TURNSTILE_NO_PROXY", "")
        or _secret_or_env("CAPMONSTER_CLOUDFLARE_NO_PROXY", "")
    )
    if explicit:
        return _truthy(explicit)
    if _truthy(_secret_or_env("CAPMONSTER_RECAPTCHA_ONLY_NO_PROXY", "0")):
        _cap_log(
            "CAPMONSTER_RECAPTCHA_ONLY_NO_PROXY is set but ignored for Turnstile; "
            "use CAPMONSTER_TURNSTILE_NO_PROXY=1 to force proxyless Turnstile."
        )
    return False


def _extract_recaptcha_params(page) -> dict:
    try:
        return page.evaluate(
            """
            () => {
                const params = {
                    websiteURL: location.href,
                    websiteKey: "",
                    recaptchaDataSValue: "",
                    enterprisePayload: {},
                    apiDomain: "",
                    pageAction: "",
                    isInvisible: false,
                    isEnterprise: false,
                };

                const widget = document.querySelector("[data-sitekey]");
                if (widget) {
                    params.websiteKey = widget.getAttribute("data-sitekey") || "";
                    params.recaptchaDataSValue = widget.getAttribute("data-s") || "";
                    params.isInvisible = (widget.getAttribute("data-size") || "").toLowerCase() === "invisible";
                    for (const attr of widget.attributes) {
                        if (!attr.name.startsWith("data-")) continue;
                        const key = attr.name.slice(5);
                        if (key === "sitekey") continue;
                        if (key === "action") params.pageAction = attr.value || params.pageAction;
                        else if (attr.value) params.enterprisePayload[key] = attr.value;
                    }
                }

                const frames = Array.from(document.querySelectorAll("iframe[src*='recaptcha']"));
                for (const frame of frames) {
                    const src = frame.getAttribute("src") || "";
                    try {
                        const url = new URL(src, location.href);
                        const key   = url.searchParams.get("k");
                        const dataS = url.searchParams.get("s");
                        const size  = url.searchParams.get("size");
                        const action = url.searchParams.get("sa") || url.searchParams.get("action");
                        if (!params.websiteKey && key) params.websiteKey = key;
                        if (!params.recaptchaDataSValue && dataS) params.recaptchaDataSValue = dataS;
                        if (dataS && !params.enterprisePayload.s) params.enterprisePayload.s = dataS;
                        if (size === "invisible") params.isInvisible = true;
                        if (action) params.pageAction = action;
                        if (url.hostname.includes("recaptcha.net")) params.apiDomain = "www.recaptcha.net";
                        if (url.hostname.includes("google.com"))    params.apiDomain = "www.google.com";
                        if (url.pathname.includes("/enterprise/") || src.includes("/enterprise/")) {
                            params.isEnterprise = true;
                        }
                    } catch (err) {}
                }

                const scripts   = Array.from(document.querySelectorAll("script:not([src])"));
                const renderRegex = /grecaptcha\\.enterprise\\.render\\([^,]+,\\s*\\{([\\s\\S]*?)\\}/g;
                for (const script of scripts) {
                    const text = script.textContent || "";
                    let match;
                    while ((match = renderRegex.exec(text)) !== null) {
                        const objectText = match[1] || "";
                        const pairRegex  = /(\\w+)\\s*:\\s*['"]([^'"]+)['"]/g;
                        let pair;
                        while ((pair = pairRegex.exec(objectText)) !== null) {
                            const key   = pair[1];
                            const value = pair[2];
                            if (key === "sitekey" && !params.websiteKey) params.websiteKey = value;
                            else if (key === "action") params.pageAction = value;
                            else if (key !== "sitekey" && value) params.enterprisePayload[key] = value;
                        }
                    }
                }

                if (window.invisibleRecaptchaKey && !params.websiteKey) {
                    params.websiteKey  = String(window.invisibleRecaptchaKey);
                    params.isInvisible = true;
                }
                if (window.invisibleRecaptchaDataS && !params.recaptchaDataSValue) {
                    params.recaptchaDataSValue        = String(window.invisibleRecaptchaDataS);
                    params.enterprisePayload.s        = String(window.invisibleRecaptchaDataS);
                }

                const seen = new Set();
                const visit = (value, depth = 0) => {
                    if (!value || depth > 8) return;
                    const type = typeof value;
                    if (type !== "object" && type !== "function") return;
                    if (seen.has(value)) return;
                    seen.add(value);
                    let entries = [];
                    try { entries = Object.entries(value); } catch (err) { return; }
                    for (const [key, child] of entries) {
                        if (typeof child === "string" && child) {
                            const lower = key.toLowerCase();
                            if (!params.websiteKey && (lower === "sitekey" || lower === "site_key")) {
                                params.websiteKey = child;
                            } else if (!params.pageAction && lower === "action") {
                                params.pageAction = child;
                            } else if (!params.recaptchaDataSValue && (lower === "s" || lower === "data-s")) {
                                params.recaptchaDataSValue = child;
                                params.enterprisePayload.s = child;
                            }
                        }
                        visit(child, depth + 1);
                    }
                };
                try {
                    visit(window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients);
                } catch (err) {}

                const captured = Array.isArray(window.__capmonsterRecaptchaEnterprisePayloads)
                    ? window.__capmonsterRecaptchaEnterprisePayloads
                    : [];
                for (const item of captured.slice().reverse()) {
                    const payload = item && item.payload;
                    if (!payload || typeof payload !== "object") continue;
                    const key = payload.sitekey || payload.siteKey || payload.websiteKey;
                    if (key && params.websiteKey && key !== params.websiteKey) continue;
                    for (const [name, value] of Object.entries(payload)) {
                        if (!value) continue;
                        if (name === "sitekey" || name === "siteKey" || name === "websiteKey") {
                            if (!params.websiteKey) params.websiteKey = String(value);
                        } else if (name === "action") {
                            if (!params.pageAction) params.pageAction = String(value);
                        } else {
                            params.enterprisePayload[name] = String(value);
                            const lower = name.toLowerCase();
                            if (!params.recaptchaDataSValue && (lower === "s" || lower === "data-s")) {
                                params.recaptchaDataSValue = String(value);
                            }
                        }
                    }
                    break;
                }
                return params;
            }
            """
        ) or {}
    except Exception as e:
        print_lg(f"[CAPTCHA] CapMonster parameter extraction failed: {e}")
        return {}


def _get_page_user_agent(page) -> str:
    try:
        return page.evaluate("() => navigator.userAgent") or ""
    except Exception:
        return ""


def _get_page_cookies(page) -> str:
    try:
        cookies = page.context.cookies(page.url)
        return "; ".join(
            f"{cookie.get('name')}={cookie.get('value')}"
            for cookie in cookies
            if cookie.get("name") and cookie.get("value") is not None
        )
    except Exception:
        return ""


def _recaptcha_unsolvable_signature(params: dict) -> str:
    payload = params.get("enterprisePayload") or {}
    return "|".join([
        (params.get("websiteURL") or "").strip(),
        (params.get("websiteKey") or "").strip(),
        (params.get("recaptchaDataSValue") or payload.get("s") or "").strip(),
        ",".join(sorted(str(key) for key in payload.keys())),
    ])


def _recaptcha_unsolvable_cooldown_seconds() -> int:
    raw = _secret_or_env("CAPMONSTER_RECAPTCHA_UNSOLVABLE_COOLDOWN_SECONDS", "900")
    try:
        return max(0, int(raw))
    except Exception:
        return 900


def _recaptcha_max_retry_rounds() -> int:
    raw = _secret_or_env("CAPMONSTER_RECAPTCHA_MAX_RETRY_ROUNDS", "3")
    try:
        return max(1, min(10, int(raw)))
    except Exception:
        return 3


def _recaptcha_recently_unsolvable(params: dict) -> bool:
    cooldown = _recaptcha_unsolvable_cooldown_seconds()
    if cooldown <= 0:
        return False
    signature = _recaptcha_unsolvable_signature(params)
    if not signature.strip("|"):
        return False
    last_signature = str(_LAST_RECAPTCHA_UNSOLVABLE.get("signature") or "")
    last_seen = float(_LAST_RECAPTCHA_UNSOLVABLE.get("ts") or 0)
    return signature == last_signature and (time.time() - last_seen) < cooldown


def _remember_recaptcha_unsolvable(params: dict) -> None:
    signature = _recaptcha_unsolvable_signature(params)
    if not signature.strip("|"):
        return
    _LAST_RECAPTCHA_UNSOLVABLE["signature"] = signature
    _LAST_RECAPTCHA_UNSOLVABLE["ts"] = time.time()


def _create_capmonster_task(client_key: str, params: dict,
                            user_agent: str, cookies: str,
                            *, use_proxy: bool = True,
                            force_standard: bool = False) -> int | None:
    website_key = (params.get("websiteKey") or "").strip()
    website_url = (params.get("websiteURL") or "").strip()
    if not website_key or not website_url:
        print_lg("[CAPTCHA] CapMonster skipped: missing website key or URL.")
        return None

    proxy_fields = _capmonster_proxy_fields(disable=not use_proxy)
    if force_standard:
        task_type = "RecaptchaV2Task" if proxy_fields else "RecaptchaV2TaskProxyless"
    elif params.get("isEnterprise"):
        task_type = "RecaptchaV2EnterpriseTask" if proxy_fields else "RecaptchaV2EnterpriseTaskProxyless"
    else:
        task_type = "RecaptchaV2Task" if proxy_fields else "RecaptchaV2TaskProxyless"
    task: dict = {
        "type": task_type,
        "websiteURL": website_url,
        "websiteKey": website_key,
    }
    task.update(proxy_fields)
    if not force_standard and params.get("isEnterprise") and params.get("enterprisePayload"):
        enterprise_payload = dict(params["enterprisePayload"])
        if params.get("recaptchaDataSValue") and not enterprise_payload.get("s"):
            enterprise_payload["s"] = params["recaptchaDataSValue"]
        task["enterprisePayload"] = enterprise_payload
    if not force_standard and params.get("recaptchaDataSValue") and _truthy(_secret_or_env("CAPMONSTER_RECAPTCHA_SEND_DATA_S_VALUE", "1")):
        task["recaptchaDataSValue"] = params["recaptchaDataSValue"]
    if not force_standard and params.get("pageAction"):
        task["pageAction"] = params["pageAction"]
    if not force_standard and params.get("apiDomain"):
        task["apiDomain"] = params["apiDomain"]
    if not force_standard and params.get("isInvisible"):
        task["isInvisible"] = True
    if user_agent:
        task["userAgent"] = user_agent
    if cookies:
        task["cookies"] = cookies

    try:
        _cap_log(f"Creating CapMonster reCAPTCHA task ({_capmonster_proxy_log(proxy_fields)}).")
        _cap_log(f"CapMonster task summary: {_capmonster_task_summary(task)}")
        response = requests.post(
            _CAPMONSTER_CREATE_TASK_URL,
            json={"clientKey": client_key, "task": task},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print_lg(f"[CAPTCHA] CapMonster createTask request failed: {e}")
        return None

    if data.get("errorId"):
        print_lg(f"[CAPTCHA] CapMonster createTask error: {data.get('errorCode') or data.get('errorDescription')}")
        return None

    task_id = data.get("taskId")
    print_lg(f"[CAPTCHA] CapMonster task created ({task_type}); taskId={task_id}.")
    return int(task_id) if task_id is not None else None


def _extract_turnstile_params(page) -> dict:
    try:
        return page.evaluate(
            """
            () => {
                const params = {
                    websiteURL: location.href,
                    websiteKey: "",
                    action: "",
                    data: "",
                    pageData: "",
                    widgetSource: "",
                };

                const attr = (el, name) => el ? (el.getAttribute(name) || "") : "";
                const widgets = Array.from(document.querySelectorAll(
                    ".cf-turnstile, [data-sitekey], [data-testid*='turnstile' i]"
                ));
                for (const widget of widgets) {
                    const cls    = widget.className || "";
                    const testid = attr(widget, "data-testid");
                    const sitekey = attr(widget, "data-sitekey") || attr(widget, "sitekey");
                    if (sitekey && (!params.websiteKey || cls.includes("cf-turnstile") || testid.toLowerCase().includes("turnstile"))) {
                        params.websiteKey  = sitekey;
                        params.action      = attr(widget, "data-action")  || params.action;
                        params.data        = attr(widget, "data-cdata")   || attr(widget, "data-data") || params.data;
                        params.pageData    = attr(widget, "data-pagedata") || params.pageData;
                        params.widgetSource = "data-sitekey";
                    }
                }

                const frames = Array.from(document.querySelectorAll(
                    "iframe[src*='challenges.cloudflare.com'], iframe[src*='turnstile']"
                ));
                for (const frame of frames) {
                    const src = attr(frame, "src");
                    try {
                        const url = new URL(src, location.href);
                        const key = url.searchParams.get("sitekey")
                            || url.searchParams.get("k")
                            || url.searchParams.get("render");
                        if (key && !params.websiteKey && key.startsWith("0x")) {
                            params.websiteKey   = key;
                            params.widgetSource = "iframe";
                        }
                        // Fallback: extract sitekey from the URL path (e.g. challenges.cloudflare.com/.../0x4.../light/)
                        if (!params.websiteKey && src) {
                            const match = src.match(/0x4[A-Za-z0-9_-]{21,23}/);
                            if (match) {
                                params.websiteKey = match[0];
                                params.widgetSource = "iframe_path_regex";
                            }
                        }
                        params.action    = params.action    || url.searchParams.get("action")       || "";
                        params.data      = params.data      || url.searchParams.get("cData")        || url.searchParams.get("cdata") || "";
                        params.pageData  = params.pageData  || url.searchParams.get("chlPageData")  || url.searchParams.get("pagedata") || "";
                    } catch (err) {}
                }

                try {
                    const cf = window._cf_chl_opt || {};
                    params.websiteKey = params.websiteKey || cf.sitekey || cf.siteKey || "";
                    params.action     = params.action     || cf.action  || "";
                    params.data       = params.data       || cf.cData   || cf.cdata  || cf.mdrd || "";
                    params.pageData   = params.pageData   || cf.chlPageData || cf.pageData || cf.md || "";
                    if (!params.widgetSource && params.websiteKey) params.widgetSource = "window._cf_chl_opt";
                } catch (err) {}

                const scripts = Array.from(document.querySelectorAll("script:not([src])"));
                for (const script of scripts) {
                    const text = script.textContent || "";
                    if (!params.websiteKey) {
                        const keyMatch = text.match(/(?:sitekey|siteKey)\\s*[:=]\\s*['"](0x[A-Za-z0-9_-]+)['"]/);
                        if (keyMatch) { params.websiteKey = keyMatch[1]; params.widgetSource = "script"; }
                    }
                    if (!params.action) {
                        const actionMatch = text.match(/action\\s*[:=]\\s*['"]([^'"]+)['"]/);
                        if (actionMatch) params.action = actionMatch[1];
                    }
                    if (!params.data) {
                        const dataMatch = text.match(/(?:cData|cdata|mdrd)\\s*[:=]\\s*['"]([^'"]+)['"]/i);
                        if (dataMatch) params.data = dataMatch[1];
                    }
                    if (!params.pageData) {
                        const pageDataMatch = text.match(/(?:chlPageData|pageData|\\bmd\\b)\\s*[:=]\\s*['"]([^'"]+)['"]/i);
                        if (pageDataMatch) params.pageData = pageDataMatch[1];
                    }
                }

                // Fallback: regex scan the entire page HTML source
                const html = document.documentElement.outerHTML || "";
                if (!params.websiteKey) {
                    const keyMatch = html.match(/0x4[A-Za-z0-9_-]{21,23}/);
                    if (keyMatch) {
                        params.websiteKey = keyMatch[0];
                        params.widgetSource = "regex_html_sitekey";
                    }
                }
                if (!params.action) {
                    const actionMatch = html.match(/action\\s*[:=]\\s*['"]([^'"]+)['"]/);
                    if (actionMatch) params.action = actionMatch[1];
                }
                if (!params.data) {
                    const dataMatch = html.match(/(?:cData|cdata|mdrd)\\s*[:=]\\s*['"]([^'"]+)['"]/i);
                    if (dataMatch) params.data = dataMatch[1];
                }
                if (!params.pageData) {
                    const pageDataMatch = html.match(/(?:chlPageData|pagedata|\\bmd\\b)\\s*[:=]\\s*['"]([^'"]+)['"]/i);
                    if (pageDataMatch) params.pageData = pageDataMatch[1];
                }

                return params;
            }
            """
        ) or {}
    except Exception as e:
        print_lg(f"[CAPTCHA] Turnstile parameter extraction failed: {e}")
        return {}


def _create_capmonster_turnstile_task(client_key: str, params: dict,
                                      user_agent: str, cookies: str,
                                      cloudflare_task_type: str = "token",
                                      html_page_base64: str = "",
                                      use_proxy: bool = True) -> int | None:
    website_key = (params.get("websiteKey") or "").strip()
    website_url = (params.get("websiteURL") or "").strip()
    if not website_url:
        print_lg("[CAPTCHA] CapMonster Turnstile skipped: missing website URL.")
        return None

    proxy_fields = _capmonster_proxy_fields(disable=not use_proxy)
    if cloudflare_task_type == "cf_clearance":
        if not proxy_fields:
            print_lg("[CAPTCHA] CapMonster cf_clearance skipped: proxy is required for Cloudflare Challenge tasks.")
            return None
        if not html_page_base64:
            print_lg("[CAPTCHA] CapMonster cf_clearance skipped: missing page HTML.")
            return None

        task = {
            "type": "TurnstileTask",
            "websiteURL": website_url,
            "websiteKey": website_key or "0x4AAAAAAADnBwMwJC38uztB",
            "cloudflareTaskType": "cf_clearance",
            "htmlPageBase64": html_page_base64,
        }
    else:
        if not website_key:
            print_lg("[CAPTCHA] CapMonster Turnstile skipped: missing website key.")
            return None
        missing = [
            name
            for name, value in (
                ("pageAction", params.get("action")),
                ("data", params.get("data")),
                ("pageData", params.get("pageData")),
                ("userAgent", user_agent),
            )
            if not str(value or "").strip()
        ]
        if missing:
            print_lg(
                "[CAPTCHA] CapMonster Turnstile token skipped: missing required "
                f"Challenge field(s): {', '.join(missing)}."
            )
            return None
        task = {
            "type": "TurnstileTask",
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        if params.get("action"):
            task["pageAction"] = params["action"]
        if params.get("data"):
            task["data"] = params["data"]
        if params.get("pageData"):
            task["pageData"] = params["pageData"]
        task["cloudflareTaskType"] = "token"

    task.update(proxy_fields)
    if user_agent:
        task["userAgent"] = user_agent
    if cookies:
        task["cookies"] = cookies

    task_label = f"TurnstileTask {cloudflare_task_type}" if cloudflare_task_type else "TurnstileTask"
    try:
        print_lg(f"[CAPTCHA] CapMonster {task_label} task: {_capmonster_proxy_log(proxy_fields)}")
        response = requests.post(
            _CAPMONSTER_CREATE_TASK_URL,
            json={"clientKey": client_key, "task": task},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print_lg(f"[CAPTCHA] CapMonster {task_label} createTask request failed: {e}")
        return None

    if data.get("errorId"):
        print_lg(
            f"[CAPTCHA] CapMonster {task_label} createTask error: "
            f"{data.get('errorCode') or data.get('errorDescription')}"
        )
        return None

    task_id = data.get("taskId")
    print_lg(f"[CAPTCHA] CapMonster task created ({task_label}).")
    return int(task_id) if task_id is not None else None


def _get_page_html_base64(page) -> str:
    try:
        html = page.content() or ""
        orig_size = len(html)
        import re
        html = re.sub(r'<style[^>]*>([\s\S]*?)</style>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<svg[^>]*>([\s\S]*?)</svg>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'\s+style="[^"]*"', '', html, flags=re.IGNORECASE)
        html = re.sub(r"\s+style='[^']*'", '', html, flags=re.IGNORECASE)
        html = re.sub(r'<img[^>]*>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<!--([\s\S]*?)-->', '', html)
        html = re.sub(r'\n\s*\n', '\n', html)
        pruned_size = len(html)
        print_lg(f"[CAPTCHA] HTML captured & pruned for CapMonster: {orig_size/1024:.1f} KB -> {pruned_size/1024:.1f} KB")
        return base64.b64encode(html.encode("utf-8", "ignore")).decode("ascii")
    except Exception as e:
        print_lg(f"[CAPTCHA] Could not capture page HTML for Cloudflare task: {e}")
        return ""



def _create_capmonster_cf_clearance_task(client_key: str, params: dict,
                                         user_agent: str, cookies: str,
                                         html_page_base64: str) -> int | None:
    website_url = (params.get("websiteURL") or "").strip()
    if not website_url:
        print_lg("[CAPTCHA] CapMonster cf_clearance skipped: missing website URL.")
        return None
    if not html_page_base64:
        print_lg("[CAPTCHA] CapMonster cf_clearance skipped: missing page HTML.")
        return None

    proxy_fields = _capmonster_proxy_fields()
    if not proxy_fields:
        print_lg("[CAPTCHA] CapMonster cf_clearance skipped: proxy is required for Cloudflare Challenge tasks.")
        return None

    task: dict = {
        "type": "TurnstileTask",
        "websiteURL": website_url,
        "websiteKey": (params.get("websiteKey") or "0x4AAAAAAADnBwMwJC38uztB"),
        "cloudflareTaskType": "cf_clearance",
        "htmlPageBase64": html_page_base64,
    }
    task.update(proxy_fields)
    if user_agent:
        task["userAgent"] = user_agent
    if cookies:
        task["cookies"] = cookies

    try:
        _cap_log(f"Creating CapMonster Cloudflare cf_clearance task ({_capmonster_proxy_log(proxy_fields)}).")
        response = requests.post(
            _CAPMONSTER_CREATE_TASK_URL,
            json={"clientKey": client_key, "task": task},
            timeout=60,
        )
        response.raise_for_status()
        data = response.json()
    except Exception as e:
        print_lg(f"[CAPTCHA] CapMonster cf_clearance createTask request failed: {e}")
        return None

    if data.get("errorId"):
        print_lg(
            "[CAPTCHA] CapMonster cf_clearance createTask error: "
            f"{data.get('errorCode') or data.get('errorDescription')}"
        )
        return None

    task_id = data.get("taskId")
    print_lg("[CAPTCHA] CapMonster task created (TurnstileTask cf_clearance).")
    return int(task_id) if task_id is not None else None


def _poll_capmonster_result(client_key: str, task_id: int,
                            timeout: int = _CAPMONSTER_TIMEOUT) -> dict | None:
    _poll_capmonster_result.last_error_code = ""
    start            = time.time()
    deadline         = time.time() + timeout
    last_status_log  = 0
    transient_errors = 0
    while time.time() < deadline:
        time.sleep(_CAPMONSTER_POLL_INTERVAL)
        try:
            response = requests.post(
                _CAPMONSTER_GET_RESULT_URL,
                json={"clientKey": client_key, "taskId": task_id},
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            transient_errors += 1
            if transient_errors <= 5:
                remaining = max(0, int(deadline - time.time()))
                _cap_log(
                    f"CapMonster getTaskResult temporary error ({transient_errors}/5): {e}; "
                    f"continuing poll ({remaining}s left)",
                    start,
                )
                time.sleep(min(5, transient_errors))
                continue
            print_lg(f"[CAPTCHA] CapMonster getTaskResult request failed after retries: {e}")
            return None

        if data.get("errorId"):
            error_code = data.get("errorCode") or data.get("errorDescription")
            _poll_capmonster_result.last_error_code = str(error_code or "")
            print_lg(f"[CAPTCHA] CapMonster getTaskResult error for taskId={task_id}: {error_code}")
            return None
        if data.get("status") == "ready":
            solution = data.get("solution") or {}
            if (
                solution.get("gRecaptchaResponse")
                or solution.get("token")
                or solution.get("cf_clearance")
                or solution.get("cookies")
            ):
                if solution.get("gRecaptchaResponse") or solution.get("token"):
                    _cap_log("CapMonster returned token.", start)
                else:
                    _cap_log("CapMonster returned Cloudflare clearance data.", start)
                return solution
            print_lg("[CAPTCHA] CapMonster returned ready without token or clearance data.")
            return None

        elapsed = int(time.time() - start)
        if elapsed >= last_status_log + 10:
            remaining = max(0, int(deadline - time.time()))
            _cap_log(f"CapMonster still processing... ({remaining}s left)", start)
            last_status_log = elapsed

    _cap_log("CapMonster timed out waiting for token.", start)
    return None


def _inject_recaptcha_token(page, token: str) -> bool:
    try:
        callbacks_invoked = page.evaluate(
            """
            (token) => {
                const invoked = new Set();
                const callTokenCallback = (fn) => {
                    if (typeof fn !== "function" || invoked.has(fn)) return;
                    invoked.add(fn);
                    try { fn(token); } catch (err) {}
                };

                const names = ["g-recaptcha-response", "g-recaptcha-response-100000"];
                for (const name of names) {
                    let field = document.querySelector(`textarea[name="${name}"], textarea#${name}`);
                    if (!field) {
                        field = document.createElement("textarea");
                        field.name  = name;
                        field.id    = name;
                        field.style.display = "none";
                        document.body.appendChild(field);
                    }
                    field.value     = token;
                    field.innerHTML = token;
                    field.dispatchEvent(new Event("input",  { bubbles: true }));
                    field.dispatchEvent(new Event("change", { bubbles: true }));
                }

                for (const widget of document.querySelectorAll("[data-callback]")) {
                    const callback = widget.getAttribute("data-callback");
                    if (callback && typeof window[callback] === "function") {
                        callTokenCallback(window[callback]);
                    }
                }

                for (const frame of document.querySelectorAll("iframe[src*='recaptcha']")) {
                    const container = frame.closest("div");
                    const callback  = container && container.getAttribute("data-callback");
                    if (callback && typeof window[callback] === "function") {
                        callTokenCallback(window[callback]);
                    }
                }

                const visit = (value, depth = 0) => {
                    if (!value || depth > 6 || typeof value !== "object") return;
                    for (const key of Object.keys(value)) {
                        let child;
                        try { child = value[key]; } catch (err) { continue; }
                        if ((key === "callback" || key === "promise-callback") && typeof child === "function") {
                            callTokenCallback(child);
                        } else if (child && typeof child === "object") {
                            visit(child, depth + 1);
                        }
                    }
                };

                try {
                    const clients = window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients;
                    if (clients) visit(clients);
                } catch (err) {}

                return invoked.size;
            }
            """,
            token,
        )
        print_lg(f"[CAPTCHA] Injected token and invoked {int(callbacks_invoked or 0)} callback(s).")
        return True
    except Exception as e:
        print_lg(f"[CAPTCHA] CapMonster token injection failed: {e}")
        return False


def _inject_turnstile_token(page, token: str) -> bool:
    try:
        callbacks_invoked = page.evaluate(
            """
            (token) => {
                const invoked = new Set();
                const callTokenCallback = (fn) => {
                    if (typeof fn !== "function" || invoked.has(fn)) return;
                    invoked.add(fn);
                    try { fn(token); } catch (err) {}
                };

                const fieldNames = [
                    "cf-turnstile-response",
                    "cf_challenge_response",
                    "turnstile-response",
                ];
                for (const name of fieldNames) {
                    let field = document.querySelector(
                        `input[name="${name}"], textarea[name="${name}"], input#${name}, textarea#${name}`
                    );
                    if (!field) {
                        field = document.createElement("input");
                        field.type  = "hidden";
                        field.name  = name;
                        field.id    = name;
                        document.body.appendChild(field);
                    }
                    field.value = token;
                    field.setAttribute("value", token);
                    field.dispatchEvent(new Event("input",  { bubbles: true }));
                    field.dispatchEvent(new Event("change", { bubbles: true }));
                }

                for (const widget of document.querySelectorAll(".cf-turnstile, [data-sitekey]")) {
                    const callback = widget.getAttribute("data-callback");
                    if (callback && typeof window[callback] === "function") {
                        callTokenCallback(window[callback]);
                    }
                }

                const visit = (value, depth = 0) => {
                    if (!value || depth > 7 || typeof value !== "object") return;
                    for (const key of Object.keys(value)) {
                        let child;
                        try { child = value[key]; } catch (err) { continue; }
                        if ((key === "callback" || key === "promise-callback" || key === "promiseCallback") && typeof child === "function") {
                            callTokenCallback(child);
                        } else if (child && typeof child === "object") {
                            visit(child, depth + 1);
                        }
                    }
                };

                try {
                    if (window.turnstile)     visit(window.turnstile);
                    if (window.__cf_chl_opt)  visit(window.__cf_chl_opt);
                } catch (err) {}

                return invoked.size;
            }
            """,
            token,
        )
        print_lg(f"[CAPTCHA] Injected Turnstile token and invoked {int(callbacks_invoked or 0)} callback(s).")
        return True
    except Exception as e:
        print_lg(f"[CAPTCHA] CapMonster Turnstile token injection failed: {e}")
        return False


def _cloudflare_clearance_accept_wait_seconds(default: int = 45) -> int:
    raw = _secret_or_env("CAPTCHA_CF_CLEARANCE_ACCEPT_WAIT", str(default))
    try:
        return max(5, min(180, int(raw)))
    except Exception:
        return default


def _turnstile_token_rescue_after_clearance_reject_enabled() -> bool:
    return _truthy(_secret_or_env("CAPTCHA_TURNSTILE_TOKEN_RESCUE_AFTER_CLEARANCE_REJECT", "1"))


def _wait_for_cloudflare_clearance(page, timeout: int = 12) -> bool:
    start    = time.time()
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.75)
        latest_page = _get_latest_live_page(page)
        if not is_cloudflare_challenge(latest_page):
            _cap_log("Cloudflare clearance confirmed after token injection.", start)
            return True
        try:
            if _indeed_submit_button_ready(latest_page):
                _cap_log("Submit button is ready after Turnstile token injection.", start)
                return True
        except Exception:
            pass
    return not is_cloudflare_challenge(_get_latest_live_page(page))


def _click_recaptcha_verify_if_visible(page) -> None:
    try:
        challenge_frame = page.frame_locator("iframe[title*='recaptcha challenge']").first
        verify_btn = challenge_frame.locator("#recaptcha-verify-button")
        if verify_btn.is_visible(timeout=1500):
            verify_btn.click(timeout=3000)
            print_lg("[CAPTCHA] Clicked reCAPTCHA Verify after token injection.")
    except Exception:
        pass


def _wait_for_recaptcha_clearance(page, timeout: int = 8) -> bool:
    start    = time.time()
    deadline = time.time() + timeout
    last_log = 0
    while time.time() < deadline:
        time.sleep(0.5)
        if _indeed_submit_button_ready(page):
            _cap_log("Submit button is ready; no need to wait for CAPTCHA UI to disappear.", start)
            return True
        challenge_visible = is_recaptcha_challenge(page)
        widget_visible    = is_recaptcha_widget_present(page)
        if not challenge_visible and not widget_visible:
            _cap_log("reCAPTCHA clearance confirmed.", start)
            return True
        state = []
        if challenge_visible:
            state.append("image challenge visible")
        if widget_visible:
            state.append("checkbox widget visible")
        elapsed = int(time.time() - start)
        if elapsed >= last_log + 2:
            _cap_log(f"Waiting for page to accept token... ({', '.join(state) or 'pending'})", start)
            last_log = elapsed
    return False


def _apply_capmonster_user_agent(page, user_agent: str) -> None:
    # CRITICAL EVASION FIX: Skip structural identity drift mid-session to maintain unified identity footprint
    print_lg("[CAPTCHA] [Bypass] Retaining local Nstbrowser core footprint structure.")
    print_lg(f"[CAPTCHA] [Bypass] Ignored CapMonster runtime modification string: {user_agent}")
    return



def _apply_capmonster_cookies(page, cookies) -> None:
    if not cookies:
        return
    try:
        parsed_url = page.evaluate("location.href")
        from urllib.parse import urlparse
        hostname = urlparse(parsed_url).hostname or ""
        domain = hostname
        if hostname.count(".") >= 2:
            domain = "." + ".".join(hostname.split(".")[-2:])
        elif hostname and not hostname.startswith("."):
            domain = "." + hostname

        cookie_payload: list[dict] = []
        to_clear = []

        if isinstance(cookies, dict):
            for name, value in cookies.items():
                if name:
                    to_clear.append(str(name))
                    cookie_payload.append({
                        "name": str(name),
                        "value": str(value),
                        "domain": domain,
                        "path": "/",
                    })
        elif isinstance(cookies, list):
            for cookie in cookies:
                if not isinstance(cookie, dict) or not cookie.get("name"):
                    continue
                name = str(cookie.get("name"))
                to_clear.append(name)
                normalized = {
                    "name": name,
                    "value": str(cookie.get("value", "")),
                    "domain": domain,
                    "path": str(cookie.get("path") or "/"),
                }
                if cookie.get("expires") is not None:
                    try:
                        normalized["expires"] = int(cookie["expires"])
                    except Exception:
                        pass
                cookie_payload.append(normalized)
        elif isinstance(cookies, str):
            for pair in cookies.split(";"):
                if "=" not in pair:
                    continue
                name, value = pair.split("=", 1)
                name = name.strip()
                if name:
                    to_clear.append(name)
                    cookie_payload.append({
                        "name": name,
                        "value": value.strip(),
                        "domain": domain,
                        "path": "/",
                    })
        if not cookie_payload:
            return

        # Expire any conflicting subdomain cookies first
        for name in to_clear:
            try:
                page.context.add_cookies([{
                    "name": name,
                    "value": "",
                    "domain": hostname,
                    "path": "/",
                    "expires": 0
                }])
            except Exception:
                pass

        page.context.add_cookies(cookie_payload)
        print_lg(f"[CAPTCHA] Applied {len(cookie_payload)} CapMonster cookie(s) for domain {domain}.")
    except Exception as e:
        print_lg(f"[CAPTCHA] Could not apply CapMonster cookies: {e}")



def _cf_clearance_cookie_present(page) -> bool:
    try:
        cookies = page.context.cookies(page.url)
        matches = [
            cookie for cookie in cookies
            if cookie.get("name") == "cf_clearance" and cookie.get("value")
        ]
        if matches:
            domains = ", ".join(sorted({str(cookie.get("domain") or "") for cookie in matches}))
            print_lg(f"[CAPTCHA] Browser now has cf_clearance cookie(s) for: {domains}")
            return True
        print_lg("[CAPTCHA] Browser does not show a cf_clearance cookie after apply.")
    except Exception as e:
        print_lg(f"[CAPTCHA] Could not verify cf_clearance cookie presence: {e}")
    return False


def _apply_capmonster_cf_clearance(page, value: str) -> bool:
    if not value:
        return False
    try:
        url = page.evaluate("location.href")
        from urllib.parse import urlparse
        hostname = urlparse(url).hostname or ""
        domain = hostname
        if hostname.count(".") >= 2:
            parts = hostname.split(".")
            domain = "." + ".".join(parts[-2:])
        elif hostname and not hostname.startswith("."):
            domain = "." + hostname

        # Expire possible stale domain and host cookies first.
        for stale_domain in {hostname, domain}:
            if not stale_domain:
                continue
            try:
                page.context.add_cookies([{
                    "name": "cf_clearance",
                    "value": "",
                    "domain": stale_domain,
                    "path": "/",
                    "expires": 0
                }])
            except Exception:
                pass

        cookie_value = str(value)
        cookie_payload = [{
            "name": "cf_clearance",
            "value": cookie_value,
            "domain": domain,
            "path": "/",
            "secure": True,
            "sameSite": "None",
        }]
        if url:
            cookie_payload.append({
                "name": "cf_clearance",
                "value": cookie_value,
                "url": url,
                "secure": True,
                "sameSite": "None",
            })
        page.context.add_cookies(cookie_payload)
        print_lg(f"[CAPTCHA] Applied CapMonster cf_clearance cookie for domain {domain} and host URL.")
        return _cf_clearance_cookie_present(page)
    except Exception as e:
        print_lg(f"[CAPTCHA] Could not apply CapMonster cf_clearance cookie: {e}")
        return False



def _dd_captcha_metric(kind: str):
    """Emit Datadog solve count + duration around a solver returning bool.

    Best-effort: silently no-ops when the datadog package/agent is absent.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            t0 = time.time()
            ok = fn(*args, **kwargs)
            try:
                from jobbots.core.datadog_metrics import gauge, increment
                tags = [
                    f"kind:{kind}",
                    f"outcome:{'solved' if ok else 'failed'}",
                    f"bot:{os.environ.get('BOT_NAME', 'unknown')}",
                ]
                increment("captcha.solve", tags=tags)
                gauge("captcha.solve_seconds", time.time() - t0, tags=tags)
            except Exception:
                pass
            return ok
        return wrapper
    return deco


@_dd_captcha_metric("recaptcha")
def solve_recaptcha_with_capmonster(page, timeout: int = _CAPMONSTER_TIMEOUT) -> bool:
    start      = time.time()
    client_key = _capmonster_client_key()
    if not client_key:
        print_lg("[CAPTCHA] CapMonster is off or no key is set; solve reCAPTCHA in the browser if it appears.")
        return False

    params = _extract_recaptcha_params(page)
    _cap_log(
        "CapMonster params: "
        f"type={'enterprise' if params.get('isEnterprise') else 'standard'}, "
        f"invisible={bool(params.get('isInvisible'))}, "
        f"sitekey={'yes' if params.get('websiteKey') else 'no'}, "
        f"data-s={'yes' if params.get('recaptchaDataSValue') else 'no'}, "
        f"enterprisePayload={','.join(sorted((params.get('enterprisePayload') or {}).keys())) or 'none'}, "
        f"pageAction={params.get('pageAction') or 'default'}, "
        f"apiDomain={params.get('apiDomain') or 'default'}",
        start,
    )
    if _recaptcha_recently_unsolvable(params):
        cooldown = _recaptcha_unsolvable_cooldown_seconds()
        _cap_log(
            "Skipping CapMonster reCAPTCHA retry: this exact Enterprise challenge "
            f"recently returned ERROR_CAPTCHA_UNSOLVABLE (cooldown={cooldown}s).",
            start,
        )
        return False

    user_agent = _get_page_user_agent(page)
    cookies = _get_page_cookies(page)
    from jobbots.core.secret_manager import get_capmonster_proxy_url
    proxy_url = get_capmonster_proxy_url()
    if _truthy(_secret_or_env("CAPMONSTER_RECAPTCHA_ONLY_NO_PROXY", "0")) or not proxy_url:
        attempts = [False]
    else:
        attempts = [True]
        if _truthy(_secret_or_env("CAPMONSTER_RECAPTCHA_PROXYLESS_FALLBACK", "1")):
            attempts.append(False)

    solution = None
    max_rounds = _recaptcha_max_retry_rounds()
    stop_retries = False
    for round_index in range(max_rounds):
        if max_rounds > 1:
            _cap_log(f"CapMonster reCAPTCHA retry round {round_index + 1}/{max_rounds}.", start)
        for attempt_index, use_proxy in enumerate(attempts):
            task_id = _create_capmonster_task(
                client_key, params,
                user_agent=user_agent,
                cookies=cookies,
                use_proxy=use_proxy,
            )
            if task_id is None:
                _cap_log("CapMonster task was not created.", start)
                continue

            _cap_log(f"Waiting for CapMonster token for taskId={task_id}... (up to {timeout}s)", start)
            solution = _poll_capmonster_result(client_key, task_id, timeout=timeout)
            if solution:
                break
            error_code = getattr(_poll_capmonster_result, "last_error_code", "")
            _cap_log(f"CapMonster taskId={task_id} finished without a usable token ({error_code or 'no_token'}).", start)
            if error_code != "ERROR_CAPTCHA_UNSOLVABLE":
                stop_retries = True
                break
            if use_proxy and attempt_index + 1 < len(attempts):
                _cap_log("Retrying reCAPTCHA without explicit CapMonster proxy.", start)
        if solution or stop_retries:
            break
        if round_index + 1 < max_rounds:
            _cap_log("Retrying reCAPTCHA with a fresh CapMonster task pair after unsolvable result.", start)

    # Some Greenhouse-hosted widgets expose an Enterprise endpoint but accept
    # the standard V2 token path.  CapMonster can create the Enterprise task
    # successfully yet return ERROR_CAPTCHA_UNSOLVABLE; retry once with the
    # documented standard V2 task shape before recording the challenge as
    # unsolvable.  Do not carry Enterprise-only fields into this fallback.
    if not solution and params.get("isEnterprise"):
        _cap_log("Enterprise task unsolvable; trying standard reCAPTCHA V2 fallback.", start)
        for use_proxy in (False, True):
            if use_proxy and not _capmonster_proxy_fields():
                continue
            task_id = _create_capmonster_task(
                client_key, params,
                user_agent=user_agent,
                cookies=cookies,
                use_proxy=use_proxy,
                force_standard=True,
            )
            if task_id is None:
                continue
            _cap_log(f"Waiting for standard V2 token for taskId={task_id}... (up to {timeout}s)", start)
            solution = _poll_capmonster_result(client_key, task_id, timeout=timeout)
            if solution:
                _cap_log("Standard reCAPTCHA V2 fallback returned a token.", start)
                break

    if not solution:
        if getattr(_poll_capmonster_result, "last_error_code", "") == "ERROR_CAPTCHA_UNSOLVABLE":
            _remember_recaptcha_unsolvable(params)
        _cap_log("CapMonster finished without a usable token.", start)
        return False

    solution_user_agent = solution.get("userAgent")
    if solution_user_agent:
        print_lg("[CAPTCHA] CapMonster returned a User-Agent with the token.")
        _apply_capmonster_user_agent(page, solution_user_agent)

    _apply_capmonster_cookies(page, solution.get("cookies"))

    token = solution.get("gRecaptchaResponse") or solution.get("token")
    if not token:
        _cap_log("CapMonster solution did not include a reCAPTCHA token.", start)
        return False
    if not _inject_recaptcha_token(page, token):
        _cap_log("Token injection failed.", start)
        return False
    _cap_log(f"Injected token ({len(token)} chars).", start)

    if is_recaptcha_expired(page):
        _cap_log("reCAPTCHA says verification expired after token injection.", start)
        return False

    _click_recaptcha_verify_if_visible(page)
    
    # Eluta custom: click "Continue" button to submit the token
    if "eluta.ca" in page.url:
        continue_btn = page.locator("button[name='submit'], button:has-text('Continue'), input[type='submit'][value='Continue']").first
        if continue_btn.count() > 0:
            _cap_log("Clicking Eluta 'Continue' button...", start)
            continue_btn.click()
            page.wait_for_timeout(3000)

    if _wait_for_recaptcha_clearance(page, timeout=8):
        _cap_log("✓ CapMonster token accepted.", start)
        return True

    if _indeed_submit_button_ready(page):
        _cap_log("Submit button is ready after token injection — returning to SmartApply submit flow.", start)
        return True

    # Eluta custom check: did the job listings load?
    if "eluta.ca" in page.url and page.locator(".lk-job-title").count() > 0:
        _cap_log("✓ Eluta job listings found after token injection.", start)
        return True

    _cap_log("CapMonster token injected, but the challenge is still visible.", start)
    return False


def _extract_hcaptcha_params(page) -> dict:
    """Pull websiteURL + websiteKey for CapMonster HCaptcha tasks."""
    try:
        return page.evaluate(
            """
            () => {
                const params = { websiteURL: location.href, websiteKey: "" };
                const widget = document.querySelector(
                    "[data-sitekey].h-captcha, .h-captcha[data-sitekey], "
                    + "#hcaptcha-widget[data-sitekey], [data-sitekey][data-hcaptcha-widget-id], "
                    + "div[data-sitekey]"
                );
                if (widget) {
                    params.websiteKey = widget.getAttribute("data-sitekey") || "";
                }
                if (!params.websiteKey) {
                    for (const frame of document.querySelectorAll("iframe[src*='hcaptcha']")) {
                        const src = frame.getAttribute("src") || "";
                        try {
                            const url = new URL(src, location.href);
                            const key = url.searchParams.get("sitekey") || url.searchParams.get("k");
                            if (key) { params.websiteKey = key; break; }
                        } catch (err) {}
                    }
                }
                return params;
            }
            """
        ) or {}
    except Exception as exc:
        print_lg(f"[CAPTCHA] hCaptcha param extraction failed: {exc}")
        return {}


def _inject_hcaptcha_token(page, token: str) -> bool:
    """Inject CapMonster hCaptcha token into response fields + callbacks."""
    try:
        invoked = page.evaluate(
            """
            (token) => {
                let n = 0;
                const names = ["h-captcha-response", "g-recaptcha-response"];
                for (const name of names) {
                    let field = document.querySelector(
                        `textarea[name="${name}"], textarea#${name}, input[name="${name}"]`
                    );
                    if (!field) {
                        field = document.createElement("textarea");
                        field.name = name;
                        field.id = name;
                        field.style.display = "none";
                        document.body.appendChild(field);
                    }
                    field.value = token;
                    field.innerHTML = token;
                    field.dispatchEvent(new Event("input", { bubbles: true }));
                    field.dispatchEvent(new Event("change", { bubbles: true }));
                    n += 1;
                }
                for (const widget of document.querySelectorAll(
                    "[data-callback], .h-captcha[data-callback]"
                )) {
                    const cb = widget.getAttribute("data-callback");
                    if (cb && typeof window[cb] === "function") {
                        try { window[cb](token); n += 1; } catch (err) {}
                    }
                }
                try {
                    if (window.hcaptcha && typeof window.hcaptcha.submit === "function") {
                        // no-op for some embeds
                    }
                } catch (err) {}
                return n;
            }
            """,
            token,
        )
        print_lg(f"[CAPTCHA] Injected hCaptcha token into {int(invoked or 0)} field(s).")
        return True
    except Exception as exc:
        print_lg(f"[CAPTCHA] hCaptcha token injection failed: {exc}")
        return False


@_dd_captcha_metric("hcaptcha")
def solve_hcaptcha_with_capmonster(page, timeout: int = _CAPMONSTER_TIMEOUT) -> bool:
    """Solve hCaptcha (common on BambooHR / some Lever boards) via CapMonster."""
    start = time.time()
    client_key = _capmonster_client_key()
    if not client_key:
        print_lg("[CAPTCHA] CapMonster is off or no key is set; cannot solve hCaptcha.")
        return False

    params = _extract_hcaptcha_params(page)
    website_key = (params.get("websiteKey") or "").strip()
    website_url = (params.get("websiteURL") or page.url or "").strip()
    if not website_key or not website_url:
        print_lg("[CAPTCHA] hCaptcha skipped: missing sitekey or URL.")
        return False

    user_agent = _get_page_user_agent(page)
    proxy_fields = _capmonster_proxy_fields()
    # Prefer proxyless for public ATS boards; fall back to proxy if configured.
    attempt_proxy_modes = [False]
    if proxy_fields and _truthy(_secret_or_env("CAPMONSTER_HCAPTCHA_USE_PROXY", "0")):
        attempt_proxy_modes = [True, False]
    elif proxy_fields and _truthy(_secret_or_env("CAPMONSTER_HCAPTCHA_PROXY_FIRST", "0")):
        attempt_proxy_modes = [True, False]

    solution = None
    for use_proxy in attempt_proxy_modes:
        fields = proxy_fields if use_proxy else {}
        task_type = "HCaptchaTask" if fields else "HCaptchaTaskProxyless"
        task = {
            "type": task_type,
            "websiteURL": website_url,
            "websiteKey": website_key,
        }
        task.update(fields)
        if use_proxy and user_agent:
            task["userAgent"] = user_agent
        try:
            _cap_log(
                f"Creating CapMonster hCaptcha task ({task_type}, "
                f"{_capmonster_proxy_log(fields)}).",
                start,
            )
            response = requests.post(
                _CAPMONSTER_CREATE_TASK_URL,
                json={"clientKey": client_key, "task": task},
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            print_lg(f"[CAPTCHA] CapMonster hCaptcha createTask failed: {exc}")
            continue
        if data.get("errorId"):
            print_lg(
                f"[CAPTCHA] CapMonster hCaptcha createTask error: "
                f"{data.get('errorCode') or data.get('errorDescription')}"
            )
            continue
        task_id = data.get("taskId")
        if task_id is None:
            continue
        _cap_log(f"Waiting for hCaptcha token taskId={task_id}...", start)
        solution = _poll_capmonster_result(client_key, int(task_id), timeout=timeout)
        if solution:
            break

    if not solution:
        _cap_log("CapMonster finished without an hCaptcha token.", start)
        return False

    token = (
        solution.get("gRecaptchaResponse")
        or solution.get("token")
        or solution.get("respKey")
        or ""
    )
    if not token:
        _cap_log("hCaptcha solution missing token field.", start)
        return False
    if not _inject_hcaptcha_token(page, token):
        return False
    _cap_log(f"✓ Injected hCaptcha token ({len(token)} chars).", start)
    try:
        page.wait_for_timeout(800)
    except Exception:
        time.sleep(0.8)
    return True


@_dd_captcha_metric("turnstile")
def solve_turnstile_with_capmonster(page, timeout: int = _CAPMONSTER_TURNSTILE_TIMEOUT) -> bool:
    """
    Solve a Cloudflare Turnstile challenge via CapMonster Cloud directly using token mode,
    falling back to cf_clearance mode.
    """
    start      = time.time()
    client_key = _capmonster_client_key()
    solve_turnstile_with_capmonster.last_diag = {}
    if not client_key:
        print_lg("[CAPTCHA] CapMonster is off or no key is set; solve Cloudflare in the browser if it appears.")
        return False

    # Wait up to 5 seconds for Turnstile parameters to load/render in the DOM
    params = {}
    for _ in range(10):
        params = _extract_turnstile_params(page)
        # Break early if we have all key fields, including websiteKey
        if params.get("data") and params.get("pageData") and params.get("websiteKey"):
            break
        time.sleep(0.5)

    sitekey = params.get("websiteKey") or "0x4AAAAAAADnBwMwJC38uztB"
    params["websiteKey"] = sitekey

    # CapMonster token mode requires pageAction. Default to 'managed' if not found.
    if not params.get("action"):
        params["action"] = "managed"


    _cap_log(
        "CapMonster Turnstile params (token mode): "
        f"sitekey={sitekey}, "
        f"source={params.get('widgetSource') or 'unknown'}, "
        f"action={'yes' if params.get('action') else 'no'}, "
        f"data={'yes' if params.get('data') else 'no'}, "
        f"pageData={'yes' if params.get('pageData') else 'no'}",
        start,
    )

    user_agent = _get_page_user_agent(page)
    cookies    = _get_page_cookies(page)

    use_proxy = not _turnstile_no_proxy_enabled()
    proxy_fields = _capmonster_proxy_fields(disable=not use_proxy)
    diag = {
        "context": _challenge_context_from_url(params.get("websiteURL") or _safe_current_url(page)),
        "url": _safe_current_url(page),
        "browser": (os.getenv("BROWSER_VENDOR") or "unknown").strip().lower() or "unknown",
        "challenge_detected": True,
        "challenge_type": "cloudflare_turnstile",
        "sitekey_present": bool(params.get("websiteKey")),
        "action_present": bool(params.get("action")),
        "data_present": bool(params.get("data")),
        "pagedata_present": bool(params.get("pageData")),
        "param_source": params.get("widgetSource") or "unknown",
        "browser_proxy": _masked_proxy_url(),
        "capmonster_mode": "turnstile_token",
        "capmonster_proxy_used": bool(proxy_fields),
        "capmonster_task_created": False,
        "capmonster_task_id": "",
        "capmonster_result": "not_attempted",
        "token_received": False,
        "cf_clearance_received": False,
        "fallback_cookie_mode_skipped": False,
        "resolved_after_capmonster": False,
        "resolved_after_reload": False,
        "final_status": "CAPMONSTER_NOT_ATTEMPTED",
    }
    solve_turnstile_with_capmonster.last_diag = diag

    from jobbots.core.secret_manager import get_capmonster_proxy_url
    proxy_url = get_capmonster_proxy_url()

    allow_proxyless_fallback = _truthy(_secret_or_env("CAPTCHA_CAPMONSTER_PROXYLESS_FALLBACK", "0"))

    # Determine attempt sequence. Cloudflare cf_clearance must use the same
    # proxy as the browser; proxyless fallback is opt-in for non-production tests.
    if not use_proxy or not proxy_url:
        attempts = [False]
    elif allow_proxyless_fallback:
        attempts = [True, False]
    else:
        attempts = [True]

    for attempt_index, current_use_proxy in enumerate(attempts):
        is_fallback = (attempt_index > 0)
        proxy_desc = "with proxy" if current_use_proxy else "proxyless"
        fallback_prefix = "Fallback: " if is_fallback else ""
        _cap_log(f"{fallback_prefix}Attempting CapMonster Turnstile solving ({proxy_desc})...", start)

        if current_use_proxy:
            # 1. Try standard TurnstileTask (token mode) with proxy (unless skipped)
            if _truthy(_SKIP_TURNSTILE_TOKEN_MODE):
                _cap_log("Skipping CapMonster Turnstile token mode with proxy (disabled via CAPTCHA_SKIP_TURNSTILE_TOKEN_MODE).", start)
            else:
                diag.update({
                    "capmonster_mode": "turnstile_token",
                    "capmonster_proxy_used": True,
                    "final_status": "TOKEN_MODE_ATTEMPTING_WITH_PROXY",
                })
                solve_turnstile_with_capmonster.last_diag = diag
                cf_task_id = _create_capmonster_turnstile_task(
                    client_key, params,
                    user_agent=user_agent,
                    cookies=cookies,
                    cloudflare_task_type="token",
                    use_proxy=True,
                )
                if cf_task_id is not None:
                    diag.update({
                        "capmonster_task_created": True,
                        "capmonster_task_id": cf_task_id,
                        "capmonster_result": "processing",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    _cap_log(f"Waiting for CapMonster Turnstile token (with proxy)... (up to {timeout}s)", start)
                    cf_solution = _poll_capmonster_result(client_key, cf_task_id, timeout=timeout)
                    if cf_solution and (cf_solution.get("token") or cf_solution.get("gRecaptchaResponse")):
                        token = cf_solution.get("token") or cf_solution.get("gRecaptchaResponse")
                        diag.update({
                            "capmonster_result": "token",
                            "token_received": True,
                            "final_status": "TOKEN_RECEIVED_WAITING_FOR_BROWSER_CLEAR",
                        })
                        solve_turnstile_with_capmonster.last_diag = diag
                        _inject_turnstile_token(page, token)
                        if _wait_for_cloudflare_clearance(page, timeout=15):
                            diag.update({
                                "resolved_after_capmonster": True,
                                "final_status": "SOLVED_BY_CAPMONSTER",
                            })
                            solve_turnstile_with_capmonster.last_diag = diag
                            _log_challenge_diag(diag)
                            _cap_log("✓ CapMonster Turnstile token accepted (with proxy).", start)
                            return True
                        diag.update({
                            "resolved_after_capmonster": False,
                            "final_status": "TOKEN_INJECTED_NOT_ACCEPTED",
                        })
                        solve_turnstile_with_capmonster.last_diag = diag
                        _log_challenge_diag(diag)
                        _cap_log("CapMonster Turnstile token injected (with proxy), but Cloudflare still appears visible.", start)
                    else:
                        error_code = getattr(_poll_capmonster_result, "last_error_code", "")
                        diag.update({
                            "capmonster_result": error_code or "no_token",
                            "token_received": False,
                            "final_status": "CAPMONSTER_RETURNED_NO_USABLE_TOKEN",
                        })
                        solve_turnstile_with_capmonster.last_diag = diag
                        _log_challenge_diag(diag)
                        _cap_log(f"CapMonster Turnstile token (with proxy) finished without usable token ({error_code or 'no_token'}).", start)
                else:
                    diag.update({
                        "capmonster_result": "task_not_created",
                        "final_status": "CAPMONSTER_TASK_NOT_CREATED",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    _log_challenge_diag(diag)

            # 2. Try cf_clearance mode with proxy
            _cap_log("Attempting fallback to cf_clearance mode (with proxy)...", start)
            diag.update({
                "capmonster_mode": "cf_clearance",
                "capmonster_result": "processing",
                "fallback_cookie_mode_skipped": False,
                "final_status": "CF_CLEARANCE_ATTEMPTING",
            })
            solve_turnstile_with_capmonster.last_diag = diag
            cf_task_id = _create_capmonster_turnstile_task(
                client_key, params,
                user_agent=user_agent,
                cookies=cookies,
                cloudflare_task_type="cf_clearance",
                html_page_base64=_get_page_html_base64(page),
                use_proxy=True,
            )
            if cf_task_id is not None:
                diag.update({
                    "capmonster_task_created": True,
                    "capmonster_task_id": cf_task_id,
                })
                solve_turnstile_with_capmonster.last_diag = diag
                _cap_log(f"Waiting for CapMonster cf_clearance (with proxy)... (up to {timeout}s)", start)
                cf_solution = _poll_capmonster_result(client_key, cf_task_id, timeout=timeout)
                if cf_solution:
                    diag.update({
                        "capmonster_result": "cf_clearance",
                        "cf_clearance_received": bool(
                            cf_solution.get("cf_clearance") or cf_solution.get("cookies")
                        ),
                        "final_status": "CF_CLEARANCE_RECEIVED_WAITING_FOR_BROWSER_CLEAR",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    returned_user_agent = cf_solution.get("userAgent")
                    if returned_user_agent:
                        print_lg("[CAPTCHA] CapMonster returned a User-Agent with cf_clearance.")
                        _apply_capmonster_user_agent(page, returned_user_agent)

                    _apply_capmonster_cookies(page, cf_solution.get("cookies"))
                    clearance_cookie_present = _apply_capmonster_cf_clearance(
                        page,
                        cf_solution.get("cf_clearance") or "",
                    )
                    if not clearance_cookie_present and not cf_solution.get("cookies"):
                        print_lg("[CAPTCHA] CapMonster returned clearance data, but browser cookie verification failed.")

                    try:
                        page.reload(wait_until="domcontentloaded", timeout=20000)
                    except Exception:
                        try:
                            page.goto(params.get("websiteURL") or page.url, wait_until="domcontentloaded", timeout=20000)
                        except Exception as e:
                            print_lg(f"[CAPTCHA] Could not reload after cf_clearance: {e}")

                    accept_wait = _cloudflare_clearance_accept_wait_seconds()
                    _cap_log(f"Waiting up to {accept_wait}s for Cloudflare to accept cf_clearance...", start)
                    if _wait_for_cloudflare_clearance(page, timeout=accept_wait):
                        diag.update({
                            "resolved_after_capmonster": True,
                            "final_status": "SOLVED_BY_CAPMONSTER_CF_CLEARANCE",
                        })
                        solve_turnstile_with_capmonster.last_diag = diag
                        _log_challenge_diag(diag)
                        _cap_log("✓ CapMonster cf_clearance accepted (with proxy).", start)
                        return True

                    if _turnstile_token_rescue_after_clearance_reject_enabled():
                        _cap_log(
                            "Trying CapMonster Turnstile token rescue (with proxy) after cf_clearance was rejected...",
                            start,
                        )
                        diag.update({
                            "capmonster_mode": "turnstile_token_rescue",
                            "capmonster_proxy_used": True,
                            "capmonster_result": "processing",
                            "token_received": False,
                            "final_status": "TOKEN_RESCUE_ATTEMPTING_WITH_PROXY",
                        })
                        solve_turnstile_with_capmonster.last_diag = diag
                        rescue_task_id = _create_capmonster_turnstile_task(
                            client_key, params,
                            user_agent=user_agent,
                            cookies=_get_page_cookies(page),
                            cloudflare_task_type="token",
                            use_proxy=True,
                        )
                        if rescue_task_id is not None:
                            diag.update({
                                "capmonster_task_created": True,
                                "capmonster_task_id": rescue_task_id,
                            })
                            solve_turnstile_with_capmonster.last_diag = diag
                            _cap_log(f"Waiting for CapMonster Turnstile token rescue (with proxy)... (up to {timeout}s)", start)
                            rescue_solution = _poll_capmonster_result(client_key, rescue_task_id, timeout=timeout)
                            if rescue_solution and (rescue_solution.get("token") or rescue_solution.get("gRecaptchaResponse")):
                                token = rescue_solution.get("token") or rescue_solution.get("gRecaptchaResponse")
                                diag.update({
                                    "capmonster_result": "token",
                                    "token_received": True,
                                    "final_status": "TOKEN_RESCUE_RECEIVED_WAITING_FOR_BROWSER_CLEAR",
                                })
                                solve_turnstile_with_capmonster.last_diag = diag
                                _inject_turnstile_token(page, token)
                                if _wait_for_cloudflare_clearance(page, timeout=20):
                                    diag.update({
                                        "resolved_after_capmonster": True,
                                        "final_status": "SOLVED_BY_CAPMONSTER_TOKEN_RESCUE_WITH_PROXY",
                                    })
                                    solve_turnstile_with_capmonster.last_diag = diag
                                    _log_challenge_diag(diag)
                                    _cap_log("✓ CapMonster Turnstile token rescue accepted (with proxy).", start)
                                    return True
                                diag.update({
                                    "resolved_after_capmonster": False,
                                    "final_status": "TOKEN_RESCUE_INJECTED_NOT_ACCEPTED",
                                })
                                solve_turnstile_with_capmonster.last_diag = diag
                                _log_challenge_diag(diag)
                                _cap_log("CapMonster Turnstile token rescue injected, but Cloudflare still appears visible.", start)
                            else:
                                error_code = getattr(_poll_capmonster_result, "last_error_code", "")
                                diag.update({
                                    "capmonster_result": error_code or "no_token",
                                    "final_status": "TOKEN_RESCUE_RETURNED_NO_USABLE_TOKEN",
                                })
                                solve_turnstile_with_capmonster.last_diag = diag
                                _log_challenge_diag(diag)
                                _cap_log(
                                    f"CapMonster Turnstile token rescue finished without usable token ({error_code or 'no_token'}).",
                                    start,
                                )
                        else:
                            diag.update({
                                "capmonster_result": "task_not_created",
                                "final_status": "TOKEN_RESCUE_TASK_NOT_CREATED",
                            })
                            solve_turnstile_with_capmonster.last_diag = diag
                            _log_challenge_diag(diag)

                    diag.update({
                        "capmonster_mode": "cf_clearance",
                        "resolved_after_capmonster": False,
                        "final_status": "CF_CLEARANCE_APPLIED_NOT_ACCEPTED",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    _log_challenge_diag(diag)
                    _cap_log("CapMonster cf_clearance applied (with proxy), but Cloudflare still appears visible.", start)
                else:
                    error_code = getattr(_poll_capmonster_result, "last_error_code", "")
                    diag.update({
                        "capmonster_result": error_code or "no_clearance",
                        "cf_clearance_received": False,
                        "final_status": "CAPMONSTER_RETURNED_NO_USABLE_CLEARANCE",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    _log_challenge_diag(diag)
                    _cap_log(f"CapMonster cf_clearance finished without usable clearance data ({error_code or 'no_clearance'}).", start)
            else:
                diag.update({
                    "capmonster_result": "task_not_created",
                    "final_status": "CAPMONSTER_CF_CLEARANCE_TASK_NOT_CREATED",
                })
                solve_turnstile_with_capmonster.last_diag = diag
                _log_challenge_diag(diag)
        else:
            # 3. Try standard TurnstileTask (token mode) proxyless
            diag.update({
                "capmonster_mode": "turnstile_token",
                "capmonster_proxy_used": False,
                "final_status": "TOKEN_MODE_ATTEMPTING_PROXYLESS",
            })
            solve_turnstile_with_capmonster.last_diag = diag
            cf_task_id = _create_capmonster_turnstile_task(
                client_key, params,
                user_agent=user_agent,
                cookies=cookies,
                cloudflare_task_type="token",
                use_proxy=False,
            )
            if cf_task_id is not None:
                diag.update({
                    "capmonster_task_created": True,
                    "capmonster_task_id": cf_task_id,
                    "capmonster_result": "processing",
                })
                solve_turnstile_with_capmonster.last_diag = diag
                _cap_log(f"Waiting for CapMonster Turnstile token (proxyless)... (up to {timeout}s)", start)
                cf_solution = _poll_capmonster_result(client_key, cf_task_id, timeout=timeout)
                if cf_solution and (cf_solution.get("token") or cf_solution.get("gRecaptchaResponse")):
                    token = cf_solution.get("token") or cf_solution.get("gRecaptchaResponse")
                    diag.update({
                        "capmonster_result": "token",
                        "token_received": True,
                        "final_status": "TOKEN_RECEIVED_WAITING_FOR_BROWSER_CLEAR",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    _inject_turnstile_token(page, token)
                    if _wait_for_cloudflare_clearance(page, timeout=15):
                        diag.update({
                            "resolved_after_capmonster": True,
                            "final_status": "SOLVED_BY_CAPMONSTER_PROXYLESS",
                        })
                        solve_turnstile_with_capmonster.last_diag = diag
                        _log_challenge_diag(diag)
                        _cap_log("✓ CapMonster Turnstile token accepted (proxyless).", start)
                        return True
                    diag.update({
                        "resolved_after_capmonster": False,
                        "final_status": "TOKEN_INJECTED_NOT_ACCEPTED",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    _log_challenge_diag(diag)
                    _cap_log("CapMonster Turnstile token injected (proxyless), but Cloudflare still appears visible.", start)
                else:
                    error_code = getattr(_poll_capmonster_result, "last_error_code", "")
                    diag.update({
                        "capmonster_result": error_code or "no_token",
                        "token_received": False,
                        "final_status": "CAPMONSTER_RETURNED_NO_USABLE_TOKEN",
                    })
                    solve_turnstile_with_capmonster.last_diag = diag
                    _log_challenge_diag(diag)
                    _cap_log(f"CapMonster Turnstile token (proxyless) finished without usable token ({error_code or 'no_token'}).", start)
            else:
                diag.update({
                    "capmonster_result": "task_not_created",
                    "final_status": "CAPMONSTER_TASK_NOT_CREATED",
                })
                solve_turnstile_with_capmonster.last_diag = diag
                _log_challenge_diag(diag)

    return False

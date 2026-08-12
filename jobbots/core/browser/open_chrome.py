from __future__ import annotations
"""
Browser Session Factory  —  SeleniumBase CDP → Playwright  (video-exact pattern)
==================================================================================
Implements the stealthy Playwright mode described in the tutorial video:

  STEP 1 — SeleniumBase spins up a stealthy browser (UC/CDP mode).
            The debug port is assigned DYNAMICALLY by SeleniumBase — no
            hardcoded 9222.

  STEP 2 — `sb.get_cdp_url()` returns the actual endpoint URL, e.g.
            "http://localhost:54321".

  STEP 3 — Playwright connects to that already-running stealthy browser via
            `playwright.chromium.connect_over_cdp(cdp_url)`.
            All subsequent page actions go through Playwright (stealthy because
            the browser it is attached to was already stealthy at launch).

  STEP 4 — `sb.solve_captcha()` and `sb.uc_gui_click_captcha()` are available
            on the SeleniumBase object for CAPTCHA solving when needed.

Exact code pattern from the video:
────────────────────────────────────
    from playwright.sync_api import sync_playwright
    from seleniumbase import Driver as SBDriver          # or SB context manager

    sb  = SBDriver(uc=True)
    url = sb.get_cdp_url()                               # dynamic — NOT hardcoded

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(url)
        context = browser.contexts[0]
        page    = context.pages[0]
        page.goto("https://ca.indeed.com")               # Playwright actions
        sb.solve_captcha()                               # SeleniumBase CAPTCHA

Module-level exports used by runAiBot.py and indeed_bot.py:
    sb       — SeleniumBase Driver  (CAPTCHA solving only)
    page     — Playwright Page      (ALL browser interactions)
    context  — Playwright BrowserContext
    browser  — Playwright Browser (CDP-connected)
    pw       — Playwright instance (call pw.stop() on shutdown)

version: 26.01.20.5.08+sb-cdp-playwright
"""

import os
import glob
import sys
import time
import json
import subprocess
import urllib.request

from jobbots.core.utils import (
    get_default_temp_profile, make_directories,
    print_lg, resolve_project_path,
)
from config.settings import (
    run_in_background, disable_extensions, safe_mode,
    file_name, failed_file_name, logs_folder_path,
    generated_resume_path, simplify_extension_path, extra_extension_paths,
    cdp_port,
)
from config.questions import default_resume_path
from jobbots.core.utils import find_default_profile_directory


def _get_proxy_url(name: str = "PROXY_URL") -> str:
    """Return a normalized proxy URL from Infisical/CLI, env, or .env."""
    from jobbots.core.secret_manager import get_browser_proxy_url, get_capmonster_proxy_url, get_proxy_url
    if name == "CAPMONSTER_PROXY_URL":
        return get_capmonster_proxy_url()
    if name == "PROXY_URL":
        return get_browser_proxy_url()
    return get_proxy_url(name)


def _effective_cdp_port() -> int | None:
    """
    Prefer ``CDP_PORT`` from the environment at call time so supervisor / login
    scripts can iterate multiple bots in one process; ``config.settings`` is
    loaded once and would otherwise stay on the first imported port.
    """
    raw = (os.environ.get("CDP_PORT") or "").strip()
    if raw:
        try:
            p = int(raw)
            if p > 0:
                return p
        except ValueError:
            pass
    try:
        p = int(cdp_port)
        return p if p > 0 else None
    except (TypeError, ValueError):
        return None


def _normalize_cdp_url(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("ws://") or value.startswith("wss://"):
        return value
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    return f"http://{value.rstrip('/')}"


def _http_cdp_base(value: str) -> str:
    """Return the HTTP base URL needed for /json/version probes."""
    value = _normalize_cdp_url(value)
    if value.startswith("ws://"):
        value = "http://" + value[len("ws://"):]
    elif value.startswith("wss://"):
        value = "https://" + value[len("wss://"):]
    if "/devtools/" in value:
        value = value.split("/devtools/", 1)[0]
    return value.rstrip("/")


def _read_cdp_version(cdp_url: str, timeout: float = 1.0) -> dict | None:
    base = _http_cdp_base(cdp_url)
    if not base:
        return None
    try:
        with urllib.request.urlopen(f"{base}/json/version", timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None


def _validated_cdp_url(cdp_url: str, label: str = "CDP endpoint") -> str | None:
    cdp_url = _normalize_cdp_url(cdp_url)
    data = _read_cdp_version(cdp_url)
    if data and (data.get("webSocketDebuggerUrl") or data.get("Browser")):
        print_lg(f"[Browser] {label} verified: {_http_cdp_base(cdp_url)}")
        return cdp_url
    if cdp_url:
        print_lg(f"[Browser] {label} is not reachable via /json/version: {_http_cdp_base(cdp_url)}")
    return None


def _candidate_chromedriver_ports() -> list[int]:
    ports: set[int] = set()
    raw = (os.environ.get("SELENIUMBASE_DRIVER_PORTS") or os.environ.get("CHROMEDRIVER_PORTS") or "").strip()
    for part in raw.replace(",", " ").split():
        try:
            p = int(part)
            if p > 0:
                ports.add(p)
        except ValueError:
            pass
    try:
        import psutil
        for conn in psutil.net_connections(kind="tcp"):
            if conn.status != psutil.CONN_LISTEN or not conn.laddr:
                continue
            host = getattr(conn.laddr, "ip", "") or conn.laddr[0]
            port = int(getattr(conn.laddr, "port", 0) or conn.laddr[1])
            if host not in ("127.0.0.1", "::1", "localhost"):
                continue
            try:
                proc = psutil.Process(conn.pid)
                name = (proc.name() or "").lower()
                cmd = " ".join(proc.cmdline()).lower()
            except Exception:
                continue
            if "uc_driver" in name or "chromedriver" in name or "uc_driver" in cmd or "chromedriver" in cmd:
                ports.add(port)
    except Exception as exc:
        print_lg(f"[Browser] psutil ChromeDriver port discovery skipped: {exc}")
    try:
        proc = subprocess.run(
            ["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
            check=False,
        )
        for line in proc.stdout.splitlines():
            lower = line.lower()
            if "uc_driver" not in lower and "chromedriver" not in lower:
                continue
            marker = "TCP "
            if marker not in line:
                continue
            endpoint = line.split(marker, 1)[1].split(" ", 1)[0]
            if ":" not in endpoint:
                continue
            try:
                ports.add(int(endpoint.rsplit(":", 1)[1]))
            except ValueError:
                continue
    except Exception as exc:
        print_lg(f"[Browser] lsof ChromeDriver port discovery skipped: {exc}")
    if not ports:
        for port in range(55000, 60001):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=0.05) as resp:
                    payload = json.loads(resp.read().decode("utf-8", errors="replace"))
                if payload.get("value", {}).get("message", "").lower().startswith("chromedriver"):
                    ports.add(port)
            except Exception:
                continue
    return sorted(ports)


def _discover_cdp_from_chromedriver_sessions() -> tuple[str | None, list[str]]:
    notes: list[str] = []
    for port in _candidate_chromedriver_ports():
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/sessions", timeout=1.0) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except Exception as exc:
            notes.append(f"chromedriver:{port} sessions unavailable ({exc})")
            continue
        sessions = payload.get("value") or []
        if not sessions:
            notes.append(f"chromedriver:{port} has no active sessions")
            continue
        for sess in sessions:
            caps = sess.get("capabilities") or {}
            addr = (caps.get("goog:chromeOptions") or {}).get("debuggerAddress")
            if not addr:
                notes.append(f"chromedriver:{port} session {sess.get('id')} has no debuggerAddress")
                continue
            cdp_url = _normalize_cdp_url(addr)
            if _validated_cdp_url(cdp_url, f"ChromeDriver {port} session {sess.get('id')} CDP"):
                return cdp_url, notes
            notes.append(f"chromedriver:{port} debuggerAddress {addr} is stale/unreachable")
    return None, notes


def _discover_cdp_from_probed_ports() -> tuple[str | None, list[str]]:
    """Probe common local debug ports directly (manual Chrome / --remote-debugging-port)."""
    notes: list[str] = []
    ports: list[int] = []
    for raw in (
        os.environ.get("EXISTING_CDP_URL"),
        os.environ.get("PLAYWRIGHT_CDP_URL"),
        os.environ.get("CDP_URL"),
        "",
    ):
        raw = (raw or "").strip()
        if not raw:
            continue
        base = _http_cdp_base(raw)
        if base.endswith(":9223") or ":922" in base:
            try:
                ports.append(int(base.rsplit(":", 1)[-1]))
            except ValueError:
                pass
    for raw in (os.environ.get("CDP_PORT"), os.environ.get("EXISTING_CDP_PORT"), "9223"):
        try:
            p = int(str(raw or "").strip())
            if p > 0 and p not in ports:
                ports.append(p)
        except (TypeError, ValueError):
            continue
    for p in (9223, 9224, 9225, 9333, 9222):
        if p not in ports:
            ports.append(p)
    for port in ports:
        url = f"http://127.0.0.1:{port}"
        verified = _validated_cdp_url(url, f"probed local port {port} CDP")
        if verified:
            return verified, notes
        notes.append(f"local port {port} has no reachable CDP /json/version")
    return None, notes


def discover_existing_cdp_url() -> tuple[str | None, list[str]]:
    """Best-effort CDP discovery for existing-browser attach mode."""
    notes: list[str] = []
    explicit = (
        os.environ.get("EXISTING_CDP_URL")
        or os.environ.get("PLAYWRIGHT_CDP_URL")
        or os.environ.get("CDP_URL")
        or ""
    ).strip()
    if explicit:
        verified = _validated_cdp_url(explicit, "explicit existing browser CDP")
        if verified:
            return verified, notes
        notes.append(f"explicit CDP URL is unreachable: {_http_cdp_base(explicit)}")
    cdp_url, port_notes = _discover_cdp_from_probed_ports()
    notes.extend(port_notes)
    if cdp_url:
        return cdp_url, notes
    cdp_url, driver_notes = _discover_cdp_from_chromedriver_sessions()
    notes.extend(driver_notes)
    return cdp_url, notes


# ── Authenticated proxy support ───────────────────────────────────────────────
def _build_proxy_chrome_config(proxy_url: str) -> tuple[str, str]:
    """Return (proxy_server_arg, extension_dir) for a possibly-authenticated proxy.

    Chrome's `--proxy-server` does not accept inline `user:pass@` (raises
    `ERR_NO_SUPPORTED_PROXIES`). This helper:
      * If the URL has credentials, strips them and writes a tiny MV3
        extension that responds to `chrome.webRequest.onAuthRequired` with
        the parsed user/pass. Returns the credential-less proxy URL plus the
        path to the extension directory.
      * If the URL has no credentials, returns it unchanged with `""` for the
        extension dir.
    Returns `("", "")` and prints a warning if the URL cannot be parsed.
    """
    import json
    import tempfile
    from urllib.parse import urlparse
    from jobbots.core.secret_manager import normalize_proxy_url
    proxy_url = normalize_proxy_url(proxy_url)
    try:
        parsed = urlparse(proxy_url)
    except Exception as exc:
        print_lg(f"[Browser] WARNING: could not parse PROXY_URL ({exc}); ignoring proxy.")
        return ("", "")

    scheme = (parsed.scheme or "http").lower()
    host = parsed.hostname or ""
    port = parsed.port
    user = parsed.username or ""
    pwd = parsed.password or ""

    if not host or not port:
        print_lg("[Browser] WARNING: PROXY_URL missing host/port; ignoring proxy.")
        return ("", "")

    proxy_server_arg = f"{scheme}://{host}:{port}"

    if not (user and pwd):
        return (proxy_server_arg, "")

    # Generate a minimal MV2 unpacked extension that handles
    # `chrome.webRequest.onAuthRequired` synchronously. MV2 is still loadable
    # as an unpacked extension via `--load-extension` and is the de-facto
    # pattern used by every Selenium / Playwright proxy-auth tutorial because
    # MV3 service workers can be paused when the first auth challenge fires,
    # which causes `ERR_INVALID_AUTH_CREDENTIALS` on the very first request.
    # The extension lives in a per-process temp dir so parallel bots don't
    # collide on the same unpacked extension across user-data dirs.
    ext_dir = tempfile.mkdtemp(prefix="proxyauth_")
    manifest = {
        "name": "Bot Proxy Auth",
        "version": "1.0.0",
        "manifest_version": 2,
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking",
        ],
        "background": {"scripts": ["bg.js"], "persistent": True},
        "minimum_chrome_version": "76.0.0",
    }
    # Proxy server itself is set via the --proxy-server CLI flag in the
    # caller; the extension only injects auth credentials when Chrome
    # encounters the 407 Proxy Authentication Required challenge.
    bg_js = (
        "function callbackFn(details) {\n"
        "  return { authCredentials: { username: %s, password: %s } };\n"
        "}\n"
        "chrome.webRequest.onAuthRequired.addListener(\n"
        "  callbackFn, {urls: ['<all_urls>']}, ['blocking']\n"
        ");\n"
    ) % (json.dumps(user), json.dumps(pwd))

    try:
        with open(os.path.join(ext_dir, "manifest.json"), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        with open(os.path.join(ext_dir, "bg.js"), "w", encoding="utf-8") as fh:
            fh.write(bg_js)
    except OSError as exc:
        print_lg(f"[Browser] WARNING: could not write proxy-auth extension ({exc}); proxy auth disabled.")
        return (proxy_server_arg, "")

    return (proxy_server_arg, ext_dir)


# ── Extension detection helpers ───────────────────────────────────────────────
_SIMPLIFY_EXT_ID = "pbanhockgagggenencehbnadejlgchfc"


def _find_simplify_extension() -> str | None:
    home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, "Library", "Application Support", "Google", "Chrome",
                     "Default", "Extensions", _SIMPLIFY_EXT_ID),
        os.path.join(home, "Library", "Application Support", "Google", "Chrome",
                     "Profile 1", "Extensions", _SIMPLIFY_EXT_ID),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome",
                     "User Data", "Default", "Extensions", _SIMPLIFY_EXT_ID),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome",
                     "User Data", "Profile 1", "Extensions", _SIMPLIFY_EXT_ID),
        os.path.join(home, ".config", "google-chrome", "Default",
                     "Extensions", _SIMPLIFY_EXT_ID),
    ]
    for ext_dir in candidates:
        if os.path.isdir(ext_dir):
            versions = sorted(glob.glob(os.path.join(ext_dir, "*")), reverse=True)
            for v in versions:
                if os.path.isdir(v) and os.path.isfile(os.path.join(v, "manifest.json")):
                    return v
    return None


def _get_extension_paths() -> list[str]:
    paths: list[str] = []
    if simplify_extension_path and os.path.isdir(simplify_extension_path):
        paths.append(simplify_extension_path)
        print_lg(f"[Browser] Simplify loaded from: {simplify_extension_path}")
    else:
        detected = _find_simplify_extension()
        if detected:
            paths.append(detected)
            print_lg(f"[Browser] Simplify auto-detected at: {detected}")
        else:
            print_lg("[Browser] Simplify not found — set simplify_extension_path in config/settings.py")
    for p in (extra_extension_paths or []):
        if os.path.isdir(p):
            paths.append(p)
            print_lg(f"[Browser] Extra extension: {p}")
    return paths


# ─────────────────────────────────────────────────────────────────────────────
# CDP URL extraction helper
# ─────────────────────────────────────────────────────────────────────────────

def _extract_cdp_url(sb) -> str | None:
    """
    Extract the Chrome DevTools Protocol endpoint URL from a running
    SeleniumBase / undetected-chromedriver session.

    SeleniumBase (v4.x) launches Chrome with a randomly chosen
    --remote-debugging-port and stores it as:
        driver.capabilities["goog:chromeOptions"]["debuggerAddress"]
        → e.g. "localhost:54321"

    We try several strategies to get it:
      1. driver.capabilities["goog:chromeOptions"]["debuggerAddress"]
      2. driver.options.debugger_address  (if options object is accessible)
      3. Parse --remote-debugging-port from Chrome's command-line args
      4. File-based: read /json/version from common candidate ports

    Returns "http://HOST:PORT" or None if all strategies fail.
    """
    # ── Strategy 1: Selenium capabilities (most reliable) ─────────────────
    try:
        caps = sb.capabilities if hasattr(sb, 'capabilities') else {}
        addr = caps.get("goog:chromeOptions", {}).get("debuggerAddress", "")
        if addr:
            url = _normalize_cdp_url(addr)
            print_lg(f"[Browser] CDP URL from capabilities: {url}")
            verified = _validated_cdp_url(url, "capabilities CDP endpoint")
            if verified:
                return verified
    except Exception:
        pass

    # ── Strategy 2: options.debugger_address ──────────────────────────────
    try:
        for attr in ('options', '_options', 'browser_profile'):
            opts = getattr(sb, attr, None)
            if opts and hasattr(opts, 'debugger_address') and opts.debugger_address:
                url = _normalize_cdp_url(opts.debugger_address)
                print_lg(f"[Browser] CDP URL from options.debugger_address: {url}")
                verified = _validated_cdp_url(url, "options.debugger_address CDP endpoint")
                if verified:
                    return verified
    except Exception:
        pass

    # ── Strategy 3: parse --remote-debugging-port from Chrome args ────────
    try:
        import re as _re
        cmd_args = []
        # Try getting args from service or capabilities
        for attr in ('service', '_service'):
            svc = getattr(sb, attr, None)
            if svc and hasattr(svc, 'service_args'):
                cmd_args = svc.service_args
                break
        # Also check capabilities for the arg
        caps = sb.capabilities if hasattr(sb, 'capabilities') else {}
        chrome_args = caps.get("goog:chromeOptions", {}).get("args", [])
        cmd_args = list(cmd_args) + list(chrome_args)

        for arg in cmd_args:
            m = _re.search(r'--remote-debugging-port[= ](\d+)', str(arg))
            if m:
                port = m.group(1)
                url = f"http://localhost:{port}"
                print_lg(f"[Browser] CDP URL from Chrome args: {url}")
                verified = _validated_cdp_url(url, "Chrome args CDP endpoint")
                if verified:
                    return verified
    except Exception:
        pass

    # ── Strategy 4: probe common ports via HTTP (/json/version) ──────────
    try:
        # Prefer non-default ports when probing so a stale bot Chrome on 9222
        # does not steal the Playwright connection from a fresh SeleniumBase run.
        candidate_ports = [9223, 9224, 9225, 9333, 9222]
        for port in candidate_ports:
            url = f"http://localhost:{port}"
            verified = _validated_cdp_url(url, f"probed port {port} CDP endpoint")
            if verified:
                print_lg(f"[Browser] CDP URL probed at port {port}: {verified}")
                return verified
    except Exception:
        pass

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Proxy-alignment helper
# ─────────────────────────────────────────────────────────────────────────────

def _log_egress_proxy_alignment(attach_label: str) -> None:
    """Log the egress proxy that CapMonster will use for Cloudflare Turnstile
    and reCAPTCHA solving. The browser profile MUST be configured
    with the same proxy: Turnstile tokens are bound to the IP that solved the
    challenge, so a mismatch causes silent token rejection by Cloudflare.
    Credentials are stripped before logging.
    """
    from jobbots.core.secret_manager import normalize_proxy_url, get_capmonster_proxy_url
    proxy = get_capmonster_proxy_url()
    if not proxy:
        print_lg(
            f"[Browser] WARNING: PROXY_URL is not set. CapMonster will solve "
            f"{attach_label} captchas proxyless. If Cloudflare requires an "
            "IP-bound token (typical for Indeed/Glassdoor) set PROXY_URL to "
            "match the profile's egress."
        )
        return
    proxy = normalize_proxy_url(proxy)
    try:
        from urllib.parse import urlparse
        p = urlparse(proxy)
        host = p.hostname or ""
        port = p.port
        scheme = (p.scheme or "http").lower()
        auth = "yes" if (p.username and p.password) else "no"
        print_lg(
            f"[Browser] Egress proxy alignment: CapMonster will solve via "
            f"{scheme}://{host}:{port} (auth={auth}). The {attach_label} "
            "profile MUST be configured with this same proxy."
        )
    except Exception as exc:
        print_lg(f"[Browser] Could not parse PROXY_URL ({exc}); CapMonster will pass it through verbatim.")


def _log_browser_egress_ip(page, attach_label: str) -> None:
    """Best-effort browser-side egress IP check for proxy alignment debugging."""
    if (os.environ.get("CAPTCHA_LOG_BROWSER_EGRESS_IP") or "1").strip().lower() in ("0", "false", "no", "off"):
        return
    try:
        result = page.evaluate(
            """
            async () => {
              const endpoints = [
                "https://api.ipify.org?format=json",
                "https://icanhazip.com/"
              ];
              for (const url of endpoints) {
                try {
                  const response = await fetch(url, { cache: "no-store" });
                  const text = await response.text();
                  try {
                    const data = JSON.parse(text);
                    if (data && data.ip) return String(data.ip).trim();
                  } catch (e) {}
                  const ip = text.trim();
                  if (ip) return ip;
                } catch (e) {}
              }
              return "";
            }
            """
        )
        if result:
            print_lg(f"[Browser] {attach_label} browser egress IP: {result}")
        else:
            print_lg(f"[Browser] {attach_label} browser egress IP check returned no value.")
    except Exception as exc:
        print_lg(f"[Browser] {attach_label} browser egress IP check failed: {exc}")
# ── Nstbrowser attach mode ───────────────────────────────────────────────────

def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def nstbrowser_keep_alive() -> bool:
    """True when the NST profile must stay open across bot process exits.

    Set ``KEEP_BROWSER=1`` or ``NSTBROWSER_KEEP_ALIVE=1`` so Playwright
    disconnects without stopping the Nstbrowser profile (quota-safe multi-job
    canaries / sequential worker dispatches in one window).
    """
    return _env_flag("KEEP_BROWSER") or _env_flag("NSTBROWSER_KEEP_ALIVE")


def _ensure_nst_cdp_port_proxy(debug_port: int, *, container: str = "jobbots-nstbrowser") -> bool:
    """On Docker Desktop (macOS), Chromium debug ports stay inside the NST container.

    Publish ``debug_port`` on the host by running a tiny socat sidecar on a shared
    user-defined network so Playwright on the Mac can reach CDP.
    Returns True when the host can open ``127.0.0.1:debug_port``.
    """
    import socket
    import subprocess

    def _port_open(host: str, port: int, timeout: float = 0.4) -> bool:
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    if debug_port <= 0:
        return False
    if _port_open("127.0.0.1", debug_port):
        return True

    network = (os.environ.get("NST_DOCKER_NETWORK") or "jobbots-net").strip() or "jobbots-net"
    proxy_name = f"nst-cdp-{debug_port}"
    try:
        subprocess.run(
            ["docker", "network", "create", network],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["docker", "network", "connect", network, container],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        pass
    # Drop any previous proxy for this port (stale mapping).
    try:
        subprocess.run(
            ["docker", "rm", "-f", proxy_name],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception:
        pass
    # Host:port -> socat in sidecar -> container:port on shared network.
    run = [
        "docker", "run", "-d", "--rm",
        "--name", proxy_name,
        f"--network={network}",
        "-p", f"127.0.0.1:{debug_port}:{debug_port}",
        "alpine/socat:latest",
        f"TCP-LISTEN:{debug_port},fork,reuseaddr",
        f"TCP:{container}:{debug_port}",
    ]
    try:
        proc = subprocess.run(run, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            print_lg(
                f"[Browser] CDP port proxy failed for {debug_port}: "
                f"{(proc.stderr or proc.stdout or '')[:240]}"
            )
            return False
    except Exception as exc:
        print_lg(f"[Browser] CDP port proxy exception for {debug_port}: {exc}")
        return False

    for _ in range(20):
        if _port_open("127.0.0.1", debug_port, timeout=0.5):
            print_lg(f"[Browser] CDP port {debug_port} published via {proxy_name}")
            return True
        time.sleep(0.25)
    print_lg(f"[Browser] CDP port proxy started but {debug_port} still closed on host")
    return False


def _resolve_nst_cdp_url(debug_port: int | None, *, prefer_ws: bool = True) -> str | None:
    """Build a host-reachable CDP URL for an NST remote debugging port."""
    if not debug_port:
        return None
    _ensure_nst_cdp_port_proxy(int(debug_port))
    try:
        import json as _json
        import urllib.request

        with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json/version", timeout=3) as resp:
            _vdata = _json.loads(resp.read().decode("utf-8"))
            ws = _vdata.get("webSocketDebuggerUrl") if prefer_ws else None
            if ws:
                # Rewrite any container-internal host to localhost for Docker Desktop.
                if "://" in ws:
                    from urllib.parse import urlparse, urlunparse

                    parsed = urlparse(ws)
                    if parsed.hostname not in ("127.0.0.1", "localhost"):
                        netloc = f"127.0.0.1:{debug_port}"
                        ws = urlunparse(parsed._replace(netloc=netloc))
                return ws
    except Exception:
        pass
    return f"http://127.0.0.1:{debug_port}"


def _open_via_nstbrowser(profile_id: str):
    """
    Open a profile in the running Nstbrowser client via its Local API
    and attach Playwright over CDP. Returns (sb=None, page, context, browser, pw).
    """
    import requests
    import atexit

    def _nst_debug_port(info: dict) -> int | None:
        if not isinstance(info, dict):
            return None
        for key in ("remoteDebuggingPort", "port", "debuggingPort"):
            raw = info.get(key)
            if raw is not None and str(raw).strip():
                try:
                    return int(raw)
                except (TypeError, ValueError):
                    continue
        return None

    from jobbots.core.browser.nst_profile_safety import require_existing_nst_profile_id

    profile_id = require_existing_nst_profile_id(profile_id, env_key="NSTBROWSER_PROFILE_ID")
    print_lg(
        f"[Browser] Opening existing Nstbrowser profile {profile_id} via Local API "
        "(reuse only — never POST /api/v2/profiles)…"
    )
    _log_egress_proxy_alignment("Nstbrowser")

    from jobbots.core.secret_manager import get_secret
    api_host = get_secret("NSTBROWSER_API_HOST", "127.0.0.1").strip()
    api_port = get_secret("NSTBROWSER_API_PORT", "8848").strip()
    # Prefer env (worker may have selected dual-account slot + key); else Infisical.
    api_key = (os.environ.get("NSTBROWSER_API_KEY") or get_secret("NSTBROWSER_API_KEY", "") or "").strip()
    if not api_key:
        try:
            from jobbots.core.browser.nst_accounts import resolve_api_key
            _slot, api_key = resolve_api_key(get_secret=get_secret)
            print_lg(f"[Browser] NST dual-account resolved slot={_slot}")
        except Exception:
            api_key = ""
    if not api_key:
        raise RuntimeError("NSTBROWSER_API_KEY is required; no fallback credential is allowed.")
    try:
        nst_slot = int((os.environ.get("_NST_RESOLVED_SLOT") or "").strip())
    except ValueError:
        nst_slot = 0
    if nst_slot not in (1, 2):
        try:
            from jobbots.core.browser.nst_accounts import resolve_api_key

            candidate_slot, candidate_key = resolve_api_key(get_secret=get_secret)
            nst_slot = candidate_slot if candidate_key == api_key else 1
        except Exception:
            nst_slot = 1
    from jobbots.core.browser.profile_lease import ProfileLease
    profile_lease = ProfileLease(profile_id)
    try:
        profile_lease.acquire()
    except RuntimeError as lease_exc:
        # Single-worker farm: a dead bot PID often leaves DynamoDB lease and blocks
        # the next lease (shows as "already leased" → exit without result).
        # Force-clear up to twice, then fail-open so apply continues on NST
        # (local Chrome fallback loses portal cookies and breaks Webshare auth).
        print_lg(
            f"[Browser] Profile lease busy for {profile_id}; force-releasing stale lease once ({lease_exc})"
        )
        acquired = False
        for attempt in range(2):
            try:
                ProfileLease(profile_id).force_release()
            except Exception:
                pass
            time.sleep(0.4 * (attempt + 1))
            try:
                profile_lease = ProfileLease(profile_id)
                profile_lease.acquire()
                acquired = True
                break
            except RuntimeError as retry_exc:
                print_lg(f"[Browser] Lease re-acquire attempt {attempt + 1} failed: {retry_exc}")
        if not acquired:
            print_lg(
                f"[Browser] Lease still busy for {profile_id}; proceeding without DynamoDB lease "
                f"(NST profile open is serialized by bot process; avoid local Chrome fallback)."
            )
            profile_lease = ProfileLease(profile_id)  # no-op acquire if table empty
            # Leave _table None so release() is a no-op.

    api_url = f"http://{api_host}:{api_port}"
    # Launch/reuse browser for an *existing* profile id — does not create profiles.
    url = f"{api_url}/api/v2/browsers/{profile_id}"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json"
    }

    # Profile proxy configuration is a one-time provisioning action.  Do not
    # mutate it on each attach: an invalid shorthand payload can leave the
    # profile on the wrong egress.  Set NSTBROWSER_SYNC_PROFILE_PROXY=1 only
    # for an explicit migration; the Job Bank provisioner uses the structured
    # NST payload unconditionally.
    from jobbots.core.secret_manager import get_browser_proxy_url, normalize_proxy_url
    proxy_url = normalize_proxy_url(get_browser_proxy_url() or "")
    sync_profile_proxy = str(os.getenv("NSTBROWSER_SYNC_PROFILE_PROXY") or "").strip().lower() in {"1", "true", "yes", "on"}
    if proxy_url and sync_profile_proxy:
        try:
            from jobbots.core.browser.nst_proxy import nst_proxy_payload, safe_proxy_host
            put_url = f"{api_url}/api/v2/profiles/{profile_id}/proxy"
            put_resp = requests.put(
                put_url,
                headers=headers,
                json=nst_proxy_payload(proxy_url),
                timeout=10
            )
            if put_resp.ok:
                # Log host only — never credentials
                _h = safe_proxy_host(proxy_url)
                try:
                    from jobbots.core.secret_manager import is_cf_heavy_portal
                    _cf = is_cf_heavy_portal()
                except Exception:
                    _cf = False
                # Keep both lane names grep-able for CI pins.
                if _cf:
                    _lane = "Proxy-Cheap preferred for CF-heavy Indeed/Glassdoor/Workopolis"
                else:
                    _lane = "Webshare static preferred for LinkedIn/other apply"
                print_lg(
                    f"[Browser] NST profile {profile_id[:8]}… proxy → {_h} "
                    f"({_lane}; CapMonster matched; profile stays separate per bot)"
                )
            else:
                print_lg(f"[Browser] Warning: failed to update Nstbrowser profile proxy: {put_resp.text}")
        except Exception as e:
            print_lg(f"[Browser] Warning: failed to update Nstbrowser profile proxy: {e}")

    config_payload = {
        "headless": False,
        "autoClose": False
    }
    launch_timeout = float(os.environ.get("NSTBROWSER_LAUNCH_TIMEOUT_SECONDS", "120"))

    cdp_url = None
    try:
        # Check if profile is already running to avoid consuming launch limit and opening multiple tabs
        status_url = f"{api_url}/api/v2/browsers"
        status_resp = requests.get(status_url, headers=headers, timeout=10)
        if status_resp.ok:
            status_data = status_resp.json()
            if status_data.get("code") == 0 or status_data.get("code") == 200:
                active_browsers = status_data.get("data")
                if isinstance(active_browsers, list):
                    for browser_info in active_browsers:
                        if browser_info and str(browser_info.get("profileId")) == str(profile_id):
                            cdp_url = browser_info.get("webSocketDebuggerUrl")
                            if not cdp_url:
                                debug_port = _nst_debug_port(browser_info)
                                if debug_port:
                                    cdp_url = _resolve_nst_cdp_url(debug_port)
                            elif cdp_url:
                                # Even when a WS URL is present it may point at a
                                # container-local host; force host-reachable form.
                                debug_port = _nst_debug_port(browser_info)
                                if debug_port:
                                    cdp_url = _resolve_nst_cdp_url(debug_port) or cdp_url
                            if cdp_url:
                                print_lg(f"[Browser] Nstbrowser profile {profile_id} is already running. Reusing existing session.")
                                break
    except Exception as e:
        print_lg(f"[Browser] Failed to check running browsers: {e}. Proceeding with launch.")

    if not cdp_url:
        # Clean up stale SingletonLock/SingletonSocket/SingletonCookie inside container to prevent startup hang
        try:
            import subprocess
            subprocess.run(
                ["docker", "exec", "jobbots-nstbrowser", "rm", "-f",
                 f"/data/{profile_id}/SingletonLock",
                 f"/data/{profile_id}/SingletonSocket",
                 f"/data/{profile_id}/SingletonCookie"],
                capture_output=True,
                text=True,
                timeout=5
            )
            print_lg(f"[Browser] Stale lock files checked/removed for profile {profile_id}")
        except Exception as e:
            print_lg(f"[Browser] Warning: failed to clean stale lock files via docker exec: {e}")

        # Concurrent portal workers can thrash NST ("retrieving browser version
        # info failed" / connection reset). Retry with backoff before giving up.
        try:
            launch_attempts = max(1, int(os.environ.get("NSTBROWSER_LAUNCH_RETRIES", "3") or "3"))
        except ValueError:
            launch_attempts = 3
        last_exc: Exception | None = None
        resp_data = None
        for attempt in range(launch_attempts):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=config_payload,
                    timeout=launch_timeout,
                )
                response.raise_for_status()
                resp_data = response.json()
                code = resp_data.get("code") if isinstance(resp_data, dict) else None
                msg = ""
                if isinstance(resp_data, dict):
                    msg = str(resp_data.get("msg") or resp_data.get("message") or "")
                # Soft-fail business errors that recover after a short wait.
                soft = any(
                    t in msg.lower()
                    for t in (
                        "version info",
                        "try again",
                        "network",
                        "busy",
                        "timeout",
                        "too many",
                    )
                )
                if code is not None and code not in (0, 200) and soft and attempt + 1 < launch_attempts:
                    print_lg(
                        f"[Browser] NST launch soft-fail attempt {attempt + 1}/{launch_attempts}: "
                        f"code={code} {msg[:120]} — retrying"
                    )
                    time.sleep(2.5 * (attempt + 1))
                    continue
                if code is not None and code not in (0, 200):
                    profile_lease.release()
                    raise RuntimeError(
                        f"Nstbrowser start browser failed: code={code} message={msg or resp_data}"
                    )
                try:
                    from jobbots.core.browser.nst_accounts import record_profile_open

                    observed = record_profile_open(nst_slot)
                    print_lg(
                        f"[Browser] NST slot {nst_slot} observed launches today: {observed} "
                        "(local counter; dashboard value takes priority)"
                    )
                except Exception as exc:
                    print_lg(f"[Browser] NST quota counter warning: {exc}")
                break
            except Exception as e:
                last_exc = e
                if attempt + 1 >= launch_attempts:
                    profile_lease.release()
                    response_detail = ""
                    try:
                        response_detail = f" Response: {response.text[:500]}"  # type: ignore[name-defined]
                    except Exception:
                        pass
                    raise RuntimeError(
                        f"Nstbrowser Local API connection failed at {url}: {e}.{response_detail}\n"
                        "Verify the Nstbrowser desktop app / container is running, the Local API is enabled, "
                        f"and profile ID '{profile_id}' is valid."
                    ) from e
                print_lg(
                    f"[Browser] NST launch attempt {attempt + 1}/{launch_attempts} failed ({e}); "
                    f"backoff {2.5 * (attempt + 1):.1f}s"
                )
                time.sleep(2.5 * (attempt + 1))

        if not isinstance(resp_data, dict):
            profile_lease.release()
            raise RuntimeError(
                f"Nstbrowser start browser returned invalid data envelope: {resp_data}"
                + (f" last_exc={last_exc}" if last_exc else "")
            )

        data = resp_data.get("data")
        if not isinstance(data, dict):
            profile_lease.release()
            raise RuntimeError(f"Nstbrowser start browser returned invalid data envelope: {resp_data}")

        cdp_url = data.get("webSocketDebuggerUrl")
        debug_port = _nst_debug_port(data)
        if debug_port:
            # Docker Desktop: always publish the debug port to the host first.
            cdp_url = _resolve_nst_cdp_url(debug_port) or cdp_url
        if not cdp_url:
            profile_lease.release()
            raise RuntimeError(f"Nstbrowser start browser returned no debugging port/url: {resp_data}")


    print_lg(f"[Browser] Nstbrowser CDP endpoint: {cdp_url}")

    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()

    browser = None
    last_err = None
    for attempt in range(5):
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            break
        except Exception as e:
            last_err = e
            print_lg(f"[Browser] CDP connect attempt {attempt + 1} failed ({e}) — retrying…")
            time.sleep(1.5)

    if browser is None:
        try:
            # Attempt to stop the profile on connection failure (never when keep-alive —
            # another process may still own a healthy session).
            if not nstbrowser_keep_alive():
                requests.delete(f"{api_url}/api/v2/browsers/{profile_id}", headers=headers, timeout=10)
        except Exception:
            pass
        profile_lease.release()
        raise RuntimeError(f"Playwright could not connect to Nstbrowser at {cdp_url}: {last_err}")

    keep_alive = nstbrowser_keep_alive()
    if keep_alive:
        def patched_close(*args, **kwargs):
            print_lg(
                "[Browser] KEEP_BROWSER/NSTBROWSER_KEEP_ALIVE: preserving Nstbrowser "
                "profile; Playwright will disconnect via pw.stop()."
            )
            profile_lease.release()

        browser.close = patched_close

        def atexit_cleanup():
            profile_lease.release()

        atexit.register(atexit_cleanup)
    else:
        # Patch browser.close to stop the Nstbrowser profile automatically
        original_close = browser.close

        def patched_close():
            try:
                original_close()
            finally:
                try:
                    requests.delete(f"{api_url}/api/v2/browsers/{profile_id}", headers=headers, timeout=10)
                except Exception:
                    pass
                profile_lease.release()

        browser.close = patched_close

        # Register an atexit cleanup to stop the profile if Python exits unexpectedly
        def atexit_cleanup():
            try:
                requests.delete(f"{api_url}/api/v2/browsers/{profile_id}", headers=headers, timeout=10)
            except Exception:
                pass
            profile_lease.release()

        atexit.register(atexit_cleanup)

    context = None
    try:
        context = max(browser.contexts, key=lambda ctx: len(ctx.pages), default=None)
    except Exception:
        context = browser.contexts[0] if browser.contexts else None
    if context is None:
        context = browser.new_context()

    page = None
    for candidate in list(context.pages or []):
        try:
            if "indeed." in (candidate.url or "").lower():
                page = candidate
                break
        except Exception:
            continue
    if page is None and context.pages:
        page = context.pages[0]
    if page is None:
        page = context.new_page()

    print_lg(
        "[Browser] ✓ Playwright attached to Nstbrowser profile "
        f"(sb=None, keep_alive={keep_alive})."
    )
    return None, page, context, browser, pw





def _open_existing_cdp_session():
    """
    Attach Playwright to an already-running browser without launching, closing,
    or modifying any browser profile.

    This mode is intended for manual-login E2E runs. It accepts either an
    explicit EXISTING_CDP_URL/CDP_URL/PLAYWRIGHT_CDP_URL, or discovers live
    SeleniumBase UC ChromeDriver sessions and validates their debuggerAddress
    with /json/version before Playwright attaches.
    """
    cdp_url, notes = discover_existing_cdp_url()

    if not cdp_url:
        detail = "\n".join(f"  - {n}" for n in notes[-12:]) or "  - no SeleniumBase/ChromeDriver sessions found"
        raise RuntimeError(
            "[Browser] BROWSER_VENDOR=existing-cdp requires a reachable active CDP endpoint.\n"
            "I will not launch a fresh browser in existing-cdp mode.\n"
            "Start or keep open the SeleniumBase UC browser with remote debugging enabled, then set one of:\n"
            "  EXISTING_CDP_URL=http://127.0.0.1:<port>\n"
            "  PLAYWRIGHT_CDP_URL=http://127.0.0.1:<port>\n"
            "  CDP_URL=http://127.0.0.1:<port>\n"
            "Discovery notes:\n"
            f"{detail}"
        )

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    print_lg(f"[Browser] Playwright attaching to existing browser via CDP → {cdp_url}…")
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
    except Exception:
        pw.stop()
        raise

    def _preserve_existing_browser_close(*args, **kwargs):
        print_lg("[Browser] Existing-CDP attach mode: preserving external browser; Playwright will disconnect via pw.stop().")

    try:
        browser.close = _preserve_existing_browser_close
    except Exception:
        pass

    context = None
    try:
        context = max(browser.contexts, key=lambda ctx: len(ctx.pages), default=None)
    except Exception:
        context = browser.contexts[0] if browser.contexts else None
    if context is None:
        browser.close()
        pw.stop()
        raise RuntimeError(
            "[Browser] Existing CDP browser exposed no contexts. Refusing to create an isolated context "
            "because it would not share the logged-in session."
        )

    _install_recaptcha_enterprise_render_hook(context)

    page = None
    for candidate in context.pages:
        try:
            if "indeed." in (candidate.url or "").lower():
                page = candidate
                break
        except Exception:
            continue
    if page is None:
        page = context.pages[0] if context.pages else context.new_page()

    print_lg("[Browser] ✓ Playwright attached to existing logged-in browser (sb=None).")
    return None, page, context, browser, pw


def _install_recaptcha_enterprise_render_hook(context) -> None:
    """Capture Enterprise render options early enough for CapMonster task payloads."""
    try:
        context.add_init_script(
            """
            (() => {
              if (window.__capmonsterRecaptchaHookInstalled) return;
              window.__capmonsterRecaptchaHookInstalled = true;
              window.__capmonsterRecaptchaEnterprisePayloads =
                window.__capmonsterRecaptchaEnterprisePayloads || [];

              const capture = (container, opts) => {
                try {
                  if (!opts || typeof opts !== 'object') return;
                  const payload = {};
                  for (const [key, value] of Object.entries(opts)) {
                    if (key === 'callback' || key === 'expired-callback' || key === 'error-callback') continue;
                    if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
                      payload[key] = String(value);
                    }
                  }
                  window.__capmonsterRecaptchaEnterprisePayloads.push({
                    ts: Date.now(),
                    container: String(container || ''),
                    payload
                  });
                  if (window.__capmonsterRecaptchaEnterprisePayloads.length > 20) {
                    window.__capmonsterRecaptchaEnterprisePayloads.shift();
                  }
                } catch (e) {}
              };

              const wrap = (grecaptcha) => {
                try {
                  const ent = grecaptcha && grecaptcha.enterprise;
                  if (!ent || !ent.render || ent.render.__capmonsterWrapped) return;
                  const originalRender = ent.render.bind(ent);
                  const wrappedRender = function(container, opts) {
                    capture(container, opts);
                    return originalRender(container, opts);
                  };
                  wrappedRender.__capmonsterWrapped = true;
                  ent.render = wrappedRender;

                  if (ent.execute && !ent.execute.__capmonsterWrapped) {
                    const originalExecute = ent.execute.bind(ent);
                    const wrappedExecute = function(sitekeyOrId, opts) {
                      if (opts && typeof opts === 'object') {
                        const payload = Object.assign({sitekey: String(sitekeyOrId || '')}, opts);
                        capture('execute', payload);
                      }
                      return originalExecute(sitekeyOrId, opts);
                    };
                    wrappedExecute.__capmonsterWrapped = true;
                    ent.execute = wrappedExecute;
                  }
                } catch (e) {}
              };

              let current = window.grecaptcha;
              try {
                Object.defineProperty(window, 'grecaptcha', {
                  configurable: true,
                  get() { return current; },
                  set(value) {
                    current = value;
                    wrap(value);
                  }
                });
              } catch (e) {}
              wrap(current);
              const timer = setInterval(() => {
                wrap(window.grecaptcha);
                if ((window.__capmonsterRecaptchaEnterprisePayloads || []).length) {
                  clearInterval(timer);
                }
              }, 50);
              setTimeout(() => clearInterval(timer), 15000);
            })();
            """
        )
        print_lg("[Browser] reCAPTCHA Enterprise render hook installed.")
    except Exception as _e:
        print_lg(f"[Browser] Could not install reCAPTCHA Enterprise render hook: {_e}")


# ─────────────────────────────────────────────────────────────────────────────
# Main factory  —  exact video pattern
# ─────────────────────────────────────────────────────────────────────────────

def createBrowserSession(isRetry: bool = False, bot_name: str = None):
    """
    Launch SeleniumBase UC, grab its dynamic CDP endpoint URL,
    then connect Playwright to the stealthy browser.

    Returns
    -------
    (sb, page, context, browser, pw)
    """
    make_directories([
        file_name, failed_file_name,
        logs_folder_path + "/screenshots",
        default_resume_path,
        generated_resume_path + "/temp",
    ])

    # ── Fingerprint browser fast-path (Nstbrowser) ───────────────────────────
    vendor = (os.environ.get("BROWSER_VENDOR") or "nstbrowser").strip().lower()
    _local_chrome_vendors = {
        "chrome", "google-chrome", "regular-chrome", "normal-chrome", "local", "seleniumbase",
    }
    nst_profile_id = (os.environ.get("NSTBROWSER_PROFILE_ID") or "").strip()
    if vendor in _local_chrome_vendors:
        nst_profile_id = ""

    if vendor in ("existing-cdp", "existing_cdp", "existing-browser", "existing_browser", "attach-cdp", "attach_cdp"):
        return _open_existing_cdp_session()

    if vendor == "nstbrowser" or nst_profile_id:
        try:
            from jobbots.core.browser.nst_profile_safety import require_existing_nst_profile_id

            bot_name = (os.environ.get("BOT_NAME") or "current bot").strip()
            target_profile_id = require_existing_nst_profile_id(
                nst_profile_id,
                bot_name=bot_name,
                env_key="NSTBROWSER_PROFILE_ID",
            )
            return _open_via_nstbrowser(target_profile_id)
        except Exception as exc:
            print_lg(f"[Browser] WARNING: NSTBrowser launch failed ({exc}); falling back to local Chrome profile.")

    ext_paths   = _get_extension_paths() if not disable_extensions and not run_in_background else []
    # ── Chrome user-data dir (persistent session / cookies) ───────────────────
    # Prefer CHROME_PROFILE_DIR (supervisor / orchestrator) so cwd never points
    # at the wrong folder; then per-bot path under repo root (not os.getcwd());
    # then legacy system Chrome profile (Windows/Linux only).
    env_profile = (os.environ.get("CHROME_PROFILE_DIR") or "").strip()
    _dedicated_profile = False
    if env_profile:
        profile_dir = os.path.abspath(os.path.expanduser(env_profile))
        os.makedirs(profile_dir, exist_ok=True)
        _dedicated_profile = True
        print_lg(f"[Browser] Chrome profile from CHROME_PROFILE_DIR: {profile_dir}")
    elif bot_name:
        profile_dir = resolve_project_path(os.path.join("data", "browser_profiles", bot_name))
        os.makedirs(profile_dir, exist_ok=True)
        _dedicated_profile = True
        print_lg(f"[Browser] Per-bot Chrome profile: {profile_dir}")
    else:
        profile_dir = find_default_profile_directory()
        if profile_dir:
            print_lg(f"[Browser] System Chrome user-data: {profile_dir}")

    # Only clear Singleton* for our isolated profile dirs — never for system Chrome.
    if profile_dir and _dedicated_profile:
        for _stale in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
            _p = os.path.join(profile_dir, _stale)
            try:
                if os.path.islink(_p) or os.path.exists(_p):
                    os.unlink(_p)
                    print_lg(f"[Browser] _clean_stale_singletons: removed {_stale}")
            except Exception:
                pass

    print_lg(
        "IF YOU HAVE MORE THAN 10 TABS OPENED, PLEASE CLOSE OR BOOKMARK THEM! "
        "Or it's highly likely that the bot will not do anything!"
    )

    # ── Effective user-data directory ──────────────────────────────────────
    # Retry used to force a guest profile, which breaks Glassdoor/Indeed one-login
    # session continuity and triggers auth/anti-bot loops. Keep the same persistent
    # dir unless safe_mode (explicit guest) or no profile path was resolved.
    if safe_mode:
        udd = get_default_temp_profile()
        print_lg("[Browser] Safe mode — temporary/guest user-data (sessions not kept).")
    elif profile_dir:
        udd = profile_dir
        if isRetry:
            print_lg("[Browser] Retry — keeping persistent profile for session continuity.")
    else:
        udd = get_default_temp_profile()
        print_lg("[Browser] No profile directory — using temporary user-data.")

    # ── Step 1: Require SeleniumBase ──────────────────────────────────────
    try:
        from seleniumbase import Driver as _SBDriver
    except ImportError:
        venv_python = os.path.join(os.getcwd(), "venv", "bin", "python")
        raise RuntimeError(
            "SeleniumBase is not installed.\n"
            f"Python interpreter: {sys.executable}\n"
            "Run in the same environment that starts the bot:\n"
            "  python -m pip install seleniumbase playwright\n"
            "  python -m playwright install chromium\n"
            f"Project venv shortcut: {venv_python} runAiBot.py --platform indeed"
        )

    # ── Proxy configuration (reads PROXY_URL from env) ──────────────────────
    # Chrome's `--proxy-server` flag does NOT accept inline `user:pass@` auth
    # (Chrome reports `ERR_NO_SUPPORTED_PROXIES`). When PROXY_URL contains
    # credentials we strip them out for `--proxy-server` and inject auth via
    # an auto-generated MV2 extension that handles `chrome.webRequest
    # .onAuthRequired` — the standard pattern for authenticated proxies in
    # headless / undetected Chrome.
    #
    # BYPASS_PROXY=1 disables the proxy entirely for this browser session.
    # Useful for one-time interactive logins where rotation isn't needed and
    # extension-based proxy auth can interact poorly with stealth Chrome.
    _bypass = (os.environ.get("BYPASS_PROXY") or "").strip().lower() in ("1", "true", "yes")
    _proxy_url_raw = "" if _bypass else _get_proxy_url("PROXY_URL")
    if _bypass:
        print_lg("[Browser] BYPASS_PROXY=1 — proxy disabled for this session.")
    _proxy_arg = ""
    _proxy_ext_dir = ""
    if _proxy_url_raw:
        _proxy_arg, _proxy_ext_dir = _build_proxy_chrome_config(_proxy_url_raw)
        # Avoid leaking credentials in logs.
        _safe_log = _proxy_arg or _proxy_url_raw
        if _proxy_ext_dir:
            print_lg(f"[Browser] Proxy enabled (auth via extension): {_safe_log}")
        else:
            print_lg(f"[Browser] Proxy enabled: {_safe_log}")
    else:
        print_lg("[Browser] No PROXY_URL set — running without proxy.")

    # Build extra Chromium args. Pinning the debug port avoids SeleniumBase's
    # random free-port probe, which can fail on locked-down macOS environments.
    extra_args_parts: list[str] = ["--no-first-run", "--no-default-browser-check", "--no-pings", "--disable-features=ChromeWhatsNewUI"]
    _cdp = _effective_cdp_port()
    if _cdp:
        extra_args_parts.append(f"--remote-debugging-port={_cdp}")

    _all_ext_paths = list(ext_paths) if ext_paths else []
    if _proxy_ext_dir:
        _all_ext_paths.append(_proxy_ext_dir)
        # Chrome 127+ disables MV2 extensions by default. The proxy-auth
        # extension is MV2 because MV3 service workers can be paused when
        # the first auth challenge fires (causes ERR_INVALID_AUTH_CREDENTIALS).
        # This flag re-enables MV2 for unpacked extensions only.
        extra_args_parts.append("--disable-features=ExtensionManifestV2Disabled")
    if _all_ext_paths:
        extra_args_parts.append(f"--load-extension={','.join(_all_ext_paths)}")

    if _proxy_arg:
        extra_args_parts.append(f"--proxy-server={_proxy_arg}")
    extra_args = extra_args_parts

    sb_kwargs: dict = {
        "browser":      "chrome",
        "uc":           True,           # undetected-chromedriver stealth patches
        "headless":     run_in_background,
        "no_sandbox":   True,
        "user_data_dir": udd,
    }
    # Chrome for Testing has a different fingerprint than system Chrome and can
    # worsen Cloudflare challenges. Opt in explicitly when needed.
    if os.environ.get("USE_CHROME_FOR_TESTING", "0").strip().lower() in ("1", "true", "yes", "on"):
        # sys is module-level; do not re-import here (makes sys local and breaks
        # earlier exception paths that reference sys.executable).
        _cft_path = os.path.join(
            sys.prefix, "lib", f"python{sys.version_info.major}.{sys.version_info.minor}",
            "site-packages", "seleniumbase", "drivers", "chrome-mac-arm64",
            "Google Chrome for Testing.app", "Contents", "MacOS", "Google Chrome for Testing"
        )
        if os.path.exists(_cft_path):
            sb_kwargs["binary_location"] = _cft_path
            print_lg(f"[Browser] Using Chrome for Testing binary: {_cft_path}")

    if extra_args:
        sb_kwargs["chromium_arg"] = extra_args

    if _cdp:
        print_lg(f"[Browser] Using CDP debug port: {_cdp}")
    print_lg("[Browser] Launching SeleniumBase UC (stealthy Chrome)…")
    sb = _SBDriver(**sb_kwargs)

    # ── Step 2: Get dynamic CDP endpoint URL ─────────────────────────────
    # SeleniumBase v4.30.x stores `options.debugger_address = "localhost:PORT"`
    # before Chrome launch.  After the driver is created, Selenium reflects
    # this back into  driver.capabilities["goog:chromeOptions"]["debuggerAddress"].
    # That gives us the *actual* random port Chrome was assigned — no hardcoded
    # port 9222 required (video's key technique).
    cdp_url = _extract_cdp_url(sb)

    if not cdp_url:
        raise RuntimeError(
            "[Browser] Could not extract CDP endpoint URL from SeleniumBase driver.\n"
            "Make sure SeleniumBase >= 4.29 is installed and Chrome launched successfully."
        )

    print_lg(f"[Browser] SeleniumBase CDP endpoint: {cdp_url}")

    # ── Step 3: Require Playwright ─────────────────────────────────────────
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        venv_python = os.path.join(os.getcwd(), "venv", "bin", "python")
        raise RuntimeError(
            "Playwright is not installed.\n"
            f"Python interpreter: {sys.executable}\n"
            "Run in the same environment that starts the bot:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium\n"
            f"Project venv shortcut: {venv_python} runAiBot.py --platform indeed"
        )

    # ── Step 4: Connect Playwright to the stealthy browser via CDP ─────────
    # Exact pattern from the video:
    #   pw  = sync_playwright().start()
    #   browser = pw.chromium.connect_over_cdp(cdp_url)
    #   context = browser.contexts[0]
    #   page    = context.pages[0]
    pw = sync_playwright().start()

    print_lg(f"[Browser] Playwright connecting via CDP → {cdp_url}…")
    browser = None
    for attempt in range(5):
        try:
            browser = pw.chromium.connect_over_cdp(cdp_url)
            break
        except Exception as e:
            if attempt < 4:
                print_lg(f"[Browser] CDP connect attempt {attempt + 1} failed ({e}) — retrying…")
                time.sleep(1.5)
            else:
                raise RuntimeError(
                    f"Playwright could not connect to Chrome at {cdp_url}.\n"
                    f"Error: {e}"
                )

    # Attach to the same context SeleniumBase/Chrome already opened. Prefer a
    # context that already has tabs; otherwise Playwright's new_context() is
    # isolated and won't share cookies/storage with the logged-in window.
    context = None
    try:
        best = None
        best_pages = -1
        for ctx in browser.contexts:
            n = len(ctx.pages)
            if n > best_pages:
                best_pages = n
                best = ctx
        context = best
    except Exception:
        context = None
    if context is None and browser.contexts:
        context = browser.contexts[0]
    if context is None:
        print_lg(
            "[Browser] WARNING: No CDP browser contexts — creating a new context "
            "(may break session continuity vs Selenium window)."
        )
        context = browser.new_context()

    if context.pages:
        page = context.pages[0]
    else:
        page = context.new_page()

    # ── CDP-level proxy auth (works in undetected-chromedriver UC mode) ─────
    # SeleniumBase UC mode strips `--load-extension` for stealth, so the MV2
    # auth-injection extension is silently dropped and Chrome falls back to
    # whatever cached creds it has — usually nothing — yielding
    # `ERR_INVALID_AUTH_CREDENTIALS`. Handling auth via the CDP Fetch domain
    # bypasses the extension entirely: Chrome forwards every 407 challenge
    # over the DevTools protocol and we respond with credentials. This is
    # the same approach Selenium Wire and most modern UC integrations use.
    if not _bypass:
        _proxy_url_for_cdp = _get_proxy_url("PROXY_URL")
        if _proxy_url_for_cdp:
            try:
                from urllib.parse import urlparse as _urlparse
                _p = _urlparse(_proxy_url_for_cdp)
                _u, _pw = (_p.username or ""), (_p.password or "")
                if _u and _pw:
                    _cdp_sess = context.new_cdp_session(page)
                    _cdp_sess.send("Fetch.enable", {
                        "handleAuthRequests": True,
                        "patterns": [{"urlPattern": "*"}],
                    })

                    def _on_auth_required(event, _s=_cdp_sess, _user=_u, _pwd=_pw):
                        try:
                            _s.send("Fetch.continueWithAuth", {
                                "requestId": event["requestId"],
                                "authChallengeResponse": {
                                    "response": "ProvideCredentials",
                                    "username": _user,
                                    "password": _pwd,
                                },
                            })
                        except Exception:
                            pass

                    def _on_request_paused(event, _s=_cdp_sess):
                        try:
                            _s.send("Fetch.continueRequest", {"requestId": event["requestId"]})
                        except Exception:
                            pass

                    _cdp_sess.on("Fetch.authRequired", _on_auth_required)
                    _cdp_sess.on("Fetch.requestPaused", _on_request_paused)
                    print_lg("[Browser] Proxy auth handler attached via CDP Fetch domain.")
            except Exception as _e:
                print_lg(f"[Browser] WARNING: could not attach CDP proxy-auth handler: {_e}")

    _install_recaptcha_enterprise_render_hook(context)

    # ── Window-title marker so the user can tell which Chrome window belongs ─
    # to which bot when running multiple bots in parallel. Title becomes e.g.
    # "[INDEED-IT] Indeed Job Search". Persists across navigations via init
    # script + a MutationObserver that re-prefixes when the page rewrites
    # document.title.
    _bot_label = (bot_name or os.environ.get("BOT_NAME") or "").upper().replace("_", "-")
    if _bot_label:
        try:
            context.add_init_script(
                """
                (() => {
                  const PREFIX = '[%s] ';
                  const apply = () => {
                    try {
                      if (!document.title.startsWith(PREFIX)) {
                        document.title = PREFIX + document.title.replace(/^\\[[^\\]]+\\]\\s+/, '');
                      }
                    } catch (e) {}
                  };
                  apply();
                  try {
                    const t = document.querySelector('title');
                    if (t) new MutationObserver(apply).observe(t, { childList: true });
                    new MutationObserver(apply).observe(document.documentElement, { subtree: true, childList: true });
                  } catch (e) {}
                })();
                """ % _bot_label
            )
            # Apply to the already-loaded page too.
            try:
                from jobbots.core.auto_mode import set_window_title_marker
                set_window_title_marker(page, bot_name or os.environ.get("BOT_NAME", ""))
            except Exception:
                pass
            print_lg(f"[Browser] Window-title marker set: [{_bot_label}]")
        except Exception as _e:
            print_lg(f"[Browser] Could not set window-title marker: {_e}")

    print_lg("[Browser] ✓ Playwright connected to stealthy browser — ready!")
    print_lg("[Browser]   page   → Playwright Page  (all browser actions)")
    print_lg("[Browser]   sb     → SeleniumBase      (CAPTCHA solving only)")

    # Maximize (best-effort)
    try:
        sb.maximize_window()
    except Exception:
        try:
            page.set_viewport_size({"width": 1920, "height": 1080})
        except Exception:
            pass

    return sb, page, context, browser, pw


# Legacy alias
def createChromeSession(isRetry: bool = False, bot_name: str = None):
    return createBrowserSession(isRetry, bot_name)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level session — imported by runAiBot.py
# ─────────────────────────────────────────────────────────────────────────────

_session = None

def _get_lazy_session():
    global _session
    if _session is None:
        bot_name = os.environ.get("BOT_NAME")
        sb, page, context, browser, pw = createBrowserSession(bot_name=bot_name)
        _session = {
            'sb': sb,
            'page': page,
            'context': context,
            'browser': browser,
            'pw': pw
        }
    return _session

def __getattr__(name: str):
    if name in ('sb', 'page', 'context', 'browser', 'pw'):
        return _get_lazy_session()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

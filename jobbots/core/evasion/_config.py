from __future__ import annotations

import os
import time
import pathlib
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
import json

# pyautogui transitively imports mouseinfo, which crashes on headless
# Linux (no $DISPLAY). On the VM/Windows hosts where this module is actually
# used the import always works; in CI/headless contexts we keep going with a
# stub so the module can still be imported for smoke tests.
try:
    import pyautogui  # type: ignore
except Exception:  # pragma: no cover — headless CI path
    class _PyAutoGuiStub:
        def __getattr__(self, name):
            raise RuntimeError(
                f"pyautogui.{name} called but pyautogui is unavailable "
                "(headless environment without DISPLAY)"
            )
    pyautogui = _PyAutoGuiStub()  # type: ignore

from jobbots.core.utils import print_lg
from jobbots.core.auto_mode import is_autonomous

# ── Config imports (all with safe fallbacks) ──────────────────────────────────
try:
    from config.settings import captcha_cf_timeout as _CF_TIMEOUT_DEFAULT
except ImportError:
    _CF_TIMEOUT_DEFAULT = 45

try:
    from config.settings import captcha_rc_timeout as _RECAPTCHA_TIMEOUT_DEFAULT
except ImportError:
    _RECAPTCHA_TIMEOUT_DEFAULT = 90

try:
    from config.settings import captcha_capmonster_timeout as _CAPMONSTER_TIMEOUT
except ImportError:
    _CAPMONSTER_TIMEOUT = 180

try:
    from config.settings import captcha_capmonster_turnstile_timeout as _CAPMONSTER_TURNSTILE_TIMEOUT
except ImportError:
    _CAPMONSTER_TURNSTILE_TIMEOUT = 120

try:
    from config.settings import captcha_cloudflare_solver as _CLOUDFLARE_SOLVER
except ImportError:
    _CLOUDFLARE_SOLVER = os.environ.get("CAPTCHA_CLOUDFLARE_SOLVER", "capsolver")

try:
    from config.settings import captcha_allow_gui_fallback as _ALLOW_GUI_FALLBACK
except ImportError:
    _ALLOW_GUI_FALLBACK = False

try:
    from config.settings import captcha_allow_manual_fallback as _ALLOW_MANUAL_FALLBACK
except ImportError:
    _ALLOW_MANUAL_FALLBACK = False

try:
    from config.settings import use_capsolver as _USE_CAPSOLVER
except ImportError:
    _USE_CAPSOLVER = True

try:
    from config.settings import use_capmonster_captcha_solver as _USE_CAPMONSTER
except ImportError:
    _USE_CAPMONSTER = False

# Skip Turnstile "token" mode and go straight to cf_clearance mode.
try:
    from config.settings import captcha_skip_turnstile_token_mode as _SKIP_TURNSTILE_TOKEN_MODE
except ImportError:
    _SKIP_TURNSTILE_TOKEN_MODE = os.environ.get("CAPTCHA_SKIP_TURNSTILE_TOKEN_MODE", "0")

# Bot identity — set BOT_INSTANCE_ID=0..3 in each bot's launch environment.
try:
    from config.settings import bot_instance_id as _BOT_INSTANCE_ID
except ImportError:
    _BOT_INSTANCE_ID = int(os.environ.get("BOT_INSTANCE_ID", "0"))

_POLL_INTERVAL = 1
_CAPSOLVER_CREATE_TASK_URL  = "https://api.capsolver.com/createTask"
_CAPSOLVER_GET_RESULT_URL   = "https://api.capsolver.com/getTaskResult"
_CAPMONSTER_CREATE_TASK_URL = "https://api.capmonster.cloud/createTask"
_CAPMONSTER_GET_RESULT_URL  = "https://api.capmonster.cloud/getTaskResult"
_CAPMONSTER_POLL_INTERVAL   = 2
_PROJECT_ROOT = _MONOREPO_ROOT

# Turnstile iframe wait: retry bounding-box up to this many times before giving up
_TURNSTILE_BBOX_RETRIES = 6
_TURNSTILE_BBOX_WAIT    = 1.2  # seconds between retries
_CAPTCHA_CALIBRATION_PATH = _MONOREPO_ROOT / "data" / "captcha_click_calibration.json"


def _elapsed(start: float) -> str:
    return f"{time.time() - start:.1f}s"


def _cap_log(message: str, start: float | None = None) -> None:
    prefix = "[CAPTCHA]"
    if start is not None:
        prefix = f"{prefix} +{_elapsed(start)}"
    print_lg(f"{prefix} {message}")


def _truthy(value) -> bool:
    return str(value).strip().lower() not in {"0", "false", "no", "off", "none", ""}


def _captcha_calibration_key() -> str:
    bot_name = (os.environ.get("BOT_NAME") or "default").strip().lower()
    return f"{bot_name}:cloudflare"


def _load_manual_cf_click_point() -> dict | None:
    try:
        if not _CAPTCHA_CALIBRATION_PATH.is_file():
            return None
        data = json.loads(_CAPTCHA_CALIBRATION_PATH.read_text(encoding="utf-8"))
        point = data.get(_captcha_calibration_key())
        if not isinstance(point, dict):
            return None
        x = int(point.get("x"))
        y = int(point.get("y"))
        if x <= 0 or y <= 0:
            return None
        return {"x": x, "y": y, **point}
    except Exception:
        return None


def _save_manual_cf_click_point(point: tuple[int, int] | None, page=None, context: str = "") -> None:
    if not point:
        return
    try:
        x, y = int(point[0]), int(point[1])
        if x <= 0 or y <= 0:
            return
        _CAPTCHA_CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(_CAPTCHA_CALIBRATION_PATH.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        try:
            url = page.url
        except Exception:
            url = ""
        key = _captcha_calibration_key()
        data[key] = {
            "x": x,
            "y": y,
            "bot_name": (os.environ.get("BOT_NAME") or "default").strip(),
            "context": context,
            "url": url,
            "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        _CAPTCHA_CALIBRATION_PATH.write_text(
            json.dumps(data, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print_lg(f"[CAPTCHA] Saved manual Cloudflare click point for {key}: ({x},{y})")
    except Exception as e:
        print_lg(f"[CAPTCHA] Could not save manual Cloudflare click point: {e}")


def _is_autonomous() -> bool:
    """Return True when running under the autonomous supervisor (no pyautogui alerts)."""
    try:
        return is_autonomous()
    except Exception:
        return os.environ.get("AUTONOMOUS_SUPERVISOR", "").strip() == "1"

from __future__ import annotations

import os
import time
import random
import subprocess

from jobbots.core.evasion._config import pyautogui, _BOT_INSTANCE_ID, _cap_log


def _focus_bot_os_window(page=None, sb=None) -> bool:
    """
    Bring this bot's Chrome window to the OS foreground before any pyautogui
    click.  Without this, clicks from one bot land on another bot's window.

    Strategy:
      1. Try Playwright page.bring_to_front() — works for CDP-connected tabs.
      2. Try SeleniumBase driver.switch_to / driver.maximize_window.
      3. On Windows, enumerate top-level HWND handles for a "Chrome" window
         matching this bot's profile/port and call SetForegroundWindow().
      4. On Linux/Mac, use wmctrl as a best-effort fallback.

    Returns True if any strategy succeeded.
    """
    # ── Step 1: Playwright bring_to_front ─────────────────────────────────
    if page is not None:
        try:
            page.bring_to_front()
            time.sleep(0.25)
        except Exception:
            pass

    # ── Step 2: SeleniumBase window focus ─────────────────────────────────
    if sb is not None:
        try:
            handles = sb.window_handles
            if handles:
                sb.switch_to.window(handles[-1])
            sb.maximize_window()
            time.sleep(0.15)
        except Exception:
            pass

    # ── Step 3: Win32 SetForegroundWindow ─────────────────────────────────
    if os.name == "nt":
        try:
            import ctypes
            import ctypes.wintypes

            user32 = ctypes.windll.user32
            EnumWindows          = user32.EnumWindows
            GetWindowTextW       = user32.GetWindowTextW
            GetClassNameW        = user32.GetClassNameW
            IsWindowVisible      = user32.IsWindowVisible
            SetForegroundWindow  = user32.SetForegroundWindow
            ShowWindow           = user32.ShowWindow
            SW_RESTORE = 9
            bot_name = (os.environ.get("BOT_NAME") or "").strip().lower()
            bot_marker = bot_name.replace("_", "-")

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.wintypes.BOOL,
                                              ctypes.wintypes.HWND,
                                              ctypes.wintypes.LPARAM)
            found_hwnds: list[tuple[int, str, str]] = []

            def _enum_cb(hwnd, _lparam):
                if not IsWindowVisible(hwnd):
                    return True
                title_buf = ctypes.create_unicode_buffer(512)
                class_buf = ctypes.create_unicode_buffer(256)
                GetWindowTextW(hwnd, title_buf, 512)
                GetClassNameW(hwnd, class_buf, 256)
                title = title_buf.value
                class_name = class_buf.value
                title_l = title.lower()
                class_l = class_name.lower()
                looks_like_chrome = (
                    "chrome" in title_l
                    or "chromium" in title_l
                    or "chrome_widgetwin" in class_l
                )
                matches_bot = (
                    not bot_name
                    or bot_name in title_l
                    or bot_marker in title_l
                )
                if looks_like_chrome and matches_bot:
                    found_hwnds.append((hwnd, title, class_name))
                return True

            EnumWindows(WNDENUMPROC(_enum_cb), 0)

            if found_hwnds:
                idx = min(_BOT_INSTANCE_ID, len(found_hwnds) - 1)
                target, title, class_name = found_hwnds[idx]
                ShowWindow(target, SW_RESTORE)
                user32.BringWindowToTop(target)
                user32.SetActiveWindow(target)
                ok = SetForegroundWindow(target)
                time.sleep(0.3)
                _cap_log(
                    f"Win32: focused Chrome HWND #{target} "
                    f"(bot slot {idx}, ok={bool(ok)}, title={title!r}, class={class_name!r})."
                )
                return True
            _cap_log(
                f"Win32: no Chrome HWND found for bot slot {_BOT_INSTANCE_ID} "
                f"(BOT_NAME={bot_name or 'unset'})."
            )
        except Exception as e:
            _cap_log(f"Win32 window focus failed: {e}")

    # ── Step 4: wmctrl on Linux/Mac ───────────────────────────────────────
    import sys
    if sys.platform == "darwin":
        focused_mac = False
        for app in ("Google Chrome", "Chromium", "Nstbrowser"):
            try:
                res = subprocess.run(
                    ["osascript", "-e", f'tell application "{app}" to activate'],
                    timeout=3, check=False,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if res.returncode == 0:
                    focused_mac = True
            except Exception:
                pass
        if focused_mac:
            time.sleep(0.35)
            return True

    try:
        subprocess.run(
            ["wmctrl", "-a", "Chrome"],
            timeout=3, check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(0.2)
        return True
    except Exception:
        pass

    return False


def _humanize_move_and_click(target_x: int, target_y: int,
                              duration_base: float = 0.35) -> None:
    """
    Move the OS mouse to (target_x, target_y) along a slightly randomised
    curved path, then click.  Cloudflare's JS fingerprints linear moveTo()
    trajectories; this makes movement look human.
    """
    start_x, start_y = pyautogui.position()
    mid_x = (start_x + target_x) / 2 + random.uniform(-40, 40)
    mid_y = (start_y + target_y) / 2 + random.uniform(-40, 40)
    steps = random.randint(50, 80)
    duration = duration_base + random.uniform(-0.08, 0.12)

    for i in range(steps + 1):
        t = i / steps
        bx = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * mid_x + t ** 2 * target_x
        by = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * mid_y + t ** 2 * target_y
        jitter_scale = 1 - t
        bx += random.uniform(-2, 2) * jitter_scale
        by += random.uniform(-2, 2) * jitter_scale
        pyautogui.moveTo(int(bx), int(by), duration=duration / steps)

    pyautogui.moveTo(target_x, target_y, duration=0.05)
    time.sleep(random.uniform(0.08, 0.18))
    pyautogui.click()

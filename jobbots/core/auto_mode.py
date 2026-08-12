"""
Autonomous-mode helpers.

When the supervisor launches a bot it sets ``AUTONOMOUS_SUPERVISOR=1``. In that
mode the bot must never block on a desktop dialog (pyautogui.alert / .confirm)
or a console ``input()`` — the only acceptable user interaction is the
one-time browser login.

Use these helpers in place of ``pyautogui.alert`` / ``pyautogui.confirm`` so a
single env flag flips every bot to fully unattended operation.

Also provides a Chrome window-title marker so users can tell which Chrome
window belongs to which bot when running multiple in parallel.
"""

from __future__ import annotations

import os


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def is_autonomous() -> bool:
    """Return True if the bot is running under the supervisor / unattended."""
    return (
        _truthy(os.environ.get("AUTONOMOUS_SUPERVISOR"))
        or _truthy(os.environ.get("SKIP_USER_START"))
        or _truthy(os.environ.get("RUN_IN_BACKGROUND"))
    )


def auto_confirm(message: str, title: str, buttons: list[str],
                 default: str | None = None) -> str:
    """
    Drop-in replacement for ``pyautogui.confirm``.

    In autonomous mode returns ``default`` (or the FIRST button, which by
    convention should be the safe / proceed option) without showing a dialog.
    Otherwise shows the real ``pyautogui.confirm`` dialog.
    """
    if is_autonomous():
        return default if default is not None else (buttons[0] if buttons else "")
    try:
        import pyautogui  # type: ignore
        return pyautogui.confirm(message, title, buttons)
    except Exception:
        return default if default is not None else (buttons[0] if buttons else "")


def auto_alert(message: str, title: str = "", button: str = "OK") -> str:
    """
    Drop-in replacement for ``pyautogui.alert``.

    In autonomous mode this is a no-op (returns ``button``) so the bot does
    not block on a desktop dialog. The message is still printed to the
    console for debugging.
    """
    if is_autonomous():
        try:
            print(f"[auto_alert] {title}: {message}".replace("\n", " | "))
        except Exception:
            pass
        return button
    try:
        import pyautogui  # type: ignore
        return pyautogui.alert(message, title, button)
    except Exception:
        return button


def set_window_title_marker(page, bot_name: str) -> None:
    """
    Inject a ``[BOT_NAME]`` prefix into the Chrome tab title so the user can
    tell which window belongs to which bot when several run in parallel.

    Safe / best-effort: any failure is swallowed (e.g. page closed mid-call).
    """
    if not bot_name:
        return
    label = bot_name.upper().replace("_", "-")
    try:
        # Set immediately on the current document, and override every future
        # title set by the page (Indeed/Glassdoor/LinkedIn rewrite document.title
        # on navigation, so we patch the property descriptor too).
        page.evaluate(
            """
            (label) => {
              const PREFIX = '[' + label + '] ';
              try {
                if (!document.title.startsWith(PREFIX)) {
                  document.title = PREFIX + document.title;
                }
              } catch (e) {}
              try {
                const titleEl = document.querySelector('title');
                if (titleEl && !titleEl.__bot_marker_observer) {
                  const obs = new MutationObserver(() => {
                    try {
                      if (!document.title.startsWith(PREFIX)) {
                        document.title = PREFIX + document.title.replace(/^\\[[^\\]]+\\]\\s+/, '');
                      }
                    } catch (e) {}
                  });
                  obs.observe(titleEl, { childList: true });
                  titleEl.__bot_marker_observer = true;
                }
              } catch (e) {}
            }
            """,
            label,
        )
    except Exception:
        pass

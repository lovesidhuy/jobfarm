from __future__ import annotations

"""Telegram alert notification module with rate-limiting and deduplication."""

import json
import time
import urllib.request
import urllib.error
from pathlib import Path
from jobbots.core.secret_manager import get_secret

# Default cooldown for identical alerts (1 hour)
ALERT_COOLDOWN_SECONDS = 3600

def _get_history_path() -> Path:
    from jobbots.core.supervised_bots import monorepo_root
    history_dir = monorepo_root() / "data"
    history_dir.mkdir(parents=True, exist_ok=True)
    return history_dir / "alert_history.json"

def _load_alert_history() -> dict[str, float]:
    path = _get_history_path()
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_alert_history(history: dict[str, float]) -> None:
    path = _get_history_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=2)
    except Exception:
        pass

def send_telegram_alert(message: str, bot_name: str | None = None, alert_type: str | None = None, force: bool = False) -> bool:
    """Send a Telegram notification alert using standard library urllib.
    
    Includes deduplication logic to avoid spamming the same alert.
    """
    bot_name = bot_name or "system"
    alert_type = alert_type or "general"
    
    # Deduplication check
    key = f"{bot_name}:{alert_type}"
    now = time.time()
    
    if not force:
        history = _load_alert_history()
        last_sent = history.get(key, 0.0)
        if now - last_sent < ALERT_COOLDOWN_SECONDS:
            # Suppress alert
            return False
            
    # Resolve credentials
    token = get_secret("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = get_secret("TELEGRAM_CHAT_ID", "").strip()
    
    if not token or not chat_id:
        print(f"[Alerts] Cannot send Telegram alert. TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set. Message: {message}")
        return False
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": f"⚠️ [{bot_name.upper()} Bot] {message}"
    }
    
    try:
        import ssl
        ssl_context = ssl._create_unverified_context()
    except Exception:
        ssl_context = None

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        
        urlopen_kwargs = {"timeout": 10}
        if ssl_context is not None:
            urlopen_kwargs["context"] = ssl_context
            
        with urllib.request.urlopen(req, **urlopen_kwargs) as response:
            res_data = response.read().decode("utf-8")
            res_json = json.loads(res_data)
            if res_json.get("ok"):
                # Save to history
                if not force:
                    history = _load_alert_history()
                    history[key] = now
                    _save_alert_history(history)
                return True
            else:
                print(f"[Alerts] Telegram API returned error: {res_data}")
                return False
    except urllib.error.URLError as e:
        print(f"[Alerts] Failed to send Telegram alert: {e}")
        return False
    except Exception as e:
        print(f"[Alerts] Unexpected error sending Telegram alert: {e}")
        return False

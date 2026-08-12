import os
import sys
import json
import time
import urllib.request
import urllib.error
import subprocess
from pathlib import Path

# Add project root to sys.path
base_dir = Path(__file__).resolve().parent
sys.path.append(str(base_dir))

from core.secret_manager import get_secret
from core.health_controller import evaluate_bot_health
from core.supervised_bots import _build_supervised_bot_configs

# Setup SSL context for urllib (allow unverified if needed)
try:
    import ssl
    ssl_context = ssl._create_unverified_context()
except Exception:
    ssl_context = None

# Force load secrets
TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = get_secret("TELEGRAM_CHAT_ID", "").strip()

while not TOKEN or not CHAT_ID:
    print("[TelegramBot] Error: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing. Retrying in 10 seconds...")
    time.sleep(10)
    TOKEN = get_secret("TELEGRAM_BOT_TOKEN", "").strip()
    CHAT_ID = get_secret("TELEGRAM_CHAT_ID", "").strip()

def get_updates(offset=None):
    url = f"https://api.telegram.org/bot{TOKEN}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=35, context=ssl_context) as response:
            data = json.loads(response.read().decode("utf-8"))
            if data.get("ok"):
                return data.get("result", [])
    except Exception as e:
        print(f"[TelegramBot] Error fetching updates: {e}")
    return []

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10, context=ssl_context) as response:
            return json.loads(response.read().decode("utf-8")).get("ok", False)
    except Exception as e:
        print(f"[TelegramBot] Error sending message: {e}")
    return False

def handle_message(msg):
    chat = msg.get("chat", {})
    chat_id = str(chat.get("id", ""))
    
    # Security: Only respond to messages from the configured CHAT_ID
    if chat_id != CHAT_ID:
        print(f"[TelegramBot] Unauthorized message from chat_id {chat_id}. Configured chat ID is {CHAT_ID}.")
        return

    text = msg.get("text", "").strip()
    if not text:
        return

    print(f"[TelegramBot] Received command: {text}")

    if text.startswith("/status"):
        try:
            enabled_bots = [cfg["bot_name"] for cfg in _build_supervised_bot_configs(include_disabled=False)]
            report = "🤖 *Jobbots Status Report*\n\n"
            for bot in enabled_bots:
                # Perform health check evaluation
                status = evaluate_bot_health(bot)
                state = status.get("state", "UNKNOWN")
                pid = status.get("pid", 0)
                last_seen = status.get("last_seen", 0.0)
                
                # Format last seen
                if last_seen > 0.0:
                    ago = int(time.time() - last_seen)
                    if ago < 60:
                        last_seen_str = "just now"
                    elif ago < 3600:
                        last_seen_str = f"{ago // 60}m ago"
                    else:
                        last_seen_str = f"{ago // 3600}h {(ago % 3600) // 60}m ago"
                else:
                    last_seen_str = "never"
                
                report += f"🔹 *{bot}*\n"
                report += f"  • Status: `{state}`\n"
                report += f"  • PID: `{pid}`\n"
                report += f"  • Last Seen: {last_seen_str}\n\n"
            send_message(chat_id, report)
        except Exception as e:
            send_message(chat_id, f"❌ Error retrieving status: {e}")

    elif text.startswith("/restart"):
        send_message(chat_id, "⏳ Triggering restart of `jobbots-supervisor.service`...")
        try:
            res = subprocess.run(["systemctl", "restart", "jobbots-supervisor.service"], capture_output=True, text=True)
            if res.returncode == 0:
                send_message(chat_id, "✅ Supervisor restarted successfully.")
            else:
                send_message(chat_id, f"❌ Failed to restart supervisor:\n```\n{res.stderr or res.stdout}\n```")
        except Exception as e:
            send_message(chat_id, f"❌ Error running systemctl: {e}")

    elif text.startswith("/help") or text.startswith("/start"):
        help_text = (
            "🤖 *Jobbots Telegram Control*\n\n"
            "Available commands:\n"
            "• `/status` - Get health status of active bots\n"
            "• `/restart` - Restart the main supervisor service\n"
            "• `/help` - Show this help message"
        )
        send_message(chat_id, help_text)

def main():
    print("[TelegramBot] Starting Telegram Listener Bot...")
    offset = None
    while True:
        try:
            updates = get_updates(offset)
            for update in updates:
                update_id = update.get("update_id")
                if update_id:
                    offset = update_id + 1
                
                message = update.get("message")
                if message:
                    handle_message(message)
        except Exception as e:
            print(f"[TelegramBot] Error in main loop: {e}")
            time.sleep(5)

if __name__ == "__main__":
    main()

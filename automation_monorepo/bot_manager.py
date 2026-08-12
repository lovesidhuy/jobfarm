#!/usr/bin/env python3
"""Local bot manager for desktop shortcuts.

Desktop shortcuts should call this file with commands such as:

    python bot_manager.py start linkedin_it
    python bot_manager.py stop linkedin_it
    python bot_manager.py status

The first client command starts a localhost manager process if one is not
already running. The manager owns the bot subprocesses and prevents duplicate
starts.

Autopilot mode is the default: Indeed/Glassdoor may run at the same time when
GUI/manual CAPTCHA fallbacks are disabled. Set BOT_MANAGER_SERIALIZE_CAPTCHA=1
to queue Indeed/Glassdoor bots one at a time for conservative testing.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = int(os.environ.get("BOT_MANAGER_PORT", "8765"))
STARTUP_TIMEOUT_SECONDS = 15

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.supervised_bots import supervised_bot_config_by_name, supervised_bot_configs  # noqa: E402
from core.supervisor_runtime import merge_dotenv_into_env, resolve_bot_python  # noqa: E402
from supervisor import build_subprocess_env  # noqa: E402


merge_dotenv_into_env(os.environ, BASE_DIR / ".env", override=False)


def _bot_names() -> list[str]:
    return [cfg["bot_name"] for cfg in supervised_bot_configs(BASE_DIR)]


def _json_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


AUTO_RESTART = _truthy(os.environ.get("BOT_MANAGER_AUTO_RESTART", "1"))
RESTART_COOLDOWN_SECONDS = int(os.environ.get("BOT_MANAGER_RESTART_COOLDOWN_SECONDS", "15"))


def _send(payload: dict[str, Any], timeout: float = 15.0) -> dict[str, Any]:
    with socket.create_connection((HOST, PORT), timeout=timeout) as sock:
        sock.sendall(_json_line(payload))
        raw = b""
        while not raw.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            raw += chunk
    if not raw:
        return {"ok": False, "message": "manager returned no response"}
    return json.loads(raw.decode("utf-8"))


def _start_server_process() -> None:
    python_exe = resolve_bot_python(BASE_DIR)
    args = [str(python_exe), str(BASE_DIR / "bot_manager.py"), "serve"]
    kwargs: dict[str, Any] = {
        "cwd": str(BASE_DIR),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(args, **kwargs)


def _ensure_server() -> None:
    try:
        _send({"cmd": "ping"}, timeout=1.0)
        return
    except Exception:
        pass
    _start_server_process()
    deadline = time.time() + STARTUP_TIMEOUT_SECONDS
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            _send({"cmd": "ping"}, timeout=1.0)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(f"bot manager did not start: {last_error}")


def _consolidate_training_data_async() -> None:
    """Consolidate training data in a background thread so the manager loop isn't blocked."""
    def task():
        try:
            indeed_script = BASE_DIR.parent / "prepare_training_data.py"
            if indeed_script.exists():
                subprocess.run([sys.executable, str(indeed_script)], cwd=str(BASE_DIR.parent))
            
            linkedin_script = BASE_DIR.parent / "prepare_training_data_linkedin_it.py"
            if linkedin_script.exists():
                subprocess.run([sys.executable, str(linkedin_script)], cwd=str(BASE_DIR.parent))
        except Exception:
            pass
    threading.Thread(target=task, daemon=True).start()


class BotManager:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.running: dict[str, dict[str, Any]] = {}
        self.desired_running: set[str] = set()
        self.pending_restarts: dict[str, float] = {}
        self.captcha_queue: deque[str] = deque()
        self.log_dir = BASE_DIR / "logs" / "bot_manager"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.python_exe = resolve_bot_python(BASE_DIR)
        self.serialize_captcha = _truthy(os.environ.get("BOT_MANAGER_SERIALIZE_CAPTCHA", "0"))

    def start_bot(self, bot_name: str) -> str:
        cfg = supervised_bot_config_by_name(bot_name, BASE_DIR)
        with self.lock:
            self.desired_running.add(bot_name)
            self.pending_restarts.pop(bot_name, None)
            if bot_name in self.running:
                return f"{bot_name} is already running."
            if bot_name in self.captcha_queue:
                return f"{bot_name} is already queued."
            
            from core.health_controller import is_bot_allowed_to_start
            allowed, reason = is_bot_allowed_to_start(bot_name)
            if not allowed:
                return f"Cannot start {bot_name}: {reason}"
            if (
                self.serialize_captcha
                and cfg["portal"] in ("indeed", "glassdoor")
                and self._captcha_running_locked()
            ):
                self.captcha_queue.append(bot_name)
                return f"{bot_name} queued. Another Indeed/Glassdoor bot is currently running."
            self._spawn_locked(cfg)
            return f"{bot_name} started."

    def stop_bot(self, bot_name: str) -> str:
        with self.lock:
            removed = False
            if bot_name in self.desired_running:
                self.desired_running.discard(bot_name)
                removed = True
            if bot_name in self.pending_restarts:
                self.pending_restarts.pop(bot_name, None)
                removed = True
            if bot_name in self.captcha_queue:
                self.captcha_queue = deque(x for x in self.captcha_queue if x != bot_name)
                removed = True
            info = self.running.get(bot_name)
            if info:
                self._terminate(info["process"])
                removed = True
            if removed:
                return f"{bot_name} stop requested."
            return f"{bot_name} is not running or queued."

    def stop_all(self) -> str:
        with self.lock:
            names = list(self.running)
            self.desired_running.clear()
            self.pending_restarts.clear()
            self.captcha_queue.clear()
            for name in names:
                self._terminate(self.running[name]["process"])
            return "Stop requested for all running bots."

    def status(self) -> dict[str, Any]:
        with self.lock:
            self._reap_locked()
            from core.health_controller import evaluate_bot_health
            bot_statuses = {}
            for name in _bot_names():
                try:
                    bot_statuses[name] = evaluate_bot_health(name)
                except Exception as e:
                    bot_statuses[name] = {
                        "bot_name": name,
                        "state": "UNKNOWN",
                        "pid": 0,
                        "last_seen": 0.0,
                        "restart_count": 0,
                        "last_crash_reason": str(e)
                    }
            return {
                "running": {
                    name: {
                        "pid": info["process"].pid,
                        "portal": info["cfg"]["portal"],
                        "started_at": info["started_at"],
                    }
                    for name, info in sorted(self.running.items())
                },
                "queued": list(self.captcha_queue),
                "desired_running": sorted(self.desired_running),
                "pending_restarts": {
                    name: max(0, int(restart_at - time.time()))
                    for name, restart_at in sorted(self.pending_restarts.items())
                },
                "enabled_bots": _bot_names(),
                "auto_restart": AUTO_RESTART,
                "serialize_captcha": self.serialize_captcha,
                "bot_statuses": bot_statuses,
            }

    def _captcha_running_locked(self) -> bool:
        return any(
            info["cfg"]["portal"] in ("indeed", "glassdoor")
            for info in self.running.values()
        )

    def _spawn_locked(self, cfg: dict[str, Any]) -> None:
        bot_name = cfg["bot_name"]
        env = build_subprocess_env(cfg, f"manager_{int(time.time())}")
        script_path = BASE_DIR / "bots" / cfg["script"]
        log_path = self.log_dir / f"{bot_name}.log"
        log_file = open(log_path, "a", encoding="utf-8")
        log_file.write(f"\n\n========== Manager start @ {time.strftime('%Y-%m-%d %H:%M:%S')} ==========\n")
        log_file.flush()
        kwargs: dict[str, Any] = {
            "cwd": str(BASE_DIR),
            "env": env,
            "stdout": log_file,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        proc = subprocess.Popen([str(self.python_exe), str(script_path)], **kwargs)
        from core.health_controller import record_bot_start
        try:
            record_bot_start(bot_name, proc.pid)
        except Exception:
            pass
        self.running[bot_name] = {
            "cfg": cfg,
            "process": proc,
            "log_file": log_file,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _terminate(self, proc: subprocess.Popen[Any]) -> None:
        try:
            if os.name == "nt":
                proc.terminate()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _reap_locked(self) -> None:
        exited: list[str] = []
        for name, info in self.running.items():
            proc = info["process"]
            if proc.poll() is not None:
                exited.append(name)
        from core.health_controller import record_bot_exit
        for name in exited:
            info = self.running.pop(name)
            exit_code = info["process"].returncode
            try:
                record_bot_exit(name, exit_code, f"Exited with code {exit_code}")
            except Exception:
                pass
            try:
                info["log_file"].close()
            except Exception:
                pass
            if AUTO_RESTART and name in self.desired_running:
                self.pending_restarts[name] = time.time() + RESTART_COOLDOWN_SECONDS
        if exited:
            self._start_next_captcha_locked()
            try:
                _consolidate_training_data_async()
            except Exception:
                pass

    def _start_next_captcha_locked(self) -> None:
        if self._captcha_running_locked() or not self.captcha_queue:
            return
        bot_name = self.captcha_queue.popleft()
        try:
            self._spawn_locked(supervised_bot_config_by_name(bot_name, BASE_DIR))
        except Exception:
            # Keep the manager alive; user can check logs/status and retry.
            pass

    def monitor_loop(self) -> None:
        while True:
            with self.lock:
                self._reap_locked()
                self._restart_due_bots_locked()
            time.sleep(2)

    def _restart_due_bots_locked(self) -> None:
        now = time.time()
        from core.health_controller import is_bot_allowed_to_start

        for bot_name, restart_at in list(self.pending_restarts.items()):
            if restart_at > now or bot_name not in self.desired_running:
                continue
            if bot_name in self.running:
                self.pending_restarts.pop(bot_name, None)
                continue
            cfg = supervised_bot_config_by_name(bot_name, BASE_DIR)
            if (
                self.serialize_captcha
                and cfg["portal"] in ("indeed", "glassdoor")
                and self._captcha_running_locked()
            ):
                continue
            allowed, _reason = is_bot_allowed_to_start(bot_name)
            if not allowed:
                continue
            try:
                self._spawn_locked(cfg)
                self.pending_restarts.pop(bot_name, None)
            except Exception:
                self.pending_restarts[bot_name] = now + RESTART_COOLDOWN_SECONDS


MANAGER = BotManager()


class Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline().decode("utf-8").strip()
        try:
            payload = json.loads(raw or "{}")
            response = handle_command(payload)
        except Exception as exc:
            response = {"ok": False, "message": str(exc)}
        self.wfile.write(_json_line(response))


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def handle_command(payload: dict[str, Any]) -> dict[str, Any]:
    cmd = payload.get("cmd")
    if cmd == "ping":
        return {"ok": True, "message": "bot manager is running"}
    if cmd == "start":
        bot_name = str(payload.get("bot", "")).strip()
        return {"ok": True, "message": MANAGER.start_bot(bot_name)}
    if cmd == "stop":
        bot_name = str(payload.get("bot", "")).strip()
        return {"ok": True, "message": MANAGER.stop_bot(bot_name)}
    if cmd == "stop-all":
        return {"ok": True, "message": MANAGER.stop_all()}
    if cmd == "status":
        return {"ok": True, "message": "status", "status": MANAGER.status()}
    return {"ok": False, "message": f"unknown command: {cmd!r}"}


def serve() -> int:
    threading.Thread(target=MANAGER.monitor_loop, daemon=True).start()
    with Server((HOST, PORT), Handler) as server:
        server.serve_forever()
    return 0


def print_response(response: dict[str, Any]) -> int:
    if "status" in response:
        status = response["status"]
        print("Bot Manager Status")
        print("==================")
        
        bot_statuses = status.get("bot_statuses") or {}
        if bot_statuses:
            # Header
            print(f"{'BOT':<18} {'STATE':<21} {'PID':<8} {'LAST SEEN':<12} {'RESTARTS':<10} {'LAST ERROR'}")
            print("-" * 88)
            now = time.time()
            for name, info in sorted(bot_statuses.items()):
                state = info.get("state", "UNKNOWN")
                pid = str(info.get("pid") or "-")
                
                last_seen_val = info.get("last_seen", 0.0)
                if last_seen_val == 0.0:
                    last_seen_str = "never"
                else:
                    diff = int(now - last_seen_val)
                    if diff < 0:
                        diff = 0
                    if diff < 60:
                        last_seen_str = f"{diff}s ago"
                    elif diff < 3600:
                        last_seen_str = f"{diff // 60}m ago"
                    elif diff < 86400:
                        last_seen_str = f"{diff // 3600}h ago"
                    else:
                        last_seen_str = f"{diff // 86400}d ago"
                        
                restarts = str(info.get("restart_count", 0))
                last_error = info.get("last_crash_reason") or "-"
                if last_error == "Exited with code 0":
                    last_error = "-"
                
                # Truncate last error to keep it readable
                if len(last_error) > 30:
                    last_error = last_error[:27] + "..."
                    
                print(f"{name:<18} {state:<21} {pid:<8} {last_seen_str:<12} {restarts:<10} {last_error}")
            print("")
            
        queued = status.get("queued") or []
        print("Queued: " + (", ".join(queued) if queued else "none"))
        desired = status.get("desired_running") or []
        print("Keep running: " + (", ".join(desired) if desired else "none"))
        print("Auto-restart: " + ("on" if status.get("auto_restart") else "off"))
        print("Indeed/Glassdoor serial mode: " + ("on" if status.get("serialize_captcha") else "off"))
        print("Enabled bots: " + ", ".join(status.get("enabled_bots") or []))
    else:
        print(response.get("message", ""))
    return 0 if response.get("ok") else 1


def client_command(args: argparse.Namespace) -> int:
    _ensure_server()
    payload: dict[str, Any] = {"cmd": args.command}
    if args.command in ("start", "stop"):
        payload["bot"] = args.bot
    response = _send(payload)
    return print_response(response)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Desktop shortcut bot manager")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("serve")
    start = sub.add_parser("start")
    start.add_argument("bot", choices=_bot_names())
    stop = sub.add_parser("stop")
    stop.add_argument("bot", choices=_bot_names())
    sub.add_parser("stop-all")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve()
    return client_command(args)


if __name__ == "__main__":
    raise SystemExit(main())

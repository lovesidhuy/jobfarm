"""Chrome orphan cleanup and LinkedIn daily-limit flag checks for the supervisor."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path
from typing import Optional, Union


def clear_chrome_singleton_locks(profile_dir: str) -> None:
    """Remove Singleton* files left by killed Chrome instances."""
    for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        try:
            p = Path(profile_dir) / lock
            if p.exists() or p.is_symlink():
                p.unlink()
        except Exception:
            pass


def kill_bot_chromes(
    profile_dir: str,
    cdp_port: Optional[Union[str, int]] = None,
    log_prefix: str = "[Supervisor]",
) -> int:
    """
    Kill chrome / chromedriver / uc_driver processes tied to a bot profile or CDP port.

    Prevents zombie browsers after crashes (multiple debugger attachments trigger
    Cloudflare debugger traps). Returns the number of processes killed.
    """
    if os.name != "nt":
        return _kill_bot_chromes_posix(profile_dir, cdp_port, log_prefix)
    return _kill_bot_chromes_windows(profile_dir, cdp_port, log_prefix)


def _kill_bot_chromes_posix(
    profile_dir: str,
    cdp_port: Optional[Union[str, int]],
    log_prefix: str,
) -> int:
    try:
        out = subprocess.run(["ps", "aux"], capture_output=True, text=True, timeout=10)
        killed = 0
        needle_profile = str(profile_dir).strip().lower()
        needle_port = f"--remote-debugging-port={cdp_port}" if cdp_port else None

        for line in out.stdout.splitlines():
            parts = line.split(None, 10)
            if len(parts) < 11:
                continue
            pid_str, cmd = parts[1], parts[10]
            cmd_lower = cmd.lower()

            is_chrome = any(
                n in cmd_lower for n in ("chrome", "chromium", "chromedriver", "uc_driver")
            )
            if not is_chrome:
                continue

            match_profile = needle_profile and (needle_profile in cmd_lower)
            match_port = needle_port and (needle_port in cmd)

            if match_profile or match_port:
                try:
                    pid = int(pid_str)
                    if pid == os.getpid():
                        continue
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
                except Exception:
                    pass

        if killed > 0:
            print(
                f"{log_prefix} Killed {killed} orphan Chrome/driver process(es) "
                f"for profile {profile_dir} (macOS/Linux)"
            )
        clear_chrome_singleton_locks(profile_dir)
        return killed
    except Exception as e:
        print(f"{log_prefix} kill_bot_chromes POSIX error: {e}")
        return 0


def _kill_bot_chromes_windows(
    profile_dir: str,
    cdp_port: Optional[Union[str, int]],
    log_prefix: str,
) -> int:
    needle_profile = str(profile_dir).replace("/", "\\").lower().strip()
    if len(needle_profile) < 20 or "\\profiles\\" not in needle_profile:
        if not cdp_port:
            print(
                f"{log_prefix} kill_bot_chromes: refusing unsafe needle "
                f"profile={needle_profile!r} port=None"
            )
            return 0
    needle_port = f"--remote-debugging-port={cdp_port}" if cdp_port else None
    try:
        ps = (
            "$names = 'chrome','chromedriver','uc_driver'; "
            "$targets = Get-CimInstance Win32_Process -Filter "
            "\"Name='chrome.exe' or Name='chromedriver.exe' or Name='uc_driver.exe'\" "
            "-ErrorAction SilentlyContinue; "
            f"$needle = '{needle_profile}'; "
            f"$port = '{needle_port or ''}'; "
            "$killed = 0; "
            "foreach ($p in $targets) { "
            "  if ($p.CommandLine -and ("
            "    ($p.CommandLine.ToLower().Contains($needle)) -or "
            "    ($port -and $p.CommandLine.Contains($port))"
            "  )) { "
            "    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue; $killed++ } catch {} "
            "  } "
            "} "
            "Write-Output $killed"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=15,
        )
        try:
            n = int((out.stdout or "0").strip().splitlines()[-1])
        except Exception:
            n = 0
        if n > 0:
            print(
                f"{log_prefix} Killed {n} orphan Chrome/driver process(es) "
                f"for profile {profile_dir}"
            )
        clear_chrome_singleton_locks(profile_dir)
        return n
    except Exception as e:
        print(f"{log_prefix} kill_bot_chromes error: {e}")
        return 0


def daily_limit_flag_present(bot_name: str, base_dir: Path) -> bool:
    """
    Return True when LinkedIn wrote ``daily_limit_reached.flag`` for this bot.

    Checks supervisor logs and master-folder locations (LinkedIn cwd is per-master).
    """
    direct = base_dir / "logs" / bot_name / "daily_limit_reached.flag"
    if direct.is_file():
        return True
    master_root = base_dir.parent / "master"
    if not master_root.is_dir():
        return False
    try:
        return any(master_root.glob(f"*/logs/{bot_name}/daily_limit_reached.flag")) or any(
            master_root.glob(f"*/*/logs/{bot_name}/daily_limit_reached.flag")
        )
    except Exception:
        return False


def kill_subprocess_tree(proc: subprocess.Popen) -> None:
    """Terminate a bot subprocess (process group on POSIX)."""
    try:
        if os.name == "nt":
            proc.terminate()
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass

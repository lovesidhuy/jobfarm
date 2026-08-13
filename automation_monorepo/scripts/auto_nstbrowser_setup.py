#!/usr/bin/env python3
"""
Fully automated Nstbrowser setup -- runs once, handles everything.

This script:
1. Checks if Nstbrowser Local API is reachable (on port 8848 by default)
2. If unreachable and on macOS, attempts to auto-start Nstbrowser.app
3. Lists existing profiles
4. Auto-creates missing profiles for all 6 bots with login startup URLs
5. Auto-assigns proxies from PROXY_URL env var or Infisical
6. Outputs ready-to-use environment variables
7. Can write directly to .env file

Usage:
    # Interactive mode (shows what it will do, asks before creating)
    python scripts/auto_nstbrowser_setup.py

    # Fully automated (no prompts, creates everything)
    python scripts/auto_nstbrowser_setup.py --auto

    # Write results to .env file
    python scripts/auto_nstbrowser_setup.py --auto --write-env
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import subprocess
import requests
import platform
from pathlib import Path

# Setup path to import core modules
here = Path(__file__).resolve().parent
repo_root = here.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from core.secret_manager import normalize_proxy_url
from core.browser.nst_proxy import nst_proxy_payload

BOT_NAMES = (
    "indeed_it",
    "indeed_general",
    "glassdoor_it",
    "glassdoor_general",
    "workopolis_it",
    "workopolis_general",
    "jobbank_it",
)

STARTUP_URL_BY_BOT = {
    # Preserve an existing authenticated session: Indeed's login route can
    # mask it, whereas the homepage reliably exposes logged-in chrome.
    "indeed_it": "https://ca.indeed.com/",
    "indeed_general": "https://ca.indeed.com/",
    "glassdoor_it": "https://www.glassdoor.ca/profile/login_input.htm",
    "glassdoor_general": "https://www.glassdoor.ca/profile/login_input.htm",
    "workopolis_it": "https://www.workopolis.com/",
    "workopolis_general": "https://www.workopolis.com/",
    "jobbank_it": "https://www.jobbank.gc.ca/dashboard",
}


def _load_env_file() -> dict[str, str]:
    """Load existing .env file if present."""
    env_vars: dict[str, str] = {}
    candidates = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent / ".env",
        Path(__file__).parent.parent.parent / ".env",
    ]
    
    for path in candidates:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env_vars[k.strip()] = v.strip()
            return env_vars
    return env_vars


def _resolve_proxy_url(cli_proxy: str | None) -> str | None:
    """Resolve proxy URL from CLI arg or fresh Infisical PROXY_URL."""
    from core.secret_manager import resolve_proxy_url
    url = resolve_proxy_url(cli_proxy)
    if url:
        if not cli_proxy:
            print("   (resolved PROXY_URL from Infisical/env)")
        return url
    return None


def _resolve_api_key(cli_key: str | None) -> str | None:
    """Resolve Nstbrowser API key from CLI arg, env, .env file, or Infisical."""
    if cli_key:
        return cli_key

    # Check env / .env file
    env_vars = _load_env_file()
    key = os.environ.get("NSTBROWSER_API_KEY") or env_vars.get("NSTBROWSER_API_KEY")
    if key:
        return key

    # Fall back to Infisical via secret_manager
    try:
        here = Path(__file__).resolve().parent
        repo_root = here.parent
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from core.secret_manager import get_secret
        key = get_secret("NSTBROWSER_API_KEY")
        if key:
            print("   (resolved NSTBROWSER_API_KEY from Infisical)")
            return key
    except Exception:
        pass
    return None


def _write_env_file(new_vars: dict[str, str]) -> bool:
    """Write or update .env file with new variables."""
    env_path = Path(__file__).parent.parent / ".env"
    
    # Read existing content
    existing_lines = []
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            existing_lines = f.readlines()
    
    # Parse existing vars
    existing_vars = {}
    for i, line in enumerate(existing_lines):
        line_stripped = line.strip()
        if line_stripped and not line_stripped.startswith("#") and "=" in line_stripped:
            k, v = line_stripped.split("=", 1)
            existing_vars[k.strip()] = (i, line)
    
    # Update or add new vars
    lines_to_add = []
    for key, value in new_vars.items():
        new_line = f"{key}={value}\n"
        if key in existing_vars:
            # Replace existing line
            idx, _ = existing_vars[key]
            existing_lines[idx] = new_line
        else:
            lines_to_add.append(new_line)
    
    # Append new vars at end
    if lines_to_add:
        if existing_lines and not existing_lines[-1].endswith("\n"):
            existing_lines.append("\n")
        existing_lines.append("# Nstbrowser Profile IDs (auto-generated)\n")
        existing_lines.extend(lines_to_add)
    
    # Write back
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(existing_lines)
        return True
    except Exception as e:
        print(f"[WARN] Failed to write .env file: {e}")
        return False


def _get_platform() -> str:
    sys_type = platform.system().lower()
    if "darwin" in sys_type:
        return "mac"
    if "windows" in sys_type:
        return "windows"
    return "linux"


class AutoNstBrowserSetup:
    def __init__(self, host: str = "127.0.0.1", port: int = 8848, api_key: str | None = None):
        self.host = host
        self.port = port
        self.api_url = f"http://{host}:{port}"
        self.api_key = api_key or ""
        if not self.api_key:
            raise ValueError("NSTBROWSER_API_KEY is required; no fallback credential is allowed.")
        self.headers = {
            "x-api-key": self.api_key,
            "Content-Type": "application/json"
        }
        self.created_profiles: dict[str, str] = {}
        self.existing_profiles: dict[str, str] = {}
    
    def check_connection(self) -> bool:
        """Check Nstbrowser Local API; try auto-start on macOS if offline."""
        print("[*] Checking Nstbrowser Local API...")
        for attempt in range(2):
            try:
                # Query profiles to verify connection and key validity
                r = requests.get(f"{self.api_url}/api/v2/profiles?pageNo=1&pageSize=1", headers=self.headers, timeout=5)
                if r.status_code in (200, 307):
                    print(f"   [OK] Nstbrowser Local API reachable at {self.api_url}")
                    return True
            except Exception:
                pass
            
            if attempt == 0 and _get_platform() == "mac" and os.path.exists("/Applications/Nstbrowser.app"):
                print("   [OFFLINE] Attempting to auto-start Nstbrowser.app...")
                try:
                    subprocess.Popen(["open", "/Applications/Nstbrowser.app"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    # Poll for up to 15 seconds
                    for _ in range(15):
                        time.sleep(1)
                        try:
                            r = requests.get(f"{self.api_url}/api/v2/profiles?pageNo=1&pageSize=1", headers=self.headers, timeout=2)
                            if r.status_code in (200, 307):
                                print(f"   [OK] Nstbrowser started and Local API is now reachable.")
                                return True
                        except Exception:
                            pass
                except Exception as e:
                    print(f"   [WARN] Failed to start Nstbrowser.app: {e}")
            
        print("   [FAIL] Nstbrowser Local API is not reachable")
        print(f"   Expected URL: {self.api_url}")
        print("   Please start Nstbrowser client/agent and verify Local API service.")
        return False
    
    def scan_profiles(self) -> dict[str, str]:
        """Scan Nstbrowser profiles to map bot name -> profile ID."""
        print("\n Scanning existing profiles...")
        url = f"{self.api_url}/api/v2/profiles?pageNo=1&pageSize=100"
        try:
            r = requests.get(url, headers=self.headers, timeout=10)
            if r.status_code not in (200, 307):
                print(f"   Error listing profiles: HTTP {r.status_code} - {r.text}")
                return {}
            
            data = r.json()
            # Nstbrowser API returns profiles under data.docs or data.list or data
            profile_list = []
            if isinstance(data, dict):
                inner_data = data.get("data")
                if isinstance(inner_data, dict):
                    profile_list = inner_data.get("docs") or inner_data.get("list") or []
                elif isinstance(inner_data, list):
                    profile_list = inner_data
            
            # Sort profiles by createdAt ascending so the older ones (original logged-in) are mapped first
            try:
                profile_list = sorted(profile_list, key=lambda x: x.get("createdAt", ""))
            except Exception:
                pass
            
            found = {}
            for p in profile_list:
                name = p.get("name")
                pid = p.get("profileId") or p.get("id") or p.get("_id")
                if name and pid:
                    # Keep the oldest one (first encountered in ascending sort)
                    if name not in found:
                        found[name] = pid
            
            # Map Nst_ bot profiles
            for bot in BOT_NAMES:
                expected_name = f"Nst_{bot}"
                if expected_name in found:
                    self.existing_profiles[bot] = found[expected_name]
                    print(f"   OK {bot}: profileId={found[expected_name]}")
                else:
                    print(f"   MISSING {bot}: NOT FOUND")
            
            return found
        except Exception as e:
            print(f"   Failed to scan profiles: {e}")
            return {}
            
    def create_missing_profiles(self, proxy_url: str | None = None, auto: bool = False) -> bool:
        """Create profiles that are missing.

        Blocked by default via ``NSTBROWSER_FORBID_CREATE`` (safe near quota).
        Opt out only with ``NSTBROWSER_FORBID_CREATE=0``.
        """
        from core.browser.nst_profile_safety import nstbrowser_forbid_create, refuse_profile_creation

        missing = [bot for bot in BOT_NAMES if bot not in self.existing_profiles]
        if not missing:
            print("\nAll profiles already exist!")
            return True

        if nstbrowser_forbid_create():
            print(
                f"\nREFUSED: would create {len(missing)} profiles ({', '.join(missing)}) but "
                "NSTBROWSER_FORBID_CREATE blocks creation (default on — quota critical). "
                "Reuse existing NSTBROWSER_PROFILE_ID_* values. "
                "Only set NSTBROWSER_FORBID_CREATE=0 if you intentionally accept burning quota."
            )
            refuse_profile_creation(context=f"auto_nstbrowser_setup missing={missing}")
                
        print(f"\nCreating {len(missing)} missing profiles...")
        if not auto:
            response = input("   Continue? [Y/n]: ").strip().lower()
            if response and response not in ("y", "yes"):
                print("   Aborted.")
                return False
                
        # Native Chrome 126 User-Agent strings matching target platforms
        ua_map = {
            "mac": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "windows": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "linux": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        }
        target_platform = _get_platform()
        user_agent = ua_map.get(target_platform, ua_map["mac"])

        for bot in missing:
            profile_name = f"Nst_{bot}"
            startup_url = STARTUP_URL_BY_BOT.get(bot, "https://example.com")
            print(f"   Creating {profile_name}...", end=" ")
            try:
                url = f"{self.api_url}/api/v2/profiles"
                payload = {
                    "name": profile_name,
                    "platform": target_platform,
                    "kernel": "chromium",
                    "kernelVersion": "126",
                    "groupName": "Default",
                    "startupUrls": [startup_url],
                    "fingerprint": {
                        "restoreLastSession": True,
                        "doNotTrack": True,
                        "userAgent": user_agent,
                        "chromeVersion": "126",
                        "navigator": {
                            "webdriver": "false",
                            "languages": ["en-US", "en"]
                        }
                    }
                }
                if proxy_url:
                    payload["proxyConfig"] = nst_proxy_payload(proxy_url)
                    
                r = requests.post(url, json=payload, headers=self.headers, timeout=15)
                if r.status_code in (200, 201):
                    resp_data = r.json()
                    if resp_data.get("code") == 200:
                        inner = resp_data.get("data", {})
                        pid = inner.get("profileId") or inner.get("id")
                        if pid:
                            self.created_profiles[bot] = pid
                            print(f"OK (profileId={pid})")
                        else:
                            print(f"FAILED: No ID returned: {resp_data}")
                    else:
                        print(f"FAILED: {resp_data.get('msg')}")
                else:
                    print(f"FAILED: HTTP {r.status_code} - {r.text}")
            except Exception as e:
                print(f"ERROR: {e}")
            time.sleep(0.5)
            
        return len(self.created_profiles) > 0
        
    def update_existing_profiles_proxy(self, proxy_url: str | None) -> None:
        """Update proxy settings for all existing Nstbrowser profiles."""
        if not proxy_url:
            print("\n[Skip] No proxy URL configured, skipping proxy updates for existing profiles.")
            return

        print(f"\nSyncing proxy configuration for existing profiles to: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")
        
        # Ensure we have scanned the profiles
        if not self.existing_profiles:
            self.scan_profiles()

        for bot, pid in self.existing_profiles.items():
            print(f"   Updating proxy for Nst_{bot} ({pid})...", end=" ")
            try:
                url = f"{self.api_url}/api/v2/profiles/{pid}/proxy"
                r = requests.put(url, json=nst_proxy_payload(proxy_url), headers=self.headers, timeout=15)
                if r.status_code in (200, 201):
                    resp_data = r.json()
                    if resp_data.get("code") == 200:
                        print("OK")
                    else:
                        print(f"FAILED: {resp_data.get('msg')}")
                else:
                    print(f"FAILED: HTTP {r.status_code} - {r.text}")
            except Exception as e:
                print(f"ERROR: {e}")

    def get_all_profile_ids(self) -> dict[str, str]:
        res = dict(self.existing_profiles)
        res.update(self.created_profiles)
        return res
        
    def print_summary(self):
        all_profiles = self.get_all_profile_ids()
        print("\n" + "="*60)
        print("NSTBROWSER SETUP COMPLETE")
        print("="*60)
        
        if self.created_profiles:
            print(f"\nCreated {len(self.created_profiles)} new profiles:")
            for bot, pid in self.created_profiles.items():
                print(f"   {bot}: {pid}")
                
        print(f"\nTotal profiles ready: {len(all_profiles)}/{len(BOT_NAMES)}")
        print("\nEnvironment variables:")
        print("-"*60)
        print("BROWSER_VENDOR=nstbrowser")
        for bot in BOT_NAMES:
            pid = all_profiles.get(bot)
            if pid:
                print(f"NSTBROWSER_PROFILE_ID_{bot.upper()}={pid}")
        print("-"*60)
        
        print("\nQuick start command example:")
        print(f"   BROWSER_VENDOR=nstbrowser NSTBROWSER_PROFILE_ID={all_profiles.get('indeed_it')} python bots/indeed_it.py")
        return all_profiles


def main():
    parser = argparse.ArgumentParser(description="Auto-setup Nstbrowser profiles")
    parser.add_argument("--auto", action="store_true", help="Run without prompts")
    parser.add_argument("--write-env", action="store_true", help="Write profile IDs to .env file")
    parser.add_argument("--update-proxy", action="store_true", help="Update proxy for existing profiles")
    parser.add_argument("--proxy", type=str, help="Proxy URL")
    parser.add_argument("--api-key", type=str, help="Nstbrowser API Key")
    parser.add_argument("--host", type=str, default="127.0.0.1", help="API Host")
    parser.add_argument("--port", type=int, default=8848, help="API Port")
    args = parser.parse_args()
    
    print("Nstbrowser Auto-Setup for Job Automation Bots\n")
    
    api_key = _resolve_api_key(args.api_key)
    proxy_url = _resolve_proxy_url(args.proxy)
    
    setup = AutoNstBrowserSetup(host=args.host, port=args.port, api_key=api_key)
    
    if not setup.check_connection():
        print("\nFAIL: Cannot proceed without Nstbrowser Local API connection.")
        sys.exit(1)
        
    setup.scan_profiles()
    
    if proxy_url:
        print(f"\nUsing proxy config: {proxy_url.split('@')[-1] if '@' in proxy_url else proxy_url}")
        if args.update_proxy:
            setup.update_existing_profiles_proxy(proxy_url)
    else:
        print("\nNo proxy configured. Profiles will run without proxy.")
        
    setup.create_missing_profiles(proxy_url=proxy_url, auto=args.auto)
    
    all_profiles = setup.print_summary()
    
    if args.write_env:
        print("\nWriting to .env file...")
        env_vars = {f"NSTBROWSER_PROFILE_ID_{bot.upper()}": str(pid) for bot, pid in all_profiles.items()}
        env_vars["BROWSER_VENDOR"] = "nstbrowser"
        if _write_env_file(env_vars):
            print("   Saved to .env")
        else:
            print("   Failed to write .env")
            sys.exit(1)


if __name__ == "__main__":
    main()

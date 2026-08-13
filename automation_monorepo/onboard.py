#!/usr/bin/env python3
"""Interactive onboarding and configuration assistant for JobFarm.

Guides new users through:
  1. Environment & Profile initialization (.env & candidate PII)
  2. Multi-model LLM connectivity check (Ollama, DeepSeek, OpenAI, Groq, Gemini, OpenRouter)
  3. Database & Queue sanity check (Local Docker MongoDB)
  4. Proxy & CAPTCHA solver verification (Direct workstation or leased residential)
  5. Interactive portal authentication (Indeed, LinkedIn, Glassdoor, Workopolis, Job Bank)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = (
    Path(__file__).resolve().parent.parent
    if Path(__file__).resolve().parent.name in ("scripts", "automation_monorepo")
    else Path(__file__).resolve().parent
)
MONOREPO_ROOT = REPO_ROOT / "automation_monorepo"

for p in (REPO_ROOT, MONOREPO_ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def load_env_file():
    env_path = MONOREPO_ROOT / ".env"
    if not env_path.is_file():
        env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    val = v.strip()
                    if (val.startswith('"') and val.endswith('"')) or (
                        val.startswith("'") and val.endswith("'")
                    ):
                        val = val[1:-1]
                    os.environ.setdefault(k.strip(), val)


def print_banner(title: str) -> None:
    width = 70
    print("\n" + "═" * width)
    print(f"  {title.center(width - 4)}")
    print("═" * width)


def ensure_env_file() -> bool:
    """Ensure .env exists; copy from .env.example if missing."""
    env_file = REPO_ROOT / ".env"
    example_file = REPO_ROOT / ".env.example"
    if not env_file.is_file():
        if example_file.is_file():
            shutil.copy(example_file, env_file)
            print("  [i] Created default .env from .env.example")
            return True
        else:
            print("  [!] .env.example not found.")
            return False
    return True


def show_status() -> dict[str, bool]:
    """Display portal authentication status table."""
    try:
        from jobbots.core.session_registry import load_session_registry
        from jobbots.core.supervised_bots import supervised_bot_configs
    except ImportError:
        print("Error loading core modules. Ensure PYTHONPATH includes repo root.")
        return {}

    reg = load_session_registry()
    configs = supervised_bot_configs(MONOREPO_ROOT)

    print("\n╔════════════════════════════════════════════════════════════════════╗")
    print("║                     PORTAL SESSION STATUS                          ║")
    print("╠════════════════════════════════════════════════════════════════════╣")

    status_map: dict[str, bool] = {}
    for cfg in configs:
        name = cfg["bot_name"]
        portal = cfg["portal"]
        entry = reg.get(name, {})
        ok = entry.get("session_ok", False)
        status_map[name] = ok

        icon = "✓" if ok else "✗"
        color_mark = "READY" if ok else "NEEDS LOGIN"
        ts = entry.get("updated_at", "never")[:19] if entry else "never"
        print(f"║  {icon} {name:<22} [{portal:<10}] {color_mark:<12} ({ts}) ║")

    print("╚════════════════════════════════════════════════════════════════════╝")

    ready = sum(1 for v in status_map.values() if v)
    total = len(status_map)
    print(f"\n  Status: {ready}/{total} bots authenticated.\n")
    return status_map


def test_database() -> bool:
    """Check MongoDB connection and queue health."""
    print_banner("TESTING MONGODB DATABASE")
    mongo_uri = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
    db_name = os.getenv("JOBBOTS_MONGO_DATABASE", "jobbots")
    print(f"  Target URI:      {mongo_uri}")
    print(f"  Target Database: {db_name}")

    try:
        from pymongo import MongoClient

        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=3000)
        client.admin.command("ping")
        db = client[db_name]
        queue_count = db.job_applications.count_documents({})
        applied_count = db.job_applications.count_documents({"status": "applied"})
        print(f"  ✓ MongoDB reachable! Total queued jobs: {queue_count} (Applied: {applied_count})")
        return True
    except Exception as e:
        print(f"  [✗] MongoDB is not reachable: {e}")
        print("      To start local MongoDB with Docker:")
        print("      docker-compose -f docker-compose.local.yml up -d mongodb")
        return False


def test_llm_connection() -> bool:
    """Test configured LLM gateway."""
    print_banner("TESTING LLM GATEWAY")
    try:
        from jobbots.core.llm_backend.ai.llm_gateway import resolve_llm_gateway
        from jobbots.core.llm_backend.ai.openaiConnections import ai_answer_question

        gw = resolve_llm_gateway()
        print(f"  Resolved Provider: {gw.provider}")
        print(f"  Base URL:          {gw.base_url}")
        print(f"  Model:             {gw.model}")

        if gw.provider == "ollama" or "localhost" in gw.base_url or "127.0.0.1" in gw.base_url:
            from jobbots.core.llm_backend.ai.ollamaConnections import (
                ollama_answer_question,
                ollama_is_available,
            )

            avail = ollama_is_available(gw.base_url)
            print(f"  Local Ollama Server Reachable: {avail}")
            if not avail:
                print("  [!] Ollama server is not running on " + gw.base_url)
                print("      Start Ollama with: ollama serve (and pull model: ollama run llama3.2)")
                return False
            ans = ollama_answer_question("Are you legally authorized to work in Canada?", model=gw.model)
            print(f"  Sample Question: 'Are you legally authorized to work in Canada?'")
            print(f"  LLM Response:    {ans}")
            print("  ✓ LLM Connection verified!")
            return True
        else:
            print("  Testing cloud completion via OpenAI-compatible endpoint...")
            ans = ai_answer_question("Are you legally authorized to work in Canada?", "Yes")
            print(f"  Sample Question: 'Are you legally authorized to work in Canada?'")
            print(f"  LLM Response:    {ans}")
            print("  ✓ Cloud LLM Gateway verified!")
            return True
    except Exception as e:
        print(f"  [✗] LLM gateway verification failed: {e}")
        return False


def test_proxy_connection() -> bool:
    """Verify proxy connectivity."""
    print_banner("TESTING NETWORK & PROXY CONNECTION")
    user = os.getenv("WEBSHARE_PROXY_USERNAME")
    pwd = os.getenv("WEBSHARE_PROXY_PASSWORD")
    host = os.getenv("WEBSHARE_PROXY_HOST", "p.webshare.io")
    port = os.getenv("WEBSHARE_PROXY_PORT", "80")

    if not user or not pwd:
        print("  [i] Running in Direct Workstation Mode (no rotational proxy configured).")
        try:
            import urllib.request

            resp = urllib.request.urlopen("https://api.ipify.org?format=json", timeout=6)
            data = json.loads(resp.read().decode("utf-8"))
            print(f"  ✓ Direct Egress IP: {data.get('ip')}")
        except Exception:
            pass
        return True

    print(f"  Testing proxy tunnel via {host}:{port}...")
    try:
        import urllib.request

        proxy_handler = urllib.request.ProxyHandler({
            "http": f"http://{user}:{pwd}@{host}:{port}",
            "https": f"http://{user}:{pwd}@{host}:{port}",
        })
        opener = urllib.request.build_opener(proxy_handler)
        resp = opener.open("http://httpbin.org/ip", timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
        print(f"  ✓ Proxy working! Egress IP: {data.get('origin')}")
        return True
    except Exception as e:
        print(f"  [✗] Proxy connection failed: {e}")
        return False


def launch_portal_login(portal: str | None = None) -> int:
    """Launch visible browser session for authenticating portal sessions."""
    print_banner("PORTAL AUTHENTICATION LOGIN WIZARD")
    print("  Opening visible Chrome session to save authenticated cookies.\n")
    login_script = MONOREPO_ROOT / "live_e2e_logins.py"
    if not login_script.is_file():
        print(f"  [✗] Login script not found at {login_script}")
        return 1

    cmd = [sys.executable, str(login_script)]
    if portal:
        cmd += ["--portal", portal]

    proc = subprocess.run(cmd, cwd=str(MONOREPO_ROOT))
    return proc.returncode


def init_candidate_profile() -> None:
    """Interactive CLI wizard to configure candidate personal info."""
    print_banner("CANDIDATE PROFILE INITIALIZATION")
    print("  Configure candidate information for autofill & screening questions.\n")

    first_name = input("  First name [Jane]: ").strip() or "Jane"
    last_name = input("  Last name [Doe]: ").strip() or "Doe"
    email = input("  Email address [user@example.com]: ").strip() or "user@example.com"
    phone = input("  Phone number [+1-555-0199]: ").strip() or "+1-555-0199"
    city = input("  City [Vancouver]: ").strip() or "Vancouver"
    state = input("  Province / State [BC]: ").strip() or "BC"
    country = input("  Country [Canada]: ").strip() or "Canada"
    role_type = input("  Job Profile (it / general) [it]: ").strip().lower() or "it"

    env_path = REPO_ROOT / ".env"
    ensure_env_file()

    overrides = {
        "CANDIDATE_FIRST_NAME": first_name,
        "CANDIDATE_LAST_NAME": last_name,
        "CANDIDATE_EMAIL": email,
        "CANDIDATE_PHONE": phone,
        "CANDIDATE_CITY": city,
        "CANDIDATE_STATE": state,
        "CANDIDATE_COUNTRY": country,
        "JOB_PROFILE": role_type,
    }

    # Append or update in .env
    lines = []
    if env_path.is_file():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    new_lines = []
    existing_keys = set()
    for line in lines:
        if "=" in line and not line.strip().startswith("#"):
            k, _ = line.split("=", 1)
            k = k.strip()
            if k in overrides:
                new_lines.append(f"{k}={overrides[k]}")
                existing_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in overrides.items():
        if k not in existing_keys:
            new_lines.append(f"{k}={v}")

    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    print(f"\n  ✓ Profile configured in {env_path.name}!")


def run_full_check():
    print_banner("JOBFARM ONBOARDING & PRE-FLIGHT AUDIT")
    print("  Open-Source Job Discovery & Application Automation Engine\n")

    ensure_env_file()
    load_env_file()

    # 1. Environment & Health
    try:
        from jobbots.app.pipeline import doctor_report

        report = doctor_report(quick=True)
        print("  System Health:")
        for k, v in report.get("checks", {}).items():
            ok = v.get("ok", True) if isinstance(v, dict) else bool(v)
            mark = "✓" if ok else "✗"
            print(f"    {mark} {k:<18} {'OK' if ok else 'CHECK REQUIRED'}")
    except Exception as e:
        print(f"  [i] Health check note: {e}")

    # 2. Database
    test_database()

    # 3. LLM Gateway
    test_llm_connection()

    # 4. Proxy / Network
    test_proxy_connection()

    # 5. Portal Sessions
    show_status()

    # Next steps summary
    print_banner("NEXT STEPS")
    print("  1. Authenticate a portal session:")
    print("     python scripts/onboard.py --login indeed")
    print("  2. Run Discovery phase:")
    print("     python -m jobbots.app.cli discover --once")
    print("  3. Run Application phase:")
    print("     python -m jobbots.app.cli apply --once")
    print("  4. Start Autonomous Supervisor:")
    print("     python automation_monorepo/supervisor.py\n")


def main():
    parser = argparse.ArgumentParser(description="JobFarm Interactive Onboarding Assistant")
    parser.add_argument("--check", action="store_true", help="Run full diagnostic pre-flight checks")
    parser.add_argument("--status", action="store_true", help="Show portal session status table and exit")
    parser.add_argument("--test-llm", action="store_true", help="Test configured LLM connection and exit")
    parser.add_argument("--test-db", action="store_true", help="Test MongoDB connection and exit")
    parser.add_argument("--test-proxy", action="store_true", help="Test proxy connection and exit")
    parser.add_argument("--init-profile", action="store_true", help="Interactive candidate profile setup")
    parser.add_argument("--login", type=str, nargs="?", const="all", help="Launch visible browser login for portal (e.g. indeed, linkedin)")
    args = parser.parse_args()

    load_env_file()

    if args.init_profile:
        init_candidate_profile()
        return

    if args.status:
        show_status()
        return

    if args.test_llm:
        test_llm_connection()
        return

    if args.test_db:
        test_database()
        return

    if args.test_proxy:
        test_proxy_connection()
        return

    if args.login:
        target_portal = None if args.login == "all" else args.login
        launch_portal_login(target_portal)
        return

    run_full_check()


if __name__ == "__main__":
    main()

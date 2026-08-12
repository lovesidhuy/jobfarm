import os
import sys
import time
from pathlib import Path

base_dir = Path(__file__).resolve().parent
sys.path.append(str(base_dir))


def load_env():
    env_path = base_dir / ".env"
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    if "=" in line:
                        k, v = line.split("=", 1)
                        os.environ[k] = v.strip('"\'')


load_env()


def test_login():
    from core.browser.open_chrome import createBrowserSession
    from core.portals.glassdoor import _wait_for_manual_login as glassdoor_login
    from core.portals.indeed import _wait_for_manual_login as indeed_login
    from core.supervised_bots import apply_bot_runtime_env_overwrite, supervised_bot_configs
    from core.supervisor_runtime import apply_imap_env_for_profile

    for cfg in supervised_bot_configs(base_dir):
        print(f"\n==========================================")
        print(f"--- Testing Login for {cfg['bot_name']} ---")
        print(f"==========================================")

        apply_bot_runtime_env_overwrite(cfg)
        apply_imap_env_for_profile(os.environ, cfg["profile"])

        sb = page = context = browser = pw = None
        try:
            sb, page, context, browser, pw = createBrowserSession(bot_name=cfg["bot_name"])
            if cfg["portal"] == "indeed":
                success = indeed_login(page, sb, timeout_minutes=2)
            elif cfg["portal"] == "glassdoor":
                success = glassdoor_login(page, sb, timeout_minutes=2)
            else:
                print(f">>> Skip {cfg['bot_name']}: use live_e2e_logins.py for LinkedIn.")
                continue

            print(f">>> Final Login Status for {cfg['bot_name']}: {'SUCCESS' if success else 'FAILED'}\n")
        except Exception as e:
            print(f">>> Final Login Status for {cfg['bot_name']}: ERROR ({e})\n")
        finally:
            if page:
                page.close()
            if browser:
                browser.close()
            if pw:
                pw.stop()
            try:
                if sb:
                    sb.quit()
            except Exception:
                pass
            time.sleep(2)


if __name__ == "__main__":
    test_login()

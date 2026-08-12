import os
import sys

def smoke_test(bot_script, bot_name, job_profile):
    print(f"--- Smoke Testing {bot_name} (Profile: {job_profile}) ---")
    os.environ["BOT_NAME"] = bot_name
    os.environ["JOB_PROFILE"] = job_profile
    os.environ["CHROME_PROFILE_DIR"] = f"data/browser_profiles/{bot_name}"

    try:
        # Import config directly to test routing
        import importlib
        if 'config.questions' in sys.modules:
            del sys.modules['config.questions']
            del sys.modules['config']
        
        import config.questions as q
        print(f"Loaded questions.py for {job_profile}. desired_salary={q.desired_salary}")

        # Check if bot script compiles
        bot_path = os.path.join("bots", bot_script)
        with open(bot_path, "r") as f:
            compile(f.read(), bot_path, "exec")
        print(f"Successfully compiled {bot_script}")

    except Exception as e:
        print(f"FAILED: {e}")
        sys.exit(1)

smoke_test("indeed_it.py", "indeed_it", "IT")
smoke_test("indeed_general.py", "indeed_general", "GENERAL")
smoke_test("glassdoor_it.py", "glassdoor_it", "IT")
smoke_test("glassdoor_general.py", "glassdoor_general", "GENERAL")
smoke_test("workopolis_it.py", "workopolis_it", "IT")
smoke_test("workopolis_general.py", "workopolis_general", "GENERAL")

print("\nSMOKE TEST PASSED: All configs load properly and scripts compile.")

import _smoke_supervisor  # noqa: E402 — run after cwd smoke tests

raise SystemExit(_smoke_supervisor.main())

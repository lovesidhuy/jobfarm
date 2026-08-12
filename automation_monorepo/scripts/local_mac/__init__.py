"""Mac-local Docker worker scripts.

Login profiles    → ``login_profiles.py``   (Mac host, headed Chrome, one-time)
Re-login helper   → ``relogin_profiles.py`` (Mac host, re-login expired sessions)
Worker entrypoint → ``worker_entrypoint.py`` (inside Docker, headless bot loop)
"""
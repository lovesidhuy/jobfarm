import os
import sys
from pathlib import Path


def main():
    _root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(_root))

    from core.supervisor_runtime import merge_dotenv_into_env
    merge_dotenv_into_env(os.environ, _root / ".env")

    from core.supervised_bots import ensure_bot_runtime_defaults
    ensure_bot_runtime_defaults("glassdoor_general", _root)

    # Init before module eviction; Sentry's global excepthook survives it.
    from core.sentry_init import init_sentry
    init_sentry("bot")

    # Evict packages that would shadow the local package imports in the target master folder
    conflicting = ["modules", "core", "config", "runAiBot"]
    for m in list(sys.modules):
        if any(m == c or m.startswith(c + ".") for c in conflicting):
            sys.modules.pop(m, None)

    target_dir = _root.parent / "master" / "gen_indeed" / "Auto_indeed"
    os.chdir(target_dir)
    sys.path.insert(0, str(target_dir))

    # Set command line arguments for the platform choice
    sys.argv = [sys.argv[0], "--platform", "glassdoor"]

    import runAiBot
    runAiBot.main()


if __name__ == "__main__":
    main()

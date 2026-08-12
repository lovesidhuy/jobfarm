#!/usr/bin/env python3
"""Runtime-import proof for Wave A bots (indeed_it, indeed_general, glassdoor_it).

Simulates each production entrypoint's sys.path / modules.__path__ bridge and
prints the exact files loaded for Wave A code paths.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def _purge_modules(prefixes: tuple[str, ...]) -> None:
    for name in list(sys.modules):
        if any(name == p or name.startswith(p + ".") for p in prefixes):
            sys.modules.pop(name, None)


def _prove(bot_name: str, master_rel: str, platform: str) -> dict:
    entry = ROOT / "bots" / f"{bot_name}.py"
    master = REPO / master_rel
    shared = ROOT.parent / "jobbots" / "core" / "shared_modules"

    # Match bots/*.py: evict conflicting packages, chdir conceptually via path.
    _purge_modules(("modules", "core", "config", "scripts"))
    # Keep monorepo on path for core.* / scripts.*
    sys.path = [p for p in sys.path if Path(p).resolve() not in {master.resolve(), shared.resolve()}]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(master) not in sys.path:
        sys.path.insert(0, str(master))

    import modules  # from master tree

    shared_str = str(shared)
    if shared_str not in list(modules.__path__):
        modules.__path__.append(shared_str)

    # Production Indeed path: modules.indeed_bot → modules.indeed.*
    indeed_bot = None
    indeed_apply = None
    indeed_bot_err = None
    indeed_apply_err = None
    try:
        indeed_bot = importlib.import_module("modules.indeed_bot")
    except Exception as exc:
        indeed_bot_err = f"{type(exc).__name__}: {exc}"
    try:
        indeed_apply = importlib.import_module("modules.indeed.apply")
    except Exception as exc:
        indeed_apply_err = f"{type(exc).__name__}: {exc}"

    qr = importlib.import_module("modules.queue_result")
    lp = importlib.import_module("core.discovery.classification.location_policy")
    aw = importlib.import_module("scripts.application_worker")

    apply_file = getattr(indeed_apply, "__file__", None) if indeed_apply else None
    apply_text = Path(apply_file).read_text(encoding="utf-8") if apply_file else ""
    # Prefer the canonical shared apply.py path for the verify-branch marker.
    shared_apply = shared / "indeed" / "apply.py"

    glassdoor_apply = master / "modules" / "glassdoor" / "apply.py"

    result = {
        "bot_name": bot_name,
        "platform_arg": platform,
        "production_entrypoint": str(entry),
        "entrypoint_exists": entry.is_file(),
        "master_runtime_tree": str(master),
        "master_exists": master.is_dir(),
        "shared_modules_path": shared_str,
        "modules_path_includes_shared": shared_str in list(modules.__path__),
        "imported": {
            "modules.indeed_bot": getattr(indeed_bot, "__file__", indeed_bot_err),
            "modules.indeed.apply": apply_file or indeed_apply_err,
            "modules.queue_result": getattr(qr, "__file__", None),
            "core.discovery.classification.location_policy": getattr(lp, "__file__", None),
            "scripts.application_worker": getattr(aw, "__file__", None),
            "glassdoor.local_apply": str(glassdoor_apply) if glassdoor_apply.is_file() else None,
        },
        "wave_a_markers": {
            "location_policy_has_global_intent_doc": "Global location intent" in Path(lp.__file__).read_text(encoding="utf-8"),
            "queue_result_has_resolve_direct": hasattr(qr, "resolve_direct_queue_result"),
            "worker_has_build_dispatch_env": hasattr(aw, "build_dispatch_env"),
            "shared_apply_has_verify_branch": "JOB_QUEUE_VERIFY_APPLY_TYPE" in shared_apply.read_text(encoding="utf-8"),
            # indeed/__init__.py merges submodule dicts, so importlib may report loop.py
            # for modules.indeed.apply; the authoritative source file is shared apply.py,
            # and the live function is proven via indeed_bot._apply_to_single_job.
            "imported_apply_is_under_shared_modules": bool(
                apply_file and "shared_modules" in str(apply_file)
            ),
            "live_apply_fn_has_verify_branch": False,
            "glassdoor_local_has_bookmark_flags": (
                "JOB_QUEUE_BOOKMARK_FIRST" in glassdoor_apply.read_text(encoding="utf-8")
                if glassdoor_apply.is_file() else False
            ),
        },
    }
    # Prove the live function body (post-merge) contains the Wave A verify branch.
    if indeed_bot is not None and hasattr(indeed_bot, "_apply_to_single_job"):
        import inspect
        src = inspect.getsource(indeed_bot._apply_to_single_job)
        result["wave_a_markers"]["live_apply_fn_has_verify_branch"] = (
            "JOB_QUEUE_VERIFY_APPLY_TYPE" in src
        )
        result["imported"]["live_apply_fn_file"] = inspect.getfile(indeed_bot._apply_to_single_job)
    return result


def main() -> int:
    proofs = [
        _prove("indeed_it", "master/it_indeed cwgeopy/Auto_indeed", "indeed"),
        _prove("indeed_general", "master/gen_indeed/Auto_indeed", "indeed"),
        _prove("glassdoor_it", "master/it_indeed cwgeopy/Auto_indeed", "glassdoor"),
    ]
    ok = all(
        p["entrypoint_exists"]
        and p["master_exists"]
        and p["wave_a_markers"]["shared_apply_has_verify_branch"]
        and p["wave_a_markers"]["live_apply_fn_has_verify_branch"]
        and "shared_modules" in str(p["imported"].get("live_apply_fn_file") or "")
        for p in proofs
    )
    print(json.dumps({"ok": ok, "proofs": proofs}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

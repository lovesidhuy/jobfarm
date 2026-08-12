"""jobbots — one entry point for the whole job-application system.

Commands
--------
doctor     Environment health: profiles, QA banks, resumes, secrets, DB, sessions
onboard    Interactive first-time portal login setup
discover   Discovery phase (scraping + screening into the application queue)
apply      Application phase (queue consumption by portal bots)
run        Full autonomous daily cycle (preflight → discover → apply → report → backup)
status     Queue counts + portal session registry
export     Run one of the existing export scripts
qa check   Replay the golden Q&A fixtures (shadow-mode parity harness)
portals    List portal adapters + profile enablement + supervised bots
bot        Run one supervised bot by name (delegates to bots/<name>.py)
shadow qa  Fixtures + adapter-vs-direct gate wiring diff (+ optional live sample)
infra      Infra module map + structural audit (--audit)
audit      Code duplication + retirement audit (read-only)
farm-check Productivity contract: proxies, slot 1, topology, ladder, portals


Every command delegates to the existing, production-proven entry points —
no behavior changes. A user never needs to know which internal bot, folder,
python path, or worker script to run.
"""
from __future__ import annotations

import argparse
import json
import sys

EXPORT_SCRIPTS = {
    "queue": "export_approved_queue.py",
    "failures": "export_queue_failures.py",
    "mongo-history": "export_mongo_history_to_csv.py",
    "linkedin-indeed": "export_linkedin_indeed_queue.py",
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="jobbots",
        description="Unified CLI for the job-application automation system.",
    )
    p.add_argument("--version", action="store_true", help="print version and exit")
    sub = p.add_subparsers(dest="command")

    d = sub.add_parser("doctor", help="environment health checks")
    d.add_argument("--quick", action="store_true", help="skip network checks (Mongo, NST API)")
    d.add_argument("--json", action="store_true", help="machine-readable output")

    o = sub.add_parser("onboard", help="interactive first-time login setup")
    o.add_argument("args", nargs=argparse.REMAINDER, help="passed through to onboard.py")

    di = sub.add_parser("discover", help="run the discovery phase")
    di.add_argument("--once", action="store_true", help="single cycle (no supervisor retry loop)")

    a = sub.add_parser("apply", help="run the application phase")
    a.add_argument("--workers", type=int, default=1, help="parallel queue consumers")
    a.add_argument("--once", action="store_true", help="single cycle")

    r = sub.add_parser("run", help="full autonomous daily cycle")
    r.add_argument("--workers", type=int, default=1)
    r.add_argument("--once", action="store_true")

    s = sub.add_parser("status", help="queue counts + session registry")
    s.add_argument("--json", action="store_true")

    e = sub.add_parser("export", help="run an export script")
    e.add_argument("kind", choices=sorted(EXPORT_SCRIPTS), help="what to export")
    e.add_argument("args", nargs=argparse.REMAINDER, help="passed through to the script")

    q = sub.add_parser("qa", help="Q&A golden-fixture tools")
    q.add_argument("qa_command", choices=["check"], help="'check' replays all fixtures")
    q.add_argument("--json", action="store_true")

    po = sub.add_parser("portals", help="list portal adapters and profile enablement")
    po.add_argument("--json", action="store_true")

    b = sub.add_parser("bot", help="run one supervised bot by name (e.g. indeed_it)")
    b.add_argument("name", help="bot name; must exist under automation_monorepo/bots/")
    b.add_argument("args", nargs=argparse.REMAINDER, help="passed through to the bot")

    sh = sub.add_parser("shadow", help="shadow-mode parity harness (compare-only)")
    sh.add_argument("shadow_command", choices=["qa"], help="'qa' replays fixtures + gate wiring")
    sh.add_argument("--live", type=int, default=0, metavar="N",
                    help="also sample N queued jobs from Mongo (read-only)")
    sh.add_argument("--profile", choices=["it", "general"], default=None)

    i = sub.add_parser("infra", help="infra module map + structural audit")
    i.add_argument("--audit", action="store_true", help="verify registry vs reality")
    i.add_argument("--json", action="store_true")

    au = sub.add_parser("audit", help="code duplication + retirement audit (read-only)")
    au.add_argument("--json", action="store_true")
    au.add_argument("--write", action="store_true",
                    help="write docs/RETIREMENT_MANIFEST.md")

    fc = sub.add_parser(
        "farm-check",
        help="productivity contract: proxies, NST slot 1, topology, ladder, portals",
    )
    fc.add_argument(
        "--live",
        action="store_true",
        help="also hit NST API slot 1 + probe proxies (needs agent + secrets)",
    )
    fc.add_argument("--json", action="store_true", help="machine-readable output")

    return p





def _cmd_doctor(args) -> int:
    from jobbots.app.pipeline import doctor_report

    report = doctor_report(quick=args.quick)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        for name, result in report["checks"].items():
            ok = result.get("ok", True) if isinstance(result, dict) else True
            mark = "✓" if ok else "✗"
            print(f"{mark} {name}")
            if isinstance(result, dict):
                for key, val in result.items():
                    if key != "ok":
                        print(f"    {key}: {val}")
        print(f"\noverall: {'OK' if report['ok'] else 'PROBLEMS FOUND'}")
    return 0 if report["ok"] else 1


def _cmd_status(args) -> int:
    from jobbots.app.pipeline import queue_counts, session_summary

    counts = queue_counts()
    if args.json:
        print(json.dumps({"queue": counts}, indent=2))
    else:
        print("=== Application queue ===")
        if counts:
            for status, n in sorted(counts.items()):
                print(f"  {status}: {n}")
        else:
            print("  (unavailable — Mongo unreachable or empty)")
        print("\n=== Portal sessions ===")
        print(session_summary())
    return 0


def _cmd_qa(args) -> int:
    from jobbots.core.qa import runner

    if args.json:
        print(json.dumps(runner.replay(), indent=2, default=str))
        return 0
    return runner.main()


def _cmd_portals(args) -> int:
    from jobbots.core.profiles.loader import available_profiles
    from jobbots.integrations.portals import registry

    payload = {
        "portals": {
            name: {"ats": cls.is_ats}
            for name, cls in sorted(registry.PORTAL_ADAPTERS.items())
        },
        "profiles": {
            name: registry.profile_portals(name)
            for name in available_profiles()
        },
        "supervised_bots": [
            {
                "bot_name": row.get("bot_name"),
                "portal": row.get("portal"),
                "job_profile": row.get("job_profile"),
                "enabled": row.get("enabled", True),
            }
            for row in registry.supervised_bots()
        ],
    }
    if args.json:
        print(json.dumps(payload, indent=2, default=str))
        return 0
    print("=== Portal adapters ===")
    for name, meta in payload["portals"].items():
        print(f"  {name} ({'ATS' if meta['ats'] else 'browser'})")
    print("\n=== Profile enablement (manifests) ===")
    for name, portals in payload["profiles"].items():
        print(f"  {name}: {', '.join(portals)}")
    print("\n=== Supervised bots (canonical registry) ===")
    for row in payload["supervised_bots"]:
        flag = "" if row["enabled"] else "  [paused]"
        print(f"  {row['bot_name']}: {row['portal']} / {row['job_profile']}{flag}")
    return 0


def _cmd_bot(args) -> int:
    import os
    import subprocess

    from jobbots.app.orchestrator import bot_python
    from jobbots.paths import MONOREPO_ROOT

    name = args.name[:-3] if args.name.endswith(".py") else args.name
    script = MONOREPO_ROOT / "bots" / f"{name}.py"
    if not script.is_file():
        print(f"unknown bot {args.name!r}: no bots/{name}.py")
        return 2
    extra = args.args[1:] if args.args[:1] == ["--"] else args.args
    proc = subprocess.run(
        [str(bot_python()), str(script), *extra],
        cwd=str(MONOREPO_ROOT),
        env=os.environ.copy(),
    )
    return proc.returncode


def _cmd_shadow(args) -> int:
    from jobbots.integrations import shadow

    return shadow.run_shadow(sample=args.live, profile=args.profile)


def _cmd_infra(args) -> int:
    from jobbots.app import infra

    if args.audit:
        report = infra.audit()
        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"modules: {', '.join(report['modules'])}")
            print(f"workflows scanned: {report['workflows_scanned']}")
            for problem in report["problems"]:
                print(f"  ✗ {problem}")
            print(f"infra audit: {'OK' if report['ok'] else 'PROBLEMS FOUND'}")
        return 0 if report["ok"] else 1
    if args.json:
        print(json.dumps(infra.module_map(), indent=2))
    else:
        print(infra.format_map())
    return 0


def _cmd_audit(args) -> int:
    from jobbots.app import retirement

    report = retirement.manifest()
    if args.write:
        from jobbots.paths import REPO_ROOT

        out = REPO_ROOT / "docs" / "RETIREMENT_MANIFEST.md"
        out.write_text(retirement.render_markdown(report), encoding="utf-8")
        print(f"wrote {out}")
    if args.json:
        print(json.dumps(report, indent=2))
        return 0
    print(
        f"duplicate groups under master/: {len(report['duplicate_groups'])} "
        f"({report['duplicate_files_total']} files, "
        f"~{report['duplicate_bytes_total'] // 1024} KiB redundant)"
    )
    print(f"unreferenced modules (candidates): {len(report['unreferenced_modules'])}")
    for m in report["unreferenced_modules"]:
        print(f"  {m['path']} ({m['bytes']} B)")
    print(f"shims tracked: {len(report['shims'])}")
    print(f"removal gate: {report['removal_gate']}")
    return 0


def _cmd_farm_check(args) -> int:
    from jobbots.app.farm_check import format_report, run_farm_check

    report = run_farm_check(live=bool(args.live))
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(format_report(report))
    return 0 if report["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.version:
        from jobbots import __version__

        print(f"jobbots {__version__}")
        return 0

    if args.command == "doctor":
        return _cmd_doctor(args)
    if args.command == "onboard":
        from jobbots.app.orchestrator import run_onboard

        extra = args.args[1:] if args.args[:1] == ["--"] else args.args
        return run_onboard(extra)
    if args.command == "discover":
        from jobbots.app.orchestrator import run_orchestrator_stage

        return run_orchestrator_stage("discover", once=args.once)
    if args.command == "apply":
        from jobbots.app.orchestrator import run_orchestrator_stage

        return run_orchestrator_stage("apply", workers=args.workers, once=args.once)
    if args.command == "run":
        from jobbots.app.orchestrator import run_orchestrator_stage

        return run_orchestrator_stage("all", workers=args.workers, once=args.once, auto=True)
    if args.command == "status":
        return _cmd_status(args)
    if args.command == "export":
        from jobbots.app.pipeline import run_export

        extra = args.args[1:] if args.args[:1] == ["--"] else args.args
        return run_export(EXPORT_SCRIPTS[args.kind], extra)
    if args.command == "qa":
        return _cmd_qa(args)
    if args.command == "portals":
        return _cmd_portals(args)
    if args.command == "bot":
        return _cmd_bot(args)
    if args.command == "shadow":
        return _cmd_shadow(args)
    if args.command == "infra":
        return _cmd_infra(args)
    if args.command == "audit":
        return _cmd_audit(args)
    if args.command == "farm-check":
        return _cmd_farm_check(args)

    parser.print_help()
    return 2





if __name__ == "__main__":
    sys.exit(main())

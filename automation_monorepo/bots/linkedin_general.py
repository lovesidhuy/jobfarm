"""Active LinkedIn General discovery entry point backed by the moved hybrid runner."""
import os, subprocess, sys
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from core.supervisor_runtime import merge_dotenv_into_env
    from core.supervised_bots import ensure_bot_runtime_defaults
    merge_dotenv_into_env(os.environ,root/".env")
    ensure_bot_runtime_defaults("linkedin_general",root)
    # Sole LinkedIn NST session applies BOTH IT and office/CS queue rows.
    # Queue profile is passed as LINKEDIN_JOB_PROFILE by application_worker;
    # honor it for form/resume hints so IT jobs are not answered as "general".
    queue_profile = (
        (os.environ.get("LINKEDIN_JOB_PROFILE") or "").strip().lower()
        or (os.environ.get("JOB_QUEUE_PROFILE") or "").strip().lower()
        or (os.environ.get("JOB_PROFILE") or "").strip().lower()
    )
    if queue_profile in {"it", "general"}:
        os.environ["LINKEDIN_JOB_PROFILE"] = queue_profile
        # JOB_PROFILE drives config/questions + resume selection in monorepo
        # helpers the hybrid runner may call via env bridges.
        os.environ["JOB_PROFILE"] = "IT" if queue_profile == "it" else "General"
    else:
        os.environ.setdefault("LINKEDIN_JOB_PROFILE", "it")
    os.environ.setdefault(
        "LINKEDIN_UNRESOLVED_QUESTION_LOG",
        str(root / "logs" / "linkedin_unresolved_questions.jsonl"),
    )

    # Map Python LLM gateway config to Node environment variables for the legacy JS runner
    try:
        from core.llm_backend.ai.llm_gateway import list_llm_gateway_chain
        chain = list_llm_gateway_chain()
        if chain:
            primary = chain[0]
            os.environ["AI_PROVIDER"] = "openai"
            os.environ["AI_API_KEY"] = primary.api_key
            os.environ["AI_MODEL_NAME"] = primary.model
            os.environ["AI_CUSTOM_URL"] = primary.base_url
            print(f"[LINKEDIN_GENERAL] Spawned Node runner configured with AI gateway: {primary.provider} (model: {primary.model})")
    except Exception as e:
        print(f"[LINKEDIN_GENERAL] Warning setting AI environment: {e}")

    runner = root.parent / "legacy" / "linkedin-ai-auto-apply-source" / "hybrid_runner.js"
    if not runner.is_file():
        print(f"[LINKEDIN_GENERAL] Legacy hybrid runner not found at: {runner}")
        print("[LINKEDIN_GENERAL] Note: LinkedIn lead discovery is natively handled via planner.py using JobSpyProvider(portals=['linkedin']).")
        sys.exit(0)
    raise SystemExit(subprocess.run(["node", str(runner)], cwd=runner.parent, env=os.environ.copy()).returncode)
if __name__ == "__main__":
    main()

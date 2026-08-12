"""Active LinkedIn IT discovery entry point backed by the moved hybrid runner."""
import os, subprocess, sys
from pathlib import Path

def main():
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from core.supervisor_runtime import merge_dotenv_into_env
    from core.supervised_bots import ensure_bot_runtime_defaults
    merge_dotenv_into_env(os.environ,root/".env")
    ensure_bot_runtime_defaults("linkedin_it",root)
    os.environ.setdefault("LINKEDIN_JOB_PROFILE","it")
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
            print(f"[LINKEDIN_IT] Spawned Node runner configured with AI gateway: {primary.provider} (model: {primary.model})")
    except Exception as e:
        print(f"[LINKEDIN_IT] Warning setting AI environment: {e}")

    runner = root.parent / "legacy" / "linkedin-ai-auto-apply-source" / "hybrid_runner.js"
    if not runner.is_file():
        print(f"[LINKEDIN_IT] Legacy hybrid runner not found at: {runner}")
        print("[LINKEDIN_IT] Note: LinkedIn lead discovery is natively handled via planner.py using JobSpyProvider(portals=['linkedin']).")
        sys.exit(0)
    raise SystemExit(subprocess.run(["node", str(runner)], cwd=runner.parent, env=os.environ.copy()).returncode)
if __name__ == "__main__":
    main()

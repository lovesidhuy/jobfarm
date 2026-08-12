"""
Per-bot AI facade. The ONLY thing the bot's runtime should call for AI work.

Combines:
- ProviderChain (Groq -> Ollama with circuit breakers)
- TokenBucket (rate-limit gate for Groq)
- TrainingLogger (every Q/A captured)
- MongoStore (persisted question records)
- Bot-tailored prompt formatting (bot supplies callables)

The AIClient does NOT own prompts. Each bot keeps its own
`modules/ai/prompts.py` and passes pre-formatted prompts in. This keeps each
bot's tailoring intact — never unified.

Construction (typical):

    from jobbots.core.llm_backend.ai_client import AIClient
    from jobbots.core.llm_backend.fallback import Provider, ProviderChain
    from jobbots.core.llm_backend.rate_limit import TokenBucket
    from jobbots.core.llm_backend.ai.groqConnections import call_groq_chat
    from jobbots.core.llm_backend.ai.ollamaConnections import call_ollama_chat

    groq_bucket = TokenBucket(cfg.state_dir / "groq_bucket.json", rate=0.5, capacity=20)
    chain = ProviderChain(
        providers=[
            Provider("groq", call_groq_chat, bucket=groq_bucket),
            Provider("ollama", call_ollama_chat),
        ],
        breaker_dir=cfg.state_dir,
        log_path=cfg.logs_dir / "ai.jsonl",
    )
    ai = AIClient(bot_id=cfg.bot_id, chain=chain, store=store, trainer=trainer)
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from .fallback import ProviderChain, ProviderResult
from .training_logger import TrainingLogger


@dataclass
class AIClient:
    bot_id: str
    chain: ProviderChain
    store: Optional[Any] = None       # MongoStore or None
    trainer: Optional[TrainingLogger] = None

    def ask(self, prompt: str, *, task_type: str = "default", **kwargs: Any) -> ProviderResult:
        return self.chain.ask(prompt, task_type=task_type, **kwargs)

    # ── Structured helpers ────────────────────────────────────────────────────
    def ask_json(self, prompt: str, *, task_type: str = "default", repair: bool = True,
                 **kwargs: Any) -> tuple[Optional[dict], ProviderResult]:
        """Ask and parse a JSON response. On parse failure, optionally re-prompt
        the *next* provider with a repair instruction. Returns (parsed_or_None, result)."""
        result = self.ask(prompt, task_type=task_type, **kwargs)
        parsed = _try_parse_json(result.text)
        if parsed is not None or not repair:
            return parsed, result
        repair_prompt = (
            "The previous response was not valid JSON. Re-emit the SAME information "
            "as a single valid JSON object only, with no prose, no code fences.\n\n"
            f"PREVIOUS RESPONSE:\n{result.text}\n\nORIGINAL PROMPT:\n{prompt}"
        )
        result2 = self.ask(repair_prompt, task_type=f"{task_type}_repair", **kwargs)
        return _try_parse_json(result2.text), result2

    # ── Question-answering with full audit trail ──────────────────────────────
    def answer_question(self, *, run_id: str, job_id: str, question: str,
                        kind: str, prompt: str, context: Optional[dict] = None,
                        accepted: Optional[bool] = None) -> tuple[str, ProviderResult]:
        result = self.ask(prompt, task_type=f"answer_{kind}")
        answer = result.text.strip()
        if self.trainer is not None:
            self.trainer.log_qa(
                run_id=run_id, job_id=job_id, question=question, kind=kind,
                answer=answer, source="ai", provider=result.provider,
                accepted=accepted, context=context or {},
            )
        if self.store is not None:
            try:
                self.store.record_question(
                    run_id=run_id, job_id=job_id, question=question, kind=kind,
                    answer=answer, source="ai", provider=result.provider,
                    accepted=accepted,
                )
            except Exception:  # noqa: BLE001 — never fail bot on storage error
                pass
        return answer, result

    def gate_decision(self, *, run_id: str, job_id: str, prompt: str
                      ) -> tuple[Optional[dict], ProviderResult]:
        parsed, result = self.ask_json(prompt, task_type="gate_decision")
        if self.store is not None:
            try:
                self.store.record_gate(
                    run_id=run_id, job_id=job_id,
                    verdict=(parsed or {}).get("verdict", "unknown"),
                    score=(parsed or {}).get("score"),
                    reasoning=(parsed or {}).get("reasoning", ""),
                    provider=result.provider, latency_ms=result.latency_ms,
                )
            except Exception:  # noqa: BLE001
                pass
        return parsed, result


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _try_parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    candidates = [text]
    m = _JSON_FENCE_RE.search(text)
    if m:
        candidates.insert(0, m.group(1))
    # Also try the substring between the first '{' and the last '}'
    if "{" in text and "}" in text:
        candidates.append(text[text.index("{"): text.rindex("}") + 1])
    for c in candidates:
        try:
            obj = json.loads(c)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None

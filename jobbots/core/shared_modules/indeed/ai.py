from __future__ import annotations

from ._bootstrap import *  # noqa: F403

try:
    from modules.qa_answer_bank import find_answer
except Exception:
    def find_answer(*args, **kwargs):
        return None


def _sync_ai_state() -> None:
    """Keep split Indeed modules pointed at the same live AI client."""
    import sys

    for module_name in (
        "modules.indeed",
        "jobbots.core.shared_modules.indeed._bootstrap",
        "jobbots.core.shared_modules.indeed.gates",
        "jobbots.core.shared_modules.indeed.questions",
        "jobbots.core.shared_modules.indeed.smartapply",
        "jobbots.core.shared_modules.indeed.apply",
        "jobbots.core.shared_modules.indeed.loop",
    ):
        module = sys.modules.get(module_name)
        if module is None:
            continue
        module.__dict__["_aiClient"] = _aiClient
        module.__dict__["_ai_provider"] = _ai_provider


def _init_ai_client():
    global _aiClient, _ai_provider
    if not use_AI:
        _init_ollama_fallback()
        _sync_ai_state()
        return
    provider = (ai_provider or "").lower().strip()
    _ai_provider = provider
    if provider == "openai":
        try:
            from modules.ai.openaiConnections import ai_create_openai_client
            _aiClient = ai_create_openai_client()
            print_lg("[Indeed] ✓ OpenAI AI client ready.")
            _sync_ai_state()
        except Exception as e:
            print_lg(f"[Indeed] ✗ Failed to init OpenAI: {e}")
    elif provider == "deepseek":
        try:
            from modules.ai.deepseekConnections import deepseek_create_client
            _aiClient = deepseek_create_client()
            print_lg("[Indeed] ✓ DeepSeek AI client ready.")
            _sync_ai_state()
        except Exception as e:
            print_lg(f"[Indeed] ✗ Failed to init DeepSeek: {e}")
    elif provider == "gemini":
        try:
            from modules.ai.geminiConnections import gemini_create_client
            _aiClient = gemini_create_client()
            print_lg("[Indeed] ✓ Gemini AI client ready.")
            _sync_ai_state()
        except Exception as e:
            print_lg(f"[Indeed] ✗ Failed to init Gemini: {e}")
    elif provider == "ollama":
        _init_ollama_fallback()
        _sync_ai_state()
    else:
        _init_ollama_fallback()
        _sync_ai_state()


def _init_ollama_fallback() -> None:
    global _aiClient, _ai_provider
    try:
        from config.settings import use_ollama_for_indeed
        if not use_ollama_for_indeed:
            return
        from config.settings import ollama_base_url as _obu
    except ImportError:
        _obu = "http://localhost:11434/v1"
    try:
        from modules.ai.ollamaConnections import ollama_is_available, ollama_answer_question
        if ollama_is_available(_obu):
            _aiClient = ollama_answer_question
            _ai_provider = "ollama"
            print_lg("[Indeed] ✓ Ollama ready.")
            _sync_ai_state()
        else:
            print_lg("[Indeed] ⚠ Ollama not reachable — continuing without AI.")
    except Exception as e:
        print_lg(f"[Indeed] Could not load Ollama: {e}")


def _close_ai_client() -> None:
    global _aiClient
    if _aiClient is None:
        return
    try:
        if _ai_provider in ("openai", "deepseek"):
            from modules.ai.openaiConnections import ai_close_openai_client
            ai_close_openai_client(_aiClient)
        print_lg(f"[Indeed] Closed {_ai_provider} AI client.")
    except Exception as e:
        print_lg(f"[Indeed] Error closing AI client: {e}")
    _aiClient = None
    _sync_ai_state()


def _sanitize_ai_form_answer(answer: str, question: str = "", options: list | None = None) -> str:
    """Keep model commentary/reasoning out of application form fields."""
    text = str(answer or "").strip()
    if not text:
        return ""

    text = re.sub(r"^```(?:text|markdown|json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()

    # Models sometimes provide reasoning followed by an explicit final answer.
    explicit = re.findall(
        r"(?:final\s+answer|answer|response)\s*:\s*(.+)",
        text,
        flags=re.I | re.S,
    )
    if explicit:
        text = explicit[-1].strip()

    text = text.strip().strip('"').strip("'").strip()
    option_labels = [str(option) for option in (options or []) if str(option).strip()]
    if option_labels:
        for option in option_labels:
            if text.casefold() == option.casefold():
                return option
        # If commentary surrounds one exact option, return only that option.
        matches = [
            option for option in option_labels
            if re.search(rf"(?<!\w){re.escape(option)}(?!\w)", text, flags=re.I)
        ]
        if len(matches) == 1:
            return matches[0]

    q = (question or "").lower()
    if any(k in q for k in ("yes or no", "yes/no", "(yes / no", "(yes/no")):
        yes_no = re.findall(r"\b(yes|no|prefer not to say)\b", text, flags=re.I)
        if yes_no:
            return yes_no[-1].title()

    return text


def _ai_answer(question: str, hint: str = "", job_context: str = "",
               options: list = None) -> str:
    qh = f"{question or ''} {hint or ''}".lower()
    if any(k in qh for k in ("recruiter or referral", "referral contact",
                             "referral name", "referrer", "referred by")):
        result = "N/A"
        log_training_event("ai_answer_skipped", job=_current_job_meta,
                           question=question, hint=hint, options=options or [],
                           reason="deterministic_no_referral_contact",
                           answer=result)
        return result
    if any(k in qh for k in ("how did you hear", "how did you find out",
                             "heard about this role", "heard about this job",
                             "heard about our company", "heard about this opportunity")):
        link = str((_current_job_meta or {}).get("job_link")
                   or (_current_job_meta or {}).get("job_href") or "").lower()
        source = str((_current_job_meta or {}).get("source") or "").lower()
        if "glassdoor" in source or "glassdoor" in link:
            result = "Glassdoor"
        elif "indeed" in source or "indeed" in link:
            result = "Indeed"
        else:
            result = "Online job posting"
        log_training_event("ai_answer_skipped", job=_current_job_meta,
                           question=question, hint=hint, options=options or [],
                           reason="deterministic_application_source",
                           answer=result)
        return result
    if any(k in qh for k in ("portfolio", "github", "project link", "project links",
                             "work samples", "code samples", "personal website")):
        try:
            from config.questions import website as _ws, professional_profile_url as _ppu
            result = _ws or _ppu or ""
        except ImportError:
            result = ""
        if result:
            log_training_event("ai_answer_skipped", job=_current_job_meta,
                               question=question, hint=hint, options=options or [],
                               reason="deterministic_portfolio_url",
                               answer=result)
            return result
    bank_match = find_answer(question=question, hint=hint, options=options)
    if bank_match:
        log_training_event("ai_answer_skipped", job=_current_job_meta,
                           question=question, hint=hint, options=options or [],
                           reason=bank_match.source,
                           answer=bank_match.answer,
                           matched_question=bank_match.matched_question,
                           match_score=bank_match.score)
        return bank_match.answer
    if _aiClient is None:
        log_training_event(
            "ai_answer_skipped",
            job=_current_job_meta,
            question=question,
            hint=hint,
            options=options or [],
            reason="ai_client_missing",
        )
        # Never skip: if AI unavailable and options provided, pick "Yes" or first option
        if options:
            for opt in options:
                if opt.lower().strip() in ("yes", "oui", "true"):
                    return opt
            return options[0]
        return ""
    started = time.time()
    try:
        provider = (_ai_provider or "").lower()
        question_for_provider = question
        if hint and provider in {"openai", "deepseek", "gemini"}:
            question_for_provider = f"{question}\n\nField/DOM context:\n{hint}"
        question_type = "single_select" if options else "text"
        if provider == "openai":
            from modules.ai.openaiConnections import ai_answer_question
            try:
                from config.questions import user_information_all
            except ImportError:
                user_information_all = ""
            ans = ai_answer_question(_aiClient, question_for_provider, options=options,
                                     question_type=question_type,
                                     job_description=job_context,
                                     user_information_all=user_information_all)
            result = _sanitize_ai_form_answer(
                ans if isinstance(ans, str) else "", question, options
            )
            log_training_event("ai_answer", job=_current_job_meta, provider=provider,
                               question=question, hint=hint, options=options or [],
                               answer=result, elapsed_ms=round((time.time() - started) * 1000))
            return result
        elif provider == "deepseek":
            from modules.ai.deepseekConnections import deepseek_answer_question
            try:
                from config.questions import user_information_all
            except ImportError:
                user_information_all = ""
            ans = deepseek_answer_question(_aiClient, question_for_provider, options=options,
                                           question_type=question_type, job_description=job_context,
                                           about_company=None, user_information_all=user_information_all)
            result = _sanitize_ai_form_answer(
                ans if isinstance(ans, str) else "", question, options
            )
            log_training_event("ai_answer", job=_current_job_meta, provider=provider,
                               question=question, hint=hint, options=options or [],
                               answer=result, elapsed_ms=round((time.time() - started) * 1000))
            return result
        elif provider == "gemini":
            from modules.ai.geminiConnections import gemini_answer_question
            try:
                from config.questions import user_information_all
            except ImportError:
                user_information_all = ""
            ans = gemini_answer_question(_aiClient, question_for_provider, options=options,
                                         question_type=question_type, job_description=job_context,
                                         about_company=None, user_information_all=user_information_all)
            result = _sanitize_ai_form_answer(
                ans if isinstance(ans, str) else "", question, options
            )
            log_training_event("ai_answer", job=_current_job_meta, provider=provider,
                               question=question, hint=hint, options=options or [],
                               answer=result, elapsed_ms=round((time.time() - started) * 1000))
            return result
        elif provider == "ollama":
            ans = _aiClient(question=question, hint=hint, job_context=job_context)
            result = _sanitize_ai_form_answer(
                ans if isinstance(ans, str) else "", question, options
            )
            log_training_event("ai_answer", job=_current_job_meta, provider=provider,
                               question=question, hint=hint, options=options or [],
                               answer=result, elapsed_ms=round((time.time() - started) * 1000))
            return result
    except Exception as e:
        print_lg(f"[Indeed] AI answer failed: {e}")
        log_training_event("ai_answer_error", job=_current_job_meta,
                           provider=_ai_provider, question=question, hint=hint,
                           options=options or [], error=f"{type(e).__name__}: {e}")
    return ""


def _ai_forced_choice(question: str, options: list, job_context: str = "") -> str:
    """
    Retry AI with a forced-choice prompt when initial AI call failed.
    Lists exact options and demands AI pick one.
    """
    if _aiClient is None or not options:
        return ""
    try:
        from modules.ai.prompts import ai_forced_choice_prompt
    except ImportError:
        return ""

    user_profile = _groq_gate_user_profile()[:800]
    options_formatted = "\n".join(f"- {opt}" for opt in options)
    prompt = ai_forced_choice_prompt.format(question, options_formatted, user_profile)

    started = time.time()
    try:
        provider = (_ai_provider or "").lower()
        if provider in ("openai", "deepseek"):
            from modules.ai.openaiConnections import ai_completion
            messages = [{"role": "user", "content": prompt}]
            result = ai_completion(_aiClient, messages, stream=False)
            result = result.strip() if isinstance(result, str) else ""
        elif provider == "gemini":
            from modules.ai.geminiConnections import gemini_answer_question
            try:
                from config.questions import user_information_all
            except ImportError:
                user_information_all = ""
            result = gemini_answer_question(
                _aiClient, prompt, options=options,
                question_type="single_select", job_description=job_context,
                about_company=None, user_information_all=user_information_all,
            )
            result = result.strip() if isinstance(result, str) else ""
        elif provider == "ollama":
            result = _aiClient(question=prompt, hint="", job_context="")
            result = result.strip() if isinstance(result, str) else ""
        else:
            return ""

        if result:
            log_training_event("ai_forced_choice", job=_current_job_meta,
                               provider=provider, question=question, options=options,
                               answer=result, elapsed_ms=round((time.time() - started) * 1000))
            print_lg(f"    [AI forced-choice] Answer: {result[:60]}")
        return result
    except Exception as e:
        print_lg(f"[Indeed] AI forced-choice failed: {e}")
        return ""


def _extract_skills_ai(description: str) -> str:
    if _aiClient is None or not description:
        return "In Development"
    try:
        provider = (_ai_provider or "").lower()
        if provider == "openai":
            from modules.ai.openaiConnections import ai_extract_skills
            return str(ai_extract_skills(_aiClient, description))
        elif provider == "deepseek":
            from modules.ai.deepseekConnections import deepseek_extract_skills
            return str(deepseek_extract_skills(_aiClient, description))
        elif provider == "gemini":
            from modules.ai.geminiConnections import gemini_extract_skills
            return str(gemini_extract_skills(_aiClient, description))
        else:
            return "In Development"
    except Exception as e:
        print_lg(f"[Indeed] Skill extraction failed: {e}")
        return "Error extracting skills"


# ─────────────────────────────────────────────────────────────────────────────
# CSV helpers
# ─────────────────────────────────────────────────────────────────────────────

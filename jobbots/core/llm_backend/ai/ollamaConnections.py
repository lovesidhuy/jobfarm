from __future__ import annotations
'''
Ollama Local LLM Connection
============================
Uses the Ollama OpenAI-compatible REST API (http://localhost:11434/v1)
to answer job-application questions without requiring any cloud API key.

Configure in config/settings.py:

    use_ollama_for_indeed = True        # enable/disable
    ollama_model          = "llama3.2"  # any model you have pulled, e.g. "mistral", "phi3"
    ollama_base_url       = "http://localhost:11434/v1"   # change if Ollama runs elsewhere
'''

import json
from jobbots.core.utils import print_lg

# ── Default config (may be overridden by importing from config/settings.py) ──
_OLLAMA_URL   = "http://localhost:11434/v1/chat/completions"
_OLLAMA_MODEL = "llama3.2"

# Build a rich user-profile string once so every call gets consistent context
def _build_user_profile() -> str:
    lines = []
    try:
        from config.personals import first_name, last_name, current_city, country, state, zipcode
        lines.append(f"Name: {first_name} {last_name}")
        if current_city:
            lines.append(f"Location: {current_city}, {state}, {country} {zipcode}")
        else:
            lines.append(f"Location: {state}, {country}")
    except ImportError:
        pass
    try:
        from config.personals import linkedin_headline
        lines.append(f"Professional headline: {linkedin_headline}")
    except ImportError:
        pass
    try:
        from config.search import current_experience
        lines.append(f"Years of experience: {current_experience}")
    except ImportError:
        pass
    try:
        from config.personals import email_address
        if email_address:
            lines.append(f"Email: {email_address}")
    except ImportError:
        pass
    # Append resume text if available from questions.py
    try:
        from config.questions import personal_summary
        if personal_summary:
            lines.append(f"\nPersonal summary / background:\n{personal_summary[:800]}")
    except ImportError:
        pass
    import os
    profile_type = os.environ.get("JOB_PROFILE", "IT").upper()
    fallback = "IT professional" if profile_type == "IT" else "customer service professional"
    return "\n".join(lines) if lines else f"A qualified {fallback} applying to jobs in Canada."


_USER_PROFILE = None   # lazy-built on first call
_AVAILABLE_MODELS_CACHE = None  # Cache available models to avoid repeated calls


def _get_available_ollama_models(base_url: str | None = None, timeout: int = 5) -> list[str]:
    """
    Fetch list of available models from Ollama and cache the result.
    Returns empty list if Ollama is unreachable.
    """
    global _AVAILABLE_MODELS_CACHE
    
    import urllib.request
    
    if _AVAILABLE_MODELS_CACHE is not None:
        return _AVAILABLE_MODELS_CACHE
    
    api_url = "http://localhost:11434/api/tags"
    if base_url:
        # Turn e.g. http://localhost:11434/v1 → http://localhost:11434/api/tags
        api_url = base_url.split("/v1")[0].rstrip("/") + "/api/tags"
    
    try:
        with urllib.request.urlopen(api_url, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            models = [m["name"] for m in data.get("models", [])]
            _AVAILABLE_MODELS_CACHE = models
            return models
    except Exception as e:
        print_lg(f"  [Ollama] Could not fetch available models: {e}")
        return []


def _get_best_available_model(preferred_model: str | None, base_url: str | None = None) -> str:
    """
    Auto-detect and return the best available Ollama model.
    If preferred_model is specified and available, use it.
    Otherwise, return the first available model.
    Returns the preferred_model unchanged if Ollama is unreachable (fail-safe).
    """
    available = _get_available_ollama_models(base_url)
    
    if not available:
        # Ollama not reachable, return what was requested as fallback
        print_lg(f"  [Ollama] No models detected, using fallback: {preferred_model or _OLLAMA_MODEL}")
        return preferred_model or _OLLAMA_MODEL
    
    # If preferred model is available, use it
    if preferred_model and any(preferred_model in m or m in preferred_model for m in available):
        print_lg(f"  [Ollama] Using preferred model: {preferred_model}")
        return preferred_model
    
    # Otherwise use first available
    best = available[0]
    print_lg(f"  [Ollama] Preferred model not found. Using available: {best}")
    print_lg(f"  [Ollama] All available: {', '.join(available)}")
    return best


def _get_user_profile() -> str:
    """Lazy-cached, project-wide applicant profile string for AI prompts."""
    global _USER_PROFILE
    if _USER_PROFILE is None:
        _USER_PROFILE = _build_user_profile()
    return _USER_PROFILE


# ── Core call ────────────────────────────────────────────────────────────────

def ollama_answer_question(
    question: str,
    hint: str = "",
    job_context: str = "",
    model: str | None = None,
    base_url: str | None = None,
    timeout: int = 30,
) -> str:
    '''
    Ask the local Ollama model to answer a free-text job application question.

    Parameters
    ----------
    question    : The question text (label / surrounding text).
    hint        : Extra attribute hints (e.g., placeholder, aria-label).
    job_context : Optional job title / description excerpt for context.
    model       : Override the Ollama model (defaults to ollama_model in settings).
    base_url    : Override the Ollama base URL (defaults to ollama_base_url in settings).
    timeout     : HTTP timeout in seconds.

    Returns
    -------
    str : The model's plain-text answer, stripped of extra whitespace.
          Returns "" on any error so the caller can fall back gracefully.
    '''
    import urllib.request

    # Resolve model / URL from settings if not explicitly passed
    if model is None:
        try:
            from config.settings import ollama_model
            model = ollama_model
        except ImportError:
            model = _OLLAMA_MODEL

    api_url = _OLLAMA_URL
    if base_url is not None:
        api_url = base_url.rstrip("/") + "/chat/completions"
    else:
        try:
            from config.settings import ollama_base_url
            api_url = ollama_base_url.rstrip("/") + "/chat/completions"
        except ImportError:
            pass

    # Auto-detect best available model if Ollama is local
    model = _get_best_available_model(model, api_url)

    user_profile = _get_user_profile()

    system_msg = (
        "You are the candidate's autonomous job-application agent. You are "
        "actively submitting this application on the candidate's behalf — "
        "there is NO human reviewer who can fix or fill in missing answers. "
        "Your single objective: get the candidate QUALIFIED through the "
        "employer's pre-screening so the application is accepted, not "
        "rejected/disqualified. Always answer truthfully using the applicant "
        "profile, but when a question allows multiple acceptable answers, "
        "pick the one most likely to keep the candidate qualified and "
        "progress to the next step.\n\n"
        "HARD RULES (never break):\n"
        "- NEVER return an empty answer, 'N/A', 'unknown', 'I don't know', "
        "or refuse. If genuinely unsure, give the best safe guess that keeps "
        "the candidate qualified.\n"
        "- For single-select / radio / dropdown questions → return EXACTLY "
        "one of the provided option labels, character-for-character. Never "
        "invent a new option.\n"
        "- For numeric / experience questions → return ONLY a plain integer "
        "(e.g. 3). If the profile is silent, pick a realistic number that "
        "meets the role's minimum.\n"
        "- For Yes/No questions → return ONLY 'Yes' or 'No'. Default to the "
        "answer that keeps the candidate qualified (e.g. 'Yes' to work "
        "eligibility, availability, agreements, acknowledgements; 'No' to "
        "needing sponsorship, criminal history, or anything disqualifying).\n"
        "- For consent / acknowledgement / 'I understand' / 'I agree' style "
        "questions → always agree / acknowledge / consent.\n"
        "- For short-answer questions → one sentence, no preamble.\n"
        "- For longer descriptive questions → ≤ 300 characters, no bullet "
        "points, no markdown.\n"
        "- Never repeat the question. Never say 'As an AI…'. Never explain "
        "your reasoning — return only the answer itself.\n"
        f"\nApplicant profile:\n{user_profile}"
    )

    user_msg_parts = [f"Question: {question}"]
    if hint:
        user_msg_parts.append(f"(Field hint: {hint})")
    if job_context:
        user_msg_parts.append(f"Job context: {job_context[:300]}")
    user_msg = "\n".join(user_msg_parts)

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system_msg},
            {"role": "user",   "content": user_msg},
        ],
        "stream": False,
        "temperature": 0.2,
    }).encode("utf-8")

    req = urllib.request.Request(
        api_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            answer = body["choices"][0]["message"]["content"].strip()
            print_lg(f"  [Ollama] Q: {question[:80]}…  →  {answer[:120]}")
            return answer
    except Exception as e:
        print_lg(f"  [Ollama] ✗ Could not get answer: {e}")
        return ""


def ollama_is_available(base_url: str | None = None, timeout: int = 5) -> bool:
    '''
    Check whether the Ollama server is reachable (quick /api/tags probe).
    Returns True if reachable, False otherwise.
    '''
    import urllib.request
    probe_url = "http://localhost:11434/api/tags"
    if base_url:
        # Turn e.g. http://localhost:11434/v1 → http://localhost:11434/api/tags
        probe_url = base_url.split("/v1")[0].rstrip("/") + "/api/tags"
    try:
        with urllib.request.urlopen(probe_url, timeout=timeout):
            return True
    except Exception:
        return False

from __future__ import annotations
##> ------ Yang Li : MARKYangL - Feature ------
import os

from config.secrets import *
from config.settings import showAiErrorAlerts
from jobbots.core.utils import print_lg, critical_error_log, convert_to_json
from jobbots.core.llm_backend.ai.prompts import *

from jobbots.core.auto_mode import auto_confirm as confirm
from jobbots.core.observability.langfuse_tracing import trace_generation, update_generation
from openai import OpenAI
from typing import Literal

def deepseek_create_client() -> OpenAI | None:
    '''
    Creates a DeepSeek client using the OpenAI compatible API.
    * Returns an OpenAI-compatible client configured for DeepSeek
    '''
    try:
        print_lg("Creating DeepSeek client...")
        if not use_AI:
            raise ValueError("AI is not enabled! Please enable it by setting `use_AI = True` in `secrets.py` in `config` folder.")

        from jobbots.core.llm_backend.ai.llm_gateway import resolve_llm_gateway

        # Prefer Akash ML / Bluesminds (DeepSeek V4 Flash). Keys live in
        # Infisical / secret_manager / .env. Answer brain stays provider-agnostic.
        try:
            secrets_model = str(llm_model or "")
        except Exception:
            secrets_model = ""
        gw = resolve_llm_gateway(default_model="deepseek-v4-flash", secrets_llm_model=secrets_model)

        if not gw.api_key or gw.api_key in {"not-needed", "YOUR_API_KEY", "changeme", ""}:
            raise ValueError(
                "No configured LLM API key found "
                "(BLUESMINDS_API_KEY / AKASHML_API_KEY / OPENROUTER_API_KEY / DEEPSEEK_API_KEY)"
            )

        base_url = (gw.base_url or "").rstrip("/")
        client = OpenAI(base_url=base_url, api_key=gw.api_key)

        print_lg("---- SUCCESSFULLY CREATED DEEPSEEK CLIENT! ----")
        print_lg(f"Using provider: {gw.provider}")
        print_lg(f"Using API URL: {base_url}")
        print_lg(f"Using Model: {gw.model}")
        print_lg("Check './config/secrets.py' / Infisical for more details.\n")
        print_lg("---------------------------------------------")
        # Stash for completion() so we don't re-resolve inconsistently.
        try:
            client._jobbots_llm_model = gw.model  # type: ignore[attr-defined]
            client._jobbots_llm_provider = gw.provider  # type: ignore[attr-defined]
        except Exception:
            pass
        return client
    except Exception as e:
        error_message = "Error occurred while creating DeepSeek client. Make sure your API connection details are correct."
        critical_error_log(error_message, e)
        # Avoid UnboundLocalError when showAiErrorAlerts is reassigned below.
        alerts = bool(globals().get("showAiErrorAlerts", True))
        if alerts:
            try:
                if "Pause AI error alerts" == confirm(
                    f"{error_message}\n{str(e)}",
                    "DeepSeek Connection Error",
                    ["Pause AI error alerts", "Okay Continue"],
                ):
                    globals()["showAiErrorAlerts"] = False
            except Exception:
                pass
        return None

def deepseek_model_supports_temperature(model_name: str) -> bool:
    '''
    Checks if the specified DeepSeek model supports the temperature parameter.
    * Takes in `model_name` of type `str` - The name of the DeepSeek model
    * Returns `bool` - True if the model supports temperature adjustments
    '''
    if "reasoner" in model_name or "r1" in model_name:
        return False
    return True

def _is_transient_llm_error(exc: BaseException) -> bool:
    """True for timeouts / rate limits / 5xx / connection blips (worth failover)."""
    name = exc.__class__.__name__
    text = f"{name}: {exc}".lower()
    markers = (
        "timeout",
        "timed out",
        "apitimeouterror",
        "readtimeout",
        "connecttimeout",
        "connection",
        "connectionreset",
        "connection aborted",
        "temporarily unavailable",
        "rate limit",
        "429",
        "502",
        "503",
        "504",
        "overloaded",
        "capacity",
        "internal server error",
        "service unavailable",
        "bad gateway",
        "gateway timeout",
        "cloudflare",
    )
    return any(m in text for m in markers)


def _completion_timeout_seconds() -> float:
    # Discovery batch screening needs more than 45s on Akash V4.
    try:
        timeout = float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "120") or "120")
    except ValueError:
        timeout = 120.0
    return max(30.0, min(timeout, 300.0))


def _run_one_completion(
    client: OpenAI,
    *,
    model_name: str,
    messages: list[dict],
    response_format: dict | None,
    temperature: float,
    stream: bool,
    timeout: float,
) -> dict | str:
    params = {
        "model": model_name,
        "messages": messages,
        "stream": stream,
        "timeout": timeout,
        # Keep completions small so low-credit OpenRouter balances and Akash
        # free tiers don't 402 on a 16k default max_tokens reservation.
        "max_tokens": 2048,
    }
    if deepseek_model_supports_temperature(model_name):
        params["temperature"] = temperature
    if response_format:
        params["response_format"] = response_format

    print_lg("Calling DeepSeek-compatible API for completion...")
    print_lg(f"Using model: {model_name}")
    print_lg(f"Message count: {len(messages)}")
    print_lg(f"Timeout: {timeout}s")
    provider = str(getattr(client, "_jobbots_llm_provider", "deepseek-compatible"))
    with trace_generation(
        name="jobbots.llm.completion",
        model=model_name,
        provider=provider,
        messages=messages,
        metadata={"stream": stream, "response_format": bool(response_format)},
    ) as observation:
        completion = client.chat.completions.create(**params)

        result = ""
        if stream:
            print_lg("--STREAMING STARTED")
            for chunk in completion:
                if chunk.model_extra and chunk.model_extra.get("error"):
                    raise ValueError(f'Error occurred with DeepSeek API: "{chunk.model_extra.get("error")}"')
                chunk_message = chunk.choices[0].delta.content
                if chunk_message is not None:
                    result += chunk_message
                print_lg(chunk_message, end="", flush=True)
            print_lg("\n--STREAMING COMPLETE")
        else:
            if completion.model_extra and completion.model_extra.get("error"):
                raise ValueError(f'Error occurred with DeepSeek API: "{completion.model_extra.get("error")}"')
            result = completion.choices[0].message.content

        if response_format:
            result = convert_to_json(result)
        update_generation(observation, output=result, completion=completion)

        print_lg("\nDeepSeek Answer:\n")
        print_lg(result, pretty=response_format is not None)
        return result


def deepseek_completion(client: OpenAI, messages: list[dict], response_format: dict = None, temperature: float = 0, stream: bool = stream_output) -> dict | ValueError:
    '''
    Completes a chat using DeepSeek-compatible gateways with failover.

    Primary: Akash ML (free). On timeout/429/5xx/connection errors, retries once
    on the same gateway then fails over to OpenRouter (when credited) then
    official DeepSeek. Prevents discovery fail-closed zero-enqueue when Akash
    stalls.
    '''
    if not client:
        raise ValueError("DeepSeek client is not available!")

    from jobbots.core.llm_backend.ai.llm_gateway import list_llm_gateway_chain

    try:
        secrets_model = str(llm_model or "")
    except Exception:
        secrets_model = ""

    timeout = _completion_timeout_seconds()
    try:
        retries = int(os.getenv("LLM_PROVIDER_RETRIES", "1") or "1")
    except ValueError:
        retries = 1
    retries = max(0, min(retries, 3))

    chain = list_llm_gateway_chain(
        default_model="deepseek-v4-flash",
        secrets_llm_model=secrets_model,
    )
    # Prefer the caller's client as the first hop (already resolved).
    primary_provider = getattr(client, "_jobbots_llm_provider", None) or ""
    primary_model = getattr(client, "_jobbots_llm_model", None) or None
    attempts: list[tuple[OpenAI, str, str]] = []
    if primary_model:
        attempts.append((client, str(primary_model), str(primary_provider or "primary")))
    for gw in chain:
        if primary_provider and gw.provider == primary_provider and primary_model == gw.model:
            continue
        try:
            hop = OpenAI(base_url=gw.base_url.rstrip("/"), api_key=gw.api_key)
            hop._jobbots_llm_model = gw.model  # type: ignore[attr-defined]
            hop._jobbots_llm_provider = gw.provider  # type: ignore[attr-defined]
            attempts.append((hop, gw.model, gw.provider))
        except Exception as exc:
            print_lg(f"[LLM] skip gateway {gw.provider}: {exc}")

    if not attempts:
        raise ValueError("No LLM gateway available for completion")

    last_error: Exception | None = None
    for hop_client, model_name, provider_label in attempts:
        for attempt_i in range(retries + 1):
            try:
                print_lg(
                    f"[LLM] provider={provider_label} model={model_name} "
                    f"attempt={attempt_i + 1}/{retries + 1}"
                )
                return _run_one_completion(
                    hop_client,
                    model_name=model_name,
                    messages=messages,
                    response_format=response_format,
                    temperature=temperature,
                    stream=stream,
                    timeout=timeout,
                )
            except Exception as e:
                last_error = e
                print_lg(f"Full error details: {e.__class__.__name__}: {str(e)}")
                if hasattr(e, "response"):
                    print_lg(
                        f"Response data: {e.response.text if hasattr(e.response, 'text') else e.response}"
                    )
                if not _is_transient_llm_error(e):
                    # Auth / 402 / bad request: try next gateway if any, else raise.
                    print_lg(f"[LLM] non-transient error on {provider_label}; trying next gateway if any")
                    break
                if attempt_i < retries:
                    print_lg(f"[LLM] transient error on {provider_label}; retrying same provider")
                    continue
                print_lg(f"[LLM] {provider_label} exhausted; failing over to next gateway")
                break

    error_message = f"DeepSeek API error: {last_error}"
    if last_error and "401" in str(last_error):
        print_lg("This appears to be an authentication error. Your API key might be invalid or expired.")
    elif last_error and "429" in str(last_error):
        print_lg("You've exceeded the rate limit on all gateways.")
    elif last_error and "402" in str(last_error):
        print_lg("Payment required / credits exhausted on a gateway.")
    raise ValueError(error_message)

def deepseek_extract_skills(client: OpenAI, job_description: str, stream: bool = stream_output) -> dict | ValueError:
    '''
    Function to extract skills from job description using DeepSeek API.
    * Takes in `client` of type `OpenAI` - The DeepSeek client
    * Takes in `job_description` of type `str` - The job description text
    * Takes in `stream` of type `bool` to indicate if it's a streaming call
    * Returns a `dict` object representing JSON response
    '''
    try:
        print_lg("Extracting skills from job description using DeepSeek...")
        
        # Using optimized DeepSeek prompt
        prompt = deepseek_extract_skills_prompt.format(job_description)
        messages = [{"role": "user", "content": prompt}]
        
        # DeepSeek API supports json_object response format
        custom_response_format = {"type": "json_object"}
        
        # Call DeepSeek completion
        result = deepseek_completion(
            client=client,
            messages=messages,
            response_format=custom_response_format,
            stream=stream
        )
        
        # Ensure the result is a dictionary
        if isinstance(result, str):
            result = convert_to_json(result)
            
        return result
    except Exception as e:
        critical_error_log("Error occurred while extracting skills with DeepSeek!", e)
        return {"error": str(e)}

def deepseek_answer_question(
    client: OpenAI, 
    question: str, options: list[str] | None = None, 
    question_type: Literal['text', 'textarea', 'single_select', 'multiple_select'] = 'text', 
    job_description: str = None, about_company: str = None, user_information_all: str = None,
    stream: bool = stream_output
) -> dict | ValueError:
    '''
    Function to answer a question using DeepSeek AI.
    * Takes in `client` of type `OpenAI` - The DeepSeek client
    * Takes in `question` of type `str` - The question to answer
    * Takes in `options` of type `list[str] | None` - Options for select questions
    * Takes in `question_type` - Type of question (text, textarea, single_select, multiple_select)
    * Takes in optional context parameters - job_description, about_company, user_information_all
    * Takes in `stream` of type `bool` - Whether to stream the output
    * Returns the AI's answer
    '''
    try:
        print_lg(f"Answering question using DeepSeek AI: {question}")
        
        # Prepare user information
        user_info = user_information_all or ""
        
        # Prepare prompt based on question type
        prompt = ai_answer_prompt.format(user_info, question)
        
        # Add options to the prompt if available
        if options and (question_type in ['single_select', 'multiple_select']):
            options_str = "OPTIONS:\n" + "\n".join([f"- {option}" for option in options])
            prompt += f"\n\n{options_str}"
            
            if question_type == 'single_select':
                prompt += "\n\nPlease select exactly ONE option from the list above."
            else:
                prompt += "\n\nYou may select MULTIPLE options from the list above if appropriate."
        
        # Add job details for context if available
        if job_description:
            prompt += f"\n\nJOB DESCRIPTION:\n{job_description}"
        
        if about_company:
            prompt += f"\n\nABOUT COMPANY:\n{about_company}"
        
        messages = [{"role": "user", "content": prompt}]
        
        # Call DeepSeek completion
        result = deepseek_completion(
            client=client,
            messages=messages,
            temperature=0.1,  # Slight randomness for more natural responses
            stream=stream
        )
        
        return result
    except Exception as e:
        critical_error_log("Error occurred while answering question with DeepSeek!", e)
        return {"error": str(e)}
##< 

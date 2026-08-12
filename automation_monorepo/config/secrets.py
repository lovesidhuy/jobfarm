"""Optional credentials and AI provider settings."""

import os
from pathlib import Path
from core.secret_manager import get_secret


###################################################### SECRET SETTINGS ######################################################

# Login credentials used by the validator and legacy auto-login code paths.
# With ixBrowser profiles the manual login state is reused, so these are only
# needed to pass validate_secrets() (min_length=5).  Set real values in
# Infisical (BOT_LOGIN_USERNAME / BOT_LOGIN_PASSWORD) or leave the defaults.
username = get_secret("BOT_LOGIN_USERNAME", "unused")   # Set BOT_LOGIN_USERNAME in Infisical
password = get_secret("BOT_LOGIN_PASSWORD", "unused")   # Set BOT_LOGIN_PASSWORD in Infisical


## Artificial Intelligence (Beta Not-Recommended)
# Use AI
use_AI = True                           # True or False, Note: True or False are case-sensitive
'''
Note: Set it as True only if you want to use AI, and If you either have a
1. Local LLM model running on your local machine, with it's APIs exposed. Example softwares to achieve it are:
    a. Ollama - https://ollama.com/
    b. llama.cpp - https://github.com/ggerganov/llama.cpp
    c. LM Studio - https://lmstudio.ai/ (Recommended)
    d. Jan - https://jan.ai/
2. OR you have a valid AI provider API key and are comfortable with any provider usage costs.
Check the provider's current pricing before enabling remote API calls.
'''

##> ------ Yang Li : MARKYangL - Feature ------
##> ------ Tim L : tulxoro - Refactor ------
# Select AI Provider
ai_provider = "deepseek"               # "openai" (also covers Ollama via OpenAI-compatible API), "deepseek", "gemini"
'''
Note: Select your AI provider.
* "openai" - OpenAI API (GPT models) OR OpenAi-compatible APIs (like Ollama)
* "deepseek" - DeepSeek API (DeepSeek models)
* "gemini" - Google Gemini API (Gemini models)
* For any other models, keep it as "openai" if it is compatible with OpenAI's api.
'''



# Your LLM url or other AI api url and port
llm_api_url = "http://127.0.0.1:11434/v1/"    # Ollama local API. Examples: "https://api.openai.com/v1/", "http://127.0.0.1:1234/v1/", "http://localhost:1234/v1/"
'''
Note: Don't forget to add / at the end of your url. You may not need this if you are using Gemini.
'''

# Your LLM API key or other AI API key 
llm_api_key = get_secret("LLM_API_KEY", "") or get_secret("DEEPSEEK_API_KEY", "not-needed")  # Set LLM_API_KEY in Infisical
'''
Note: Leave it empty as "" or "not-needed" if not needed. Else will result in error!
If you are using ollama, you MUST put "not-needed".
'''

# Your LLM model name or other AI model name
llm_model = "deepseek/deepseek-chat" # Full path required. Examples: "gpt-3.5-turbo", "gpt-4o", "llama-3.2-3b-instruct", "deepseek/deepseek-chat"

llm_spec = "openai"                # Examples: "openai", "openai-like", "openai-like-github", "openai-like-mistral"
'''
Note: Currently "openai", "deepseek", "gemini" and "openai-like" api endpoints are supported.
Most LLMs are compatible with openai, so keeping it as "openai-like" will work.
'''

from core.secret_manager import get_secret  # noqa: F811 (re-import guard for module-level access)
# Optional Groq gate for Indeed/Glassdoor job-fit checks.
# It is ignored unless use_groq_job_gate = True in config/settings.py.
# Leave this blank if you only want local rules and local Ollama.
groq_api_key = get_secret("GROQ_API_KEY", "")
groq_model = "llama-3.1-8b-instant"

# # Yor local embedding model name or other AI Embedding model name
# llm_embedding_model = "nomic-embed-text-v1.5"

# Do you want to stream AI output?
stream_output = False                    # Examples: True or False. (False is recommended for performance, True is recommended for user experience!)
'''
Set `stream_output = True` if you want to stream AI output or `stream_output = False` if not.
'''
##

#!/usr/bin/env bash
set -euo pipefail
set +x

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFISICAL_ENV="${INFISICAL_ENV:-prod}"
INFISICAL_PATH="${INFISICAL_PATH:-/}"

read_infisical_secret() {
  local name="$1"
  local line
  line="$(infisical secrets get "$name" --env "$INFISICAL_ENV" --path "$INFISICAL_PATH" --output dotenv --silent 2>/dev/null || true)"
  line="$(printf '%s\n' "$line" | awk -F= -v key="$name" '$1 == key {print; exit}')"
  if [[ -z "$line" ]]; then
    return 0
  fi
  printf '%s' "${line#*=}" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
}

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  OPENROUTER_API_KEY="$(read_infisical_secret OPENROUTER_API_KEY)"
  export OPENROUTER_API_KEY
fi

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  DEEPSEEK_API_KEY="$(read_infisical_secret DEEPSEEK_API_KEY)"
  export DEEPSEEK_API_KEY
fi

if [[ -z "${LLM_API_KEY:-}" ]]; then
  LLM_API_KEY="$(read_infisical_secret LLM_API_KEY)"
  export LLM_API_KEY
fi

if [[ -z "${OPENROUTER_API_KEY:-}" && -z "${DEEPSEEK_API_KEY:-}" && -z "${LLM_API_KEY:-}" ]]; then
  echo "OpenRouter/DeepSeek key missing. Add OPENROUTER_API_KEY, DEEPSEEK_API_KEY, or LLM_API_KEY in Infisical env '$INFISICAL_ENV' path '$INFISICAL_PATH'." >&2
  exit 1
fi

cd "$ROOT_DIR"
exec node hybrid_runner.js

#!/usr/bin/env bash
# Start self-hosted Firecrawl for the automation monorepo.
# API: http://127.0.0.1:3002
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FC_DIR="$ROOT/infra/firecrawl"

if [[ ! -f "$FC_DIR/docker-compose.yaml" ]]; then
  echo "[firecrawl] cloning upstream into infra/firecrawl..."
  git clone --depth 1 https://github.com/mendableai/firecrawl.git "$FC_DIR"
fi

# Ensure monorepo .env exists for Firecrawl
if [[ ! -f "$FC_DIR/.env" ]]; then
  if [[ -f "$FC_DIR/.env.example" ]]; then
    cp "$FC_DIR/.env.example" "$FC_DIR/.env"
  else
    cat >"$FC_DIR/.env" <<'EOF'
NUM_WORKERS_PER_QUEUE=4
PORT=3002
HOST=0.0.0.0
REDIS_URL=redis://redis:6379
REDIS_RATE_LIMIT_URL=redis://redis:6379
PLAYWRIGHT_MICROSERVICE_URL=http://playwright-service:3000/scrape
USE_DB_AUTHENTICATION=false
TEST_API_KEY=local
BULL_AUTH_KEY=local
CRAWL_CONCURRENT_REQUESTS=6
MAX_CONCURRENT_JOBS=3
BROWSER_POOL_SIZE=3
OPENAI_BASE_URL=https://api.akashml.com/v1
OPENAI_API_KEY=
MODEL_NAME=deepseek-ai/DeepSeek-V4-Flash
LOGGING_LEVEL=info
ALLOW_LOCAL_WEBHOOKS=true
BLOCK_MEDIA=true
EOF
  fi
fi

# Force auth off for monorepo clients
if grep -q '^USE_DB_AUTHENTICATION=' "$FC_DIR/.env"; then
  sed -i.bak 's/^USE_DB_AUTHENTICATION=.*/USE_DB_AUTHENTICATION=false/' "$FC_DIR/.env" && rm -f "$FC_DIR/.env.bak"
else
  echo 'USE_DB_AUTHENTICATION=false' >>"$FC_DIR/.env"
fi
if ! grep -q '^TEST_API_KEY=' "$FC_DIR/.env"; then
  echo 'TEST_API_KEY=local' >>"$FC_DIR/.env"
fi

# Sync Akash key from monorepo .env
if [[ -f "$ROOT/.env" ]]; then
  KEY="$(grep -E '^(AKASHML_API_KEY|BLUESMINDS_API_KEY)=' "$ROOT/.env" | head -1 | cut -d= -f2- | tr -d '"' | tr -d "'")"
  if [[ -n "${KEY:-}" ]]; then
    if grep -q '^OPENAI_API_KEY=' "$FC_DIR/.env"; then
      # escape & for sed
      ESC="${KEY//&/\\&}"
      sed -i.bak "s|^OPENAI_API_KEY=.*|OPENAI_API_KEY=${ESC}|" "$FC_DIR/.env" && rm -f "$FC_DIR/.env.bak"
    else
      echo "OPENAI_API_KEY=${KEY}" >>"$FC_DIR/.env"
    fi
    if grep -q '^OPENAI_BASE_URL=' "$FC_DIR/.env"; then
      sed -i.bak 's|^OPENAI_BASE_URL=.*|OPENAI_BASE_URL=https://api.akashml.com/v1|' "$FC_DIR/.env" && rm -f "$FC_DIR/.env.bak"
    else
      echo 'OPENAI_BASE_URL=https://api.akashml.com/v1' >>"$FC_DIR/.env"
    fi
    if grep -q '^MODEL_NAME=' "$FC_DIR/.env"; then
      sed -i.bak 's|^MODEL_NAME=.*|MODEL_NAME=deepseek-ai/DeepSeek-V4-Flash|' "$FC_DIR/.env" && rm -f "$FC_DIR/.env.bak"
    else
      echo 'MODEL_NAME=deepseek-ai/DeepSeek-V4-Flash' >>"$FC_DIR/.env"
    fi
  fi
fi

if ! docker info >/dev/null 2>&1; then
  echo "[firecrawl] Docker daemon is not running. Start Docker Desktop, then re-run:"
  echo "  $0"
  exit 1
fi

cd "$FC_DIR"
# Prefer pull of prebuilt images (override.yml). Fall back to local build only if pull fails.
echo "[firecrawl] pulling prebuilt images (preferred; avoids multi-GB local build)..."
if docker compose pull api playwright-service 2>&1 | tail -20; then
  echo "[firecrawl] pull ok — starting stack..."
else
  echo "[firecrawl] pull failed — building locally (needs free disk; 10–20 min)..."
  docker compose build api playwright-service nuq-postgres 2>&1 | tail -30
fi
docker compose up -d redis rabbitmq nuq-postgres playwright-service api 2>&1 | tail -40

echo "[firecrawl] waiting for API on :3002..."
for i in $(seq 1 90); do
  code=$(curl -sS -o /tmp/fc_ping.json -w '%{http_code}' -X POST "http://127.0.0.1:3002/v1/scrape" \
    -H 'Content-Type: application/json' \
    -H 'Authorization: Bearer local' \
    -d '{"url":"https://example.com","formats":["markdown"]}' 2>/dev/null || echo 000)
  if [[ "$code" == "200" ]]; then
    echo "[firecrawl] UP → http://127.0.0.1:3002 (HTTP $code)"
    echo "[firecrawl] monorepo env: FIRECRAWL_API_BASE=http://127.0.0.1:3002 FIRECRAWL_API_KEY=local"
    head -c 120 /tmp/fc_ping.json; echo
    exit 0
  fi
  # 401/500 means process is listening
  if [[ "$code" == "401" || "$code" == "500" || "$code" == "400" ]]; then
    echo "[firecrawl] API listening (HTTP $code) — check logs if scrape fails"
    docker compose ps
    exit 0
  fi
  sleep 4
done

echo "[firecrawl] timeout waiting for API — logs:"
docker compose logs --tail=40 api || true
exit 1

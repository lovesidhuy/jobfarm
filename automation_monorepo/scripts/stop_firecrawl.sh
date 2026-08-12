#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/infra/firecrawl"
docker compose down
echo "[firecrawl] stopped"

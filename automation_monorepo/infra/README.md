# Infra

## Firecrawl

**Default: Firecrawl cloud** (`https://api.firecrawl.dev`) with student/paid
`FIRECRAWL_API_KEY=fc-...` in Infisical + local `.env`.

Used **only** for ATS discovery search (`firecrawl_ats` fail-safe when Google
CDP is empty). Scrape is **off** by default (`FIRECRAWL_SCRAPE_ENABLED=0`) so
other modules do not burn credits.

| Item | Cloud (preferred) | Self-host (optional) |
|------|-------------------|----------------------|
| API | `https://api.firecrawl.dev` | `http://127.0.0.1:3002` |
| Key | `FIRECRAWL_API_KEY=fc-...` | `local` + `FIRECRAWL_SELF_HOST=1` |
| Start | none | `scripts/start_firecrawl.sh` (RAM-heavy) |

### Wire secrets

```bash
# local .env (gitignored)
FIRECRAWL_SELF_HOST=0
FIRECRAWL_API_BASE=https://api.firecrawl.dev
FIRECRAWL_API_KEY=fc-...
FIRECRAWL_ATS_ENABLED=1
FIRECRAWL_SCRAPE_ENABLED=0
GOOGLE_CDP_FIRECRAWL_FALLBACK=1

# Infisical
infisical secrets set FIRECRAWL_API_KEY=fc-... FIRECRAWL_API_BASE=https://api.firecrawl.dev \
  FIRECRAWL_SELF_HOST=0 --env=dev
```

### Consumers (must use shared client)

- `core.firecrawl_client` — search (+ optional scrape)
- `core.discovery.providers.firecrawl_ats` — Greenhouse/Lever SERP fail-safe
- `core.discovery.providers.google_cdp_provider` — Firecrawl then Tavily fallback

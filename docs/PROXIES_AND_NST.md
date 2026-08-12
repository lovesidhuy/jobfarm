# Proxy Ladders, Solvers & NSTbrowser Configuration

## Why Proxies & Anti-Detect Browsers?
Job platforms enforce aggressive rate limits, CAPTCHA challenges, and Cloudflare protections against automated sessions. JobFarm employs a multi-tier evasion strategy:

1. **Proxy Ladders**:
   - **Tier 1 (Direct / Datacenter)**: Fast ATS API polling and non-protected search discovery.
   - **Tier 2 (Sticky Residential Proxies)**: Form submission, Cloudflare clearance, and CAPTCHA bypass (Webshare, Proxy-Cheap). Webshare residential sticky endpoints are recommended for Cloudflare-heavy portals (Indeed, Glassdoor).

2. **CAPTCHA & Challenge Solvers**:
   - **CapSolver (`AntiCloudflareTask` / `AntiTurnstileTask` / `ReCaptchaV2EnterpriseTask`)**: High-reliability solver providing full `cf_clearance` cookie generation and Turnstile/reCAPTCHA resolution.
   - **CapMonster**: Fallback solver support.
   - **Playwright DOM / Humanizer Fallback**: Local heuristic clicks and event dispatches.

3. **NSTbrowser Integration & Multi-Slot Architecture**:
   - Multi-slot profile configuration allowing seamless switching between profile sets (Slot 1 / Slot 2) with isolated browser fingerprints, persistent session cookies, and dedicated proxy bindings.

## Configuration

### 1. Proxy Configuration
Set proxy credentials in `.env` or via your secret manager:
```bash
# Webshare Residential Proxies (recommended for Cloudflare-heavy portals)
WEBSHARE_PROXY_HOST=p.webshare.io
WEBSHARE_PROXY_PORT=80
WEBSHARE_PROXY_USERNAME=your_username
WEBSHARE_PROXY_PASSWORD=your_password
WEBSHARE_PROXY_URL=http://user:pass@p.webshare.io:80

# General Evasion Proxy URL (bound to solver tasks)
CAPSOLVER_PROXY_URL=http://user:pass@p.webshare.io:80
```

### 2. CAPTCHA Solver Settings
```bash
# Evasion Solver Mode
CAPTCHA_CLOUDFLARE_SOLVER=capsolver
USE_CAPSOLVER=1

# API Credentials
CAPSOLVER_API_KEY=your_capsolver_key
```

### 3. NST Multi-Slot Profile Switching
When switching between profile sets (e.g., Slot 1 to Slot 2):
1. Configure profile IDs in your runtime environment or secrets.
2. Ensure the NST agent is running with the matching API key and port.
3. Bind residential proxy endpoints to the target profile IDs.

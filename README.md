# JobsFarm 🚜

<p align="center">
  <strong>Open-source job discovery & application automation.</strong>
</p>

<div align="center">
  <h2>Supported Platforms & Operational Reality</h2>
  <p>JobsFarm supports 9+ major job boards and ATS platforms. Due to varying anti-bot measures (Cloudflare, CAPTCHAs) and lead volume, productivity varies by portal. <em>For maximum success, pair JobsFarm with high-quality leased rotational proxies, CapMonster/CapSolver, and NSTbrowser.</em></p>
</div>

<table>
  <thead>
    <tr>
      <th>Platform</th>
      <th>Volume & Productivity</th>
      <th>Known Evasion / Operational Quirks</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td><strong>Indeed</strong></td>
      <td>🟢 <strong>Highest Volume & Productivity</strong></td>
      <td>The primary engine of JobsFarm. Excellent conversion rates, but heavily dependent on strong residential proxies to avoid shadow-banning.</td>
    </tr>
    <tr>
      <td><strong>LinkedIn</strong></td>
      <td>🟢 <strong>High Volume (2nd Best)</strong></td>
      <td>Consistent high volume and very stable native DOM form-filling. </td>
    </tr>
    <tr>
      <td><strong>Workopolis</strong></td>
      <td>🟡 <strong>High Productivity, Low Volume</strong></td>
      <td>Very reliable automation and high conversion rates, but overall job listing volume is significantly lower.</td>
    </tr>
    <tr>
      <td><strong>Greenhouse</strong></td>
      <td>🟡 <strong>High Productivity</strong></td>
      <td>Great application productivity for direct ATS leads. Forms are stable and easy to traverse.</td>
    </tr>
    <tr>
      <td><strong>Glassdoor</strong></td>
      <td>🟠 <strong>Moderate</strong></td>
      <td>Prone to aggressive Cloudflare (CF) loops and occasional JobSpy HTTP lead-generation blocks. Requires CDP stealth scraping to maintain flow.</td>
    </tr>
    <tr>
      <td><strong>Job Bank Canada</strong></td>
      <td>🟠 <strong>Low Volume</strong></td>
      <td>Successfully automated, but yields a very low volume of relevant tech/professional leads.</td>
    </tr>
    <tr>
      <td><strong>BambooHR</strong></td>
      <td>🟠 <strong>Low Volume</strong></td>
      <td>Applications are highly successful when found, but there are very few leads using this ATS in the wild.</td>
    </tr>
    <tr>
      <td><strong>Lever</strong></td>
      <td>🔴 <strong>Bottlenecked</strong></td>
      <td>Prone to strict <strong>hCaptcha</strong> challenges on submission which can throttle throughput.</td>
    </tr>
    <tr>
      <td><strong>Ashby</strong></td>
      <td>🔴 <strong>Bottlenecked</strong></td>
      <td>Aggressive anti-spam thresholds. Will silently drop or block applications if it detects high-velocity bot-like submission patterns.</td>
    </tr>
  </tbody>
</table>

<br>

> 💡 **Infrastructure Tip:** To achieve the highest throughput on strict portals like Indeed, Glassdoor, and Lever, you **must** use persistent **NSTbrowser profiles** paired with leased, high-quality rotational residential proxies and a solver like **CapMonster or CapSolver**.

<p align="center">
  <a href="#key-features">Features</a> •
  <a href="#quickstart">Quickstart</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#multi-model-llm-gateway">AI Gateway</a> •
  <a href="#deployment-options">Deployment</a> •
  <a href="#onboarding">Onboarding</a> •
  <a href="#citations--acknowledgements">Citations</a> •
  <a href="#disclaimer">Disclaimer</a>
</p>

---

## Overview

**JobsFarm** is a high-throughput, hands-free job discovery and application automation engine designed to operate either locally on your workstation or as a multi-node cloud farm on AWS and GCP.

By combining browser automation (Playwright/Chrome CDP), anti-detection evasion (proxy ladders, Cloudflare bypass, CapMonster CAPTCHA resolution), an autonomous AI question-answering brain (curated 7,300+ line QA bank + multi-model LLM gateway), and automated ATS API scrapers, JobsFarm automates every phase of modern job hunting—from discovery to final submission.

```
┌─────────────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│  Multi-Layer    │ ──> │   Discovery Queue   │ ──> │   AI Question Brain  │
│  Discovery      │     │  (MongoDB / Leases) │     │ (Ollama/DeepSeek/GPT)│
└─────────────────┘     └─────────────────────┘     └──────────────────────┘
         │                         │                            │
         ▼                         ▼                            ▼
┌─────────────────┐     ┌─────────────────────┐     ┌──────────────────────┐
│ 9+ Job Portals  │     │   Anti-Detection    │     │   Verified Final     │
│ & ATS Adapters  │     │  (NST/Proxy Ladders)│     │     Application      │
└─────────────────┘     └─────────────────────┘     └──────────────────────┘
```

---

## Key Features

- **Multi-Portal & Direct ATS Automation**:
  - **Job Boards**: Indeed (`ca.indeed.com` & global), Glassdoor, LinkedIn Easy Apply, Workopolis, Job Bank Canada, Google Jobs.
  - **LinkedIn Automation Engine**: High-accuracy modal automation with in-DOM form analysis and stealth Playwright ghost-cursor execution for modal submission and bot-detection evasion.
  - **Discovery Aggregation**: Native `python-jobspy` pipeline for multi-source search querying and automated lease deduplication.
  - **Direct ATS Adapters**: Native In-DOM QA and form autofill engine supporting Greenhouse, Ashby, Lever, BambooHR.
- **Autonomous Multi-Model AI Answering**:
  - **Local & Offline**: Ollama (Llama 3.2, DeepSeek-R1, Mistral).
  - **Cloud Providers**: DeepSeek (`deepseek-chat`), OpenAI (`gpt-4o`, `gpt-4o-mini`), Google Gemini, Groq (`llama-3.3-70b`), OpenRouter, AkashML.
  - **Answer Brain**: 7,300+ pre-curated screening answers + STAR methodology generator for complex behavioral questions.
- **Anti-Detection & Evasion Engine**:
  - **Captcha & Cloudflare Resolution**: CapSolver (`AntiCloudflareTask` cf_clearance, Turnstile, reCAPTCHA v2 / Enterprise) and CapMonster fallback.
  - **Browser Humanizer**: Realistic mouse curvature, typing cadence, and dynamic DOM event simulation.
  - **Proxy Ladders**: Automatic fallback rotation across datacenter and residential proxy pools (Webshare, Proxy-Cheap).
  - **Anti-Detect Browser Support**: Native integration with NSTbrowser dual-profile sync and local Chrome persistent sessions.
- **Dual Deployment Architecture**:
  - **Local Farm**: One-command launch with `docker-compose.local.yml` and local supervisor.
  - **Cloud Farm**: Full Infrastructure-as-Code with Terraform (AWS & GCP), Packer golden AMIs, and Infisical secret management.
- **Observability & Telemetry**:
  - MongoDB application history, lease locks, event logging, Google Sheets & Google Drive live reporting, and Telegram real-time alerts.

---

## Quickstart

### Prerequisites
- Python 3.10+ (Python 3.11 or 3.12 recommended)
- Google Chrome or Chromium
- Docker & Docker Compose (for local MongoDB queue)

### 1. Clone and Install
```bash
git clone https://github.com/JobsFarm/JobsFarm.git
cd JobsFarm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
playwright install chromium
```

### 2. Interactive Onboarding
Run the interactive setup assistant to initialize your profile and verify your configuration:
```bash
python scripts/onboard.py --check
```
To configure candidate details:
```bash
python scripts/onboard.py --init-profile
```
> See [docs/AI_ONBOARDING_AND_PII_GUIDE.md](docs/AI_ONBOARDING_AND_PII_GUIDE.md) for the complete AI and candidate profile verification runbook.

### 3. Launch Local Infrastructure
Start the local MongoDB job queue and metrics store:
```bash
docker-compose -f docker-compose.local.yml up -d mongodb
```

### 4. Run the Farm
Launch autonomous discovery and application execution:
```bash
# Autonomous Multi-Portal Supervisor
python automation_monorepo/supervisor.py

# Or run discovery / application cycles via unified CLI:
jobbots discover --once
jobbots apply --once

# Or run a single portal target:
jobbots bot indeed_it
```

---

## Multi-Model LLM Gateway

Configure your preferred LLM provider in `.env` or during onboarding:

| Provider | `LLM_PROVIDER` | Free / Paid | Recommended Model |
|---|---|---|---|
| **Ollama (Local)** | `ollama` | **100% Free** | `llama3.2`, `deepseek-r1` |
| **DeepSeek** | `deepseek` | Paid (Low cost) | `deepseek-chat` |
| **OpenAI** | `openai` | Paid | `gpt-4o-mini`, `gpt-4o` |
| **Groq** | `groq` | Free tier / Paid | `llama-3.3-70b-versatile` |
| **Google Gemini** | `gemini` | Free tier / Paid | `gemini-1.5-flash` |
| **AkashML / OpenRouter**| `akashml` / `openrouter` | Free tier / Paid | `deepseek-ai/DeepSeek-V4-Flash` |

All providers share the exact same battle-tested question-answering prompt schema and skill extraction policies.

---

## Deployment Options

### 1. Local Workstation Mode
Run directly on macOS, Linux, or Windows with persistent Chrome profiles:
```bash
python scripts/onboard.py --status
python automation_monorepo/supervisor.py
```

### 2. Cloud Farm (AWS / GCP)
Deploy a headless distributed worker fleet on AWS EC2 or GCP Compute Engine:
```bash
# Configure cloud credentials and deploy
./deploy.sh --provider=aws
```
See [docs/CLOUD_DEPLOYMENT.md](docs/CLOUD_DEPLOYMENT.md) for full cloud runbooks.

---

## Repository Structure

```
JobsFarm/
├── automation_monorepo/
│   ├── bots/                # Entrypoints for 9+ portal bots
│   ├── config/              # Profile configs (IT and General templates)
│   ├── scripts/             # Harvest, sync, and application workers
│   └── tests/               # Unit and integration test suite
├── data/
│   └── qa_banks/            # Curated 7,300+ line answer banks
├── docs/                    # Architectural and deployment guides
├── infra/
│   ├── ansible/             # Host configuration playbooks
│   ├── docker/              # Worker containers
│   ├── packer/              # Golden AMI templates
│   └── terraform/           # AWS and GCP cloud farm IaC
├── jobbots/                 # Canonical core runtime package
│   ├── app/                 # CLI, orchestrator, pipeline
│   ├── core/                # Evasion, ATS, discovery, LLM gateway
│   └── integrations/        # Portal and cloud integrations
├── profiles/
│   ├── example/             # Template profile manifests
│   └── resumes/             # Sample PDF resumes & templates
└── scripts/
    └── onboard.py           # Interactive onboarding assistant
```

---

## Citations & Acknowledgements

JobsFarm builds upon and adapts insights from the open-source automation community:
- **Auto_Job_Applier** by **Sai Vignesh Golla** and contributors — inspiration and foundational concepts for LinkedIn Easy Apply automation, form interaction, and Chrome extension integration.
- **Custom Multi-Portal Architecture** — proprietary ground-up automation engines for Indeed (`ca.indeed.com` & global), Glassdoor, Workopolis, Job Bank Canada, and direct ATS platforms (Greenhouse, Ashby, Lever, BambooHR).
- **python-jobspy** — high-efficiency scraping and multi-board job discovery across LinkedIn, Indeed, Glassdoor, and Google Jobs.
- **Playwright & Puppeteer** community projects — robust CDP session hooks, anti-detection stealth harnesses, and ghost cursor emulation.

All adapted components have been refactored, hardened for production multi-portal farming, and unified under the AGPL-3.0 license.

---

## Ethical Use & Platform Disclaimer

> [!WARNING]
> **Terms of Service Notice**: Automated application submission may violate the Terms of Service of specific job platforms. Users are solely responsible for ensuring compliance with applicable platform policies, rate limits, and legal requirements.
> 
> **Recommendations**:
> - Use reasonable application limits (`switch_number: 30-50`) to avoid aggressive polling.
> - Verify that all AI-generated answers truthfully represent your genuine qualifications.
> - Enable review pauses (`pause_before_submit = True`) during initial setup to inspect filled forms.

---

## License

This project is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)**. See the [LICENSE](LICENSE) file for details.

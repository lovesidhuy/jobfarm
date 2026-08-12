# AI Assistant Onboarding & PII Configuration Guide 🤖📋

This guide provides an automated onboarding checklist and instructions for AI agents and human users to ensure a new operator is **100% correctly onboarded** for high-throughput, maximum-accuracy job application submissions while keeping their personal identifiable information (PII) safe, private, and cleanly isolated.

---

## 1. Architecture: PII Isolation vs. Public Code

To maintain strict open-source safety while submitting authentic applications with full candidate credentials:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           PRIVATE RUNTIME                               │
│                                                                         │
│  .env (local secrets)              profiles/<yourname>/ (user data)    │
│  ├── PII: Name, Email, Phone       ├── profile.yaml                    │
│  ├── Target Geo & Postal Code      ├── searches.yaml                   │
│  ├── LLM Keys / CapSolver Token    └── resumes/ (PDF resumes & CL)     │
│  └── Sticky Proxy Credentials                                           │
└─────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼ injected via env / manifests
┌─────────────────────────────────────────────────────────────────────────┐
│                       PUBLIC / GENERIC CODEBASE                         │
│                                                                         │
│  jobbots/                          automation_monorepo/                 │
│  ├── ats/ (DOM Autofill Engine)    ├── bots/ (9+ Portal Automations)    │
│  ├── evasion/ (CapSolver/CF)       ├── config/ (Base Template Schema)   │
│  └── llm_backend/ (AI Gateway)     └── scripts/ (Harvest & Workers)     │
└─────────────────────────────────────────────────────────────────────────┘
```

> [!IMPORTANT]
> **Zero PII in Public Tracking**: Personal information lives **only** in local `.env`, `profiles/<yourname>/`, and the local secure secret manager. Never hardcode real names, personal phone numbers, or private emails into git-tracked template files in `jobbots/` or `automation_monorepo/config/`.

---

## 2. Onboarding Pre-Flight Checklist for AI Agents

When an AI assistant onboards a new user, execute this 6-step checklist to ensure high application effectiveness:

```mermaid
graph TD
    A[1. Initialize Environment] --> B[2. Configure Candidate PII & Resumes]
    B --> C[3. Verify AI / LLM Gateway]
    C --> D[4. Configure Evasion & Proxies]
    D --> E[5. Authenticate Portal Sessions]
    E --> F[6. Run Sanity Dry-Run & Queue Check]
```

---

### Step 1: Environment & Secrets Setup
1. Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
2. Set core environment variables in `.env`:
   - `JOB_PROFILE=it` (or `general`)
   - `TARGET_LOCATION="Vancouver, BC"`
   - `TARGET_REGION=METRO_VAN`
   - `MONGODB_URI=mongodb://localhost:27017`
   - `JOBBOTS_MONGO_DATABASE=jobbots`

---

### Step 2: Candidate PII & Resume Configuration
For maximum application success rate, ensure the candidate profile is comprehensive.

1. **Create Profile Directory**:
   ```bash
   cp -r profiles/example profiles/my_profile
   ```
2. **Configure Candidate PII in `profiles/my_profile/`**:
   | Field | Description | Example / Best Practice |
   |---|---|---|
   | `first_name` & `last_name` | Legal name | `Jane`, `Doe` |
   | `email` | Active inbox for employer replies | `jane.doe@gmail.com` |
   | `phone` | Phone with country code | `+1-604-555-0199` |
   | `current_city` | Current city in target region | `Vancouver` |
   | `state` / `province` | State or Province | `BC` |
   | `zipcode` / `postal` | Real postal code for distance match | `V6B 1A1` |
   | `country` | Country of residence | `Canada` |
   | `linkedin` | Public LinkedIn profile URL | `https://www.linkedin.com/in/janedoe/` |
   | `github` / `portfolio` | Portfolio or GitHub URL | `https://github.com/janedoe` |
   | `work_authorization` | Legal status in country | `Canadian Citizen / PR / Open Work Permit` |
   | `sponsorship_required` | Needs visa sponsorship | `No` |
   | `years_of_experience` | Years in target industry | `3` |
   | `desired_salary` | Target baseline salary | `75000` |
   | `notice_period` | Availability to start | `Immediate` or `2 weeks` |

3. **Place Resume Files**:
   Ensure formatted PDF resumes are placed in `profiles/resumes/` (or your profile folder):
   - `profiles/resumes/sample_resume_it.pdf`
   - `profiles/resumes/sample_resume_general.pdf`

---

### Step 3: Verify AI / LLM Gateway
The AI question-answering brain dynamically resolves complex screening questions (e.g., behavioral STAR questions, skill assessments).

1. **Option A: Free Offline Local AI (Ollama)**
   ```bash
   # Install & start Ollama
   ollama serve
   ollama pull llama3.2
   ```
   Set in `.env`:
   ```bash
   LLM_PROVIDER=ollama
   OLLAMA_BASE_URL=http://localhost:11434
   OLLAMA_MODEL=llama3.2
   ```

2. **Option B: Cloud AI (DeepSeek, OpenAI, Groq, Gemini)**
   Set in `.env`:
   ```bash
   LLM_PROVIDER=deepseek
   DEEPSEEK_API_KEY=your_key_here
   ```

3. **Verify AI Connection**:
   ```bash
   python scripts/onboard.py --test-llm
   ```

---

### Step 4: Evasion, Solvers & Proxy Configuration
Job platforms (Indeed, Glassdoor, Workopolis) require residential proxies and challenge solvers for uninterrupted runs.

1. **Configure CapSolver (Primary Cloudflare & CAPTCHA Solver)**:
   ```bash
   CAPTCHA_CLOUDFLARE_SOLVER=capsolver
   USE_CAPSOLVER=1
   CAPSOLVER_API_KEY=your_capsolver_api_key
   ```

2. **Configure Sticky Residential Proxies (Webshare / Proxy-Cheap)**:
   ```bash
   BYPASS_PROXY=0
   WEBSHARE_PROXY_HOST=p.webshare.io
   WEBSHARE_PROXY_PORT=80
   WEBSHARE_PROXY_USERNAME=your_username
   WEBSHARE_PROXY_PASSWORD=your_password
   WEBSHARE_PROXY_URL=http://user:pass@p.webshare.io:80
   CAPSOLVER_PROXY_URL=http://user:pass@p.webshare.io:80
   ```

3. **Test Proxy Egress**:
   ```bash
   python scripts/onboard.py --test-proxy
   ```

---

### Step 5: Authenticate Portal Sessions
To apply autonomously, each portal requires an authenticated browser session.

1. **Check Session Status**:
   ```bash
   python scripts/onboard.py --status
   ```
2. **Log into Portal Accounts**:
   Launch individual portal browsers to log in and save cookies:
   - **Indeed**: `python automation_monorepo/scripts/run_indeed_it_chrome.py`
   - **Workopolis**: `python automation_monorepo/scripts/run_workopolis_it_chrome.py`
   - **Glassdoor**: `python automation_monorepo/scripts/run_glassdoor_it_chrome.py`
   - **LinkedIn**: Log into your LinkedIn account in your dedicated Chrome profile.

---

### Step 6: Start Local Queue & Launch Farm

1. **Start MongoDB Queue**:
   ```bash
   docker-compose -f docker-compose.local.yml up -d mongodb
   ```

2. **Run One-Shot Health Check / Smoke Test**:
   ```bash
   pytest automation_monorepo/tests -k "test_entrypoints_smoke or test_dom_autofill"
   ```

3. **Start Autonomous Farm Supervisor**:
   ```bash
   python automation_monorepo/supervisor.py
   ```

---

## 3. High-Effectiveness Form-Fill Verification Table

When the autofill engine encounters application pages, verify that these standard form interactions succeed:

| Component | Standard Behavior | Failure Mode & Recovery |
|---|---|---|
| **Text Inputs** | Injected with React synthetic event dispatch (`input`, `change`, `blur`) | If React state doesn't update, `INJECT_VALUES_JS` triggers `_valueTracker.setValue`. |
| **Radio / Checkboxes** | Matches exact semantic label or AI synonym | If ambiguous, resolves against profile baseline or defaults to affirmative work eligibility. |
| **Combobox / Typeahead** | Types query, waits for popup, and clicks matching dropdown option | Falls back to hidden input binding (e.g. Lever location picker). |
| **Resume Upload** | Attaches matching PDF from `profiles/resumes/` | Disambiguates between resume and cover letter upload slots via local container text scoping. |
| **CAPTCHA / Turnstile** | Solves pre-submit at step 8c via CapSolver `AntiCloudflareTask` / `AntiTurnstileTask` | If solver token expires, retries before final click. |

---

## 4. Summary of Verification Commands

```bash
# 1. Check all portal login states
python scripts/onboard.py --status

# 2. Test LLM question answering gateway
python scripts/onboard.py --test-llm

# 3. Test residential proxy tunnel
python scripts/onboard.py --test-proxy

# 4. Verify full automated test suite
pytest automation_monorepo/tests
```

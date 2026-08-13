# JobFarm Quickstart Guide

This guide walks you through setting up JobFarm from scratch in under 5 minutes.

## 1. Installation
```bash
git clone https://github.com/jobfarm/jobfarm.git
cd jobfarm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
playwright install chromium
```

> **Note for LinkedIn Hybrid Runner**: If using LinkedIn Easy Apply automation:
> ```bash
> (cd legacy/linkedin-ai-auto-apply-source && npm install)
> ```

## 2. Profile Setup & Environment
Run the interactive setup wizard to configure your candidate profile, LLM provider, and environment:
```bash
python scripts/onboard.py --init-profile
```
Or manually configure `.env`:
```bash
cp .env.example .env
```
Drop your PDF resume into `profiles/resumes/sample_resume_it.pdf` (or set `RESUME_PATH` in `.env`).

> For complete candidate data configuration and PII privacy safeguards, see [AI_ONBOARDING_AND_PII_GUIDE.md](AI_ONBOARDING_AND_PII_GUIDE.md).

## 3. Verify Environment Health & Connectivity
Run the pre-flight checks:
```bash
python scripts/onboard.py --check
```
Verify individual subsystems as needed:
- Check environment: `jobbots doctor --quick`
- Test AI gateway: `python scripts/onboard.py --test-llm`
- Test database: `python scripts/onboard.py --test-db`
- Test network: `python scripts/onboard.py --test-proxy`

## 4. Authenticate Portal Sessions
Launch visible Chrome to log into your job portals:
```bash
python scripts/onboard.py --login indeed
python scripts/onboard.py --login linkedin
```

## 5. Start Local Farm
Start the local MongoDB queue:
```bash
docker-compose -f docker-compose.local.yml up -d mongodb
```
Launch discovery and applications:
```bash
# Run one discovery cycle
jobbots discover --once

# Run one application cycle
jobbots apply --once

# Run full autonomous supervisor
python automation_monorepo/supervisor.py
```


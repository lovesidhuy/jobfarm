# JobFarm Quickstart Guide

This guide walks you through setting up JobFarm from scratch in under 5 minutes.

## 1. Installation
```bash
git clone https://github.com/jobfarm/jobfarm.git
cd jobfarm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 2. Profile Setup & Environment
```bash
cp .env.example .env
cp -r profiles/example profiles/my_profile
```
Drop your PDF resume into `profiles/resumes/sample_resume_it.pdf`.

> For complete candidate data configuration and PII privacy safeguards, see [AI_ONBOARDING_AND_PII_GUIDE.md](AI_ONBOARDING_AND_PII_GUIDE.md).

## 3. Run Onboarding
Launch the interactive setup assistant:
```bash
python scripts/onboard.py
```
Follow the prompts to verify your LLM gateway (`--test-llm`), proxy (`--test-proxy`), and check session logins (`--status`).

## 4. Start Local Farm
```bash
docker-compose -f docker-compose.local.yml up -d mongodb
python automation_monorepo/supervisor.py
```

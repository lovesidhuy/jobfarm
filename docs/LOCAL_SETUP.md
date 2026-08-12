# Local Setup & Testing Guide

## Architecture of Local Farm
When running locally, JobFarm uses:
1. **Local Chrome / NSTbrowser**: Opens browser instances with persistent cookies.
2. **Local MongoDB**: Runs in Docker (`localhost:27017`) to track claimed leases and prevent duplicate applications.
3. **Local or Cloud LLM**: Answers screening questions via `http://localhost:11434/v1` (Ollama) or cloud endpoints.

## Step-by-Step Local Setup

### 1. Start MongoDB
```bash
docker-compose -f docker-compose.local.yml up -d
```

### 2. Verify Session Status
```bash
python scripts/onboard.py --status
```

### 3. Run a Single Portal
```bash
python -m jobbots.app.cli run --portal indeed --profile it
```

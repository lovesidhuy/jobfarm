# JobFarm Documentation 📚

Welcome to the **JobFarm** open-source documentation. Below is an index of all guides, architecture references, and setup instructions.

---

## 🚀 Getting Started
* **[QUICKSTART.md](QUICKSTART.md)**: 5-minute setup guide to clone, configure, and launch JobFarm.
* **[LOCAL_SETUP.md](LOCAL_SETUP.md)**: Detailed local workstation and MongoDB Docker environment configuration.
* **[AI_ONBOARDING_AND_PII_GUIDE.md](AI_ONBOARDING_AND_PII_GUIDE.md)**: Complete candidate profile schema, QA answer brain configuration, and personal privacy (PII) safeguards.

---

## 🏗️ Architecture & Core Systems
* **[two-stage-job-pipeline.md](two-stage-job-pipeline.md)**: Authoritative architectural overview of Phase I Discovery (`planner.py`) and Phase II Queue Application (`application_worker.py`).
* **[PROXIES_AND_NST.md](PROXIES_AND_NST.md)**: Anti-detection evasion, proxy ladder tiers (Webshare, Proxy-Cheap), CapSolver/CapMonster settings, and NSTbrowser persistent session integration.

---

## ☁️ Cloud & Fleet Deployment
* **[CLOUD_DEPLOYMENT.md](CLOUD_DEPLOYMENT.md)**: Cloud worker fleet deployment via Terraform (AWS & GCP) and Packer golden AMIs.
* **[GCP_VM_HOSTING_HYBRID.md](GCP_VM_HOSTING_HYBRID.md)**: Multi-cloud hybrid VM hosting architecture and CLI integration guide.

---

## 🧪 Quality & Testing
* **[LOCAL_TESTING_GUIDE.md](LOCAL_TESTING_GUIDE.md)**: Unit, integration, and dry-run validation workflows for local development.
* **[proof/README.md](proof/README.md)**: Redacted performance ledger, application proof samples, and metrics verification.

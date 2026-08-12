# Changelog

All notable changes to **JobFarm** will be documented in this file.

## [0.2.0] - 2026-08-12
### Added
- **Native In-DOM QA & Form Autofill Engine (`DOMAutofillEngine`)**:
  - High-performance, 100% native in-DOM field extraction traversing multi-frame trees and piercing open shadow DOM roots.
  - Native React 16-19 / Vue synthetic event dispatchers (`input`, `change`, `blur`, `tracker.setValue`) for rock-solid framework state updates without external server requirements.
  - Generic profile answer resolver with fallback to the curated QA bank and DeepSeek LLM.
- **CapSolver Evasion & Challenge Resolution**:
  - Integrated `jobbots/core/evasion/_capsolver.py` for Cloudflare Turnstile, reCAPTCHA v2 / Enterprise, and hCaptcha.
  - Seamless fallback cascade: CapSolver → CapMonster → local/manual solvers.
- **Declarative ATS Registry (`PlatformSpec`)**:
  - Modular registration system allowing future ATS platforms to declare host suffixes, aliases, and DOM markers in a single place.
- **Title-Level Exclusive Geo Filtering**:
  - Added `title_exclusive_out_of_area` in `location_policy.py` to prevent SERP search-centre tags from leaking out-of-area jobs into application queues.

### Improved
- **Indeed & Workopolis Session Stability**:
  - Hydrated session cookies by navigating to homepages first, eliminating `/account/login` dead ends and unnecessary manual login wait stalls in autonomous mode.
  - Added same-window SmartApply detection and improved job description extraction with Crawl4AI fallback.
- **BambooHR & Lever ATS Adapters**:
  - Added local upload slot scoping in BambooHR to prevent resume and cover letter file input cross-contamination.
  - Enhanced Lever hidden location input bindings.
- **Job Bank Discovery**:
  - Configurable keyword sets (`_search_terms()` and `_core_terms()`) to prevent typeahead exhaustion.

## [0.1.0] - 2026-08-10 — Initial Open-Source Release
### Added
- **Multi-Portal Automation Engine**: 9+ portal adapters including Indeed, Glassdoor, LinkedIn, Workopolis, Job Bank Canada, Google Jobs, Greenhouse, Ashby, Lever, BambooHR.
- **Multi-Model LLM Gateway**: Support for Ollama (local offline), DeepSeek, OpenAI GPT-4o, Google Gemini, Groq, OpenRouter, and AkashML.
- **Autonomous Answer Brain**: Curated 7,300+ line QA answer bank and STAR situation-task-action-result response generator.
- **Anti-Detection & Evasion**: CapMonster solver integration, Cloudflare Turnstile bypass, browser humanizer, and proxy ladder rotation (Webshare, Proxy-Cheap).
- **Interactive Onboarding Assistant**: CLI setup wizard (`scripts/onboard.py`) for profile creation, LLM validation, proxy configuration, and portal session persistence.
- **Dual Deployment Architecture**:
  - Local mode (macOS / Linux / Windows with Chrome or NSTbrowser + Docker Compose).
  - Cloud Farm mode (AWS / GCP Terraform IaC + Packer golden AMI + Infisical secrets).
- **Comprehensive Open-Source Documentation**: Full guides for Quickstart, Architecture, Local Testing, Cloud Farm, and Proxy Configuration.

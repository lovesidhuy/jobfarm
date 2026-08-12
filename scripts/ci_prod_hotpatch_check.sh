#!/usr/bin/env bash
# CI pin checks for production hotpatches. Fail with explicit names.
# After the Phase-2 refactor, canonical core lives in jobbots/core/ with a
# shim at automation_monorepo/core/. Grep both so either layout passes.
set -eo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
fail=0
check() {
  local desc="$1"
  shift
  if ! "$@"; then
    echo "::error::hotpatch failed: ${desc}"
    echo "  cmd: $*"
    fail=1
  fi
}

# Grep pattern in jobbots/core first, then automation_monorepo/core shim tree.
core_grep() {
  local pattern="$1"
  local rel="$2"  # path under core/, e.g. secret_manager.py
  if grep -qE "$pattern" "jobbots/core/${rel}" 2>/dev/null; then
    return 0
  fi
  if grep -qE "$pattern" "automation_monorepo/core/${rel}" 2>/dev/null; then
    return 0
  fi
  return 1
}

core_test() {
  local rel="$1"
  [[ -f "jobbots/core/${rel}" || -f "automation_monorepo/core/${rel}" ]]
}

check "linkedin_sole_worker" grep -q linkedin_sole_worker automation_monorepo/scripts/application_worker.py
check "LINKEDIN_JOB_PROFILE" grep -q LINKEDIN_JOB_PROFILE automation_monorepo/scripts/application_worker.py
check "is_cf_heavy_portal" core_grep is_cf_heavy_portal secret_manager.py
check "stamp_cf_heavy_proxy_env" core_grep stamp_cf_heavy_proxy_env secret_manager.py
check "PROXY_CHEAP_URL" core_grep PROXY_CHEAP_URL secret_manager.py
check "WEBSHARE_PROXY_URL secret" core_grep WEBSHARE_PROXY_URL secret_manager.py
check "get_browser_proxy_url" core_grep get_browser_proxy_url browser/open_chrome.py
check "Webshare static preferred" core_grep "Webshare static preferred" browser/open_chrome.py
check "Proxy-Cheap preferred for CF-heavy" core_grep "Proxy-Cheap preferred for CF-heavy" browser/open_chrome.py
check "CF-heavy portal" grep -q "CF-heavy portal=" automation_monorepo/scripts/application_worker.py
check "WEBSHARE bootstrap" core_grep WEBSHARE_PROXY_URL bootstrap_bot_launch.py
check "WEBSHARE hybrid" grep -q WEBSHARE_PROXY_URL legacy/linkedin-ai-auto-apply-source/hybrid_runner.js
check "webshare-primary" grep -q webshare-primary legacy/linkedin-ai-auto-apply-source/hybrid_runner.js
check "puppeteer-core 21.11.0" grep -q '"puppeteer-core": "21.11.0"' legacy/linkedin-ai-auto-apply-source/package.json
check "puppeteer 21.11.0" grep -q '"puppeteer": "21.11.0"' legacy/linkedin-ai-auto-apply-source/package.json
check "pickConsentOption" grep -q pickConsentOption legacy/linkedin-ai-auto-apply-source/hybrid_heuristics.js
check "_is_yes_no_options" core_grep _is_yes_no_options shared_modules/form_answers.py
check "form AI markers" bash -c 'grep -qE "forcing AI pick|llm_optmap|_profile_dossier" jobbots/core/shared_modules/form_answers.py legacy/linkedin-ai-auto-apply-source/hybrid_runner.js 2>/dev/null || grep -qE "forcing AI pick|llm_optmap|_profile_dossier" automation_monorepo/core/shared_modules/form_answers.py legacy/linkedin-ai-auto-apply-source/hybrid_runner.js'
check "auth_without_sponsor" core_grep 'auth_without_sponsor|without the need for visa sponsorship' shared_modules/form_answers.py
check "auth_without_sponsorship policy" core_grep auth_without_sponsorship llm_backend/answer_policy.py
check "without.0,40 hybrid" grep -Fq 'without.{0,40}' legacy/linkedin-ai-auto-apply-source/hybrid_heuristics.js
check "sponsorship hybrid" grep -q sponsorship legacy/linkedin-ai-auto-apply-source/hybrid_heuristics.js
check "privacy consent" core_grep 'privacy notice|I consent' shared_modules/form_answers.py
check "question_unresolved" core_grep question_unresolved shared_modules/indeed/questions.py
check "cover_letter_uploaded" core_grep cover_letter_uploaded shared_modules/indeed/smartapply.py
check "blank resume picker" core_grep "switched from blank resume tab to populated picker" shared_modules/indeed/form_steps.py
check "ATS_GREENHOUSE_LEVER_EMAIL" core_grep ATS_GREENHOUSE_LEVER_EMAIL ats/engine.py
check "ATS_GREENHOUSE_LEVER_IMAP" core_grep ATS_GREENHOUSE_LEVER_IMAP_APP_PASSWORD ats/mixins/verification.py
check "identity_lock" core_grep 'pronoun_label_is_male|identity_lock' shared_modules/indeed/questions.py
check "requeue for retry" core_grep "requeue for retry" shared_modules/indeed/smartapply.py
check "CAPTCHA still visible" core_grep "CAPTCHA still visible after SmartApply exit" shared_modules/indeed/apply.py
check "nst cdp port proxy" core_grep _ensure_nst_cdp_port_proxy browser/open_chrome.py
check "DISCOVERY_BATCH_AI_CHUNK code" core_grep DISCOVERY_BATCH_AI_CHUNK shared_modules/indeed/gates.py
check "DEEPSEEK_TIMEOUT_SECONDS code" core_grep DEEPSEEK_TIMEOUT_SECONDS llm_backend/ai/deepseekConnections.py
check "trace_generation" core_grep trace_generation llm_backend/ai/deepseekConnections.py
check "langfuse pin" grep -q 'langfuse>=4.4,<5' requirements.txt
check "LANGFUSE enabled" grep -q LANGFUSE_TRACING_ENABLED=1 packer/linux/runtime-prod-overrides.conf
check "jobbank in overrides" grep -q jobbank packer/linux/runtime-prod-overrides.conf
check "APPLICATION_WORKERS 3or4" grep -qE 'JOBBOTS_APPLICATION_WORKERS=[34]' packer/linux/runtime-prod-overrides.conf
check "NSTBROWSER_LAUNCH_RETRIES" grep -q NSTBROWSER_LAUNCH_RETRIES packer/linux/runtime-prod-overrides.conf
check "ACTIVE_SLOT 1|2|auto" grep -qE '^NSTBROWSER_ACTIVE_SLOT=(1|2|auto)$' packer/linux/runtime-prod-overrides.conf
check "no ACTIVE_SLOT hardcode" bash -c "! grep -R --include='*.service' -q '^Environment=NSTBROWSER_ACTIVE_SLOT=' packer/linux/systemd/"
check "PROFILE_ID resolve" core_grep NSTBROWSER_PROFILE_ID_ browser/nst_accounts.py
check "PROFILE_ID_2" core_grep PROFILE_ID_2_ browser/nst_accounts.py
check "ALLOW_PROFILE_ID_ROTATION" grep -q NSTBROWSER_ALLOW_PROFILE_ID_ROTATION scripts/bootstrap_stock_worker.sh
check "APPLY_PORTALS set" grep -q '^JOBBOTS_APPLY_PORTALS=indeed,linkedin,glassdoor,workopolis,jobbank,google,greenhouse,lever,ashby,bamboohr$' packer/linux/runtime-prod-overrides.conf
check "jobbank direct official" grep -q '^JOBBANK_DIRECT_APPLY_ENABLED=1$' packer/linux/runtime-prod-overrides.conf
check "jobbank email retired" grep -q '^JOBBOTS_JOBBANK_EMAIL_APPLY_RETIRED=1$' packer/linux/runtime-prod-overrides.conf
check "jobbank Metro Van SERP" grep -q '^JOBBOTS_JOBBANK_LOCATION=Vancouver, BC$' packer/linux/runtime-prod-overrides.conf
check "GENERAL_APPLY indeed" grep -q '^JOBBOTS_GENERAL_APPLY_PORTALS=indeed$' packer/linux/runtime-prod-overrides.conf
check "ROTATE_PROXY=0" grep -q '^NSTBROWSER_ROTATE_PROXY=0$' packer/linux/runtime-prod-overrides.conf
check "Metro Vancouver only" grep -q '^METRO_VANCOUVER_ONLY=1$' packer/linux/runtime-prod-overrides.conf
check "ATS board budget" grep -q '^ATS_BOARD_API_MAX_SLUGS_PER_PLATFORM=250$' packer/linux/runtime-prod-overrides.conf
check "Feashliaa seed importer" test -f automation_monorepo/scripts/import_feashliaa_seeds.py
check "Feashliaa seed module" core_test discovery/external_seeds.py
check "final Metro queue guard" grep -q _is_metro_vancouver_queue_job automation_monorepo/scripts/application_worker.py
check "GLASSDOOR hybrid" grep -q '^GLASSDOOR_DISCOVERY_PROVIDER=hybrid$' packer/linux/runtime-prod-overrides.conf
check "supervisor n>=5" grep -q 'if n >= 5' automation_monorepo/supervisor.py
check "supervisor workopolis" grep -q workopolis automation_monorepo/supervisor.py
check "phase A_fast" grep -q phase=A_fast packer/linux/bin/jobbots-discover-ats-it
check "ats providers" grep -q 'ats_board_api,jobspipe,adzuna,firecrawl_ats,tavily_ats' packer/linux/bin/jobbots-discover-ats-it
check "ensure_registry_ready bin" grep -q ensure_registry_ready packer/linux/bin/jobbots-discover-ats-it
check "phase B_crossmatch" grep -q phase=B_crossmatch packer/linux/bin/jobbots-discover-ats-it
check "ATS_INCLUDE_GOOGLE=0" grep -q '^JOBBOTS_ATS_INCLUDE_GOOGLE=0$' packer/linux/runtime-prod-overrides.conf
check "JOBSPIPE max" grep -q '^JOBSPIPE_MAX_QUERIES_PER_RUN=12$' packer/linux/runtime-prod-overrides.conf
check "ADZUNA max" grep -q '^ADZUNA_MAX_QUERIES_PER_RUN=10$' packer/linux/runtime-prod-overrides.conf
check "ats timer 40min" grep -q OnUnitActiveSec=40min packer/linux/systemd/jobbots-discover-ats.timer
check "mongo db name helper" core_grep _jobbots_mongo_db_name discovery/slug_registry.py
check "JOBBOTS_MONGO_DATABASE" core_grep JOBBOTS_MONGO_DATABASE discovery/slug_registry.py
check "ensure_registry_ready growth" core_grep ensure_registry_ready discovery/registry_growth.py
check "grow_from_application_queue" core_grep grow_from_application_queue discovery/registry_growth.py
check "Direct ATS enqueue" core_grep 'Direct ATS enqueue \(skip company_save AI\)' discovery/planner.py
check "enqueue planner" core_grep enqueue_ discovery/planner.py
check "ats_slug_registry path" core_grep ats_slug_registry discovery/slug_registry.py
check "general hard gate" core_grep 'general hard gate: configured office/customer-service title' discovery/_gate_adapter.py
check "floor retail gate" core_grep 'floor retail/clinical/trades title' discovery/_gate_adapter.py
check "profile=profile" core_grep profile=profile discovery/planner.py
check "REENQUEUE cooldown" grep -q JOBBOTS_REENQUEUE_COOLDOWN_SECONDS packer/linux/runtime-prod-overrides.conf
check "form_stalled" core_grep form_stalled job_queue.py
check "no confirmation requeue" core_grep 'submit clicked but no confirmation' job_queue.py
check "LinkedIn datetime claim" core_grep 'Accept either so LinkedIn' job_queue.py
check "next_attempt_at query" core_grep next_attempt_at job_queue.py
check "fail-open EA IT" core_grep 'fail-open: EA IT title signal overrode batch AI' discovery/planner.py
check "INDEED general batch 3" grep -q JOBBOTS_INDEED_GENERAL_TERM_BATCH=3 packer/linux/runtime-prod-overrides.conf
check "LINKEDIN term batch 3" grep -q LINKEDIN_DISCOVERY_TERM_BATCH=3 packer/linux/runtime-prod-overrides.conf
check "indeed general BATCH_SIZE" grep -q JOBBOTS_INDEED_GENERAL_TERM_BATCH packer/linux/bin/jobbots-discover-indeed-general
check "bash -n scripts" bash -n packer/linux/bin/jobbots-discover-indeed-general packer/linux/bin/jobbots-discover-ats-it scripts/lifecycle.sh scripts/bootstrap_stock_worker.sh
check "jobbank_it.py" test -f automation_monorepo/bots/jobbank_it.py
check "jobbank direct apply" core_test jobbank_direct_apply.py
check "SERP_CACHE=0" grep -q DISCOVERY_SERP_CACHE=0 packer/linux/runtime-prod-overrides.conf
check "BATCH_AI_CHUNK override" grep -qE 'DISCOVERY_BATCH_AI_CHUNK=(4|8|12)' packer/linux/runtime-prod-overrides.conf
check "DEEPSEEK timeout override" grep -qE 'DEEPSEEK_TIMEOUT_SECONDS=(90|120)' packer/linux/runtime-prod-overrides.conf
check "JOBBOTS_DATA_DIR" grep -q '^JOBBOTS_DATA_DIR=/var/lib/jobbots$' packer/linux/runtime-prod-overrides.conf
check "dry_run engine" core_grep dry_run ats/engine.py
check "page-primary confirmation policy" core_test ats/confirmation.py
check "page confirmation primary" core_grep 'page confirmation' ats/engine.py
check "successfully submitted pin" core_grep 'successfully submitted' ats/confirmation.py
check "submitted successfully pin" core_grep 'submitted successfully' ats/confirmation.py
check "no form-gone success" core_grep 'do NOT treat submit-button disappearance' ats/confirmation.py
check "PLATFORM_ASHBY" core_grep PLATFORM_ASHBY discovery/ats_slugs.py
check "PLATFORM_BAMBOOHR" core_grep PLATFORM_BAMBOOHR discovery/ats_slugs.py
check "JOBSPY_GOOGLE override" grep -q JOBSPY_GOOGLE_ENABLED packer/linux/runtime-prod-overrides.conf
check "google_ats" core_grep google_ats discovery/providers/jobspy_provider.py
check "JOBSPY planner" core_grep JOBSPY_GOOGLE_ENABLED discovery/planner.py
check "test_jobspy_google" test -f automation_monorepo/tests/test_jobspy_google.py
check "list_llm_gateway_chain" core_grep list_llm_gateway_chain llm_backend/ai/llm_gateway.py
check "LLM_FALLBACK_OPENROUTER code" core_grep LLM_FALLBACK_OPENROUTER llm_backend/ai/llm_gateway.py
check "transient llm error" core_grep _is_transient_llm_error llm_backend/ai/deepseekConnections.py
check "fail-open gates" core_grep fail-open shared_modules/indeed/gates.py
check "LLM_FALLBACK_OPENROUTER=1" grep -q LLM_FALLBACK_OPENROUTER=1 packer/linux/runtime-prod-overrides.conf
check "BATCH_AI 4or8" grep -qE 'DISCOVERY_BATCH_AI_CHUNK=(4|8)' packer/linux/runtime-prod-overrides.conf
check "discover-indeed-it" test -f packer/linux/bin/jobbots-discover-indeed-it
check "discover-indeed-general" test -f packer/linux/bin/jobbots-discover-indeed-general
check "discover-linkedin-general" test -f packer/linux/bin/jobbots-discover-linkedin-general
check "discover-glassdoor-it" test -f packer/linux/bin/jobbots-discover-glassdoor-it
check "glassdoor portals flag" grep -q -- '--portals glassdoor,workopolis' packer/linux/bin/jobbots-discover-glassdoor-it
check "discover-ats-it" test -f packer/linux/bin/jobbots-discover-ats-it
check "discover-jobbank-it" test -f packer/linux/bin/jobbots-discover-jobbank-it
check "application.service" test -f packer/linux/systemd/jobbots-application.service
check "application-general.service" test -f packer/linux/systemd/jobbots-application-general.service
check "linkedin-general timer" grep -q jobbots-discover-linkedin-general.timer scripts/bootstrap_stock_worker.sh
check "indeed-general timer" grep -q jobbots-discover-indeed-general.timer scripts/bootstrap_stock_worker.sh
check "ats timer install" grep -q jobbots-discover-ats.timer scripts/bootstrap_stock_worker.sh
check "jobbank timer install" grep -q jobbots-discover-jobbank.timer scripts/bootstrap_stock_worker.sh
# Farm productivity contract (post-refactor, ephemeral build/destroy)
check "farm_check module" test -f jobbots/app/farm_check.py
check "farm-check CLI" grep -q farm-check jobbots/app/cli.py
check "farm topology tests" test -f automation_monorepo/tests/test_farm_topology.py
check "preflight no google_it NST" bash -c "! grep -q -- '--bot google_it' scripts/bootstrap_stock_worker.sh"
check "preflight NST active slot" grep -Eq 'NSTBROWSER_ACTIVE_SLOT=\$\{NSTBROWSER_ACTIVE_SLOT:-[12]\}|NSTBROWSER_ACTIVE_SLOT=2' scripts/bootstrap_stock_worker.sh
check "preflight farm-check" grep -q 'farm-check' scripts/bootstrap_stock_worker.sh
check "lifecycle farm-check" grep -q 'farm-check' scripts/lifecycle.sh
check "ACTIVE_SLOT pin 1|2" grep -Eq '^NSTBROWSER_ACTIVE_SLOT=[12]$' packer/linux/runtime-prod-overrides.conf
check "linkedin sole NST" core_grep linkedin_general browser/nst_accounts.py
check "default secret getter" core_grep _default_secret_getter browser/nst_accounts.py
check "portable packaging filter" grep -q 'os.path.lexists' scripts/bootstrap_stock_worker.sh
check "planner gate jobbots path" grep -q 'jobbots/core/discovery/planner.py' scripts/bootstrap_stock_worker.sh
check "pip install -e jobbots" grep -q 'pip install -q -e /opt/jobbots/app' scripts/bootstrap_stock_worker.sh
check "vm farm-check after sync" grep -q 'python -m jobbots.app.cli farm-check' scripts/bootstrap_stock_worker.sh
check "libreoffice in provision" grep -q libreoffice-writer packer/scripts/provision_linux.sh
check "libreoffice in bootstrap" grep -q libreoffice-writer scripts/bootstrap_stock_worker.sh
check "travis privacy grep jobbots" grep -q "privacy notice.*jobbots/core/shared_modules/form_answers" .travis.yml

if grep -R --include='*.service' -E 'DISCOVERY_SERP_CACHE=1|DISCOVERY_TERM_MEMORY=1|DISCOVERY_BATCH_AI_MAX=40' packer/linux/systemd/; then
  echo '::error::systemd units hardcode discovery volume knobs'
  fail=1
fi

if [[ "$fail" != 0 ]]; then
  echo '::error::one or more prod hotpatch markers missing'
  exit 1
fi
echo 'prod hotpatch markers OK'

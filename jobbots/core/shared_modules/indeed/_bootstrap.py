"""
Indeed Job Applier Bot  —  Playwright Edition
==============================================
All browser interactions now use the Playwright Page API through the
SeleniumBase → CDP → Playwright bridge established in open_chrome.py.

API translation reference:
  driver.get(url)                      → page.goto(url, wait_until='domcontentloaded')
  driver.current_url                   → page.url
  driver.title                         → page.title()
  driver.page_source                   → page.content()
  find_element(By.CSS_SELECTOR, sel)   → page.query_selector(sel)
  find_elements(By.CSS_SELECTOR, sel)  → page.query_selector_all(sel)
  find_element(By.XPATH, xp)           → page.query_selector(f"xpath={xp}")
  element.text                         → element.inner_text()
  element.get_attribute(x)             → element.get_attribute(x)   [same]
  element.click()                      → element.click()             [same]
  element.send_keys(text)              → element.fill(text)          [clears first]
  element.is_displayed()               → element.is_visible()
  element.is_selected()                → element.is_checked()
  execute_script("arg[0].click()",el)  → el.click(force=True)
  execute_script("arg[0].value=x",el)  → el.fill(x)
  Select(el).select_by_visible_text(t) → el.select_option(label=t)
  driver.save_screenshot(path)         → page.screenshot(path=path)
  WebDriverWait(...).until(...)        → page.wait_for_selector(sel, timeout=ms)
  file_input.send_keys(path)           → file_input.set_input_files(path)
  All window handle logic              → page objects / context.expect_page()

CAPTCHA checkpoints added after every navigation call.
"""

import csv
import os
import re
import time
from datetime import datetime, timedelta
from random import shuffle
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from jobbots.core.utils import (
    make_directories,  # noqa: F401 - re-exported through star imports
    print_lg,
    resolve_project_path,
)
from jobbots.core.portals.training_logger_legacy import (  # noqa: F401 - re-exported through star imports
    log_training_event,
    page_dom_snapshot,
    element_dom_snapshot,
    training_log_path,
)
from jobbots.core.evasion._handlers import check_and_handle_captcha  # noqa: F401 - re-exported through star imports
from jobbots.core.evasion._detection import (  # noqa: F401 - re-exported through star imports
    is_browser_alive,
    is_cloudflare_challenge,
    try_recover_page,
)
from jobbots.core.portals.mongo_storage_legacy import (  # noqa: F401 - re-exported through star imports
    get_job_ids,
    save_job_record,
)
from config.settings import skipped_file_name
try:
    from config.settings import file_name as _settings_file_name
except ImportError:
    _settings_file_name = "all excels/indeed_general_applied_applications_history.csv"
try:
    from config.settings import failed_file_name as _settings_failed_file_name
except ImportError:
    _settings_failed_file_name = "all excels/indeed_general_failed_applications_history.csv"
try:
    from config.settings import _bot_name
except ImportError:
    _bot_name = "indeed_general"

# ── Speed constants — all artificial waits in one place ──────────────────────
# Reduce these to speed up; increase them if bot trips bot-detection.
_T_STEP    = 0.3   # top of each SmartApply step (was 2.0s)
_T_NAV     = 0.4   # after click-then-navigate (was 1.5-2.0s)
_T_ACTION  = 0.2   # between quick UI actions (was 0.5-1.0s)
_T_RESUME  = 0.4   # resume selection spinner (was 1.5-2.0s)
_T_Q       = 0.1   # between questions (was 1.0s)

# ── Company blacklist / whitelist ─────────────────────────────────────────────
try:
    from config.search import about_company_bad_words, about_company_good_words
except ImportError:
    about_company_bad_words = []
    about_company_good_words = []

# ── Run-control settings ──────────────────────────────────────────────────────
try:
    from config.settings import run_non_stop
except ImportError:
    run_non_stop = False
try:
    from config.settings import run_in_background
except ImportError:
    run_in_background = False
try:
    from config.settings import captcha_allow_gui_fallback, captcha_allow_manual_fallback
except ImportError:
    captcha_allow_gui_fallback = False
    captcha_allow_manual_fallback = False
try:
    from config.settings import click_gap
except ImportError:
    click_gap = 0
try:
    from config.settings import logs_folder_path
except ImportError:
    logs_folder_path = "logs"
try:
    from config.settings import use_groq_job_gate
except ImportError:
    use_groq_job_gate = False
try:
    from config.settings import indeed_easy_apply_gate
except ImportError:
    indeed_easy_apply_gate = "local"
try:
    from config.search import date_posted as _cfg_date_posted
except ImportError:
    _cfg_date_posted = ""
try:
    from config.search import indeed_remote_filter as _cfg_indeed_remote_filter
except ImportError:
    _cfg_indeed_remote_filter = ""
try:
    from config.search import on_site as _cfg_on_site
except ImportError:
    _cfg_on_site = []
try:
    from config.search import search_terms, search_location, search_locations
except ImportError:
    search_terms = []
    search_location = ""
    search_locations = []
try:
    from config.search import randomize_search_order
except ImportError:
    randomize_search_order = False

# ── Pause / interaction settings ─────────────────────────────────────────────
try:
    from config.questions import pause_before_submit
except ImportError:
    try:
        from config.settings import pause_before_submit
    except ImportError:
        pause_before_submit = False
try:
    from config.questions import pause_at_failed_question
except ImportError:
    try:
        from config.settings import pause_at_failed_question
    except ImportError:
        pause_at_failed_question = False
try:
    from config.settings import skip_sign_in_jobs
except ImportError:
    skip_sign_in_jobs = True
try:
    from config.settings import skip_easy_apply
except ImportError:
    skip_easy_apply = False
try:
    from config.search import easy_apply_only
except ImportError:
    easy_apply_only = False
try:
    from config.settings import save_company_site_jobs
except ImportError:
    save_company_site_jobs = False

if run_in_background:
    pause_before_submit = False
    pause_at_failed_question = False

if os.getenv("FORCE_PAUSE_BEFORE_SUBMIT", "").strip().lower() in ("1", "true", "yes", "on"):
    pause_before_submit = True

# ── Personal info ─────────────────────────────────────────────────────────────
try:
    from config.personals import (
        first_name, last_name, phone_number,
        street, zipcode, country, state, current_city,
    )
    try:
        from config.personals import ethnicity as _configured_ethnicity, gender as _configured_gender
    except ImportError:
        _configured_ethnicity = "Decline"
        _configured_gender = "Decline"
    try:
        from config.personals import middle_name
    except ImportError:
        middle_name = ""
except ImportError as _e:
    print_lg(f"[Indeed] Could not import config.personals: {_e}")
    first_name = last_name = middle_name = phone_number = ""
    street = zipcode = country = state = current_city = ""
    _configured_ethnicity = "Decline"
    _configured_gender = "Decline"

# ── Salary / notice-period ────────────────────────────────────────────────────
try:
    from config.questions import desired_salary as _ds
except ImportError:
    _ds = 75000
try:
    from config.questions import current_ctc as _cc
except ImportError:
    _cc = 0
try:
    from config.questions import notice_period as _np
except ImportError:
    _np = 30

desired_salary_monthly = str(round(_ds / 12, 2))
desired_salary_lakhs   = str(round(_ds / 100000, 2))
desired_salary_str     = str(_ds)
current_ctc_monthly    = str(round(_cc / 12, 2))
current_ctc_lakhs      = str(round(_cc / 100000, 2))
current_ctc_str        = str(_cc)
notice_period_months   = str(_np // 30)
notice_period_weeks    = str(_np // 7)
notice_period_str      = str(_np)

# ── Resume path ───────────────────────────────────────────────────────────────
try:
    from config.questions import default_resume_path
    if default_resume_path:
        default_resume_path = resolve_project_path(default_resume_path)
except ImportError:
    try:
        from config.settings import generated_resume_path as default_resume_path
        if default_resume_path:
            default_resume_path = resolve_project_path(default_resume_path)
    except ImportError:
        default_resume_path = ""

# ── Cover letter / summary ────────────────────────────────────────────────────
try:
    from config.questions import cover_letter
except ImportError:
    cover_letter = ""
try:
    from config.questions import profile_summary
except ImportError:
    profile_summary = ""

# ── Extra personals ───────────────────────────────────────────────────────────
try:
    from config.questions import profile_headline
except ImportError:
    profile_headline = "IT Professional"
try:
    from config.questions import recent_employer
except ImportError:
    recent_employer = ""
# When Indeed asks the candidate to enter a job that demonstrates relevant
# experience for an IT role, we point at the closest IT-relevant job from the
# resume: Bell Canada (technical support, networking troubleshooting). The KPU
# studies are added separately on the education step.
RELEVANT_EXPERIENCE_JOB_TITLE = "Sales & Technical Support Representative"
RELEVANT_EXPERIENCE_COMPANY = "Bell Canada (Authorized Dealer)"
KPU_SCHOOL_NAME = "Kwantlen Polytechnic University (KPU)"
try:
    from config.personals import email_address
except ImportError:
    email_address = ""
try:
    from config.search import current_experience
except ImportError:
    current_experience = 4

try:
    from config.questions import years_of_experience as _yoe_str
except ImportError:
    _yoe_str = str(current_experience)

try:
    from config.questions import (
        meets_minimum_work_age,
        has_legal_work_documents,
        can_work_in_person,
        can_work_evenings,
        can_work_weekends,
        can_work_full_time_40_hours,
        can_travel_between_local_locations,
        can_commute_up_to_one_hour,
        has_valid_drivers_license,
        has_reliable_vehicle,
        can_stand_for_long_periods,
        can_lift_up_to_70_lbs,
        is_vaccinated_against_covid,
        has_health_office_reception_experience,
        has_dental_reception_experience,
        weekly_work_availability,
        can_freely_travel_to_us,
    )
except ImportError:
    meets_minimum_work_age = True
    has_legal_work_documents = True
    can_work_in_person = True
    can_work_evenings = True
    can_work_weekends = True
    can_work_full_time_40_hours = True
    can_travel_between_local_locations = True
    can_commute_up_to_one_hour = True
    has_valid_drivers_license = True
    has_reliable_vehicle = True
    can_stand_for_long_periods = True
    can_lift_up_to_70_lbs = True
    is_vaccinated_against_covid = True
    has_health_office_reception_experience = True
    has_dental_reception_experience = True
    weekly_work_availability = "Fully available 7 days a week for all shifts, including mornings, days, afternoons, evenings, nights, weekends, and holidays. I can work any schedule, rotating shifts, and any number of hours required."
    can_freely_travel_to_us = False

# ── AI provider config ────────────────────────────────────────────────────────
try:
    from config.secrets import use_AI, ai_provider
except ImportError:
    use_AI = False
    ai_provider = "ollama"
try:
    from config.secrets import groq_api_key, groq_model
except ImportError:
    groq_api_key = os.getenv("GROQ_API_KEY", "")
    groq_model = "llama-3.1-8b-instant"

_aiClient    = None
_ai_provider = None

# ── Sign-in wall detection ────────────────────────────────────────────────────
# Used only for *non-Indeed* hosts (see session._is_sign_in_page).
# Do NOT treat Indeed ``/auth?`` as external — that is SSO intermediate.
_SIGNIN_URL_KEYWORDS = [
    'login', 'log-in', 'sign-in', 'signin', 'sign_in',
    'signup', 'sign-up', 'sign_up', 'register', 'registration',
    '/auth/', '/auth?', 'oauth', 'account/create', 'create-account',
    'new-account', '/join', 'onboard', 'sso/', '/sso?',
    'workday.com', 'myworkday', 'okta.com', 'auth0.com',
]
_SIGNIN_TITLE_KEYWORDS = [
    'sign in', 'log in', 'login', 'sign up', 'signup',
    'create account', 'create an account', 'register',
    'join us', 'welcome back', 'get started',
]
_SIGNIN_BODY_KEYWORDS = [
    'sign in', 'log in', 'create account', 'sign up',
    'register now', 'join now', 'create a free account',
    'already have an account', 'forgot your password',
]


# ── File paths (dynamic and bot-isolated) ────────────────────────────────────────────────────────────────
INDEED_APPLIED_FILE = resolve_project_path(_settings_file_name)
INDEED_FAILED_FILE  = resolve_project_path(_settings_failed_file_name)
INDEED_SKIPPED_FILE = resolve_project_path(skipped_file_name)

# Platform tag used for MongoDB and logging
INDEED_PLATFORM_TAG = _bot_name

INDEED_HOME       = "https://ca.indeed.com"
INDEED_SEARCH     = "https://ca.indeed.com/jobs"
SMARTAPPLY_DOMAIN = "smartapply.indeed.com"

_STEP_CONTACT       = "contact-info"
_STEP_LOCATION      = "profile-location"
_STEP_RESUME        = "resume"
_STEP_PRIVACY       = "privacy"
_STEP_EXPERIENCE    = "experience"
_STEP_REVIEW        = "review"
_STEP_QUAL          = "qualification-questions-module"
_STEP_EMP_QUESTIONS = "questions-module"
_STEP_RESUME_SELECT = "resume-selection-module"
_STEP_APPLY_BY_ID   = "applybyapplyablejobid"
_SUBMITTED_KEYWORDS = (
    "your application has been submitted",
    "application submitted",
    "thank you for applying",
)
_ALREADY_APPLIED_KEYWORDS = (
    "you've already applied",
    "you have already applied",
    "already applied to this job",
)
_INDEED_SAVED_KEYWORDS = (
    "saved",
    "job saved",
    "saved job",
)
# Indeed's "Job hidden" / "Not interested" pill (clicked thumbs-down on the card).
# Detected on card text so we skip jobs the user has dismissed.
_INDEED_HIDDEN_KEYWORDS = (
    "job hidden",
    "you hid this job",
    "you hid this",
    "this job is hidden",
    "not interested",
)

_RE_EXP = re.compile(r'[(]?\s*(\d+)\s*[)]?\s*[-to]*\s*\d*[+]*\s*year[s]?', re.IGNORECASE)
_RE_JK  = re.compile(r'[?&]jk=([a-zA-Z0-9]+)')
_SUGGESTED_JOB_MARKERS = (
    "similar to jobs you explored",
    "similar jobs",
    "recommended jobs",
    "people also searched",
)

_FROMAGE_MAP = {
    "last 24 hours": 1,
    "past 24 hours": 1,
    "last 3 days":   3,
    "last 7 days":   7,
    "past week":     7,
    "last 14 days":  14,
    "past month":    30,
    "all dates":     None,
    "any time":      None,
    "":              None,
}

_REMOTE_WORK_FILTERS = {
    # Captured from Indeed's richSearchComponentModel on 2026-05-23.
    "remote": {"remotejob": "1", "attr": "DSQF7", "label": "Remote"},
    "hybrid": {"remotejob": "2", "attr": "PAXZC", "label": "Hybrid work"},
}

_CARD_SELECTORS = [
    "#mosaic-provider-jobcards ul.jobsearch-ResultsList > li div.job_seen_beacon",
    "#mosaic-provider-jobcards > ul > li div.job_seen_beacon",
    "#mosaic-provider-jobcards li[data-jk]",
    "div.job_seen_beacon",
    "div[data-testid='slider_container']",
    "li[data-jk]",
    "li.css-5lfssm",
    "div[data-jk]",
]

# ── Module-level session state ────────────────────────────────────────────────
_current_job_context:         str  = ""
_current_job_meta:            dict = {}
_use_new_resume:              bool = True
_randomly_answered_questions: set  = set()
# Per-job memo of already-answered form-field keys. Prevents the bot from
# re-typing the same field across multiple passes of the employer-questions
# step (root cause of the 2026-05-12 loop where one MSP application
# answered the LinkedIn textarea 112 times). Cleared at the start of every
# new SmartApply run.
_answered_field_keys:         set  = set()


# ─────────────────────────────────────────────────────────────────────────────
# AI Initialisation (unchanged from Selenium version)
# ─────────────────────────────────────────────────────────────────────────────

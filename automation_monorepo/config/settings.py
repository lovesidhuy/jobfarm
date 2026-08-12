"""Runtime settings for the Indeed and Glassdoor job automation bot."""


###################################################### BOT SETTINGS ######################################################

# >>>>>>>>>>> Browser and application handling <<<<<<<<<<<

# Keep the External Application tabs open?
close_tabs = False                  # True or False, Note: True or False are case-sensitive
'''
Note: RECOMMENDED TO LEAVE IT AS `True`, if you set it `False`, be sure to CLOSE ALL TABS BEFORE CLOSING THE BROWSER!!!
'''

# >>>>>>>>>>> External Apply / Simplify Settings <<<<<<<<<<<

# Skip Easy Apply jobs entirely and only process external "Apply on company site" jobs?
# Set to True when you only want to collect/save external application links.
skip_easy_apply = False             # True or False

# Enable the worth-saving feature: evaluate and save external/company-site jobs?
save_company_site_jobs = True        # True or False

# Skip external jobs where the company site shows a sign-in / sign-up / create-account wall?
# Set to True to automatically skip and log these jobs instead of getting stuck.
skip_sign_in_jobs = True            # True or False

# Path(s) to browser extensions you want loaded into the bot's Chrome session.
# The bot opens a separate Chrome window — your extensions won't load unless you list them here.
# SIMPLIFY: Leave as "" to auto-detect Simplify from your Chrome profile, or set the full folder path.
# Example (Mac):  "/Users/yourname/Library/Application Support/Google/Chrome/Default/Extensions/pbanhockgagggenencehbnadejlgchfc/2.6.2_0"
# Example (Win):  "C:\\Users\\yourname\\AppData\\Local\\Google\\Chrome\\User Data\\Default\\Extensions\\pbanhockgagggenencehbnadejlgchfc\\2.6.2_0"
simplify_extension_path = ""        # Leave "" to auto-detect, or set full path to the Simplify extension version folder

# Additional extension folders to load (list of paths), e.g. ["path/to/ext1", "path/to/ext2"]
extra_extension_paths = []          # [] to disable

# How many seconds to wait after opening an external company page before switching back.
# Increase this if you use Simplify (browser extension) so it has time to detect and fill the form.
# Set to 0 to switch back immediately.
external_apply_wait_time = 8        # seconds (recommended: 6-10 if using Simplify, 0 to disable wait)

# Keep for compatibility with older modules; not used by the current Indeed flow.
follow_companies = False            # True or False, Note: True or False are case-sensitive

# Do you want the program to run continuously until you stop it? (Beta)
run_non_stop = False                # True or False, Note: True or False are case-sensitive
'''
Note: Will be treated as False if `run_in_background = True`
'''
alternate_sortby = True             # True or False, Note: True or False are case-sensitive
cycle_date_posted = True            # True or False, Note: True or False are case-sensitive
stop_date_cycle_at_24hr = True      # True or False, Note: True or False are case-sensitive





# >>>>>>>>>>> RESUME GENERATOR (Experimental & In Development) <<<<<<<<<<<

# Give the path to the folder where all the generated resumes are to be stored
generated_resume_path = "all resumes/" # (In Development)





import os
from core.secret_manager import get_secret

_job_profile = (get_secret("JOB_PROFILE", "IT") or "IT").upper()

# >>>>>>>>>>> Global Settings <<<<<<<<<<<

# Directory and name of the files where history of applied jobs is saved (Sentence after the last "/" will be considered as the file name).
_bot_name = get_secret("BOT_NAME", "default")
file_name = f"all excels/{_bot_name}_applied_applications_history.csv"
failed_file_name = f"all excels/{_bot_name}_failed_applications_history.csv"
skipped_file_name = f"all excels/{_bot_name}_skipped_applications_history.csv"
logs_folder_path = f"logs/{_bot_name}/"

# >>>>>>>>>>> Local MongoDB History <<<<<<<<<<<

# Legacy portal writers mirror applied/failed rows into MongoDB as well as CSV.
# The collection stores all portal/profile variants and separates them by
# platform (`indeed_it`, `glassdoor_general`, etc.) plus status/job_id.
use_mongodb = get_secret("USE_MONGODB", "true").lower() not in {"0", "false", "no", "off"}
mongodb_uri = get_secret("MONGODB_URI", "mongodb://localhost:27017")
mongodb_database = get_secret("JOBBOTS_MONGO_DATABASE", get_secret("MONGODB_DB_NAME", "jobbots"))
mongodb_collection = get_secret("MONGODB_HISTORY_COLLECTION", "job_history")

# >>>>>>>>>>> Auto Backup <<<<<<<<<<<

# Create a timestamped backup of job-history files and MongoDB history at startup.
auto_backup = True                  # True or False
backup_folder_path = "backups/"
backup_keep_latest = 20             # Number of backup folders to keep

# Save detailed Indeed troubleshooting events as JSONL.
# Useful when you want to inspect form questions, chosen answers, submit issues,
# save failures, or CAPTCHA recovery after a run.
indeed_training_logging = True      # True or False

# Use Groq as an extra job-fit filter before saving/applying.
# General baseline uses local Ollama gate first; IT baseline uses Groq.
use_groq_job_gate = _job_profile != "GENERAL"            # True or False

# General baseline evaluates full job descriptions with local Ollama before
# saving/applying. IT baseline does not use this extra job gate.
use_ollama_job_gate = False          # True or False

# Set the maximum amount of time allowed to wait between each click in secs
click_gap = 0                       # Enter max allowed secs to wait approximately. (Only Non Negative Integers Eg: 0,1,2,3,....)

# If you want to see Chrome running then set run_in_background as False (May reduce performance). 
run_in_background = get_secret("RUN_IN_BACKGROUND", "false").lower() in ("true", "1", "yes", "on")           # True or False

# If you want to disable extensions then set disable_extensions as True (Better for performance)
disable_extensions = False          # True or False, Note: True or False are case-sensitive

# Run in safe mode. Set this true if chrome is taking too long to open or if you have multiple profiles in browser. This will open chrome in guest profile!
# ⚠️  IMPORTANT: If you use Simplify (or any browser extension) to auto-fill external job applications,
#     set safe_mode = False so the bot uses your real Chrome profile where the extension is installed.
#     safe_mode = True opens a temporary guest profile that has NO extensions loaded.
#     For Glassdoor + Indeed one-login, a guest profile also breaks session continuity and can cause auth/redirect loops — keep safe_mode = False and use per-bot profiles under data/browser_profiles/.
safe_mode = False                   # True or False, Note: True or False are case-sensitive

# Do you want scrolling to be smooth or instantaneous? (Can reduce performance if True)
smooth_scroll = False               # True or False, Note: True or False are case-sensitive

# If enabled (True), the program would keep your screen active and prevent PC from sleeping. Instead you could disable this feature (set it to false) and adjust your PC sleep settings to Never Sleep or a preferred time. 
keep_screen_awake = True            # True or False, Note: True or False are case-sensitive (Note: Will temporarily deactivate when any application dialog boxes are present (Eg: Pause before submit, Help needed for a question..))

# Run in undetected mode to bypass anti-bot protections (Preview Feature, UNSTABLE. Recommended to leave it as False)
stealth_mode = True                # True or False, Note: True or False are case-sensitive

# >>>>>>>>>>> CAPTCHA / Anti-Bot Settings <<<<<<<<<<<

# CDP remote debugging port.  SeleniumBase launches Chrome on this port so
# Playwright can connect to the same stealthy browser session.
# Change if port 9222 is already occupied on your machine.
cdp_port = int(get_secret("CDP_PORT", 9224))

# 0-indexed slot for this bot instance (0-3), used to target the correct
# Chrome window on the VM when multiple bots are running.
bot_instance_id = int(get_secret("BOT_INSTANCE_ID", 0))

# How long (seconds) to wait for a Cloudflare Turnstile ("Just a moment…")
# challenge to be resolved before continuing.  Auto-solve is attempted first;
# this is the manual fallback window.
captcha_cf_timeout = 45            # seconds

# Cloudflare solving strategy:
#   "seleniumbase" = current GUI/UC Cloudflare bypass flow.
#   "capmonster"  = try CapMonster Cloud Turnstile token first, then fall back
#                   to non-GUI reload/polling unless GUI fallback is enabled.
captcha_cloudflare_solver = get_secret("CAPTCHA_CLOUDFLARE_SOLVER", "seleniumbase")  # "seleniumbase" or "capmonster"

# Use CapMonster API tokens for CAPTCHA solving.
# Leave this False when you do not have a CAPTCHA API key. The bot will keep the
# browser open and wait for you to solve CAPTCHA challenges yourself.
use_capmonster_captcha_solver = True        # True or False

# Allow the bot to use your desktop mouse/keyboard for stubborn CAPTCHA pages.
# Default is False: the browser stays open and waits for you to solve it.
# Set True only if you want the bot to try assisted GUI clicks.
captcha_allow_gui_fallback = False         # True or False

# Wait for you to solve Cloudflare manually if the automatic options fail.
# Turn this off when you want the run to keep moving without waiting for you.
captcha_allow_manual_fallback = False      # True or False

# How long (seconds) to wait for a Google reCAPTCHA v2 image challenge
# (buses, fire hydrants, traffic lights etc.) to be solved manually.
captcha_rc_timeout = 90            # seconds

# How long (seconds) to wait for CapMonster to return a reCAPTCHA token.
# Indeed's Enterprise reCAPTCHA can take longer than the manual fallback window.
captcha_capmonster_timeout = 180   # seconds

# How long (seconds) to wait for CapMonster Cloudflare Turnstile tokens.
captcha_capmonster_turnstile_timeout = 120   # seconds

# Force Turnstile token tasks to run proxyless. Keep this False when browser
# traffic uses a proxy; Cloudflare tokens/cookies are often bound to solver IP.
captcha_turnstile_no_proxy = str(get_secret("CAPMONSTER_TURNSTILE_NO_PROXY", "0")).strip().lower() in ("1", "true", "yes", "on")  # True or False

# Skip CapMonster Turnstile "token" mode and go straight to cf_clearance
# (cookie injection) mode. Token mode has a ~0% success rate on Indeed/Glassdoor
# and otherwise wastes the full turnstile timeout above before falling back.
captcha_skip_turnstile_token_mode = str(get_secret("CAPTCHA_SKIP_TURNSTILE_TOKEN_MODE", "1")).strip().lower() in ("1", "true", "yes", "on")  # True or False

# Passive wait (seconds) before active Cloudflare solvers run.
captcha_cf_patient_wait = int(get_secret("CAPTCHA_CF_PATIENT_WAIT", "15"))

# Skip page.reload() during Cloudflare handling — reload often re-triggers the challenge.
captcha_cf_skip_reload = str(get_secret("CAPTCHA_CF_SKIP_RELOAD", "1")).strip().lower() in ("1", "true", "yes", "on")

# Do you want to get alerts on errors related to AI API connection?
showAiErrorAlerts = False            # True or False, Note: True or False are case-sensitive

# >>>>>>>>>>> Ollama (Local LLM) Settings for Indeed Bot <<<<<<<<<<<

# Enable Ollama to answer free-text / textarea questions on Indeed SmartApply forms.
# Ollama must be running locally before starting the bot.
# Install: https://ollama.ai  — then run: ollama pull llama3.2:3b
use_ollama_for_indeed = False         # True or False

# Which locally-pulled Ollama model to use. Run `ollama list` to see available models.
# Good lightweight choices: "llama3.2:3b", "llama3.2", "mistral", "phi3", "gemma2"
ollama_model = "llama3.2:3b"               # model name as shown by `ollama list`

# URL of the Ollama OpenAI-compatible endpoint (change if Ollama runs on a non-default port)
ollama_base_url = get_secret("OLLAMA_BASE_URL", "http://localhost:11434/v1")

# Resume generation is currently experimental and disabled by default.
# use_resume_generator = False       # True or False


chrome_profile_dir = get_secret("CHROME_PROFILE_DIR", ".chrome-profile")


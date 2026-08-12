from __future__ import annotations
"""
Workopolis Job Applier Bot — Playwright / Indeed SmartApply Edition
"""

import os
import csv
import time
from datetime import datetime
from random import shuffle
from urllib.parse import parse_qs, quote_plus, unquote_plus, urljoin, urlparse

from ..indeed import smartapply as smartapply_impl
smartapply = smartapply_impl
from jobbots.core.utils import (
    make_directories,
    resolve_project_path,
    print_lg,
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
from jobbots.core.utils import truncate_for_csv  # noqa: F401 - re-exported through star imports
from config.search import search_location
from config.search import search_terms, randomize_search_order

try:
    from config.search import search_locations
except ImportError:
    search_locations = [search_location] if search_location else []

_location_override = (
    os.getenv("WORKOPOLIS_SEARCH_LOCATIONS", "").strip()
    or os.getenv("WORKOPOLIS_SEARCH_LOCATION", "").strip()
)
if _location_override:
    search_locations = [loc.strip() for loc in _location_override.split("|") if loc.strip()]
    if search_locations:
        search_location = search_locations[0]
from config.settings import _bot_name, file_name, failed_file_name

try:
    from config.settings import logs_folder_path
except ImportError:
    logs_folder_path = "logs"

try:
    from config.settings import skipped_file_name
except ImportError:
    skipped_file_name = f"all excels/{_bot_name}_skipped_applications_history.csv"

try:
    from config.settings import run_non_stop, run_in_background
except ImportError:
    run_non_stop = False
    run_in_background = False

try:
    from config.search import date_posted as _cfg_date_posted
except ImportError:
    _cfg_date_posted = ""

try:
    from config.questions import profile_headline as _profile_headline
except ImportError:
    _profile_headline = ""

try:
    from config.search import bad_words as _bad_words, security_clearance as _security_clearance
except ImportError:
    _bad_words = []
    _security_clearance = False

# ── Constants ─────────────────────────────────────────────────────────────────

WORKOPOLIS_HOME = "https://www.workopolis.com/"
WORKOPOLIS_APPLIED_FILE = resolve_project_path(file_name)
WORKOPOLIS_FAILED_FILE  = resolve_project_path(failed_file_name)
WORKOPOLIS_SKIPPED_FILE = resolve_project_path(skipped_file_name)

_T_STEP   = 0.4   # between major steps
_T_NAV    = 0.6   # after navigation
_T_ACTION = 0.3   # between quick actions
_T_CARD   = 0.2   # between card interactions
_T_SEARCH = 1.0   # after submitting search


# ─────────────────────────────────────────────────────────────────────────────
# CSV + Directory helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    make_directories(os.path.dirname(WORKOPOLIS_APPLIED_FILE))
    make_directories(os.path.dirname(WORKOPOLIS_FAILED_FILE))
    make_directories(os.path.dirname(WORKOPOLIS_SKIPPED_FILE))

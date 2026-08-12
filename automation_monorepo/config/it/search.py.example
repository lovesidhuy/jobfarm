"""Search terms, filters, and job-fit preferences (IT-Indeed bot).

Search terms tailored to a Network Administration & Security student with
AWS certifications and customer-facing tech-support background. Order matters:
highest-success roles (QA/IT Support/Data) first so we hit those before
switch_number caps the term.

Source of truth for IT-specific tuning lives in
``data/training/it_training_data.json``.
"""


###################################################### SEARCH PREFERENCES ######################################################

# These terms are searched on Indeed and Glassdoor.
# Hero IT terms only (see hero_terms.py). Full 200+ list retired from main loop.
import importlib.util as _ilu
from pathlib import Path as _Path
_hero_path = _Path(__file__).resolve().parent / "hero_terms.py"
_hero_spec = _ilu.spec_from_file_location("it_hero_terms", _hero_path)
_hero = _ilu.module_from_spec(_hero_spec)
_hero_spec.loader.exec_module(_hero)
search_terms = list(_hero.HERO_SEARCH_TERMS)
company_site_search_terms = list(_hero.COMPANY_SITE_THIN_TERMS)
portal_core_search_terms = list(_hero.PORTAL_CORE_TERMS)

# Search location(s) for the current Metro Vancouver-only collection run.
# Remote and blank-location passes are temporarily paused.
search_location = "Vancouver, British Columbia, Canada"     # Used by Glassdoor bot (single location)
search_locations = [
    "Surrey, BC",
    "Vancouver, BC",
    "Richmond, BC",
    "Burnaby, BC",
    "Coquitlam, BC",
    "Langley, BC",
    "Delta, BC",
    "White Rock, BC",
    "New Westminster, BC",
    "North Vancouver, BC",
    "Port Coquitlam, BC",
]
search_radius_km = 25
# After how many number of applications in current search should the bot switch to next search?
switch_number = 30        # Smaller for IT so we cycle through more roles per session. Only numbers > 0; do NOT use quotes.

# Do you want to randomize the search order for search_terms?
randomize_search_order = False     # True of False, Note: True or False are case-sensitive


# >>>>>>>>>>> Job Search Filters <<<<<<<<<<<
''' 
You could set your preferences or leave them as empty to not select options except for 'True or False' options. Below are some valid examples for leaving them empty:
This is below format: QUESTION = VALID_ANSWER

## Examples of how to leave them empty. Note that True or False options cannot be left empty! 
* question_1 = ""                    # answer1, answer2, answer3, etc.
* question_2 = []                    # (multiple select)
* question_3 = []                    # (dynamic multiple select)

## Some valid examples of how to answer questions:
* question_1 = "answer1"                  # "answer1", "answer2", "answer3" or ("" to not select). Answers are case sensitive.
* question_2 = ["answer1", "answer2"]     # (multiple select) "answer1", "answer2", "answer3" or ([] to not select). Note that answers must be in [] and are case sensitive.
* question_3 = ["answer1", "Random AnswER"]     # (dynamic multiple select) "answer1", "answer2", "answer3" or ([] to not select). Note that answers must be in [] and need not match the available options.

'''

sort_by = ""                       # "Most recent", "Most relevant" or ("" to not select) 
date_posted = "Past week"         # "Any time", "Past month", "Past week", "Past 24 hours" or ("" to not select)
salary = ""                        # "$40,000+", "$60,000+", "$80,000+", "$100,000+", "$120,000+", "$140,000+", "$160,000+", "$180,000+", "$200,000+"

# IT-Indeed default: False, so BOTH flows run through the AI gate.
#  - Easy Apply jobs        → gate → Indeed SmartApply (auto-apply).
#  - Apply on company site  → gate → click Indeed Save bookmark for later manual review.
ease_apply_only_legacy_alias = False  # (kept for clarity; main flag below)
easy_apply_only = True             # EA-first farm; company-site only via rare thin harvest

experience_level = ["Internship", "Entry level", "Associate", "Mid-Senior level", "Director", "Executive"]              # (multiple select) "Internship", "Entry level", "Associate", "Mid-Senior level", "Director", "Executive"
job_type = ["Full-time", "Part-time", "Contract", "Temporary", "Volunteer", "Internship", "Other"]                      # (multiple select) "Full-time", "Part-time", "Contract", "Temporary", "Volunteer", "Internship", "Other"
on_site = []                       # (multiple select) "On-site", "Remote", "Hybrid". Leave empty; Indeed auto-enables Remote only for the blank-location pass.

companies = []                     # (dynamic multiple select) make sure the name you type in list exactly matches with the company name you're looking for, including capitals. 
                                   # Eg: "7-eleven", "Google","X, the moonshot factory","YouTube","CapitalG","Adometry (acquired by Google)","Meta","Apple","Byte Dance","Netflix", "Snowflake","Mineral.ai","Microsoft","JP Morgan","Barclays","Visa","American Express", "Snap Inc", "JPMorgan Chase & Co.", "Tata Consultancy Services", "Recruiting from Scratch", "Epic", and so on...
location = []                      # (dynamic multiple select)
industry = []                      # (dynamic multiple select)
job_function = []                  # (dynamic multiple select)
job_titles = []                    # (dynamic multiple select)
benefits = []                      # (dynamic multiple select)
commitments = []                   # (dynamic multiple select)

under_10_applicants = False        # True or False, Note: True or False are case-sensitive
in_your_network = False            # True or False, Note: True or False are case-sensitive
fair_chance_employer = False       # True or False, Note: True or False are case-sensitive


## >>>>>>>>>>> RELATED SETTING <<<<<<<<<<<

# Pause after applying filters to let you modify the search results and filters?
pause_after_filters = False         # True or False, Note: True or False are case-sensitive

##




## >>>>>>>>>>> SKIP IRRELEVANT JOBS <<<<<<<<<<<
 
# Avoid applying to these companies, and companies with these bad words in their 'About Company' section...
about_company_bad_words = ["Crossover"]       # (dynamic multiple search) or leave empty as []. Ex: ["Staffing", "Recruiting", "Name of Company you don't want to apply to"]

# Skip checking for `about_company_bad_words` for these companies if they have these good words in their 'About Company' section... [Exceptions, For example, I want to apply to "Robert Half" although it's a staffing company]
about_company_good_words = []      # (dynamic multiple search) or leave empty as []. Ex: ["Robert Half", "Dice"]

# Avoid applying to jobs whose description contains these phrases.  IT-focused list:
#  - Hard rejects: US citizenship, security clearance, French-required, trades certs.
#  - Non-IT title noise: kitchen/restaurant, mechanic/welder, driving licences.
bad_words = [
    # Hard legal / certification blockers
    "US Citizen", "USA Citizen", "No C2C", "No Corp2Corp",
    "French required", "bilingual French", "english and french required",
    "Security Clearance", "polygraph", "Secret Clearance",
    # Trades / heavy labour that don't fit IT student profile
    "Class 1", "AZ driver", "Red Seal", "journeyperson", "journeyman",
    "CNC", "Welder", "welder", "Mechanic", "Plumber", "Electrician",
    "Carpenter", "Roofer",
    # Restaurant / food (often pop up in entry-level searches)
    "line cook", "pizza maker", "sous chef", "dishwasher", "barista",
    "server/cashier", "front of house",
    # Licensed clinical healthcare (not a fit for an IT student)
    "registered nurse", "licensed practical nurse", "HCA certificate",
    # Sales pressure / commission-only
    "commission only", "commission-only", "door-to-door",
]

# Do you have an active Security Clearance? (True for Yes and False for No)
security_clearance = False         # True or False, Note: True or False are case-sensitive

# Do you have a Masters degree? (True for Yes and False for No). If True, the tool will apply to jobs containing the word 'master' in their job description and if it's experience required <= current_experience + 2 and current_experience is not set as -1. 
did_masters = False                 # True or False, Note: True or False are case-sensitive

# Avoid applying to jobs if their required experience is above your current_experience.
# IT-Indeed: 3 years of relevant IT-adjacent / tech-support experience (KPU 2022+, Bell Canada tech-support 2018-2021).
# Set -1 if you want to apply to all ignoring their required experience.
current_experience = 3             # Integers > -2 (Ex: -1, 0, 1, 2, 3, 4...)
##

# >>>>>>>>>>> Indeed Run-Control Settings <<<<<<<<<<<

# Cycle through date_posted values across multiple run_non_stop cycles?
# When True the bot will rotate: Any time → Past month → Past week → Past 24 hours → Any time …
cycle_date_posted = True           # True or False


# Glassdoor uses the same IT search terms (no Glassdoor-specific subset for IT).
# The General profile defines a tighter Glassdoor subset; for IT we reuse the
# full list so `core/portals/glassdoor.py` and `glassdoor_it.py` can import.
glassdoor_search_terms = list(getattr(_hero, 'PORTAL_CORE_TERMS', search_terms))

# LinkedIn JobSpy discovery only — short high-signal IT list (full ``search_terms``
# is too large × metro cities × dual passes; a prior full run timed out and
# discarded ~40k in-memory jobs). Indeed/Glassdoor/Workopolis keep ``search_terms``.
linkedin_search_terms = list(_hero.LINKEDIN_HERO_TERMS)


# Wave B.1 Glassdoor discovery: metro cities only (no Remote / empty remote pass).
# Wired via ``config/it/glassdoor_search.py`` when portals=["glassdoor"].
glassdoor_search_locations = [
    loc for loc in search_locations if (loc or "").strip() and (loc or "").strip().lower() != "remote"
]
glassdoor_easy_apply_only = True

"""Search terms, filters, and job-fit preferences — GENERAL (office / CS).

Production discovery uses ``hero_terms.HERO_SEARCH_TERMS`` (short list).
``search_terms_legacy`` holds the long office/CS catalog for rare deep harvests.
"""

from __future__ import annotations

from config.general import hero_terms as _hero

###################################################### SEARCH PREFERENCES ######################################################

# These terms are searched on the selected platform.
##search_terms = ["IT Support Specialist","Help Desk Analyst","Network Administrator","System Administrator","Technical Support Analyst","Cloud Support Associate","AWS Cloud Engineer","DevOps Engineer","Site Reliability Engineer","SOC Analyst","Cybersecurity Analyst","Security Operations Analyst","Backend Developer","Software Developer Intern","Automation Engineer","QA Automation Tester","IT Technician","Desktop Support Technician","Technical Customer Support","NOC Technician","Infrastructure Engineer","Cloud Engineer Intern","Platform Engineer","Linux System Administrator","Windows System Administrator","IT Operations Analyst","Field Service Technician","Service Desk Analyst","Application Support Analyst","IT Analyst","Systems Support Specialist","Network Support Specialist","Junior Network Engineer","Junior Systems Engineer","Security Analyst Intern","Information Security Analyst","Penetration Tester Intern","Vulnerability Analyst","Cloud Security Analyst","DevSecOps Engineer","Build and Release Engineer","CI/CD Engineer","Support Engineer","Production Support Engineer","Incident Response Analyst","Technical Operations Engineer","IT Coordinator","IT Assistant","Junior Software Engineer","Full Stack Developer Intern","API Developer","Python Developer Intern","Java Developer Intern","Web Developer","Frontend Developer Intern","Database Administrator","Data Analyst","Business Intelligence Analyst"]

search_terms_legacy = [
    # Customer Service & Office Support Only
    "Customer Service Representative",
    "Customer Service Associate",
    "Customer Service Agent",
    "Customer Service Clerk",
    "Customer Service Assistant",
    "Customer Experience Associate",
    "Customer Experience Representative",
    "Customer Care Representative",
    "Customer Care Associate",
    "Client Service Representative",
    "Client Service Associate",
    "Client Services Assistant",
    "Guest Services Representative",
    "Guest Services Associate",
    "Member Services Representative",
    "Member Service Representative",
    "Service Representative",
    "Service Desk Representative",
    "Service Counter Representative",

    # Front desk / reception / office desk roles
    "Receptionist",
    "Front Desk Receptionist",
    "Front Desk Agent",
    "Front Desk Clerk",
    "Front Desk Associate",
    "Office Assistant",
    "Office Clerk",
    "Office Support Clerk",
    "Office Services Clerk",
    "Office Services Assistant",
    "Administrative Assistant",
    "Admin Assistant",
    "Administrative Clerk",
    "Admin Clerk",
    "Administrative Support Clerk",
    "Clerical Assistant",
    "Clerical Clerk",
    "General Office Clerk",
    "Information Desk Clerk",
    "Information Desk Assistant",

    # Light PC / data / document work
    "Data Entry Clerk",
    "Data Entry Assistant",
    "Data Entry Operator",
    "Order Entry Clerk",
    "Order Entry Assistant",
    "Order Desk Clerk",
    "Records Clerk",
    "Records Assistant",
    "File Clerk",
    "Filing Clerk",
    "Scanning Clerk",
    "Scanning Assistant",
    "Document Clerk",
    "Document Scanner",
    "Document Control Assistant",
    "Document Processing Clerk",
    "Mailroom Clerk",
    "Mail Clerk",
    "Mailroom Assistant",
    "Mail Sorter",
    "Print Clerk",
    "Copy Centre Clerk",
    "Copy Center Clerk",

    # Call centre / phone support - non-IT
    "Call Centre Representative",
    "Call Center Representative",
    "Call Centre Agent",
    "Call Center Agent",
    "Contact Centre Agent",
    "Contact Center Agent",
    "Contact Centre Representative",
    "Contact Center Representative",
    "Phone Representative",
    "Telephone Representative",
    "Appointment Scheduler",
    "Appointment Coordinator",
    "Appointment Booking Clerk",
    "Booking Clerk",
    "Scheduling Assistant",
    "Scheduling Coordinator",
    "Customer Booking Agent",

    # Easy admin/service backup roles
    "Rental Agent",
    "Rental Associate",
    "Reservation Agent",
    "Reservation Clerk",
    "Reservations Agent",
    "Service Administrator",
    "Service Coordinator",
    "Service Assistant",
    "Program Assistant",
    "Department Assistant",
    "Intake Coordinator",
    "Intake Clerk",
    "Office Coordinator",
    "Operations Assistant",
    "Operations Clerk",
    "Business Support Assistant",
    "Warranty Administrator",
    "Warranty Clerk",
    "Claims Assistant",
    "Claims Clerk",
    "Returns Clerk",
    "Returns Associate",

    # Simple sales / counter / showroom (desk-based support)
    "Inside Sales Representative",
    "Inside Sales Associate",
    "Sales Support Representative",
    "Sales Support Associate",
    "Sales Support Clerk",
    "Showroom Receptionist",
    "Customer Sales Representative",
    "Customer Sales Associate",
    "Counter Service Representative",

    # Easy clinic / office reception, no certificate focus
    "Clinic Receptionist",
    "Medical Receptionist",
    "Dental Receptionist",
    "Patient Service Representative",
    "Patient Services Clerk",
    "Patient Booking Clerk",
    "Appointment Clerk",

    # Simple banking / membership / service roles
    "Bank Teller",
    "Member Service Advisor",
    "Member Service Associate",
    "Customer Service Advisor",
    "Service Advisor",
    "Client Care Associate",

    # Entry-level office/customer support wording
    "Entry Level Customer Service",
    "Entry Level Receptionist",
    "Entry Level Office Assistant",
    "Entry Level Data Entry",
    "No Experience Customer Service",
    "Training Provided Customer Service",
    "Training Provided Receptionist",
    "Training Provided Office Assistant"
]

# Production Easy Apply farm — short office/CS hero only (Indeed general).
# Glassdoor/Workopolis have no general bots (IT only). LinkedIn is one bot
# with IT+office terms; see config/general/hero_terms.LINKEDIN_HERO_TERMS.
search_terms = list(_hero.HERO_SEARCH_TERMS)
linkedin_search_terms = list(_hero.LINKEDIN_HERO_TERMS)
glassdoor_search_terms = list(search_terms)  # unused in prod (no glassdoor_general)

# Search location(s)
search_location = "Vancouver, British Columbia, Canada"
search_locations = [
    "Surrey, BC",
    "Vancouver, BC",
    "Burnaby, BC",
    "Richmond, BC",
    "Langley, BC",
    "Delta, BC",
    "White Rock, BC",
    "New Westminster, BC",
    "Coquitlam, BC",
    "Port Coquitlam, BC",
    "North Vancouver, BC",
]
search_radius_km = 25
# After how many number of applications in current search should the bot switch to next search? 
switch_number = 60        # Apply to more jobs per search term before rotating

# Do you want to randomize the search order for search_terms?
randomize_search_order = True     # True of False, Note: True or False are case-sensitive

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
date_posted = "Past week"         # 7 days — cycle_date_posted still rotates across runs
salary = ""                        # "$40,000+", "$60,000+", "$80,000+", "$100,000+", "$120,000+", "$140,000+", "$160,000+", "$180,000+", "$200,000+"

easy_apply_only = True             # True or False, Note: True or False are case-sensitive

experience_level = ["Internship", "Entry level", "Associate", "Mid-Senior level", "Director", "Executive"]
job_type = ["Full-time", "Part-time", "Contract", "Temporary", "Volunteer", "Internship", "Other"]                      # (multiple select) "Full-time", "Part-time", "Contract", "Temporary", "Volunteer", "Internship", "Other"
on_site = []                       # (multiple select) "On-site", "Remote", "Hybrid"

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

# Avoid applying to these companies if they have these bad words in their 'Job Description' section...  (In development)
bad_words = [
    # Hard legal / certification blockers
    "US Citizen", "USA Citizen", "No C2C", "No Corp2Corp",
    "French required", "bilingual French", "english and french required",
    "Security Clearance", "polygraph", "Secret Clearance",
    # Commercial driving / trades tickets — require specific licences
    "Class 1 licence", "Class 1 license", "AZ licence", "AZ license",
    "Red Seal", "journeyperson", "journeyman",
    "CNC", "Welder", "welder", "Mechanic", "Plumber", "Electrician",
    "Carpenter", "Roofer", "HVAC ticket", "refrigeration ticket",
    # Licensed clinical healthcare — require RN/LPN/HCA certification
    "registered nurse", "licensed practical nurse", "LPN licence", "LPN license",
    "RN licence", "RN license", "HCA certificate", "health care aide certificate",
    # Commission-only / high-risk sales
    "commission only", "commission-only", "door-to-door", "door to door",
    "100% commission", "commission based only",
    # Physical labor, strenuous requirements, and food safety certs
    "heavy lifting", "heavy labour", "heavy labor",
    "lift 25", "lift 30", "lift 40", "lift 50", "lift 60",
    "lift up to 25", "lift up to 30", "lift up to 40",
    "lift up to 50", "lift up to 60",
    "able to lift", "must lift", "required to lift",
    "repetitive lifting", "manual lifting", "physically demanding",
    "strenuous", "loading and unloading", "load and unload",
    "material handling", "pallet jack", "forklift", "order picking",
    "picker packer", "stand for long periods", "standing for long periods",
    "prolonged standing", "stand for extended", "standing for extended",
    "physical labor", "physical labour", "manual labor", "manual labour",
    "food handler certificate", "food safe", "foodsafe",
]

# Skip applying to jobs if their TITLE matches any of these keywords (strictly checked in TITLE only)
bad_titles = [
    # Security / enforcement
    "security", "guard", "loss prevention officer", "asset protection",
    "mobile patrol", "parking enforcement", "traffic control", "crowd control",
    "door supervisor", "concierge security",

    # Cleaning / janitorial / housekeeping
    "cleaning", "sanitation", "sanitation worker", "building cleaner",
    "commercial cleaner", "night cleaner", "light duty cleaner",
    "laundry attendant", "linen attendant", "room checker",
    "cleaner", "janitor", "custodian", "housekeeper", "housekeeping", "room attendant",
    "dish washer", "dishwasher",

    # Warehouse / labour / physical
    "warehouse", "labour", "labor", "production worker", "factory worker",
    "assembly worker", "assembler", "manufacturing associate",
    "dock worker", "loader", "unloader", "package handler",
    "shipping receiving", "shipping/receiving", "shipper receiver",
    "stock handler", "freight", "cargo handler",
    "material handler", "forklift", "order picker", "picker", "packer",
    "packaging associate", "labourer", "laborer", "mover", "packager", "sorter",

    # Driver / delivery
    "driver", "delivery driver", "courier", "truck driver",
    "class 1", "class 3", "class 5 driver", "route driver",

    # Trades / mechanic / technician physical roles
    "mechanic", "installer", "apprentice", "technician",
    "service technician", "field technician", "maintenance worker",
    "maintenance technician", "handyman", "carpenter", "plumber",
    "electrician", "hvac", "roofer", "painter", "landscaper", "landscaping", "gardener",

    # Insurance / finance sales
    "insurance broker", "insurance agent", "life insurance",
    "financial advisor", "financial services representative",
    "wealth advisor", "investment advisor", "mortgage broker",
    "benefits advisor", "commission sales",
    "underwriter", "claims adjuster", "actuary", "insurance advisor", "insurance claims",

    # Healthcare / care aide roles
    "care aide", "health care aide", "healthcare aide", "personal support worker",
    "support worker", "community support worker", "residential support worker",
    "home support worker", "caregiver", "nursing assistant",

    # IT / technical roles — skip for this ASAP non-IT run
    "it support", "help desk", "technical support", "desktop support",
    "network support", "systems administrator", "system administrator",
    "software developer", "web developer", "programmer", "cybersecurity",
    "noc technician", "computer technician",
    "software engineer", "frontend developer", "backend developer", "full stack",
    "cloud engineer", "DevOps", "data analyst", "business analyst", "QA analyst",
    "quality assurance analyst", "computer science", "programming", "Python", "JavaScript", "SQL",

    # Heavy marketing / sales and high-risk sales
    "digital marketing", "marketing manager", "marketing coordinator",
    "social media manager", "social media coordinator", "SEO", "Google Ads",
    "business development", "lead generation",

    # Food / hospitality / kitchen / baking (strictly NO cooking, NO food)
    "cook", "chef", "barista", "server", "waiter", "waitress", "hostess", "host",
    "bartender", "baker", "baking", "food counter", "kitchen helper", "line cook",
    "prep cook", "sous chef", "food service worker", "dietary aide", "kitchen porter",
    "busser", "crew member", "restaurant team member", "deli", "bakery",

    # Heavy retail / cashier (floor-based / standing roles)
    "cashier", "clerk cashier", "grocery clerk", "produce clerk", "deli clerk",
    "bakery clerk", "meat cutter", "stock associate", "stocker", "shelf stocker",
    "night stocker", "merchandise stocker", "merchandiser", "lot associate",
    "lot attendant", "parking attendant", "gas station attendant", "convenience store clerk",
    "sales floor associate", "retail associate", "retail sales associate",
    "retail clerk", "store associate", "store clerk", "floor associate"
]                     # (dynamic multiple search) or leave empty as []. Case Insensitive. Ex: ["word_1", "phrase 1", "word word", "polygraph", "US Citizenship", "Security Clearance"]

# Do you have an active Security Clearance? (True for Yes and False for No)
security_clearance = False         # True or False, Note: True or False are case-sensitive

# Do you have a Masters degree? (True for Yes and False for No). If True, the tool will apply to jobs containing the word 'master' in their job description and if it's experience required <= current_experience + 2 and current_experience is not set as -1. 
did_masters = False                 # True or False, Note: True or False are case-sensitive

# Avoid applying to jobs if their required experience is above your current_experience. (Set value as -1 if you want to apply to all ignoring their required experience...)
current_experience = 7             # Integers > -2 (Ex: -1, 0, 1, 2, 3, 4...)
##

# >>>>>>>>>>> Indeed Run-Control Settings <<<<<<<<<<<

# Cycle through date_posted values across multiple run_non_stop cycles?
# When True the bot will rotate: Any time → Past month → Past week → Past 24 hours → Any time …
cycle_date_posted = True           # True or False

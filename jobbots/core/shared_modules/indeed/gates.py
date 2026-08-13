from __future__ import annotations

from ._bootstrap import *  # noqa: F403

def _groq_gate_user_profile() -> str:
    """Full IT candidate profile sent to Groq. Anchored on `all resumes/resumedump.txt`."""
    return """
Name: Jane Doe
Application email: user@example.com
Resume email: user@example.com
Phone: 555-0199
Location: Surrey, BC, Canada
Portfolio: https://example.com/portfolio
LinkedIn: https://linkedin.com/in/example
Desired salary: $70,000 CAD (flexible $60-90K for the right IT role; will accept internships/co-ops/contracts)
Work authorization: Canadian Citizen/Permanent Resident (does not need sponsorship)
Years of relevant IT experience: 3 (KPU BTech IT since Sep 2022 + 3 years Bell Canada technical support)
Certifications: AWS Certified Solutions Architect – Associate (Nov 2024); AWS Cloud Practitioner (Feb 2024)

Target IT roles (in order of preference, derived from `data/training/it_training_data.json`):
• QA / Testing: Quality Assurance Analyst, QA Analyst, QA Tester, Software Test Engineer, SDET, Automation Tester, Manual Tester, Test Analyst, Quality and Assurance Specialist.
• IT Support / Service Desk / Help Desk: IT Support, IT Support Co-op, IT Support Analyst, IT Technician, Service Desk, Help Desk, Desktop Support, Technical Support Analyst/Specialist/Engineer/Representative, Client Services Specialist/Analyst, Product Support Specialist, Application Support Specialist, Systems Onboarding & Tech Support Coordinator, ERP Support Analyst, Customer Support Specialist (corporate technical support), Application Support, Production Support, Support Engineer, Computer Technician, Field Service Technician.
• Data: Data Analyst, Junior Data Analyst, Data Collection Specialist, BI Analyst, Reporting Analyst, Data Quality Analyst.
• Systems / Network / Infrastructure: Systems Administrator, Network Administrator, Network Technician, Network Support, Infrastructure Analyst/Support, IT Infrastructure Technician, NOC Technician/Analyst, Security Systems Technician.
• Cloud / DevOps: Cloud Support Associate/Analyst/Engineer, AWS/Azure Support, Cloud Operations Analyst, Junior DevOps Engineer, Cloud Infrastructure Engineer.
• Security: SOC Analyst, Security Analyst, Information Security Analyst, Cybersecurity Analyst, Network Security Analyst, Vulnerability Analyst, IT Cyber Security Consultant, IT Security Consultant.
• AI / Machine Learning: AI Engineer, Machine Learning Engineer, GenAI Developer, Prompt Engineer, LLM Developer, NLP Developer, AI Specialist.
• Generic IT: IT Analyst, IT Coordinator, IT Assistant, Junior Software Engineer, Database Administrator, IT Consultant.

Resume Summary of Qualifications:
Bachelor of Technology candidate (Network Administration & Security, KPU) and AWS Certified Solutions Architect with hands-on experience in enterprise networking, cloud infrastructure, and systems security across Linux and Windows Server environments. Built and validated security infrastructure including RADIUS/EAP-TLS authentication, OSSEC HIDS, and cloud-native architectures on AWS, with practical experience in incident response, log analysis, and root cause documentation. Over three years of customer-facing technical support at Bell Canada diagnosing iOS/Android, Wi-Fi, and network configuration issues daily, alongside 2.5+ years operating in a high-volume clinical field environment at Vancouver Coastal Health.

Technical Skills:
Networking & Security: Cisco IOS, VLANs, VPNs, OSPF, BGP, 802.1X, WPA3, Wi-Fi 6, Firewall Policy, Nmap, Wireshark.
Security Operations: Splunk, Wazuh, OSSEC, SIEM, Autopsy, FTK, endpoint hardening.
Cloud & Virtualization: AWS (VPC, EC2, S3, IAM, CloudFormation), Docker, Terraform, VMware, Hyper-V.
Systems & Automation: Windows Server (AD DS, GPO, NPS), Linux (Ubuntu/CentOS), Python (Boto3), Bash, Ansible.
Dev & Tools: Java, SQL (MySQL/MongoDB), RESTful APIs, Git, Postman, Spring Boot, technical documentation.

Education:
KPU — Bachelor of Technology, Information Technology | Specialization: Network Administration & Security (Sep 2022 – Dec 2026 expected).
Coursework: Cloud Computing, System Security, Networking Technologies, Digital Forensics, IoT Systems.

Experience:
Porter — Vancouver Coastal Health, Oct 2022 – Present, Vancouver, BC. Patient-tracking software, mobile devices, equipment checks, inventory logs, escalation. (NOTE: Porter is a non-IT role; do NOT use this as a reason to approve other non-IT general-work roles. The candidate is targeting IT roles only.)
Sales & Technical Support Representative — Bell Canada (Authorized Dealer), Apr 2018 – Aug 2021, Surrey, BC. Diagnosed iOS/Android, Wi-Fi/Bluetooth, network configuration issues across 20+ daily client interactions. Maintained resolution logs, escalated systemic issues, translated 5G/architecture into plain language.

Decision preference for the gate (STRICT — IT-only bot):
APPROVE only these IT roles in Surrey/Vancouver/Lower Mainland/BC/Canada or remote: QA / Software Test, IT Support / Service Desk / Help Desk / Desktop Support / Technical Support, Client Services Specialist/Analyst, Product Support Specialist, Application Support Specialist, Systems Onboarding & Tech Support Coordinator, Customer Support Specialist (corporate technical support blends), Application/Production Support, Network/Systems/Cloud Administration, Security Systems Technician, Cloud Infrastructure Engineer, IT Cyber Security Consultant, AI Engineer, Machine Learning Engineer, GenAI Developer, Prompt Engineer, LLM Developer, NLP Developer, AI Specialist, Infrastructure, NOC, Cloud Support (AWS/Azure), Data Analyst (junior), BI/Reporting Analyst, SOC / Security Analyst (junior), DevOps (junior), Junior Software Engineer, Database Administrator, IT Analyst/Coordinator/Assistant, and IT internships/co-ops.

HARD REJECT (fit_score must be 0-39, hire_chance "low") for any role that is not explicitly IT/technical, even if the candidate has "transferable" customer service or general-work experience. Specifically reject: customer service / call centre / contact centre (except corporate technical support blends), retail / cashier / sales associate / brand ambassador, hospitality / banquet / catering / food service / front desk / guest services, healthcare / clinical / dental / patient services / rehabilitation / childcare / ECE / education assistant, HR / recruiting / talent acquisition / employee experience / people & culture, legal / paralegal / conveyancer / law clerk / real estate, accounting / payroll / accounts payable / bookkeeping / fundraising / stewardship, construction / trades / project coordinator (non-IT) / estimator / road works, marketing (non-technical) / brand / paid media / graphic design / product/UX/UI design, operations coordinator / admin / receptionist / office assistant / shipping & inventory / data entry, kitchen / restaurant / cleaning / janitorial / housekeeping / security guard / driving (Class 1/AZ).

Also REJECT roles requiring: US citizenship / security clearance / polygraph, French-required or English-and-French-required, trade tickets (Red Seal, journeyman, welder, electrician, plumber, carpenter, roofer, mechanic, HVAC), licensed clinical healthcare (RN/LPN/HCA), commission-only sales, senior leadership or 8+ years experience minimums, and roles outside Canada that are not remote-eligible.

The candidate's "transferable skills" from Porter / Bell are NOT a valid reason to APPROVE a non-IT role. If the title is not in the APPROVE list above, set save=false, fit_score≤39, hire_chance="low".
"""


_GROQ_GATE_USER_PROFILE = _groq_gate_user_profile()


def _tiny_job_gate_user_profile() -> str:
    """Compact IT profile used by the local Ollama fallback gate."""
    return (
        "IT student in Surrey, BC — KPU Bachelor of Technology in Information Technology "
        "(Network Administration & Security), AWS Certified Solutions Architect – Associate, "
        "AWS Cloud Practitioner. 3 years of IT-relevant experience (Bell Canada technical "
        "support diagnosing iOS/Android, Wi-Fi, networking; KPU lab projects: AWS VPC, RADIUS/"
        "EAP-TLS, OSSEC HIDS, Spring Boot, Docker, Python). Targeting QA Analyst, IT Support, "
        "Service Desk, Help Desk, Desktop Support, Technical Support, Client Services Specialist/Analyst, Product Support Specialist, "
        "Application Support Specialist, Systems Onboarding & Tech Support Coordinator, IT Cyber Security Consultant, IT Security Consultant, "
        "Customer Support Specialist (technical/corporate), Systems/Network/Cloud Administrator, Security Systems Technician, "
        "Cloud Infrastructure Engineer, NOC, SOC Analyst, Junior Cybersecurity/Security Analyst, Junior DevOps, Junior Software Engineer, "
        "Database Administrator, IT Consultant, AI/ML Engineer, Prompt Engineer, and any IT internship/co-op. Canadian Citizen/PR, no sponsorship needed. Desired $70K CAD."
    )


def _gate_text_has_it_signal(text: str) -> bool:
    low = (text or "").lower()
    phrases = (
        "information technology", "it support", "it service", "service desk",
        "help desk", "helpdesk", "desktop support", "technical support",
        "application support", "production support", "computer technician",
        "systems administrator", "system administrator", "network administrator",
        "network technician", "network support", "infrastructure analyst",
        "infrastructure support", "cloud support", "cloud engineer",
        "site reliability", "security analyst", "soc analyst", "qa analyst",
        "qa engineer", "qa tester", "quality assurance", "software test",
        "automation tester", "software developer", "software engineer",
        "web developer", "backend developer", "frontend developer",
        "full stack", "data analyst", "business intelligence", "bi analyst",
        "reporting analyst", "data engineer", "data analytics",
    )
    tokens = (
        "it", "sysadmin", "network", "infrastructure", "noc", "cloud", "aws",
        "azure", "devops", "sre", "cyber", "cybersecurity", "security", "siem", "iam",
        "vulnerability", "sdet", "backend", "frontend", "api", "python", "java", 
        "javascript", "database", "sql", "qa", "test", "tester", "developer", 
        "programmer", "support", "helpdesk", "linux", "systems"
    )
    padded = f" {low} "
    if any(phrase in padded for phrase in phrases):
        return True
    return any(re.search(rf"\b{re.escape(token)}\b", low) for token in tokens)


def _extract_json_object(text) -> dict:
    if isinstance(text, dict):
        for key in ("data", "content", "message"):
            nested = text.get(key)
            if isinstance(nested, str) and "{" in nested:
                parsed = _extract_json_object(nested)
                if parsed:
                    return parsed
        return text
    text = (text or "").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except Exception:
        return {}


def check_local_title_relevance(title: str, easy_apply: bool = False) -> tuple[bool, str]:
    """
    Checks if a job title is relevant for the IT profile based on strict combination rules
    and mandatory signals for generic words.
    Returns (is_rejected, reason).
    """
    title_l = (title or "").lower()

    # 1. Hard exclusions (direct non-IT fields or bad combinations)
    
    # physical security
    if "security" in title_l:
        physical_security_terms = ["guard", "officer", "supervisor", "patrol", "site", "commissioner", "warden", "concierge"]
        cyber_context = any(term in title_l for term in (
            "information security", "cyber security", "cybersecurity", "ciso",
            "application security", "network security", "cloud security",
        ))
        if not cyber_context and any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in physical_security_terms):
            return True, f"physical security role: {title}"
            
    # non-IT support
    if "support" in title_l:
        non_it_support_terms = ["sales", "order", "service only", "client support", "b2b", "wholesale", "floral", "cafe", "café", "ambassador"]
        if any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in non_it_support_terms):
            strong_it_signals = ["it", "technical", "helpdesk", "help desk", "desktop", "deskside", "systems", "network", "software"]
            if not any(re.search(rf"\b{re.escape(sig)}\b", title_l) for sig in strong_it_signals):
                return True, f"non-IT support role: {title}"

    # sales + technical
    if "technical" in title_l and "sales" in title_l:
        return True, f"technical sales / sales engineering: {title}"

    # Pure sales / hotel / front-office — never IT (false-approved via JD keywords)
    pure_sales_titles = (
        "sales coordinator", "sales assistant", "sales associate",
        "sales representative", "sales rep", "sales clerk",
        "account coordinator", "revenue coordinator", "reservations coordinator",
        "guest services", "front desk", "concierge",
    )
    if any(t in title_l for t in pure_sales_titles):
        return True, f"non-IT sales/hospitality title: {title}"
    if re.search(r"\bsales\b", title_l) and not any(
        s in title_l for s in ("salesforce", "sales engineer", "pre-sales", "presales", "technical sales")
    ):
        # Bare "Sales …" without IT qualifier
        if not any(s in title_l for s in ("it ", " software", "saas", "tech ", "technical")):
            return True, f"non-IT sales title: {title}"

    # Electrical / power / civil engineer — not software (JD often still mentions "systems")
    if re.search(r"\belectrical engineer\b", title_l) or re.search(r"\bpower engineer\b", title_l):
        if not any(s in title_l for s in ("software", "firmware", "embedded", "controls software")):
            return True, f"non-IT electrical/power engineer: {title}"
    if re.search(r"\bcivil engineer\b", title_l) or re.search(r"\bmechanical engineer\b", title_l):
        if not any(s in title_l for s in ("software", "mechatronics", "robotics", "automation software")):
            return True, f"non-IT traditional engineer: {title}"

    # Non-software programmer domain (fire alarm, PLC, HMI, CNC, machinery)
    if "programmer" in title_l:
        non_sw_prog_terms = ["fire alarm", "alarm", "plc", "hmi", "machinery", "cnc", "cabinet", "millwork", "fabrication", "panel"]
        if any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in non_sw_prog_terms):
            return True, f"non-software programmer domain: {title}"

    # Non-IT Customer Care / Education / Client Care / Family Care
    if any(term in title_l for term in ("customer care", "customer education", "client care", "patient care", "family care", "member care", "guest education")):
        strong_it_signals = ["it", "technical", "helpdesk", "help desk", "desktop", "deskside", "systems", "network", "software"]
        if not any(re.search(rf"\b{re.escape(sig)}\b", title_l) for sig in strong_it_signals):
            return True, f"non-IT customer care/education role: {title}"

    # Non-IT traditional CAD / civil / structural / piping / HVAC design / drafting
    if any(term in title_l for term in ("civil designer", "structural designer", "cad designer", "piping designer", "hvac designer", "draftsperson", "draftsman", "structural detailer")):
        if not any(s in title_l for s in ("software", "it", "systems", "network")):
            return True, f"non-IT traditional design role: {title}"

    # Non-IT print production / bindery / media production
    if any(term in title_l for term in ("print production", "bindery", "print specialist", "press operator", "digital print")):
        if not any(s in title_l for s in ("software", "it", "systems")):
            return True, f"non-IT print production role: {title}"

    # wrong data domains
    if "data" in title_l:
        wrong_data_domains = ["marketing", "environmental", "home collection", "field data", "survey", "gis", "geospatial", "entry specialist"]
        if any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in wrong_data_domains):
            return True, f"non-IT data domain: {title}"

    # non-IT developer
    if "developer" in title_l:
        non_it_dev_domains = ["apparel", "product", "fashion", "clothing", "design", "jewelry", "toy", "real estate"]
        if any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in non_it_dev_domains):
            strong_dev_signals = ["software", "web", "app", "database", "python", "java", "c#", "net"]
            if not any(re.search(rf"\b{re.escape(sig)}\b", title_l) for sig in strong_dev_signals):
                return True, f"non-IT developer: {title}"

    # non-IT QA
    if any(q in title_l for q in ["qa", "quality assurance", "quality analyst", "quality control", "qc"]):
        non_it_qa_domains = ["haccp", "food", "safety", "manufacturing", "clinical", "medical", "construction", "biomedical"]
        if any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in non_it_qa_domains):
            strong_qa_signals = ["software", "web", "app", "test", "automation", "sdet", "it"]
            if not any(re.search(rf"\b{re.escape(sig)}\b", title_l) for sig in strong_qa_signals):
                return True, f"non-IT quality assurance/control: {title}"

    # non-IT coordinator
    if "coordinator" in title_l:
        non_it_coord_domains = ["field", "home", "survey", "construction", "operations", "project trades", "intake", "registration", "clinic", "event", "office", "marketing", "logistics"]
        if any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in non_it_coord_domains):
            strong_it_signals = ["it", "systems", "helpdesk", "help desk", "network", "software", "technology"]
            if not any(re.search(rf"\b{re.escape(sig)}\b", title_l) for sig in strong_it_signals):
                return True, f"non-IT coordinator: {title}"

    # teaching roles
    teaching_terms = ["instructor", "teacher", "tutor", "trainer", "professor", "educator"]
    if any(re.search(rf"\b{re.escape(term)}\b", title_l) for term in teaching_terms):
        return True, f"teaching/training role: {title}"

    # language requirements (e.g. French / Mandarin required in title)
    if any(lang in title_l for lang in ["french required", "mandarin required", "french/english", "english/french"]):
        return True, f"unwanted bilingual requirement in title: {title}"
    if re.search(r"\bfrench\b", title_l) and "french not required" not in title_l:
        return True, f"French language indicated in title: {title}"
    if re.search(r"\bbilingual\b", title_l) and not any(lang in title_l for lang in (
        "mandarin", "cantonese", "chinese", "punjabi", "hindi", "spanish",
        "korean", "japanese", "portuguese", "tagalog",
    )):
        return True, "bilingual requirement in title (French expected)"
    french_title_markers = (
        "spécialiste", "specialiste", "administrateur", "représentant", "representant",
        "chargé de", "charge de", "technicien(ne)", "technicien·ne", "professeur", "français", "francais",
        "ingénieur", "ingenieur", "développeur", "developpeur",
    )
    if any(marker in title_l for marker in french_title_markers):
        return True, f"French job title: {title}"

    # Software/engineering developer titles (e.g. "Full Stack Developer") without
    # needing "software" literally in the title.
    if re.search(r"\bdeveloper\b", title_l):
        software_dev_markers = (
            "full stack", "full-stack", "software", "web", "backend", "frontend",
            "front-end", "back-end", "mobile", "application", "embedded software",
            "sql", "python", "java", "javascript", "typescript", ".net", "react",
            "node", "api", "salesforce", "dynamics", "sharepoint",
            "c++", "c#", "c/c++", "rust", "golang", "php", "ruby", "swift", "kotlin",
            "html", "css", "ai", "ml", "genai", "artificial intelligence", "machine learning",
            "nlp", "llm", "deep learning", "computer vision", "reactjs", "angular", "vue",
            "node.js", "express", "django", "flask", "spring", "springboot", "laravel",
            "dotnet", "unreal", "unity", "sap"
        )
        if any(marker in title_l for marker in software_dev_markers):
            return False, ""

    # 2. Must-have IT signals for generic words
    generic_words = ["support", "security", "developer", "data", "coordinator", "analyst", "assistant", "specialist", "technician"]
    has_generic_word = any(re.search(rf"\b{re.escape(word)}\b", title_l) for word in generic_words)
    if has_generic_word and not easy_apply:
        must_have_it_signals = [
            "it", "help desk", "helpdesk", "service desk", "desktop", "deskside",
            "technical support", "tech support", "systems", "network", "cloud",
            "aws", "azure", "microsoft 365", "m365", "office 365", "active directory",
            "software", "web", "database", "cybersecurity", "cyber security",
            "soc", "siem", "information security", "qa", "quality assurance",
            "sdet", "automation", "python", "java", "api", "linux", "programming",
            "programmer", "security analyst", "vulnerability", "sql", "data analyst",
            "data engineer", "data analytics", "bi analyst", "business intelligence",
            "full stack", "full-stack", "engineer", "technologist", "tester",
            "penetration", "salesforce", "erp", "dynamics", "sharepoint",
            "information technology", "technology", "noc", "devops", "sre",
            "client services", "client support", "product support", "application support",
            "systems onboarding", "co-op", "coop", "consultant", "troubleshooting",
            "troubleshoot", "prompt", "prompt engineer",
            "c++", "c#", "c/c++", "rust", "golang", "php", "ruby", "swift", "kotlin",
            "typescript", "javascript", "html", "css", "ai", "ml", "genai",
            "artificial intelligence", "machine learning", "nlp", "llm", "deep learning",
            "computer vision", "react", "angular", "vue", "node", "node.js", "express",
            "django", "flask", "spring", "springboot", "laravel", "dotnet", ".net",
            "unreal", "unity", "sap"
        ]
        has_strong_signal = False
        for sig in must_have_it_signals:
            pattern = re.escape(sig)
            if sig.startswith((".", "/")):
                pattern = r"(?:^|[^a-zA-Z0-9_])" + pattern + r"\b"
            elif sig.endswith(("+", "#")):
                pattern = r"\b" + pattern + r"(?:$|[^a-zA-Z0-9_])"
            elif "/" in sig:
                pattern = r"\b" + pattern + r"(?:$|[^a-zA-Z0-9_])"
            else:
                pattern = r"\b" + pattern + r"\b"
            
            if re.search(pattern, title_l):
                has_strong_signal = True
                break
        
        if not has_strong_signal:
            return True, f"generic title lacking explicit IT signal: {title}"

    return False, ""


def _obvious_non_it_reject(title: str, company: str, location: str,
                           card_text: str, job_details: str,
                           easy_apply: bool = False) -> tuple[bool, str]:
    """Hard rejects we don't need to spend Groq tokens on. Anything that survives
    falls through to either the title whitelist or the Groq/Ollama LLM gate."""
    # Data-quality nulls must never reach apply (company drives dedupe/email).
    company_s = (company or "").strip().lower()
    if company_s in {"", "nan", "none", "null", "n/a", "na", "unknown", "undefined"}:
        return True, "invalid_company"
    # Mass remote-job spam farms (LinkedIn EA volume with near-zero local fit).
    spam_companies = (
        "turing", "turing.com", "outlier", "mercor", "andela",
        "toptal", "gun.io", "arc.dev",
    )
    if any(s == company_s or s in company_s for s in spam_companies):
        return True, "spam_staffing_farm_company"

    # Check local title gates first
    rejected, reason = check_local_title_relevance(title, easy_apply=easy_apply)
    if rejected:
        return True, reason

    text = " ".join([title, company, location, card_text, job_details]).lower()
    title_l = (title or "").lower()

    if _looks_fully_french(text):
        return True, "French-language posting"

    # Hard legal / certification blockers – the bot cannot pass these.
    hard_rejects = (
        ("us citizen",                       "requires US citizenship"),
        ("u.s. citizen",                     "requires US citizenship"),
        ("must be a us citizen",             "requires US citizenship"),
        ("class 1 licence",                  "requires Class 1 licence"),
        ("class 1 license",                  "requires Class 1 licence"),
        ("az driver",                        "requires AZ/Class 1 driving licence"),
        ("red seal",                         "requires Red Seal trade ticket"),
        ("journeyman",                       "requires journeyman trade ticket"),
        ("journeyperson",                    "requires journeyman trade ticket"),
        ("registered nurse",                 "requires RN licence"),
        ("licensed practical nurse",         "requires LPN licence"),
        (" rn license",                      "requires RN licence"),
        (" rn licence",                      "requires RN licence"),
        ("hca certificate",                  "requires HCA certificate"),
        ("health care aide certificate",     "requires HCA certificate"),
        ("red seal welder",                  "requires trade ticket"),
        ("commission only",                  "commission-only role"),
        ("commission-only",                  "commission-only role"),
    )
    for term, reason in hard_rejects:
        if term in text:
            return True, reason

    if "french" in text and any(k in text for k in (
        "french required", "bilingual french",
        "english and french", "english/french",
        "french/english", "english et fran",
    )):
        return True, "French required"

    # IT-Indeed title blacklist — obviously non-IT roles by title.
    clearly_non_it_title_terms = (
        # Restaurant / food / hospitality
        "cook", "chef", "barista", "server", "waiter", "waitress",
        "host", "hostess", "pizza maker", "dishwasher",
        "kitchen helper", "line cook", "sous chef", "prep cook",
        "front of house", "back of house", "bartender",
        # Trades
        "mechanic", "welder", "plumber", "electrician",
        "carpenter", "roofer", "hvac technician", "millwright",
        "alarm installer", "alarm technician", "security alarm",
        "low voltage installer", "cable installer", "fiber installer",
        "cable technician", "cabling technician", "fiber technician",
        "telecom technician", "telecommunications technician",
        "telecom installer", "telecommunications installer",
        "telecom & security", "telecom and security",
        "electrical technician", "electronics technician",
        "electronic technician", "electrical field service",
        "electrical service", "electrical apprentice",
        "maintenance technician", "facilities maintenance",
        "facility maintenance", "building maintenance",
        "maintenance repair", "appliance technician",
        # Personal services
        "esthetician", "hair stylist", "barber", "nail technician",
        "massage therapist", "dental hygienist",
        # Driving / labour
        "truck driver", "class 1 driver", "long haul", "delivery driver",
        "forklift operator", "general labourer", "general laborer",
        "warehouse worker", "order picker", "picker packer",
        "material handler", "shipping/receiving", "shipper receiver",
        # Security / cleaning
        "security guard", "loss prevention", "janitor", "housekeeper",
        "housekeeping aide", "cleaner",
        # Retail / front-line sales (not technical)
        "cashier", "merchandiser", "stock associate", "grocery clerk",
        "store clerk", "retail associate", "retail sales associate",
        "sales & service technician", "sales and service technician",
        "residential sales", "residential service technician",
        "door-to-door", "door to door",
        # Licensed clinical / patient-care
        "registered nurse", "licensed practical nurse", "care aide",
        "resident care", "personal support worker", "medical office assistant",
        "clinic receptionist", "unit clerk", "porter", "patient transport",
        # Admin / front-desk / school operations
        "front desk", "receptionist", "office assistant", "office administrator",
        "administrative assistant", "administrative coordinator",
        "school coordinator", "program coordinator", "team coordinator",
        "clinic coordinator", "patient coordinator", "dental receptionist",
        # Non-IT support workers (e.g. ABA / autism / behavioural support).
        # "IT support" is fine; this catches "support worker" specifically.
        "support worker", "behaviour interventionist", "behavioral interventionist",
        "behaviour therapist", "behavioral therapist", "aba therapist",
        "ed assistant", "education assistant", "child & youth worker",
        # Senior / specialist non-IT
        "truck mechanic", "diesel mechanic", "auto body", "automotive service",
        # Lab / manufacturing QC (not software QA)
        "qc technician", "quality control technician", "lab technician",
        "laboratory technician", "manufacturing technician",
        "production technician", "warehouse technician",
        "pharmacy technician", "veterinary technician",
        "quality assurance technician", "qa technician",
        "quality assurance coordinator", "qa coordinator",
        "quality assurance associate", "qa associate",
        # Childcare / education (caught last cycle)
        "childcare", "child care", "infant lead", "early childhood educator",
        "ece ", "preschool", "daycare",
        # Retail / hospitality assistants caught last cycle
        "menswear", "boutique", "store associate", "sales associate",
        "banquet", "catering server", "guest services agent",
        "front desk agent", "front desk guest", "concierge",
        # Healthcare-adjacent / clinical caught last cycle
        "dental assistant", "certified dental", "patient services",
        "rehabilitation assistant", "life enrichment", "health care assistant",
        # Legal / real estate / paralegal
        "paralegal", "conveyancer", "real estate", "law clerk",
        "legal intake", "legal assistant",
        # HR / recruiting / talent (NOT IT despite the keyword "IT" appearing)
        "hr coordinator", "hr manager", "human resources manager",
        "human resources coordinator", "recruiter", "talent acquisition",
        "employee experience", "people & culture", "people and culture",
        # Finance / accounting / clerical / capital markets (not IT)
        "accounts payable", "accounts receivable", "bookkeeper",
        "payroll specialist", "payroll administrator",
        "stewardship officer", "fundraising",
        "private equity", "venture studio", "venture capital",
        "equity analyst", "investment analyst", "portfolio analyst",
        "asset management", "fund accountant", "fund analyst",
        "financial analyst", "finance analyst", "credit analyst", "capital markets",
        # Government / policy / non-IT coordinator roles
        "intergovernmental", "major projects coordinator",
        "policy analyst", "government relations",
        # Generic CSR / call centre (keep "customer support engineer")
        "customer support representative", "customer service representative",
        "customer care associate", "client service representative",
        "call centre agent", "call center agent", "contact centre agent",
        "contact center agent", "guest services associate",
        "data entry clerk", "office coordinator", "administrative support",
        "program assistant", "nurse practitioner", "physician assistant",
        "clinical research", "inventory control", "contact centers",
        "contact centres",
        # Construction / project trades (non-IT project mgmt)
        "construction project", "construction coordinator",
        "site superintendent", "site coordinator",
        "estimator", "rope access",
        # Generic admin coordinators caught last cycle
        "intake coordinator", "shipping and inventory",
        "shipping & inventory", "data entry admin",
        "operations coordinator", "registration coordinator",
        "road works", "marine service",
        # Marketing / brand / sales / media
        "brand specialist", "brand ambassador", "sales ambassador",
        "sales coordinator", "sales assistant", "sales associate",
        "sales representative", "electrical engineer", "power engineer",
        "civil engineer", "mechanical engineer",
        "menswear assistant", "director of paid media", "paid media",
        "graphic designer", "product designer", "ux designer",
        "ui designer",  # UI engineer is OK, designer is not
        # Trades / non-IT admin (title-gate review FPs before word-boundary fix)
        "painter", "detailer", "claims associate", "claims administrator",
        "claims consultant", "credit specialist", "patient liaison",
        "occupational therapist", "aircraft maintenance", "avionics",
        "pro paint", "mailroom", "fleet maintenance fueler",
        # Custom additions for non-IT roles seen in local runs
        "eyelash", "lash technician", "lash artist",
        "tire technician", "automotive technician", "tire service",
        "chemist", "cannabis", "marijuana", "counsellor", "counselor",
        "marketing", "social media", "content creator", "payroll",
        "operations associate", "ecam",
        # 2026-07-25 live-prod spillover (applied at score=null before gate
        # hardening): grounds/golf, vehicle rental, bid/proposal ops, gaming
        # front-line support — none are IT disciplines.
        "greenkeeper", "groundskeeper", "golf course", "turf maintenance",
        "irrigation technician", "landscaper", "landscaping",
        "rental agent", "rental service agent", "rental sales agent",
        "rental representative", "leasing agent", "leasing consultant",
        "bid operations", "bid coordinator", "proposal coordinator",
        "proposal specialist", "tendering",
        "player experience", "player support", "guest experience specialist",
        "parking attendant", "valet", "traffic control",
        "event staff", "event coordinator", "banquet server",
        # Non-IT support / customer care / print / fire alarm / traditional design additions
        "print production", "print bindery", "bindery", "print specialist",
        "fire alarm", "alarm programmer", "alarm installer",
        "civil designer", "structural designer", "civil/structural",
        "customer care specialist", "customer care associate", "customer care representative",
        "customer education specialist", "customer education coordinator",
        "geo content specialist", "content specialist",
        # 2026-07-31 quality audit — applied with score=100 via JD keyword bleed
        "human resources", "hr specialist", "hr generalist", "people operations",
        "marketer", "digital marketer", "seo specialist", "growth marketer",
        "events coordinator", "event coordinator", "event planner",
        "geotechnical", "construction -", "construction technologist",
        "virtual design & construction", "virtual design and construction",
        "enrollment coordinator", "financial services", "student financial",
        "retail operations", "retail financial",
        "project geotechnical", "civil utility", "heavy equipment",
        "geomorphologist", "coastal professional",
    )
    for term in clearly_non_it_title_terms:
        if term in title_l:
            return True, f"non-IT title: {term}"

    # Title-domain hard rejects that are NOT IT even when JD mentions software/systems.
    # "Analyst" alone is not enough — require explicit non-IT domain in the title.
    non_it_title_domains = (
        r"\bhr\b", r"\bhuman resources?\b", r"\brecruit(er|ing|ment)\b",
        r"\bmarketing\b", r"\bmarketer\b", r"\bseo\b", r"\bsocial media\b",
        r"\bevents?\b", r"\bgeotechnical\b", r"\bconstruction\b",
        r"\benrollment\b", r"\bfinancial services\b", r"\bretail operations\b",
        r"\bprivate equity\b", r"\bventure\b", r"\bintergovernmental\b",
        r"\bnurse\b", r"\bdental\b", r"\bpharmacy\b", r"\baccountant\b",
    )
    if any(re.search(pat, title_l) for pat in non_it_title_domains):
        # Allow if title also has strong IT discipline tokens (e.g. "IT Recruiter" still out;
        # "Marketing Technology Analyst" / "HRIS Analyst" may pass via target whitelist first).
        it_discipline = (
            r"\bit\b", r"\binformation technology\b", r"\bhelp ?desk\b",
            r"\bservice desk\b", r"\bdesktop support\b", r"\bsysadmin\b",
            r"\bnetwork\b", r"\bcyber\b", r"\bdevops\b", r"\bsoftware\b",
            r"\bdeveloper\b", r"\bqa\b", r"\bquality assurance\b", r"\bsdet\b",
            r"\bhris\b", r"\bsap\b", r"\berp\b", r"\bsystems? admin",
        )
        if not any(re.search(pat, title_l) for pat in it_discipline):
            return True, "non-IT title domain"
    return False, ""


def _obvious_target_role_approve(title: str, company: str) -> tuple[bool, str]:
    """Title-only whitelist of IT roles that we approve without burning an LLM call."""
    title_l = (title or "").lower()
    normalized_title = re.sub(r"[^a-z0-9+#.]+", " ", title_l).strip()
    it_target_terms = (
        # QA / Testing
        "qa analyst", "qa engineer", "qa tester", "qa automation",
        "quality assurance", "quality and assurance",
        "software test", "test analyst", "software test engineer", "sdet",
        "manual qa", "automation tester", "automation test",
        "api tester", "web tester", "qa test engineer",
        # NOT bare "test engineer" (matches ultrasound / acoustic / mechanical).
        # IT Support / Service Desk / Help Desk / Technical Support
        "it support", "it technician", "it analyst", "it coordinator",
        "it assistant", "it administrator", "it specialist", "it operations",
        "it infrastructure", "it technical specialist", "it engineer",
        "it compliance engineer", "it application specialist",
        "it event management", "it lead",
        "it intern", "it co-op", "it coop", "technology intern", "technology co-op",
        "service desk", "help desk", "helpdesk",
        "help desk technician", "help desk analyst", "service desk coordinator",
        "erp support", "erp support analyst", "client services specialist",
        "client services analyst", "remote client services analyst", "product support specialist",
        "application support specialist", "systems onboarding", "tech support coordinator",
        "cyber security consultant", "security consultant", "it consultant",
        "desktop support", "deskside support",
        "technical support", "tech support", "technical support specialist",
        "technical support associate", "technical support agent",
        "technical support coordinator", "technical service representative",
        "technical services analyst", "technical services specialist",
        "field tech analyst", "desktop technician", "technical analyst ii",
        "technical analyst",  # e.g. Technical Analyst - Network
        "application support", "production support",
        "software support", "support engineer", "support specialist",
        "user support technician", "pos support", "computer technician",
        "computer repair technician",
        # NOT bare "field service technician" (matches heavy equipment / HVAC).
        "it field service", "field service it",
        "systems support", "network support", "workstation support",
        # Data
        "data analyst", "data analytics", "data quality", "data collection",
        "data services specialist",
        "business intelligence", "bi analyst",
        "business intel engineer",
        "reporting analyst", "analytics analyst",
        "data warehouse analyst", "data scientist", "data processing technician",
        # Systems / Network / Infrastructure / NOC
        "systems administrator", "system administrator", "sysadmin",
        "network administrator", "network technician",
        "network engineer", "wireless and connectivity integration engineer",
        "telecom engineer", "cabling & infrastructure", "cabling and infrastructure",
        "information systems analyst", "information systems technician",
        "business systems analyst", "business system analyst",
        # Bare "business analyst" is too broad (finance/healthcare FP). Prefer IT-qualified forms.
        "application analyst", "integration specialist",
        "implementation consultant", "systems integration", "hris specialist",
        "infrastructure analyst", "infrastructure support",
        "noc technician", "noc analyst",
        # Cloud / DevOps
        "cloud support", "cloud engineer", "aws support", "azure support",
        "cloud operations", "cloud analyst", "cloud solutions analyst",
        "devops engineer", "devops analyst", "site reliability",
        "devops specialist", "build and release", "ci/cd", "cicd", "platform engineer",
        # Security
        "soc analyst", "security analyst", "information security analyst",
        "cybersecurity analyst", "security operations", "network security",
        "vulnerability analyst", "privacy analyst", "penetration tester", "incident response",
        "grc consultant", "purview consultant", "cyber offensive testing",
        "cyber",
        # Database / Software / Generic IT engineering
        "database administrator", "dba", "sql developer",
        "software developer", "software engineer", "web developer",
        "software development engineer", "front end engineer", "front-end engineer",
        "frontend engineer", "backend engineer", "back-end engineer",
        "firmware developer", "firmware engineer", "embedded firmware",
        "algorithm engineer", "computer vision engineer", "fpga engineer",
        "backend developer", "frontend developer", "full stack developer",
        "full-stack developer", "application developer", "mobile developer",
        "associate software", "entry level software", "systems analyst",
        "technology analyst", "information technology analyst",
        "junior software engineer", "junior developer",
        "software developer intern", "developer intern",
        "python developer intern", "java developer intern",
        "backend developer intern", "web developer intern",
        "quantitative developer", "quantitative engineer", "quant developer", "quant engineer",
        "software analyst intern", "quality engineering co-op", "quality engineering coop",
        "engineering coop student", "engineering co-op student",
        "computer science", "digital technology specialist",
        "machine learning engineer", "machine learning research",
        "ml engineer", "ai/ml developer", "ai ml developer",
        "hris analyst", "sap analyst", "basis analyst",
        "data management analyst", "imaging informatics",
        "informatics coordinator", "erp testing",
        "technical writer", "ai integration", "ai automation specialist",
        "solutions engineer", "solution engineer", "technical solutions engineer",
        # NOT bare "technical engineer" (matches hydrotechnical / mechanical).
        "system consultant", "technology procurement specialist",
        "product analyst", "vcio",
        # NOT bare "compliance analyst" (finance/AML).
        # NOT bare "business analyst" without systems/IT context for finance/healthcare BA noise.
        "it business analyst", "systems business analyst", "technical business analyst",
        "business systems analyst", "business system analyst",
    )
    # Word-boundary matching only — substring "it specialist" must NOT match
    # "Credit Specialist".
    if any(
        _whole_phrase_in_text(term, title_l)
        or _whole_phrase_in_text(
            re.sub(r"[^a-z0-9+#.]+", " ", term).strip(), normalized_title
        )
        for term in it_target_terms
    ):
        if "customer" in title_l and not any(term in title_l for term in (
            "product support", "technical support", "tech support",
            "software support", "application support",
        )):
            return False, ""
        # Non-IT domains that still contain IT-looking tokens after boundary match
        non_it_domain = (
            "avionics", "aircraft", "paint", "claims", "occupational therapist",
            "ultrasound", "hydrotechnical", "heavy equipment", "fleet maintenance",
            "mailroom", "buyer,", "pro paint", "sales specialist",
            "financial planning", "home & community", "primary health",
            "energuide", "ecommerce business analyst", "e-commerce business analyst",
            "financial applications",  # often ERP-adjacent ops, not pure IT desk
        )
        if any(n in title_l for n in non_it_domain):
            return False, ""
        return True, "IT target role"
    return False, ""


def _ollama_gate_should_save_minimal(title: str, company: str) -> tuple[bool, str]:
    approved, reason = _obvious_target_role_approve(title, company)
    if approved:
        return True, f"local target match: {reason}"

    if not _gate_text_has_it_signal(title):
        return False, "strict local fallback: no IT role signal in title"

    try:
        from config.settings import use_ollama_for_indeed, ollama_base_url, ollama_model
    except Exception:
        use_ollama_for_indeed = True
        ollama_base_url = "http://localhost:11434/v1"
        ollama_model = "llama3.2:3b"

    if not use_ollama_for_indeed:
        return False, "Ollama gate disabled; strict fallback rejected"

    prompt = f"""
Decide if this job is worth saving for later review for an IT-only job search.

Candidate profile:
{_tiny_job_gate_user_profile()}

Job title: {title}
Company: {company}

Save only IT, cloud, cybersecurity, network/systems, QA/software testing,
software/database, data/BI/reporting analyst, service desk, help desk, desktop
support, technical support, application support, or IT internship/co-op roles.
Reject customer service, sales, marketing, recruiting, admin, healthcare,
warehouse, retail, trades, finance/accounting, operations, hospitality, and
other general-work roles unless the title is explicitly technical/IT.
Return only JSON exactly like:
{{"save": true, "reason": "short reason"}}
""".strip()

    payload = {
        "model": ollama_model,
        "messages": [
            {
                "role": "system",
                "content": "You are a strict job-fit gate. Return compact valid JSON only.",
            },
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "temperature": 0.1,
    }
    max_attempts = 3
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            api_url = ollama_base_url.rstrip("/") + "/chat/completions"
            req = Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=300) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (data["choices"][0]["message"]["content"] or "").strip()
            result = _extract_json_object(content)
            if not result or "save" not in result:
                return False, "Ollama gate invalid response; strict fallback rejected"
            raw_save = result.get("save")
            if isinstance(raw_save, str):
                save = raw_save.strip().lower() in {"true", "yes", "1", "save", "apply"}
            else:
                save = bool(raw_save)
            reason = str(result.get("reason") or "Ollama returned no reason")[:220]
            # Fail-closed harden: a tiny local LLM is too easily convinced by
            # "transferable customer service skills" wording to lift a non-IT
            # title to save=true.  Require an additional explicit IT phrase in
            # the title before honouring an Ollama APPROVE.
            if save and not _title_has_explicit_it_phrase(title):
                return False, (
                    "Ollama said save but title lacks explicit IT phrase — "
                    f"fail-closed reject ({reason})"
                )
            return save, f"Ollama fallback: {reason}"
        except Exception as e:
            print_lg(f"Ollama gate attempt {attempt}/{max_attempts} failed: {e}")
            last_exc = e
            if attempt < max_attempts:
                time.sleep(2 * attempt)

    return False, f"Ollama gate unavailable ({type(last_exc).__name__}); strict fallback rejected"


_IT_PHRASE_REQUIRED = (
    # Multi-word phrases that unambiguously identify an IT role. A token like
    # "java" or "python" alone is NOT enough — too many non-IT job descriptions
    # mention them in passing (e.g. "Java certificate", "python script user").
    "information technology", "it support", "it service", "service desk",
    "help desk", "helpdesk", "desktop support", "technical support",
    "application support", "production support", "software support",
    "support engineer", "computer technician",
    "systems administrator", "system administrator", "network administrator",
    "network technician", "network support", "infrastructure analyst",
    "infrastructure support", "cloud support", "cloud engineer",
    "site reliability", "security analyst", "soc analyst", "qa analyst",
    "qa engineer", "qa tester", "quality assurance analyst",
    "software test", "automation tester", "software developer",
    "software engineer", "web developer", "backend developer",
    "frontend developer", "front-end developer", "back-end developer",
    "full stack", "full-stack", "data analyst", "business intelligence",
    "bi analyst", "reporting analyst", "data engineer", "data analytics",
    "devops engineer", "platform engineer", "cybersecurity",
    "vulnerability analyst", "database administrator",
    "cloud infrastructure engineer", "security systems technician",
    "client services specialist", "client services analyst",
    "client support", "product support", "application support specialist",
    "systems onboarding", "tech support coordinator", "cyber security consultant",
    "security consultant", "it consultant", "it co-op", "it coop",
    "erp support", "erp support analyst",
    # Whole-word-only short triggers (never substring — "ai" must not match Painter)
    "developer", "programmer", "sysadmin", "cyber",
    "qa", "erp", "aws", "html", "css", "java",
    "windows", "linux", "troubleshooting",
)


def _whole_phrase_in_text(phrase: str, text: str) -> bool:
    """Match multi-word or short IT phrases with word boundaries.

    Prevents false positives like:
      * ``ai`` inside Painter / Claims / Maintenance
      * ``it specialist`` inside Credit Specialist
      * ``ml`` inside AML
    """
    phrase = (phrase or "").strip().lower()
    text = (text or "").lower()
    if not phrase or not text:
        return False
    # Multi-word: require the full phrase as contiguous whole words.
    parts = [re.escape(p) for p in phrase.split() if p]
    if not parts:
        return False
    pattern = r"(?<![a-z0-9])" + r"\s+".join(parts) + r"(?![a-z0-9])"
    return bool(re.search(pattern, text))


def _title_has_explicit_it_phrase(title: str) -> bool:
    low = (title or "").lower()
    if "customer" in low and not any(
        t in low for t in ("product support", "technical support", "tech support",
                           "software support", "application support")
    ):
        return False
    return any(_whole_phrase_in_text(phrase, low) for phrase in _IT_PHRASE_REQUIRED)


def _support_role_has_technical_context(title: str, card_text: str, job_details: str) -> bool:
    title_l = (title or "").lower()
    has_trigger = any(term in title_l for term in (
        "support", "service desk", "help desk", "helpdesk", "client services", "client support",
        "product support", "application support", "systems onboarding", "tech support coordinator",
        "technical support", "implementation support"
    ))
    if not has_trigger:
        return False
    if any(term in title_l for term in (
        "collision support", "customer support advocate",
        "program support", "project support", "sales support",
    )):
        return False

    text = " ".join([title, card_text, job_details]).lower()
    tech_signals = (
        "software", "saas", "api", "database", "sql", "cloud", "aws", "azure",
        "network", "server", "windows", "linux", "microsoft 365", "office 365",
        "active directory", "jira", "zendesk", "technical troubleshooting",
        "troubleshoot technical", "application", "platform", "web app",
        "hardware", "technical", "troubleshooting", "ticket", "tickets", "help desk",
        "service desk", "erp", "crm", "intune", "vpn", "remote support",
        "implementation", "onboarding", "configuration", "documentation"
    )
    non_tech_signals = (
        "collision", "auto body", "insurance claim", "claims", "sales quota",
        "cold call", "retail", "warehouse", "physiotherapy", "bakery",
    )
    return any(signal in text for signal in tech_signals) and not any(
        signal in text for signal in non_tech_signals
    )


def _easy_apply_has_broad_technical_evidence(title: str, card_text: str,
                                             job_details: str) -> bool:
    """Volume-friendly local approval for cheap applications.

    Require:
      1. Title looks IT-adjacent (not HR/events/construction with tech jargon in JD)
      2. Two distinct technical signal groups across title/card/description

    JD-only keyword bleed was approving Human Resources, Events Coordinator,
    Geotechnical, and Construction roles at score=100 (2026-07-31 audit).
    """
    title_l = (title or "").lower()
    # Title must carry an IT-adjacent token. Pure "Specialist/Coordinator/Analyst"
    # without tech context is deferred to batch AI (or hard-rejected above).
    title_it_adjacent = any(
        tok in title_l for tok in (
            "it ", " it", "it-", "it/", "/it", "(it)",
            "help desk", "helpdesk", "service desk", "desktop",
            "technical", "technology", "software", "firmware",
            "qa ", "qa/", "quality assurance", "sdet", "test engineer",
            "systems", "system admin", "sysadmin", "network", "cloud",
            "devops", "security", "cyber", "developer", "programmer",
            "engineer", "technician", "support", "infrastructure",
            "application", "database", "information technology",
            "information systems", "soc ", "data analyst", "data engineer",
            "business systems", "systems analyst", "technology analyst",
            "platform", "devops", "sre", "site reliability",
        )
    )
    if not title_it_adjacent:
        return False
    # Domain poison in title → never approve from JD keywords alone.
    if any(bad in title_l for bad in (
        "human resource", "hr specialist", "hr generalist", "recruiter",
        "marketing", "marketer", "event ", "events ", "geotechnical",
        "construction", "enrollment", "financial service", "retail operation",
        "civil ", "mechanical", "electrical engineer", "nurse", "dental",
        "warehouse", "driver", "cook", "chef", "cashier", "sales associate",
    )):
        return False

    text = " ".join([title, card_text, job_details]).lower()
    signal_groups = (
        ("troubleshoot", "technical support", "help desk", "service desk", "ticketing"),
        ("software", "application", "saas", "platform", "api"),
        ("network", "tcp/ip", "vpn", "wifi", "wi-fi", "dns", "dhcp"),
        ("windows", "linux", "active directory", "microsoft 365", "office 365"),
        ("cloud", "aws", "azure", "docker", "kubernetes"),
        ("database", "sql", "python", "java", "javascript"),
        ("cybersecurity", "information security", "siem", "soc", "firewall"),
        ("quality assurance", "software test", "test automation", "qa analyst"),
    )
    matched_groups = sum(any(term in text for term in group) for group in signal_groups)
    return matched_groups >= 2


def _ai_title_is_it_role(title: str) -> bool:
    """Quick AI check: is this job title IT-relevant? Returns True/False."""
    if _aiClient is None or not title:
        return False
    prompt = (
        "You are filtering jobs for an IT candidate (help desk, QA, support, sysadmin, "
        "networking, cloud, DevOps, software, data, cybersecurity, etc.).\n\n"
        f"Job title: \"{title}\"\n\n"
        "Is this an IT-related role the candidate should apply to? "
        "Answer ONLY 'Yes' or 'No'."
    )
    try:
        provider = (_ai_provider or "").lower()
        if provider in ("openai", "deepseek"):
            from modules.ai.openaiConnections import ai_completion
            messages = [{"role": "user", "content": prompt}]
            result = ai_completion(_aiClient, messages, stream=False)
            result = (result or "").strip().lower()
        elif provider == "gemini":
            from modules.ai.geminiConnections import gemini_answer_question
            try:
                from config.questions import user_information_all
            except ImportError:
                user_information_all = ""
            result = gemini_answer_question(
                _aiClient, prompt, options=["Yes", "No"],
                question_type="single_select", job_description="",
                about_company=None, user_information_all=user_information_all,
            )
            result = (result or "").strip().lower()
        elif provider == "ollama":
            result = _aiClient(question=prompt, hint="", job_context="")
            result = (result or "").strip().lower()
        else:
            return False

        approved = result.startswith("yes")
        print_lg(f"    [AI title gate] \"{title}\" → {result} ({'approved' if approved else 'rejected'})")
        log_training_event("ai_title_gate", job=_current_job_meta,
                           title=title, ai_answer=result, approved=approved,
                           provider=provider)
        return approved
    except Exception as e:
        print_lg(f"    [AI title gate] error: {e}")
        return False


def _local_easy_apply_gate_should_apply(title: str, company: str, location: str,
                                        card_text: str, job_details: str) -> tuple[bool, str]:
    """Cheap title-first gate for Indeed Easy Apply / SmartApply jobs.

    Easy Apply is low-friction but still must be IT. Order of checks:
      1. _obvious_non_it_reject  → hard reject (childcare, banquet, etc.)
      2. senior/lead/management  → hard reject
      3. _obvious_target_role_approve / explicit IT phrase / technical context
         → fast approve
      4. Otherwise → ambiguous (discovery batch-title-screens; browser rejects)
    """
    reject, reason = _obvious_non_it_reject(title, company, location, card_text, job_details, easy_apply=True)
    if reject:
        return False, reason

    senior_reject, senior_reason = _senior_save_gate_reject(title)
    if senior_reject:
        return False, f"local easy-apply gate: {senior_reason}"

    approved, reason = _obvious_target_role_approve(title, company)
    if approved:
        return True, f"local easy-apply target match: {reason}"

    if _title_has_explicit_it_phrase(title):
        return True, "local easy-apply gate: explicit IT phrase in title"

    if _support_role_has_technical_context(title, card_text, job_details):
        return True, "local easy-apply gate: support role has technical context"

    if _easy_apply_has_broad_technical_evidence(title, card_text, job_details):
        return True, "local easy-apply gate: multiple technical signals in posting"

    return False, (
        "local easy-apply gate: no explicit IT phrase or technical support "
        "context (ambiguous_title — batch title screen in discovery)"
    )


def _senior_save_gate_reject(title: str) -> tuple[bool, str]:
    """Fast reject for company-site save path; keep IC lead titles in support/desk."""
    low_title = (title or "").lower()
    ic_support_lead_ok = (
        "technical support", "tech support", "help desk", "helpdesk",
        "service desk", "desktop support", "it support", "support specialist",
        "support analyst", "support engineer", "support technician",
        "support coordinator", "network support", "application support",
    )
    if re.search(r"\blead\b", low_title) and any(term in low_title for term in ic_support_lead_ok):
        return False, ""

    senior_patterns = [
        r"\bsenior\b", r"\bsr\.?\b", r"\blead\b", r"\bprincipal\b", r"\bprinciple\b",
        r"\bmanager\b", r"\bmanaging\b", r"\bmanagement\b", r"\bdirector\b",
        r"\bvp\b", r"\bavp\b", r"\bhead\b", r"\bchief\b", r"\barchitect\b",
        r"\bstaff\b", r"\bstaff-level\b", r"\bexpert\b", r"\bsupervisor\b", r"\bsupervising\b",
        r"\bfounding\b", r"\bdistinguished\b", r"\bexecutive\b",
        r"\biii\b", r"\biv\b", r"\bv\b", r"\b(tier|level)\s*(3|iii|4|iv|5|v)\b",
    ]
    if any(re.search(pat, low_title) for pat in senior_patterns):
        return True, "strict title check: senior/lead/management role rejected"
    return False, ""


def _local_company_site_gate(title: str, company: str, location: str,
                             card_text: str, job_details: str) -> tuple[str, str]:
    """Strict, zero-cost first pass for company-site jobs.

    Returns ``approve``, ``reject``, or ``ai``. Only ambiguous roles should
    reach the paid model. Hard non-IT/legal and seniority checks are performed
    by the caller before this function.
    """
    text = " ".join([title, card_text, job_details]).lower()

    # Requirements clearly beyond the candidate's three relevant years are a
    # safe local rejection. Keep vague/preferred experience for AI review.
    required_years = [
        int(value) for value in re.findall(
            r"(?:minimum|at least|requires?|must have|required)"
            r"[^.\n]{0,80}?\b(\d{1,2})\+?\s+years?\b",
            text,
        )
    ]
    if required_years and max(required_years) >= 5:
        return "reject", (
            "local company-site gate: requires at least "
            f"{max(required_years)} years of experience"
        )

    target_match, _ = _obvious_target_role_approve(title, company)
    title_l = (title or "").lower()
    early_career = bool(re.search(
        r"\b(?:junior|jr\.?|entry[- ]level|new grad|graduate|intern|internship|"
        r"co[- ]?op|student|level 1|tier 1|associate)\b",
        (title or "").lower(),
    ))

    # These families are unambiguously IT. That classification lets the local
    # gate skip AI only after the JD passes candidate-fit checks below.
    high_confidence_it_title = any(term in title_l for term in (
        "it support", "information technology support", "service desk",
        "help desk", "helpdesk", "desktop support", "desktop technician",
        "technical support", "application support", "product support",
        "user support technician", "field support technician",
        "it technician", "information technology technician",
        "it systems analyst", "it systems administrator", "it administrator",
        "systems administrator", "system administrator", "network administrator",
        "network engineer", "network security administrator",
        "infrastructure technician", "infrastructure engineer",
        "cloud systems administrator", "cloud network engineer",
        "devops engineer", "devops specialist", "site reliability engineer",
        "security analyst", "security engineer", "cyber security",
        "cybersecurity", "security operations", "offensive security",
        "software developer", "software engineer", "software development engineer",
        "application developer", "mobile developer", "mainframe developer",
        "front end engineer", "frontend engineer", "data engineer",
        "data scientist", "data analyst", "business intelligence engineer",
        "qa analyst", "qa tester", "quality assurance", "sdet",
        "software dev qa", "software test", "test automation",
    ))

    has_usable_description = len(" ".join([card_text, job_details]).strip()) >= 120

    if target_match and early_career and not has_usable_description:
        return "ai", (
            "local company-site gate: early-career IT title but JD is required "
            "for candidate-fit review"
        )

    # A clear IT title only answers "is this an IT job?" It does not establish
    # candidate fit. Never approve an ordinary company-site role from its title
    # alone; require a usable JD and run the deterministic qualification checks
    # below. Missing descriptions remain reviewable instead of auto-approved.
    if target_match and high_confidence_it_title and not has_usable_description:
        return "ai", (
            "local company-site gate: clear IT title but JD is required for "
            "candidate-fit review"
        )

    if target_match and has_usable_description and _easy_apply_has_broad_technical_evidence(
        title, card_text, job_details
    ):
        return "approve", (
            "local company-site gate: clear IT title and JD passed local "
            "candidate-fit checks"
        )

    if (has_usable_description and early_career
            and _support_role_has_technical_context(title, card_text, job_details)):
        return "approve", (
            "local company-site gate: early-career technical support JD passed "
            "local candidate-fit checks"
        )

    return "ai", "local company-site gate: ambiguous fit requires AI review"


def _groq_gate_should_save(title: str, company: str, location: str,
                           card_text: str, job_details: str,
                           saving_only: bool = False) -> tuple[bool, str]:
    """LLM job-fit gate.

    Used for higher-cost "worth saving for company-site/manual review" decisions.
    Indeed Easy Apply / SmartApply uses _local_easy_apply_gate_should_apply()
    instead, so Groq tokens and strict hire-chance grading are reserved for
    non-Easy Apply jobs that would be saved for later.
    """
    # Use only the true hard-reject layer here. Company-site generic titles
    # must reach the description-aware local gate (or AI) rather than being
    # discarded solely because the title lacks a canonical IT phrase.
    reject, reason = _obvious_non_it_reject(
        title, company, location, card_text, job_details, easy_apply=True
    )
    if reject:
        return False, reason

    # Stricten save gate: Fast reject senior/lead/management roles before querying LLM
    if saving_only:
        reject_senior, senior_reason = _senior_save_gate_reject(title)
        if reject_senior:
            return False, senior_reason

        local_decision, local_reason = _local_company_site_gate(
            title, company, location, card_text, job_details
        )
        if local_decision == "approve":
            return True, local_reason
        if local_decision == "reject":
            return False, local_reason

    # Only auto-approve via whitelisted target role if we are applying.
    # For saving, we want to run the strict AI details check to verify if highly qualifying.
    if not saving_only:
        approved, reason = _obvious_target_role_approve(title, company)
        if approved:
            return True, reason

    # Call strict/lenient AI gate based on saving_only
    if saving_only:
        # Strict "Highly Qualified" screening gate for saving company-site jobs
        prompt = f"""
You are a strict job-fit evaluator. Decide if the candidate is HIGHLY QUALIFYING for this job.
We only want to save this job if the candidate meets the qualifications closely and is a strong match.

Candidate Profile:
- Education: KPU Bachelor of Technology in Information Technology (Network Administration & Security). Expected graduation: Dec 2026.
- Certifications: AWS Certified Solutions Architect – Associate, AWS Cloud Practitioner.
- Experience: 3 years of technical support at Bell Canada (diagnosing consumer Wi-Fi, routers, iOS/Android, networking).
- Academic Projects: AWS VPC setup, RADIUS/EAP-TLS authentication, OSSEC HIDS, Spring Boot, Docker, Python scripting.
- Targeting: Help Desk, Service Desk, Desktop Support, Tech Support, Customer Support Specialist (technical/corporate), QA Analyst/Tester, Junior Data Analyst, NOC/SOC, Junior Network/Systems/Cloud Admin, Security Systems Technician, Cloud Infrastructure Engineer, Junior DevOps, Junior Software Engineer, DBA, and IT co-op/internship roles.

Job Details:
Title: {title}
Company: {company}
Location: {location}
Description: {job_details[:4000]}

Rules:
1. Candidate is NOT highly qualifying if the job requires >3 years of experience in engineering, administration, development, or security.
2. Candidate is NOT highly qualifying if the job is a senior, lead, principal, or management position. However, do NOT reject Cloud Infrastructure Engineer roles simply because they mention terms like "technical mentorship", "total infrastructure ownership", or "governance" as long as the experience required doesn't strictly exceed 3 years.
3. Candidate is NOT highly qualifying if the job requires skills or technologies completely unrelated to their profile (e.g., senior C++ developers, Salesforce consultants, graphic designers, marketing manager, sales representatives, billing/payroll clerks). Note that:
   - Security Systems Technician roles (access control, CCTV, door strikes) are IP-based network devices and are highly relevant to the candidate's Network Administration & Security specialization—do NOT reject them as unrelated trades.
   - Customer Support Specialist roles should be accepted if they have technical context (handling system tickets, structured inquiries, documentation, saas, biotech) as the candidate's Bell Canada tech support experience is highly transferable.
4. Candidate MUST have a strong overlap with the core job requirements (e.g. cloud, network/systems, technical support, corporate customer support, basic QA automation, Python, AWS, or IT service management).
5. If the fit is weak or if you are unsure, choose false.

Return a JSON object exactly like this:
{{"save": true, "reason": "short explanation of why candidate is highly qualifying"}}
or
{{"save": false, "reason": "short explanation of why candidate is not highly qualifying"}}
""".strip()
    else:
        # Lenient "IT Job Fit" gate for Easy Apply evaluation
        prompt = f"""
You are a job-fit evaluator. Decide if this IT-related job is a good fit for the candidate's profile to apply to.
We want to apply to good entry-level to intermediate IT roles, and we should NOT be overly strict or reject good opportunities.

Candidate Profile:
- Education: KPU Bachelor of Technology in Information Technology (Network Administration & Security). Expected graduation: Dec 2026.
- Certifications: AWS Certified Solutions Architect – Associate, AWS Cloud Practitioner.
- Experience: 3 years of technical support at Bell Canada (diagnosing consumer Wi-Fi, routers, iOS/Android, networking).
- Academic Projects: AWS VPC setup, RADIUS/EAP-TLS authentication, OSSEC HIDS, Spring Boot, Docker, Python scripting.
- Targeting: Help Desk, Service Desk, Desktop Support, Tech Support, Customer Support Specialist (technical/corporate), QA Analyst/Tester, Junior Data Analyst, NOC/SOC, Junior Network/Systems/Cloud Admin, Security Systems Technician, Cloud Infrastructure Engineer, Junior DevOps, Junior Software Engineer, DBA, and IT co-op/internship roles.

Job Details:
Title: {title}
Company: {company}
Location: {location}
Description: {job_details[:4000]}

Rules:
1. The role MUST be IT-related or have strong IT/technical context (e.g., tech support, helpdesk, corporate customer support, QA, software testing, cloud/sysadmin/network admin, security systems technician, junior developer, junior data analyst, cybersecurity/SOC).
2. Do NOT reject the job just because the candidate doesn't have every single tool or technology mentioned in the description, as long as they have the foundational knowledge.
3. Do NOT reject the job if it asks for 1-3 years of experience (or up to 4 years, and up to 5-6 years for security systems technician/field roles, or roles posted by agencies like Express Employment), as the candidate has 3 years of relevant tech support and academic IT experience.
4. REJECT only if the job is clearly senior/lead/manager (e.g. requires 5+ years experience except as noted above, senior architecture, managing teams) or is in a completely different field with no technical/IT context (e.g., medical, finance, retail, customer service sales with no IT duties, mechanical trade, marketing, etc.). Note that:
   - Security Systems Technician roles (CCTV, access control) are IP-based network devices and fit the candidate's Network Administration & Security specialization. Do NOT reject them.
   - Cloud Infrastructure Engineer roles should NOT be rejected for senior-sounding terms like "technical mentorship" or "infrastructure ownership" if the candidate's certifications (AWS Solutions Architect Associate) cover the core cloud architecture.
   - Customer Support Specialist roles should be accepted if they have technical context (handling system tickets, structured inquiries, documentation, saas, biotech) as the candidate's Bell Canada tech support experience is highly transferable.
5. Be lenient: if it is a reasonable IT job that they can do, choose true.

Return a JSON object exactly like this:
{{"save": true, "reason": "short explanation of why this is a good IT job match"}}
or
{{"save": false, "reason": "short explanation of why this job is not a fit (e.g., non-IT, senior, unrelated field)"}}
""".strip()

    return _execute_ai_gate_completion(prompt)


def _execute_ai_gate_completion(prompt: str) -> tuple[bool, str]:
    # Try main AI client (DeepSeek, OpenAI, Gemini)
    if _aiClient is not None:
        try:
            provider = (_ai_provider or "").lower()
            if provider == "openai":
                from modules.ai.openaiConnections import ai_completion
                messages = [
                    {"role": "system", "content": "You are a strict job-fit gate. Return compact valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
                result_str = ai_completion(_aiClient, messages, response_format={"type": "json_object"}, stream=False)
                result = _extract_json_object(result_str)
            elif provider == "deepseek":
                from modules.ai.deepseekConnections import deepseek_completion
                messages = [
                    {"role": "system", "content": "You are a strict job-fit gate. Return compact valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
                result_str = deepseek_completion(_aiClient, messages, response_format={"type": "json_object"}, stream=False)
                result = _extract_json_object(result_str)
            elif provider == "gemini":
                from modules.ai.geminiConnections import gemini_answer_question
                try:
                    from config.questions import user_information_all
                except ImportError:
                    user_information_all = ""
                result_str = gemini_answer_question(
                    _aiClient, prompt, options=None,
                    question_type="text", job_description="",
                    about_company=None, user_information_all=user_information_all,
                )
                result = _extract_json_object(result_str)
            elif provider == "ollama":
                result_str = _aiClient(question=prompt, hint="", job_context="")
                result = _extract_json_object(result_str)
            else:
                result = {}

            if result and "save" in result:
                raw_save = result.get("save")
                if isinstance(raw_save, str):
                    save = raw_save.strip().lower() in {"true", "yes", "1", "save", "apply"}
                else:
                    save = bool(raw_save)
                reason = str(result.get("reason") or "No reason provided")[:220]
                return save, f"[{provider.upper()} Gate] {reason}"
        except Exception as e:
            print_lg(f"AI strict save gate failed: {e}")

    # Fallback to local Ollama request
    try:
        from config.settings import use_ollama_for_indeed, ollama_base_url, ollama_model
    except Exception:
        use_ollama_for_indeed = True
        ollama_base_url = "http://localhost:11434/v1"
        ollama_model = "llama3.2:3b"

    if use_ollama_for_indeed:
        payload = {
            "model": ollama_model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a strict job-fit gate. Return compact valid JSON only.",
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "temperature": 0.1,
        }
        try:
            api_url = ollama_base_url.rstrip("/") + "/chat/completions"
            req = Request(
                api_url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            content = (data["choices"][0]["message"]["content"] or "").strip()
            result = _extract_json_object(content)
            if result and "save" in result:
                raw_save = result.get("save")
                if isinstance(raw_save, str):
                    save = raw_save.strip().lower() in {"true", "yes", "1", "save", "apply"}
                else:
                    save = bool(raw_save)
                reason = str(result.get("reason") or "No reason provided")[:220]
                return save, f"[Ollama Fallback Gate] {reason}"
        except Exception as e:
            print_lg(f"Ollama fallback gate failed: {e}")

    # If all AI gates fail, fail-closed for saving
    return False, "AI gate unavailable; strict fallback rejected"


def _general_local_gate_reject(title: str, company: str, location: str, description: str) -> tuple[bool, str]:
    """Local check for indeed_general bot to reject unwanted roles (IT, food/cooking, physical labor, female-dominated clinical)."""
    title_l = (title or "").lower()
    desc_l = (description or "").lower()

    # 1. Reject IT / Software / Technical roles
    it_title_keywords = [
        "it support", "help desk", "helpdesk", "technical support", "desktop support",
        "network support", "systems administrator", "system administrator",
        "software developer", "web developer", "programmer", "cybersecurity",
        "noc technician", "computer technician", "software engineer", "frontend developer",
        "backend developer", "full stack", "cloud engineer", "devops", "data analyst",
        "business analyst", "qa analyst", "quality assurance analyst", "computer science",
        "programming", "python", "javascript", "sql", "network specialist", "systems analyst",
        "database administrator", "database analyst", "solutions architect", "cloud architect",
        "security operations center", "soc analyst", "systems engineer", "network engineer"
    ]
    for term in it_title_keywords:
        if term in title_l:
            return True, f"IT/technical title: {term}"

    # 2. Reject Cooking / Food / Beverage / Restaurant / Barista roles
    food_title_keywords = [
        "cook", "chef", "barista", "server", "waiter", "waitress", "hostess", "host",
        "bartender", "baker", "baking", "food counter", "kitchen helper", "line cook",
        "prep cook", "sous chef", "food service worker", "dietary aide", "kitchen porter",
        "busser", "crew member", "restaurant team member", "deli", "bakery",
        "dining helper", "beverage server", "beverage attendant", "kitchen staff", "restaurant staff",
        "dish washer", "dishwasher", "food handler"
    ]
    for term in food_title_keywords:
        if term in title_l:
            return True, f"food/cooking title: {term}"

    # 3. Reject Physical Labor / Warehouse / Driver / Cleaning / Trades roles
    physical_title_keywords = [
        "warehouse", "labour", "labor", "production worker", "factory worker",
        "assembly worker", "assembler", "manufacturing associate",
        "dock worker", "loader", "unloader", "package handler",
        "shipping receiving", "shipping/receiving", "shipper receiver",
        "stock handler", "freight", "cargo handler",
        "material handler", "forklift", "order picker", "picker", "packer",
        "packaging associate", "labourer", "laborer", "mover", "packager", "sorter",
        "driver", "delivery driver", "courier", "truck driver", "route driver",
        "mechanic", "installer", "apprentice", "technician", "service technician",
        "field technician", "maintenance worker", "maintenance technician", "handyman",
        "carpenter", "plumber", "electrician", "hvac", "roofer", "painter",
        "cleaner", "janitor", "custodian", "housekeeper", "housekeeping", "room attendant",
        "laundry attendant", "linen attendant", "laundry worker", "sanitation", "floor associate",
        "stock associate", "stocker", "shelf stocker", "night stocker", "merchandise stocker",
        "merchandiser", "lot associate", "lot attendant", "parking attendant",
        "gas station attendant", "convenience store clerk", "sales floor associate",
        "retail associate", "retail sales associate", "retail clerk", "store associate",
        "store clerk", "gardener", "landscaper", "landscaping", "construction"
    ]
    for term in physical_title_keywords:
        if term in title_l:
            return True, f"physical/manual labor title: {term}"

    # 4. Reject clinical/healthcare/beauty/childcare female-dominated roles requiring specific licenses
    female_dominated_patterns = [
        r"\bcare aide\b", r"\bnurse\b", r"\bregistered nurse\b", r"\blpn\b", r"\brn\b", r"\bhca\b",
        r"\bpersonal support worker\b", r"\bchildcare\b", r"\bchild care\b", r"\bece\b",
        r"\bearly childhood\b", r"\bdaycare\b", r"\bnanny\b", r"\bbabysitter\b",
        r"\besthetician\b", r"\bcosmetologist\b", r"\bhair stylist\b", r"\bnail technician\b",
        r"\bdental hygienist\b", r"\bspa therapist\b", r"\bsalon\b"
    ]
    for pattern in female_dominated_patterns:
        if re.search(pattern, title_l):
            return True, f"female-dominated/clinical title: {pattern}"

    # 5. Check description for gender preferences
    gender_pref_terms = [
        "female preferred", "women preferred", "female candidates preferred", "women candidates preferred",
        "female only", "women only", "female staff", "women staff", "females only", "females preferred",
    ]
    for term in gender_pref_terms:
        if term in desc_l:
            return True, f"explicit gender preference: {term}"

    # 6. Check description for physical labor / lifting requirements
    physical_desc_terms = [
        "heavy lifting", "heavy labour", "heavy labor",
        "lift 30", "lift 40", "lift 50", "lift 60",
        "lift up to 30", "lift up to 40", "lift up to 50", "lift up to 60",
        "able to lift", "must lift", "required to lift", "manual lifting",
        "repetitive lifting", "physically demanding", "strenuous",
        "loading and unloading", "load and unload", "material handling",
        "pallet jack", "forklift operator", "stand for long periods",
        "standing for long periods", "prolonged standing", "stand for extended",
        "standing for extended", "physical labor", "physical labour",
        "manual labor", "manual labour", "food handler certificate",
        "food safe", "foodsafe"
    ]
    for term in physical_desc_terms:
        if term in desc_l:
            return True, f"physical description marker: {term}"

    return False, ""


_GENERAL_ASAP_CANDIDATE_PROFILE = """
Candidate Profile:
- Name: Jane Doe
- Experience:
  1. Bell Canada Sales & Technical Support Representative (3 years): customer service, call center, sales, billing, ticketing.
  2. Vancouver Coastal Health Porter (2.5+ years): equipment tracking, dispatching, inventory logs, operations.
- URGENT: Needs employment ASAP and will accept customer service and office/clerical support.
- Targeting: Customer service, call centre, reception, front desk, admin/data entry, clerk, coordinator, intake/scheduling.
""".strip()


_GENERAL_ASAP_GATE_RULES = """
Rules:
1. REJECT if clearly IT/software (developer, software engineer, DevOps, IT support, help desk, QA tester, network/sysadmin, etc.) — those go to a separate IT account.
2. REJECT food service/cooking (cook, chef, barista, kitchen helper, prep cook, food counter, host, server, dining, crew member).
3. REJECT physical labor or heavy lifting (warehouse, stocking, general labour, cleaning/janitor/housekeeping, mover, packaging, forklift, etc.).
4. REJECT clinical healthcare requiring licenses (RN, LPN, HCA, care aide, PSW), licensed beauty/spa (esthetician, cosmetologist, nail tech), childcare requiring ECE (daycare, nanny), or explicit gender preference ("female only", "women preferred").
5. REJECT only clear management/executive titles: manager, director, vice president, VP, head of, general manager, superintendent. Do NOT reject "senior" or "lead" when the role is still individual-contributor work (e.g. senior customer service rep, team lead cashier).
6. REJECT US citizenship, security clearance, or mandatory bilingual French.
7. REJECT commission-only or door-to-door sales.
8. DEFAULT TO APPROVE only for: customer service, call center, reception, front desk, admin assistant, data entry, clerk, office coordinator, guest services, clinic/medical reception, billing clerk, intake/scheduling.
9. When unsure, reject if it looks physical or food related, else approve if it is clean customer service or office work.
""".strip()


def _groq_gate_should_save_general(title: str, company: str, location: str,
                                   card_text: str, job_details: str,
                                   easy_apply: bool = False) -> tuple[bool, str]:
    """AI screening gate for the indeed_general bot.
    Evaluates whether the general job is a good fit for the candidate's non-IT general work profile.
    """
    prompt = f"""
You are a job-fit evaluator. Decide if this job is a good fit for the candidate's GENERAL work profile.

{_GENERAL_ASAP_CANDIDATE_PROFILE}

Job Details:
Title: {title}
Company: {company}
Location: {location}
Description: {job_details[:4000]}

{_GENERAL_ASAP_GATE_RULES}

Return a JSON object exactly like this:
{{"save": true, "reason": "short explanation of why this general role is a good fit"}}
or
{{"save": false, "reason": "short explanation of why this job is not a fit (e.g., IT role, licensed clinical role, etc.)"}}
""".strip()

    return _execute_ai_gate_completion(prompt)


def _get_job_details_text(page) -> str:
    detail = _get_job_description(page)
    if detail:
        return " ".join(detail.split())
    try:
        return " ".join((page.inner_text("body") or "").split())[:8000]
    except Exception:
        return ""


def _load_job_details_for_gate(page, job_id: str, title: str) -> str:
    if not _page_has_job_detail(page):
        try:
            _open_job_detail_from_results(page, job_id, title)
        except Exception:
            pass
    return _get_job_details_text(page)


def _extract_structured_job_data(job_id: str, raw_markdown: str, title: str, company: str, location: str) -> dict:
    """Extracts structured job details using the active LLM client and saves to data/extracted_jobs/<job_id>.json."""
    if not job_id:
        return {}
    
    prompt = f"""
You are an expert job parser. Extract structured details from the following job posting markdown text.

Job Metadata:
Expected Title: {title}
Expected Company: {company}
Expected Location: {location}

Job Posting Markdown:
{raw_markdown[:6000]}

Return a JSON object with the following keys and data types:
- "title": Job title (string)
- "company": Company name (string)
- "location": Job location (string)
- "salary": Salary information if available, or null (string or null)
- "remote_type": Remote, Hybrid, or On-site (string, default "On-site")
- "apply_type": Apply type, e.g., "Easy Apply", "SmartApply", "External", or "Unknown" (string)
- "seniority": Seniority level (e.g., "Junior", "Mid", "Senior", "Lead", "Intern", or "Unknown") (string)
- "language_requirements": List of required languages, e.g. ["English"] (list of strings)
- "requirements": List of key required skills, experience, or certifications (list of strings)
- "nice_to_have": List of preferred or nice-to-have qualifications (list of strings)
- "tech_stack": List of specific technologies, programming languages, databases, cloud providers, or tools mentioned (list of strings)
- "responsibilities": List of main duties and responsibilities (list of strings)

Return only a valid JSON object.
""".strip()

    result = {}
    if _aiClient is not None:
        try:
            provider = (_ai_provider or "").lower()
            if provider == "openai":
                from modules.ai.openaiConnections import ai_completion
                messages = [
                    {"role": "system", "content": "You are a precise job parser. Return compact valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
                result_str = ai_completion(_aiClient, messages, response_format={"type": "json_object"}, stream=False)
                result = _extract_json_object(result_str)
            elif provider == "deepseek":
                from modules.ai.deepseekConnections import deepseek_completion
                messages = [
                    {"role": "system", "content": "You are a precise job parser. Return compact valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
                result_str = deepseek_completion(_aiClient, messages, response_format={"type": "json_object"}, stream=False)
                result = _extract_json_object(result_str)
            elif provider == "gemini":
                from modules.ai.geminiConnections import gemini_answer_question
                try:
                    from config.questions import user_information_all
                except ImportError:
                    user_information_all = ""
                result_str = gemini_answer_question(
                    _aiClient, prompt, options=None,
                    question_type="text", job_description="",
                    about_company=None, user_information_all=user_information_all,
                )
                result = _extract_json_object(result_str)
            elif provider == "ollama":
                result_str = _aiClient(question=prompt, hint="", job_context="")
                result = _extract_json_object(result_str)
        except Exception as e:
            print_lg(f"[Indeed] Failed to parse job data using LLM: {e}")

    # Fallback/default structure if extraction failed or returned empty
    default_schema = {
        "title": title or result.get("title") or "Unknown",
        "company": company or result.get("company") or "Unknown",
        "location": location or result.get("location") or "Unknown",
        "salary": result.get("salary") or None,
        "remote_type": result.get("remote_type") or "Unknown",
        "apply_type": result.get("apply_type") or "Unknown",
        "seniority": result.get("seniority") or "Unknown",
        "language_requirements": result.get("language_requirements") or [],
        "requirements": result.get("requirements") or [],
        "nice_to_have": result.get("nice_to_have") or [],
        "tech_stack": result.get("tech_stack") or [],
        "responsibilities": result.get("responsibilities") or [],
    }

    # Merge extracted details into default_schema to ensure keys always exist
    for key, val in default_schema.items():
        if key not in result or result[key] is None:
            result[key] = val

    result["raw_markdown"] = raw_markdown or ""

    # Ensure directory exists and save
    try:
        dest_dir = resolve_project_path("data/extracted_jobs")
    except Exception:
        dest_dir = "data/extracted_jobs"
    
    os.makedirs(dest_dir, exist_ok=True)
    dest_file = os.path.join(dest_dir, f"{job_id}.json")
    
    try:
        with open(dest_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print_lg(f"[Indeed] Saved structured job data to {dest_file}")
    except Exception as e:
        print_lg(f"[Indeed] Error saving structured job data to {dest_file}: {e}")

    return result


def screen_job_with_ai(title: str, company: str, description: str, location: str = "", easy_apply: bool = False) -> tuple[bool, int, str]:
    """Individual job screening gate using the active LLM client or local rules."""
    # Global IT-only local-gate mode. Legacy Indeed/Glassdoor/Workopolis paths
    # still call this function; honor the shared deterministic policy instead
    # of making an LLM request when the no-AI pipeline is enabled.
    if (os.getenv("HARD_GATE_ONLY", "0").strip().lower() in {"1", "true", "yes", "on"}
            and os.environ.get("JOB_PROFILE", "IT").upper() == "IT"):
        from jobbots.core.discovery._gate_adapter import hard_screen_job
        return hard_screen_job(
            title=title, company=company, description=description,
            location=location, easy_apply=easy_apply,
        )
    saving_only = not easy_apply
    
    # Check if we are running under the GENERAL job profile
    profile_type = os.environ.get("JOB_PROFILE", "IT").upper()
    if profile_type == "GENERAL":
        # 1. Local General gate (reject female-dominated and other bad roles)
        reject, reason = _general_local_gate_reject(title, company, location or "", description)
        if reject:
            print_lg(f"    [screen_job_with_ai] local general reject: {reason}")
            return False, 20, f"local general reject: {reason}"
            
        # 2. AI General screening gate
        passed, gate_reason = _groq_gate_should_save_general(
            title, company, location or "", "", description, easy_apply=easy_apply
        )
        score = 85 if passed else 25
        return passed, score, gate_reason

    # 1. Local obvious non-IT reject check (IT profile)
    reject, reason = _obvious_non_it_reject(title, company, location or "", "", description, easy_apply=easy_apply)
    if reject:
        print_lg(f"    [screen_job_with_ai] local obvious reject: {reason}")
        return False, 20, f"local obvious reject: {reason}"
        
    # 2. Detailed AI screening (IT profile)
    passed, gate_reason = _groq_gate_should_save(
        title, company, location or "", "", description, saving_only=saving_only
    )
    score = 85 if passed else 25
    return passed, score, gate_reason


def _batch_screen_chunk_with_ai(jobs_payload: list[dict]) -> dict[str, dict]:
    """Single LLM call for one chunk of job cards. Returns normalized decisions."""
    if not jobs_payload:
        return {}

    profile_type = os.environ.get("JOB_PROFILE", "IT").upper()
    if profile_type == "GENERAL":
        prompt = f"""
You are a job-fit evaluator. Evaluate a batch of general (non-IT) job openings for the candidate.

{_GENERAL_ASAP_CANDIDATE_PROFILE}

{_GENERAL_ASAP_GATE_RULES}

List of jobs to evaluate:
{json.dumps(jobs_payload, indent=2)}

Return a JSON object exactly like this, mapping each job ID to a decision (PROCEED or REJECT) and a short reason:
{{
  "job_id_1": {{"decision": "PROCEED", "reason": "Reason for decision"}},
  "job_id_2": {{"decision": "REJECT", "reason": "Reason for decision"}}
}}
""".strip()
    else:
        prompt = f"""
You are a job-fit evaluator. Evaluate a batch of job openings for the candidate.

Candidate Profile:
- Education: KPU Bachelor of Technology in Information Technology (Network Administration & Security). Expected graduation: Dec 2026.
- Certifications: AWS Certified Solutions Architect – Associate, AWS Cloud Practitioner.
- Experience: 3 years of technical support at Bell Canada (diagnosing consumer Wi-Fi, routers, iOS/Android, networking).
- Academic Projects: AWS VPC setup, RADIUS/EAP-TLS authentication, OSSEC HIDS, Spring Boot, Docker, Python scripting.
- Targeting: Help Desk, Service Desk, Desktop Support, Tech Support, Customer Support Specialist (technical/corporate), QA Analyst/Tester, Junior Data Analyst, NOC/SOC, Junior Network/Systems/Cloud Admin, Security Systems Technician, Cloud Infrastructure Engineer, Junior DevOps, Junior Software Engineer, DBA, and IT co-op/internship roles.

Rules by Application Type:
1. For Easy Apply (has_easy_apply=true): Approve (PROCEED) ONLY if the TITLE is a real IT/tech role the candidate should do day-to-day:
   Help desk / service desk / desktop support / IT support / tech support, QA/SDET/software test,
   junior-mid systems/network/cloud/admin, junior software/web/data, SOC/security analyst, IT co-op/intern.
   HARD REJECT (even if description mentions software/systems/AI/data):
   - HR / recruiting / people ops / enrollment / student services
   - Marketing / digital marketer / SEO / social media / brand / content
   - Events / admin coordinator / office coordinator without IT in title
   - Geotechnical / civil / mechanical / construction / trades / field telecom install
   - Finance/retail/ops analyst without systems/IT/data-engineering in the title
   - Pure customer service / sales / retail without technical support in the title
   - Clearly senior/lead/manager/director (>5 years required) except IC help-desk leads
   Do NOT invent IT fit from job-description keywords alone. Title must be IT.
2. For Company Apply (has_easy_apply=false): Be strict. Reject unless the candidate is highly qualified for the role (entry-level Network/SysAdmin, Help Desk, Service Desk, Desktop Support, Cloud Support, junior DevOps/QA).

List of jobs to evaluate:
{json.dumps(jobs_payload, indent=2)}

Return a JSON object exactly like this, mapping each job ID to a decision (PROCEED or REJECT) and a short reason:
{{
  "job_id_1": {{"decision": "PROCEED", "reason": "Reason for decision"}},
  "job_id_2": {{"decision": "REJECT", "reason": "Reason for decision"}}
}}
""".strip()

    result = {}
    if _aiClient is not None:
        try:
            provider = (_ai_provider or "").lower()
            if provider == "openai":
                from modules.ai.openaiConnections import ai_completion
                messages = [
                    {"role": "system", "content": "You are a precise job evaluator. Return compact valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
                result_str = ai_completion(_aiClient, messages, response_format={"type": "json_object"}, stream=False)
                result = _extract_json_object(result_str)
            elif provider == "deepseek":
                from modules.ai.deepseekConnections import deepseek_completion
                messages = [
                    {"role": "system", "content": "You are a precise job evaluator. Return compact valid JSON only."},
                    {"role": "user", "content": prompt}
                ]
                result_str = deepseek_completion(_aiClient, messages, response_format={"type": "json_object"}, stream=False)
                result = _extract_json_object(result_str)
            elif provider == "gemini":
                from modules.ai.geminiConnections import gemini_answer_question
                try:
                    from config.questions import user_information_all
                except ImportError:
                    user_information_all = ""
                result_str = gemini_answer_question(
                    _aiClient, prompt, options=None,
                    question_type="text", job_description="",
                    about_company=None, user_information_all=user_information_all,
                )
                result = _extract_json_object(result_str)
            elif provider == "ollama":
                result_str = _aiClient(question=prompt, hint="", job_context="")
                result = _extract_json_object(result_str)
        except Exception as e:
            print_lg(f"[Indeed] Failed batch screening LLM call: {e}")

    if not result:
        # Fallback to local Ollama request
        try:
            from config.settings import use_ollama_for_indeed, ollama_base_url, ollama_model
        except Exception:
            use_ollama_for_indeed = True
            ollama_base_url = "http://localhost:11434/v1"
            ollama_model = "llama3.2:3b"

        if use_ollama_for_indeed:
            payload = {
                "model": ollama_model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a precise job evaluator. Return compact valid JSON only.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "stream": False,
                "temperature": 0.1,
            }
            try:
                api_url = ollama_base_url.rstrip("/") + "/chat/completions"
                req = Request(
                    api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=90) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                content = (data["choices"][0]["message"]["content"] or "").strip()
                result = _extract_json_object(content)
            except Exception as e:
                print_lg(f"[Indeed] Ollama fallback batch screening failed: {e}")

    final_decisions = {}
    if isinstance(result, dict):
        for jid, val in result.items():
            if isinstance(val, dict):
                decision = str(val.get("decision") or "").strip().upper()
                reason = str(val.get("reason") or "No reason provided").strip()
                if decision in ("PROCEED", "REJECT"):
                    final_decisions[str(jid)] = {"decision": decision, "reason": reason}
    return final_decisions


def _batch_fail_open_decision(job: dict) -> dict | None:
    """When all LLM gateways fail, still approve clear Easy-Apply IT titles.

    Prevents zero-enqueue days when Akash times out and OpenRouter is slow.
    Never fail-open company-site / non-IT / senior-looking titles.
    """
    title = str(job.get("title") or "")
    ea = bool(job.get("has_easy_apply"))
    if not ea:
        return None
    if not _title_has_explicit_it_phrase(title):
        return None
    # Re-check hard rejects so fail-open stays conservative.
    rejected, reason = _obvious_non_it_reject(
        title,
        str(job.get("company") or ""),
        str(job.get("location") or ""),
        str(job.get("card_text") or "")[:300],
        "",
        easy_apply=True,
    )
    if rejected:
        return None
    senior_reject, _ = _senior_save_gate_reject(title)
    if senior_reject:
        return None
    return {
        "decision": "PROCEED",
        "reason": "fail-open: explicit IT Easy Apply title after LLM gateway failure",
    }


def batch_screen_jobs_with_ai(jobs: list[dict]) -> dict[str, dict]:
    """Performs card-level batch evaluations of a list of job cards.

    Chunks into small batches (default 8) so Akash/DeepSeek does not timeout.
    Completions use Akash → OpenRouter failover (see deepseek_completion).
    Missing chunk decisions fail-open only for clear Easy Apply IT titles.
    """
    if not jobs:
        return {}

    try:
        chunk_size = int(os.getenv("DISCOVERY_BATCH_AI_CHUNK", "8") or "8")
    except ValueError:
        chunk_size = 8
    chunk_size = max(4, min(chunk_size, 40))

    # Simplify payload to save prompt tokens
    jobs_payload = []
    by_jid: dict[str, dict] = {}
    for job in jobs:
        jid = str(job.get("jid") or "")
        payload = {
            "jid": jid,
            "title": job.get("title"),
            "company": job.get("company"),
            "location": job.get("location"),
            "has_easy_apply": job.get("has_easy_apply"),
            "card_text_snippet": (job.get("card_text") or "")[:300],
        }
        jobs_payload.append(payload)
        if jid:
            by_jid[jid] = job

    final_decisions: dict[str, dict] = {}
    total_chunks = (len(jobs_payload) + chunk_size - 1) // chunk_size
    for i in range(0, len(jobs_payload), chunk_size):
        chunk = jobs_payload[i : i + chunk_size]
        print_lg(
            f"[Indeed] Batch screen chunk {i // chunk_size + 1}"
            f"/{total_chunks}"
            f" size={len(chunk)}"
        )
        part = _batch_screen_chunk_with_ai(chunk)
        if not part:
            print_lg(
                f"[Indeed] Batch chunk {i // chunk_size + 1} empty after LLM "
                f"failover — applying fail-open for clear IT Easy Apply titles"
            )
            for item in chunk:
                jid = str(item.get("jid") or "")
                src = by_jid.get(jid) or item
                fo = _batch_fail_open_decision(src)
                if fo and jid:
                    final_decisions[jid] = fo
                    print_lg(f"[Indeed] fail-open PROCEED jid={jid} title={src.get('title')!r}")
            continue
        final_decisions.update(part)
        # Fill holes in a partial response with fail-open for clear EA IT titles.
        for item in chunk:
            jid = str(item.get("jid") or "")
            if jid and jid not in final_decisions:
                src = by_jid.get(jid) or item
                fo = _batch_fail_open_decision(src)
                if fo:
                    final_decisions[jid] = fo
                    print_lg(
                        f"[Indeed] fail-open PROCEED (missing id) jid={jid} "
                        f"title={src.get('title')!r}"
                    )
    return final_decisions

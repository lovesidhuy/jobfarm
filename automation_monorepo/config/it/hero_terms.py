"""High-signal IT discovery terms (EA-first farm).

``search.py`` imports these as the primary ``search_terms``. Full historical
lists can live in ``search_terms_legacy`` for rare deep/company-site harvests.

Design:
  - One hero per fuzzy cluster (Indeed/Glassdoor already expand synonyms)
  - Explicit co-op / intern / student / entry IT for thin company-site path
  - No bare tech-stack keywords (java, html, css) — noise factories
"""

from __future__ import annotations

# Main Easy Apply loop — keep ~35–45; wrappers may further subset.
HERO_SEARCH_TERMS: list[str] = [
    # QA / Test
    "QA Analyst",
    "QA Engineer",
    "SDET",
    "Software Test Engineer",
    "QA Intern",
    "QA Co-op",
    # IT Support / Desk
    "IT Support",
    "IT Support Analyst",
    "IT Support Specialist",
    "Help Desk Technician",
    "Help Desk Analyst",
    "Service Desk Analyst",
    "Technical Support Analyst",
    "Desktop Support",
    "Junior IT Technician",
    "Entry Level IT Support",
    "IT Support Co-op",
    "IT Intern",
    "IT Student",
    "IT Co-op",
    # Data
    "Data Analyst",
    "Junior Data Analyst",
    "Business Systems Analyst",
    # Systems / Network
    "Systems Administrator",
    "Junior Systems Administrator",
    "Network Technician",
    "Network Support Technician",
    "IT Analyst",
    # Cloud / DevOps / Security (entry-friendly)
    "Cloud Support",
    "Cloud Engineer",
    "Junior DevOps Engineer",
    "SOC Analyst",
    "Security Analyst",
    # Software entry / student
    "Junior Software Developer",
    "Software Developer Intern",
    "Software Engineer Co-op",
    "Junior Software Engineer",
    # Additional IT/QA-related titles
    "IT Specialist",
    "IT Coordinator",
    "IT Administrator",
    "IT Operations Analyst",
    "Junior Systems Engineer",
    "Technical Specialist",
    "Application Support Analyst",
    "Information Technology Analyst",
    "Information Technology Support",
    "Help Desk Specialist",
    "Service Desk Specialist",
    "Desktop Support Analyst",
    "Desktop Support Technician",
    "Junior Network Engineer",
    "Cyber Security Analyst",
    "Information Security Analyst",
    "Junior Developer",
    "Web Developer",
    "Front End Developer",
    "Junior Front End Developer",
    "Junior Web Developer",
    "Junior Programmer",
    "Product Support Specialist",
    "Customer Support Engineer",
    "Support Engineer",
]

# LinkedIn IT Easy Apply farm (JobSpy × Metro cities). IT-only title list —
# office/CSR is a second pass on the same sole LinkedIn bot (linkedin_general).
# Expanded synonyms (2026-07) for higher EA volume without non-IT flood.
LINKEDIN_HERO_TERMS: list[str] = [
    # === Primary: Helpdesk / IT Support ===
    "Helpdesk Technician",
    "Help Desk Analyst",
    "Help Desk Specialist",
    "Help Desk Technician",
    "IT Support Specialist",
    "IT Support Analyst",
    "IT Support Technician",
    "IT Support",
    "Technical Support Analyst",
    "Technical Support Specialist",
    "Service Desk Analyst",
    "Service Desk Technician",
    "Desktop Support Technician",
    "Desktop Support Analyst",
    "Desktop Support",
    "System Support Specialist",
    "IT Help Desk",
    "Tier 1 Support",
    "Tier 2 Support",
    "First Line Support",
    "IT Office Support",
    "IT Onsite Technician",
    "Computer Technician",
    "PC Technician",
    "Field IT Technician",
    # === Secondary: Light IT Ops / Network ===
    "Network Support Technician",
    "Network Administrator",
    "Junior Network Administrator",
    "Systems Administrator",
    "Junior Systems Administrator",
    "System Administrator",
    "IT Infrastructure Analyst",
    "IT Analyst",
    "IT Specialist",
    "Application Support Analyst",
    "Application Support",
    "IT Coordinator",
    "IT Operations Analyst",
    "Computer Support Specialist",
    "IT Assistant",
    "Technology Deployment Technician",
    "Support Engineer",
    "Customer Support Engineer",
    "Product Support Specialist",
    "NOC Technician",
    # === Tertiary: Co-op / Intern / Entry ===
    "IT Co-op",
    "IT Intern",
    "IT Student",
    "IT Support Co-op",
    "Junior IT Support",
    "Entry Level IT Support",
    "Junior IT Technician",
    # === QA — manual/junior ===
    "QA Analyst",
    "QA Intern",
    "QA Co-op",
    "Manual Tester",
    "Quality Assurance Analyst",
    "Junior QA Analyst",
    # === Light software entry (high EA volume) ===
    "Junior Software Developer",
    "Software Developer Intern",
    "Junior Web Developer",
    "Data Analyst",
    "Junior Data Analyst",
]

# Rare company-site / all-leads harvest only (not every timer tick).
COMPANY_SITE_THIN_TERMS: list[str] = [
    "IT Co-op",
    "IT Intern",
    "IT Student",
    "IT Support Co-op",
    "QA Intern",
    "QA Co-op",
    "Software Developer Intern",
    "Software Engineer Co-op",
    "Entry Level IT Support",
    "Junior IT Technician",
    "Junior Data Analyst",
    "Junior Software Developer",
]

# Glassdoor / Workopolis core — high-signal Metro EA terms only.
# Bare "Software Engineer" floods national senior SWE; prefer support/QA/admin.
PORTAL_CORE_TERMS: list[str] = [
    # Productive manual-browse terms (IT support / desk first)
    "IT Support",
    "IT Support Analyst",
    "IT Support Specialist",
    "Help Desk Technician",
    "Help Desk Analyst",
    "Service Desk Analyst",
    "Desktop Support",
    "Technical Support Analyst",
    "Systems Administrator",
    "Junior Systems Administrator",
    "Network Support Technician",
    "QA Analyst",
    "QA Engineer",
    "SDET",
    "IT Analyst",
    "IT Co-op",
    "IT Intern",
    "Junior Data Analyst",
    "Application Support Analyst",
]

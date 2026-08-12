"""Glassdoor IT discovery overrides (Wave B.1).

Metro Vancouver cities only — no ``Remote``, no empty ``""`` remote pass.
Easy Apply only (no metro_all_leads / company-site discovery).

Location strings are tuned for JobSpy's Glassdoor ``findPopularLocationAjax``
lookup (bare ``City, BC`` returns 400; bare ``Richmond`` resolves to Ontario).
Spaces are fine here — ``normalize_glassdoor_location`` pre-encodes them for
JobSpy's unescaped URL builder.

Search terms are reused from ``config/it/search.py`` by the planner.
"""

# Core Metro Van cities first (Glassdoor 429s hard — keep the fanout modest).
# ``normalize_glassdoor_location`` maps these for locationAjax.
search_locations = [
    "Vancouver",
    "Burnaby",
    "Surrey",
    "Richmond BC",
    "Coquitlam",
    "North Vancouver",
]

search_location = "Vancouver"
search_radius_km = 25
easy_apply_only = True

# Glassdoor location + GraphQL + per-job description fetch 429s quickly.
glassdoor_request_pause_seconds = 2.0

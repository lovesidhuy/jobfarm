"""Workopolis IT discovery overrides (mirror Glassdoor Wave B.1).

Metro Vancouver cities only — no ``Remote``, no empty ``""`` remote pass.
Quick Apply / Easy Apply only (no company-site / unverified enqueue).

Search terms are reused from ``config/it/search.py`` by the planner.
Workopolis uses our custom HTTP (+ browser fallback) provider — not JobSpy.
"""

# Core Metro Van cities (same commute set as Glassdoor; Workopolis accepts
# plain city names with radius).
search_locations = [
    "Vancouver",
    "Burnaby",
    "Surrey",
    "Richmond",
    "Coquitlam",
]

search_location = "Vancouver"
search_radius_km = 25
easy_apply_only = True

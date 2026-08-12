"""
core.answer_policy — deterministic answer-policy engine.

Single source of truth for *what* to answer. The runtime is responsible for
*how* to apply that answer to the DOM (see `core.answer_controls`).

Design
------
This module contains **no I/O, no DOM, no AI calls**. Pure functions only.

The classifier maps a free-form question (plus optional options + control
type) onto a `Decision` describing:

    category    machine-readable bucket (e.g. "auth_ca", "availability_yes")
    intent      one of: "yes" | "no" | "numeric" | "text" | "decline" | None
    value       concrete answer text (when intent is text/numeric)
    source      decision_source label written into training logs
    ai_allowed  if False, runtime MUST NOT call an LLM for this question
    confidence  "hard" | "soft"   ("hard" => never override with AI)

Hard-locked categories (`ai_allowed=False`, `confidence="hard"`) reflect the
policy the user explicitly set:

    Identity / eligibility (immutable, never invent or stretch):
        auth_ca               -> yes        (Canadian PR, authorised in CA)
        sponsorship_ca        -> no         (no Canadian sponsorship needed)
        auth_us               -> no         (NOT authorised in US)
        sponsorship_us        -> yes        (would need sponsorship in US)
        gender                -> male
        pronouns              -> he/him
        veteran               -> no
        disability            -> no
        indigenous            -> no
        hispanic_latino       -> no
        criminal              -> no
        non_compete           -> no

    Optimisation layer (always YES — never randomly say "No"):
        availability_weekend, availability_evening, availability_night,
        availability_overtime, availability_holiday, availability_shift,
        availability_full_time, travel, commute, relocate, on_site,
        physical_stand, physical_lift, communication, multitasking,
        teamwork, customer_facing, adaptability, motivation, software_familiar,
        transferable_it_skills

    Numeric / configured (intent reflects DOM type):
        salary_expected       -> numeric (from config)
        years_experience      -> numeric (from config; per-skill table later)
        start_date            -> text (configured / today)
        interview_availability -> text (computed from today)

For everything else `classify` returns `Decision(category="unmatched")` and
the runtime is free to consult bot-specific rules or AI.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Default policy values — overridable by the runtime via `PolicyValues`.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyValues:
    """Bot-tunable values fed into the classifier.

    Each bot's runtime should construct one of these from its own
    `config/personals.py` / `config/questions.py` and pass it to
    `classify(..., values=values)`.
    """

    gender: str = "male"
    pronouns: str = "he/him"
    desired_salary: int = 80000
    years_experience: int = 3
    desired_start_date_str: str = ""
    full_name: str = ""
    interview_availability_text: str = ""

    # Hard identity flags (the user has locked these).
    authorized_canada: bool = True
    needs_canada_sponsorship: bool = False
    authorized_us: bool = False
    needs_us_sponsorship: bool = True
    is_veteran: bool = False
    has_disability: bool = False
    is_indigenous: bool = False
    is_hispanic_latino: bool = False
    has_criminal_record: bool = False

    # Optimisation flags — almost always True.
    can_work_weekends: bool = True
    can_work_evenings: bool = True
    can_work_nights: bool = True
    can_work_overtime: bool = True
    can_work_holidays: bool = True
    can_travel: bool = True
    can_commute: bool = True
    can_relocate: bool = True
    can_work_on_site: bool = True
    can_stand_long: bool = True
    can_lift_70_lbs: bool = True
    can_freely_travel_to_us: bool = False  # honest answer (not authorised)
    has_drivers_license: bool = True
    has_reliable_vehicle: bool = True
    speaks_english_fluent: bool = True
    speaks_french: bool = False


@dataclass(frozen=True)
class Decision:
    category: str
    intent: Optional[str] = None       # yes | no | numeric | text | decline
    value: Optional[str] = None        # concrete answer for text/numeric
    source: str = ""                   # decision_source label
    ai_allowed: bool = True
    confidence: str = "soft"           # hard | soft
    matched_keywords: tuple[str, ...] = field(default_factory=tuple)

    @property
    def matched(self) -> bool:
        return self.category != "unmatched"


# ---------------------------------------------------------------------------
# Keyword tables — kept compact and explicit.
# ---------------------------------------------------------------------------

# Each entry: (category, [keywords]).  ORDER MATTERS — first match wins, so
# more-specific patterns must come first.
_HARD_RULES: list[tuple[str, list[str]]] = [
    # --- US authorization / freely travel (must come BEFORE generic auth) ---
    ("auth_us", [
        "authorized to work in the us", "authorized to work in the united states",
        "eligible to work in the us", "eligible to work in the united states",
        "legally authorized to work in the us", "work authorization in the us",
        "us work authorization", "u.s. work authorization",
    ]),
    ("travel_us", [
        "travel to the us", "travel to us", "travel freely to the us",
        "travel freely to us", "freely travel to the us", "freely travel to us",
    ]),
    # --- Auth WITHOUT sponsorship (compound Yes) BEFORE bare sponsorship ---
    # "authorized to work in BC without the need for visa sponsorship?" → Yes
    ("auth_without_sponsorship", [
        "without the need for visa sponsorship",
        "without need for visa sponsorship",
        "without the need for sponsorship",
        "without need for sponsorship",
        "without visa sponsorship",
        "without requiring sponsorship",
        "without requiring visa sponsorship",
        "authorized to work without sponsorship",
        "eligible to work without sponsorship",
        "legally authorized to work without",
    ]),
    # --- Sponsorship (BEFORE generic auth which uses "authorized") ----------
    ("sponsorship_ca", [
        "require sponsorship", "need sponsorship", "visa sponsorship",
        "sponsorship to work in canada", "sponsorship now or in the future",
        "require visa", "need visa", "work permit sponsorship",
    ]),
    # --- Canadian work eligibility -----------------------------------------
    ("auth_ca", [
        "authorized to work in canada", "eligible to work in canada",
        "legally authorized to work in canada", "legal right to work in canada",
        "work in canada", "authorized to work in the country",
        "eligible to work in the country", "legally entitled to work",
        "authorized to work in british columbia",
        "eligible to work in british columbia",
        "authorized to work in bc",
        "documents légaux", "documents legaux",
    ]),
    # --- Demographics / EEO -----------------------------------------------
    ("gender",        [
        "gender", "what is your sex", "what is your gender",
        "select your sex", "biological sex", " sexe",
        # Bare "sex"/"sexe" only when not part of longer EEO race copy —
        # classifier still matches substrings; keep phrases preferred.
        "your sex", "sex?", "sexe?",
    ]),
    ("pronouns",      ["pronoun", "preferred pronoun"]),
    ("veteran",       ["veteran", "armed forces", "protected veteran", "military service"]),
    ("disability",    ["disability", "disabled", "differently abled", "handicap"]),
    ("indigenous",    ["indigenous", "aboriginal", "first nation", "métis", "metis", "inuit"]),
    ("hispanic",      ["hispanic", "latino", "latinx", "hispanique"]),
    ("race",          ["visible minority", "racial", "racialized", "ethnicity",
                       "ethnic origin", "what is your race"]),
    ("lgbtq",         ["lgbtq", "sexual orientation", "sexual identity"]),
    # --- Criminal / legal -------------------------------------------------
    ("criminal",      ["convicted", "felony", "criminal charge", "criminal offence",
                       "criminal offense", "criminal record", "criminal history"]),
    ("non_compete",   ["non-compete", "non compete", "restrictive covenant",
                       "employment bond", "bonded obligation",
                       "restrictive obligation", "conflict of interest"]),
    # --- Availability — always YES (per locked policy) ---------------------
    ("availability_weekend",  ["weekend", "weekends"]),
    ("availability_evening",  ["evening", "evenings"]),
    ("availability_night",    ["overnight", "night shift", "graveyard"]),
    ("availability_overtime", ["overtime"]),
    ("availability_holiday",  ["holiday", "holidays", "public holiday"]),
    ("availability_shift",    ["shift work", "rotating shift", "rotating shifts",
                               "shifts including"]),
    ("availability_full_time", ["40 hours", "full-time hours", "full time hours",
                                 "tuesday to saturday", "monday to friday",
                                 "monday through friday"]),
    # --- Travel / commute / relocate / on-site -----------------------------
    ("travel",     ["willing to travel", "travel for work", "travel between locations"]),
    ("commute",    ["commute", "commuting", "reliably commute"]),
    ("relocate",   ["relocate", "relocation", "willing to relocate"]),
    ("on_site",    ["on-site", "onsite", "in-office", "in office", "in person",
                    "in-person", "come to the office", "work from office"]),
    # --- Physical capability ----------------------------------------------
    ("physical_stand", ["stand for long periods", "standing for long periods",
                         "long periods of time"]),
    ("physical_lift",  ["lift up to", "weighing up to", "able to lift",
                         "lifting requirements"]),
    # --- Driver / vehicle -------------------------------------------------
    ("drivers_license", ["valid driver's license", "valid drivers license",
                          "valid driving licence", "valid driver's licence",
                          "valid drivers licence",
                          "driver's license", "drivers license",
                          "driver's licence", "drivers licence",
                          "driving licence", "driving license",
                          "bc driver", "bc license", "bc licence",
                          "g licence", "g license", "class 5"]),
    ("vehicle",         ["reliable vehicle", "access to a reliable vehicle",
                          "own vehicle", "personal vehicle"]),
    # --- Salary / numeric -------------------------------------------------
    ("salary_expected", ["salary expectation", "salary expectations",
                          "desired salary", "desired pay",
                          "expected salary", "expected compensation",
                          "wage expectation", "wage expectations",
                          "compensation expectation",
                          "annually in cad", "annual salary", "annual compensation",
                          "base salary", "base pay", "hourly rate",
                          "what are your wage", "what is your wage",
                          "what is your salary", "what are your salary",
                          "starting pay", "pay range", "pay rate",
                          "what is your pay", "what is the pay",
                          "what is the starting pay"]),
    # --- Years of experience ---------------------------------------------
    ("years_experience", ["how many years", "years of experience",
                           "amount of experience", "years have you"]),
    # --- Start date / interview availability ------------------------------
    ("start_date", ["start date", "desired start", "available to start",
                     "availability date", "date available", "available date",
                     "earliest available", "earliest start"]),
    ("interview_availability", ["interview availability", "available for an interview",
                                 "availability for a call", "available for a call",
                                 "phone screen", "screening call",
                                 "2-3 dates", "two to three dates"]),
    # --- Referral ---------------------------------------------------------
    ("referral", ["referred by", "referral name", "recommended by",
                   "current employee referral", "referrer"]),
    # --- Languages --------------------------------------------------------
    ("english_proficiency", ["speak english", "fluent in english",
                              "english proficiency", "proficient in english",
                              "english language"]),
    ("french_proficiency",  ["speak french", "fluent in french", "bilingual",
                              "français", "francais"]),
    # --- Confirmations / consent / privacy --------------------------------
    ("confirm_truthful", ["i confirm", "i certify", "i declare", "i acknowledge",
                           "true and complete", "truthful", "misrepresentation",
                           "falsification"]),
    ("consent_data", ["consent", "authorize processing", "privacy policy",
                       "personal information", "data processing"]),
    ("background_check", ["background check", "criminal record check",
                           "police check", "record check"]),
    ("drug_test", ["drug test", "substance test"]),
]


# ---------------------------------------------------------------------------
# Outcome resolver
# ---------------------------------------------------------------------------


def _yes_no(b: bool) -> str:
    return "yes" if b else "no"


def _hard(category: str, intent: str, source: str,
          value: Optional[str] = None) -> Decision:
    return Decision(
        category=category, intent=intent, value=value,
        source=source, ai_allowed=False, confidence="hard",
    )


def _soft(category: str, intent: str, source: str,
          value: Optional[str] = None) -> Decision:
    return Decision(
        category=category, intent=intent, value=value,
        source=source, ai_allowed=True, confidence="soft",
    )


def _resolve(category: str, v: PolicyValues) -> Decision:
    """Map a category to a hard-locked Decision using policy values."""

    # ── Identity / eligibility — never AI ──────────────────────────────────
    if category == "auth_without_sponsorship":
        # "authorized … without the need for visa sponsorship?" → Yes (Canada).
        return _hard(
            "auth_without_sponsorship",
            _yes_no(v.authorized_canada and not v.needs_canada_sponsorship),
            "policy_auth_without_sponsorship",
        )
    if category == "auth_ca":
        return _hard("auth_ca", _yes_no(v.authorized_canada), "policy_auth_ca")
    if category == "sponsorship_ca":
        return _hard("sponsorship_ca", _yes_no(v.needs_canada_sponsorship),
                     "policy_sponsorship_ca")
    if category == "auth_us":
        return _hard("auth_us", _yes_no(v.authorized_us), "policy_auth_us")
    if category == "travel_us":
        return _hard("travel_us", _yes_no(v.can_freely_travel_to_us),
                     "policy_travel_us")
    if category == "gender":
        return _hard("gender", "text", "policy_gender", value=v.gender)
    if category == "pronouns":
        return _hard("pronouns", "text", "policy_pronouns", value=v.pronouns)
    if category == "veteran":
        return _hard("veteran", _yes_no(v.is_veteran), "policy_veteran")
    if category == "disability":
        return _hard("disability", _yes_no(v.has_disability), "policy_disability")
    if category == "indigenous":
        return _hard("indigenous", _yes_no(v.is_indigenous), "policy_indigenous")
    if category == "hispanic":
        return _hard("hispanic", _yes_no(v.is_hispanic_latino), "policy_hispanic")
    if category == "race":
        return _hard("race", "decline", "policy_race_decline")
    if category == "lgbtq":
        return _hard("lgbtq", "decline", "policy_lgbtq_decline")
    if category == "criminal":
        return _hard("criminal", _yes_no(v.has_criminal_record), "policy_criminal")
    if category == "non_compete":
        return _hard("non_compete", "no", "policy_no_non_compete")

    # ── Availability — always YES (locked) ─────────────────────────────────
    if category == "availability_weekend":
        return _hard(category, _yes_no(v.can_work_weekends), "policy_avail_weekend")
    if category == "availability_evening":
        return _hard(category, _yes_no(v.can_work_evenings), "policy_avail_evening")
    if category == "availability_night":
        return _hard(category, _yes_no(v.can_work_nights), "policy_avail_night")
    if category == "availability_overtime":
        return _hard(category, _yes_no(v.can_work_overtime), "policy_avail_overtime")
    if category == "availability_holiday":
        return _hard(category, _yes_no(v.can_work_holidays), "policy_avail_holiday")
    if category in ("availability_shift", "availability_full_time"):
        return _hard(category, "yes", "policy_avail_yes")

    # ── Travel / commute / relocate / on-site ──────────────────────────────
    if category == "travel":
        return _hard(category, _yes_no(v.can_travel), "policy_travel")
    if category == "commute":
        return _hard(category, _yes_no(v.can_commute), "policy_commute")
    if category == "relocate":
        return _hard(category, _yes_no(v.can_relocate), "policy_relocate")
    if category == "on_site":
        return _hard(category, _yes_no(v.can_work_on_site), "policy_on_site")

    # ── Physical capability ───────────────────────────────────────────────
    if category == "physical_stand":
        return _hard(category, _yes_no(v.can_stand_long), "policy_stand")
    if category == "physical_lift":
        return _hard(category, _yes_no(v.can_lift_70_lbs), "policy_lift")

    # ── Driver / vehicle ──────────────────────────────────────────────────
    if category == "drivers_license":
        return _hard(category, _yes_no(v.has_drivers_license), "policy_dl")
    if category == "vehicle":
        return _hard(category, _yes_no(v.has_reliable_vehicle), "policy_vehicle")

    # ── Numeric / text ────────────────────────────────────────────────────
    if category == "salary_expected":
        return _hard(category, "numeric", "policy_salary",
                     value=str(v.desired_salary))
    if category == "years_experience":
        return _hard(category, "numeric", "policy_years",
                     value=str(v.years_experience))
    if category == "start_date":
        return _hard(category, "text", "policy_start_date",
                     value=v.desired_start_date_str)
    if category == "interview_availability":
        return _hard(category, "text", "policy_interview_availability",
                     value=v.interview_availability_text)
    if category == "referral":
        return _hard(category, "text", "policy_no_referral", value="N/A")

    # ── Languages ─────────────────────────────────────────────────────────
    if category == "english_proficiency":
        return _hard(category, _yes_no(v.speaks_english_fluent), "policy_english")
    if category == "french_proficiency":
        return _hard(category, _yes_no(v.speaks_french), "policy_french")

    # ── Consent / confirmations ───────────────────────────────────────────
    if category == "confirm_truthful":
        return _hard(category, "yes", "policy_confirm_yes")
    if category == "consent_data":
        return _hard(category, "yes", "policy_consent_yes")
    if category == "background_check":
        return _hard(category, "yes", "policy_bg_check_yes")
    if category == "drug_test":
        return _hard(category, "yes", "policy_drug_test_yes")

    return Decision(category="unmatched", ai_allowed=True, confidence="soft")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_WORD_RE = re.compile(r"[a-z0-9']+")


def _norm(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _has_kw(text_norm: str, kw: str) -> bool:
    """Whole-phrase match against normalised text. Allows word boundaries."""
    kw_norm = _norm(kw)
    if not kw_norm:
        return False
    return kw_norm in text_norm


def classify(question: str,
             options: Optional[Iterable[str]] = None,
             control_type: Optional[str] = None,
             values: Optional[PolicyValues] = None) -> Decision:
    """
    Map a question to a Decision.

    Parameters
    ----------
    question      Visible question text or hint.
    options       For radios/selects, list of visible option labels. Helps
                  classify edge cases where the question text is empty but
                  the option set is diagnostic (e.g. demographic dropdowns).
    control_type  "radio_group_name" | "select" | "text" | "textarea" | None
    values        Per-bot policy values. Defaults to the locked defaults.
    """
    v = values or PolicyValues()
    qn = _norm(question)
    on = _norm(" ".join(options or []))
    haystack = f"{qn} {on}".strip()
    if not haystack:
        return Decision(category="unmatched")

    matched: list[str] = []

    for category, kws in _HARD_RULES:
        for kw in kws:
            if _has_kw(haystack, kw):
                matched.append(kw)
                d = _resolve(category, v)
                return Decision(
                    category=d.category, intent=d.intent, value=d.value,
                    source=d.source, ai_allowed=d.ai_allowed,
                    confidence=d.confidence,
                    matched_keywords=tuple(matched),
                )

    # Fallback: option set alone is diagnostic for some demographic dropdowns.
    if options:
        opts_l = " ".join(options).lower()
        if "prefer not to disclose" in opts_l and any(
            k in opts_l for k in ("aboriginal", "métis", "metis", "inuit",
                                  "indigenous", "hispanic", "latino")
        ):
            d = _resolve("indigenous", v) if "indigenous" in opts_l else \
                _resolve("hispanic", v) if "hispanic" in opts_l else \
                _resolve("race", v)
            return d

    return Decision(category="unmatched")


def map_intent_to_option(intent: str, option_labels: list[str]) -> Optional[str]:
    """
    Given a Decision intent ("yes"/"no"/"decline"/...) and a list of visible
    option labels, return the best-matching label or None.

    Synonym-aware: handles Yes./Yep/True/Oui/Si, No./Nope/False/Non, plus
    "prefer not to disclose" for `decline`.
    """
    if not intent or not option_labels:
        return None

    syn = {
        "yes":     ("yes", "yep", "yeah", "y", "true", "oui", "si",
                    "agree", "i agree", "i consent", "consent", "i confirm",
                    "i certify", "i acknowledge", "authorized", "eligible"),
        "no":      ("no", "nope", "nah", "n", "false", "non",
                    "disagree", "do not", "i do not", "decline",
                    "not a veteran", "none"),
        "decline": ("prefer not to disclose", "prefer not to say",
                    "prefer not", "do not wish", "decline to answer",
                    "decline", "not disclose"),
    }
    # Normalise intent before synonym lookup so "Yes." / "no." / " YES " all
    # land on the canonical bucket.
    intent_key = _norm(intent)
    norm_opts = [(o, _norm(o)) for o in option_labels]

    # 1. Exact match
    for original, on in norm_opts:
        if on == intent_key:
            return original

    # 2. Word boundary decision matching for no / yes / decline
    if intent_key in ("no", "false"):
        neg_re = re.compile(r"\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b", re.I)
        for original, on in norm_opts:
            if neg_re.search(original):
                return original
    elif intent_key in ("yes", "true"):
        pos_re = re.compile(r"\b(yes|am|have|do|i am|i have|authorized|eligible|willing)\b", re.I)
        neg_re = re.compile(r"\b(no|not|n't|don't|do not|decline|prefer not|cannot|unwilling|none)\b", re.I)
        for original, on in norm_opts:
            if pos_re.search(original) and not neg_re.search(original):
                return original
    elif intent_key in ("decline", "prefer not"):
        dec_re = re.compile(r"\b(decline|prefer not|not wish|dont wish|don't wish|rather not)\b", re.I)
        for original, on in norm_opts:
            if dec_re.search(original):
                return original

    # 3. Safe word boundary / long substring match (len >= 4)
    if len(intent_key) >= 4 and intent_key not in ("yes", "no", "true", "false", "decline"):
        for original, on in norm_opts:
            if intent_key in on or on in intent_key:
                return original

    return None

"""Thin adapter that imports the existing AI screening gate for use by the
discovery planner without pulling in the full Indeed browser bootstrap.

The gate functions live in ``core.shared_jobbots.core.shared_modules.indeed.gates`` and require
certain global symbols from the ``_bootstrap`` module.  This adapter
isolates the discovery engine from those browser-session globals by calling
the gate functions directly with their public signature.

Phase I discovery mirrors Indeed browser Phase I cleaning:

  * Easy Apply → ``_local_easy_apply_gate_should_apply`` (senior/lead/director
    title reject, non-IT reject, explicit IT phrase / target-role approve)
  * Company-site / VERIFY → ``_senior_save_gate_reject`` +
    ``_local_company_site_gate`` (same save-path cleaning)

So Phase II (application worker) can focus on apply/bookmark execution.
"""
from __future__ import annotations

import logging
import os
import json
import re
import sys
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

_log = logging.getLogger("discovery.gate_adapter")

# Lazy-loaded reference to the screening function
_screen_fn = None
_review_overrides: dict[str, str] | None = None


def _review_key(title: str, company: str) -> str:
    normalize = lambda value: re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
    return f"{normalize(company)}|{normalize(title)}"


def _load_review_overrides() -> dict[str, str]:
    global _review_overrides
    if _review_overrides is not None:
        return _review_overrides
    path = _MONOREPO_ROOT / "data" / "training" / "it_title_gate_overrides.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        _review_overrides = {str(k): str(v).lower() for k, v in raw.items()}
    except Exception:
        _review_overrides = {}
    return _review_overrides


def _import_indeed_gates() -> Any:
    """Import ``jobbots.core.shared_modules.indeed.gates`` with the shared_modules path shim."""
    if "pyautogui" not in sys.modules:
        import unittest.mock as mock
        dummy = mock.MagicMock()
        sys.modules["pyautogui"] = dummy
        sys.modules["mouseinfo"] = dummy
    monorepo = _MONOREPO_ROOT
    target_dir = monorepo.parent / "master" / "it_indeed cwgeopy" / "Auto_indeed"
    if str(target_dir) not in sys.path:
        sys.path.insert(0, str(target_dir))
    import modules
    # Canonical location since Phase 2: jobbots/core/shared_modules.
    shared_path = str(monorepo.parent / "jobbots" / "core" / "shared_modules")
    if hasattr(modules, "__path__") and shared_path not in modules.__path__:
        modules.__path__.append(shared_path)

    import jobbots.core.shared_modules.indeed.gates as gates
    return gates


def _jd_hard_blockers(description: str) -> str | None:
    """Explicit JD blockers shared by Easy Apply and company-site Phase I."""
    detail_text = " ".join((description or "").lower().split())
    required_years = [
        int(value) for value in re.findall(
            r"(?:minimum|at least|must have|required|requires?)"
            r"[^.]{0,100}?\b(\d{1,2})\+?\s+years?\b",
            detail_text,
        )
    ]
    if required_years and max(required_years) >= 5:
        return f"explicitly requires {max(required_years)}+ years"

    hard_requirement_markers = (
        ("security clearance", "requires security clearance"),
        ("must be a canadian citizen", "requires Canadian citizenship"),
        ("must be a us citizen", "requires US citizenship"),
        ("u.s. citizen", "requires US citizenship"),
        ("french required", "French is required"),
        ("bilingual french", "French is required"),
        ("english and french", "French is required"),
        ("red seal", "requires a Red Seal trade ticket"),
        ("journeyman", "requires a journeyman trade ticket"),
        ("class 1 licence", "requires a Class 1 licence"),
        ("class 1 license", "requires a Class 1 licence"),
        ("registered nurse", "requires an RN licence"),
        ("licensed practical nurse", "requires an LPN licence"),
    )
    for marker, reason in hard_requirement_markers:
        if marker in detail_text:
            return reason
    return None


# Fallback allowlist when company-site local gate defers to AI (hard-gate-only mode).
_IT_TITLE_SIGNALS = (
    "information technology", "it support", "it technician", "it analyst",
    "it coordinator", "it assistant", "it administrator", "it intern",
    "it operations", "service desk", "help desk", "helpdesk", "desktop support",
    "technical support", "application support", "product support", "support engineer",
    "systems administrator", "system administrator", "systems analyst", "system analyst",
    "network administrator", "network engineer", "network analyst", "network technician",
    "cloud engineer", "cloud support", "cloud administrator", "cloud operations",
    "devops", "site reliability", "sre", "infrastructure", "noc ",
    "cybersecurity", "cyber security", "security analyst", "security engineer",
    "security operations", "soc analyst", "cloud security", "identity and access", "iam ",
    "software engineer", "software developer", "software development", "software integration",
    "programmer", "research developer", "application development", "full stack",
    "frontend", "front-end", "front end", "backend", "back end", "web developer", "application developer",
    "qa analyst", "qa tester", "qa engineer", "qa specialist", "qa automation", "quality assurance",
    "sdet", "test automation", "erp testing",
    "data analyst", "data engineer", "data scientist", "product analytics", "people analytics",
    "business intelligence", "bi analyst", "data management", "data analytics",
    "machine learning", "ai/ml", "artificial intelligence", "ai integration", "ai products",
    "database administrator", "database analyst", "computer technician", "technical analyst",
    "technical support engineer",
    "digital technology", "technology deployment", "informatics", "hris analyst", "systems coordinator",
    "software analyst", "sap analyst", "sap analytics", "sap/ariba", "peoplesoft", "basis analyst",
    "it business analyst",
    "information technology student", "it student", "engineering co-op", "engineering coop",
    "software analyst intern", "associate cloud",
)

# General is intentionally a separate office / customer-service profile.  It
# must not be routed through the IT-only gate above: doing so makes a healthy
# General discovery run find hundreds of listings and then reject every one
# before the queue.  Keep this deliberately tied to the configured General
# search family so it does not turn into an unbounded all-jobs feed.
_GENERAL_TITLE_SIGNALS = (
    "customer service", "customer care", "customer experience",
    "client service", "client services", "guest services", "member service",
    "patient service", "receptionist", "front desk", "office assistant",
    "office clerk", "office coordinator", "administrative assistant",
    "administrative coordinator", "admin assistant", "data entry", "order entry",
    "call centre", "call center", "contact centre", "contact center",
    "appointment scheduler", "scheduling coordinator", "operations assistant",
)

# Floor retail / clinical / trades noise that matches "customer service" or
# "front desk" substrings but is not productive for the office/CS farm.
_GENERAL_HARD_REJECT_MARKERS = (
    "cashier", "grocery", "produce clerk", "deli clerk", "bakery associate",
    "retail associate", "sales associate", "stock associate", "store associate",
    "dental", "dentist", "veterinary", "veterinar", "medical office assistant",
    "medical receptionist", "clinic receptionist", "patient care coordinator",
    "nurse", "phlebotom", "pharmacy assistant", "optometr",
    "immigration", "real estate", "rental agent", "greenkeeper", "golf",
    "server ", "bartender", "barista", "hostess", "busser", "dishwasher",
    "warehouse", "forklift", "driver", "courier", "delivery",
    "security guard", "janitor", "housekeep", "cleaner",
)

_GENERAL_SENIORITY_MARKERS = (
    "manager", "director", "senior", "sr.", "lead", "supervisor",
    "principal", "head of", "vice president", "vp ",
)


def hard_screen_job(
    *,
    title: str,
    company: str,
    description: str,
    location: str = "",
    easy_apply: bool = False,
    profile: str = "it",
) -> tuple[bool, int, str]:
    """Run Indeed Phase I cleaning without initializing an AI client.

    Easy Apply and company-site / VERIFY use the same local gate functions as
    the Indeed browser bot so LinkedIn/Glassdoor/Workopolis discovery do not
    enqueue roles Phase II would still need to seniority-filter.
    """
    profile_key = (profile or "it").strip().lower()

    # General's configured targets are office/customer-service roles.  Apply
    # only universal blockers and a bounded General title family; importing
    # the Indeed IT gate here would reject valid General jobs as non-IT.
    if profile_key == "general":
        title_text = " ".join((title or "").lower().split())
        company_text = " ".join((company or "").lower().split())
        # Null/garbage company (pandas NaN stringified) must never reach apply.
        if company_text in {"", "nan", "none", "null", "n/a", "na", "-"}:
            return False, 0, "general hard gate: invalid_company"
        jd_block = _jd_hard_blockers(description)
        if jd_block:
            return False, 0, f"general hard gate: {jd_block}"
        if any(marker in title_text for marker in _GENERAL_SENIORITY_MARKERS):
            return False, 0, "general hard gate: senior or management title"
        if any(marker in title_text for marker in _GENERAL_HARD_REJECT_MARKERS):
            return False, 0, "general hard gate: floor retail/clinical/trades title"
        if any(signal in title_text for signal in _GENERAL_TITLE_SIGNALS):
            return True, 100, "general hard gate: configured office/customer-service title"
        return False, 0, "general hard gate: title outside configured office/customer-service targets"

    gates = _import_indeed_gates()

    override = _load_review_overrides().get(_review_key(title, company))
    if override == "skip":
        return False, 0, "hard gate: user-reviewed skip override"
    if override == "apply":
        # User-reviewed apply list (senior/staff/supervisor IT roles included).
        return True, 100, "hard gate: user-reviewed apply override"

    company_text = " ".join((company or "").lower().split())
    if company_text in {"", "nan", "none", "null", "n/a", "na", "-"}:
        return False, 0, "hard gate: ambiguous_title: invalid_company"

    title_text = (title or "").lower()
    if "programmer" in title_text and any(
        term in title_text for term in ("cnc", "cabinet", "millwork", "fabrication")
    ):
        return False, 0, "hard gate: non-software programmer domain"

    jd_block = _jd_hard_blockers(description)
    if jd_block:
        return False, 0, f"hard gate: {jd_block}"

    card_text = ""
    details = description or ""

    # ── Easy Apply: local IT gate; only *obvious* non-IT is hard-reject.
    # Unsure titles (no explicit IT phrase) are tagged ``ambiguous_title`` so
    # the planner can batch-screen titles before apply/reject.
    if easy_apply:
        approved, reason = gates._local_easy_apply_gate_should_apply(
            title, company, location, card_text, details,
        )
        if approved:
            return True, 100, f"hard gate: {reason}"
        reason_l = (reason or "").lower()
        # Hard rejects: obvious non-IT / senior-lead — never spend batch AI.
        if _is_hard_local_reject(reason_l):
            return False, 0, f"hard gate: {reason}"
        # Unsure IT-ness → Phase I batch title screen (not Phase II re-gate).
        return False, 0, f"hard gate: ambiguous_title: {reason}"

    # ── Company-site / VERIFY: Indeed save-path Phase I cleaning ──────────
    rejected, reason = gates._obvious_non_it_reject(
        title, company, location, card_text, details, easy_apply=True,
    )
    if rejected:
        return False, 0, f"hard gate: {reason}"

    senior_reject, senior_reason = gates._senior_save_gate_reject(title)
    if senior_reject:
        return False, 0, f"hard gate: {senior_reason}"

    local_decision, local_reason = gates._local_company_site_gate(
        title, company, location, card_text, details,
    )
    if local_decision == "approve":
        return True, 100, f"hard gate: {local_reason}"
    if local_decision == "reject":
        return False, 0, f"hard gate: {local_reason}"

    # Local gate deferred to AI — title lacks clear IT signal → batch title AI.
    if not any(signal in title_text for signal in _IT_TITLE_SIGNALS):
        return False, 0, (
            "hard gate: ambiguous_title: title lacks an explicit IT discipline"
        )
    return True, 100, f"hard gate: {local_reason}"


def _is_hard_local_reject(reason_l: str) -> bool:
    """True when the local Easy Apply reason is a permanent hard reject."""
    markers = (
        "obvious non-it",
        "non-it title",
        "non-it role",
        "non-it support",
        "non-it data",
        "non-it ",
        "non it ",
        "physical security role",
        "technical sales",
        "sales engineering",
        "senior",
        "sr.",
        "lead",
        "principal",
        "staff",
        "director",
        "architect",
        "management role",
        "strict title check",
        "user-reviewed skip",
        "non-software programmer",
        "explicitly requires",
        "security clearance",
        "citizenship",
        "french is required",
        "red seal",
        "journeyman",
        "class 1",
        "registered nurse",
        "licensed practical",
        "childcare",
        "banquet",
        "barista",
        "warehouse associate",
        "cashier",
        "cook ",
        "chef",
        "server ",
        "driver",
        "labourer",
        "laborer",
        "retail associate",
    )
    return any(m in reason_l for m in markers)


def is_ambiguous_title_reason(reason: str) -> bool:
    """Planner helper: local hard-screen deferred this title to batch AI."""
    r = (reason or "").lower()
    return (
        "ambiguous_title" in r
        or "title lacks an explicit it discipline" in r
        or "no explicit it phrase" in r
    )



def batch_ai_screen_jobs(jobs: list[dict]) -> dict[str, dict]:
    """Run the legacy LLM only for explicitly deferred batch decisions."""
    if not jobs:
        return {}
    import sys
    from pathlib import Path

    monorepo = _MONOREPO_ROOT
    target_dir = monorepo.parent / "master" / "it_indeed cwgeopy" / "Auto_indeed"
    if str(target_dir) not in sys.path:
        sys.path.insert(0, str(target_dir))
    import modules
    shared_path = str(monorepo.parent / "jobbots" / "core" / "shared_modules")
    if hasattr(modules, "__path__") and shared_path not in modules.__path__:
        modules.__path__.append(shared_path)
    from jobbots.core.shared_modules.indeed.gates import _init_ai_client, batch_screen_jobs_with_ai
    _init_ai_client()
    return batch_screen_jobs_with_ai(jobs)


def _ensure_gate_loaded() -> None:
    """Lazy-import the screening gate from the Indeed shared module."""
    global _screen_fn
    if _screen_fn is not None:
        return

    import sys
    from pathlib import Path
    monorepo = _MONOREPO_ROOT
    target_dir = monorepo.parent / "master" / "it_indeed cwgeopy" / "Auto_indeed"
    if str(target_dir) not in sys.path:
        sys.path.insert(0, str(target_dir))

    try:
        import modules
        shared_path = str(monorepo.parent / "jobbots" / "core" / "shared_modules")
        if hasattr(modules, "__path__") and shared_path not in modules.__path__:
            modules.__path__.append(shared_path)
    except ImportError:
        pass

    try:
        from jobbots.core.shared_modules.indeed import screen_job_with_ai, _init_ai_client
        _init_ai_client()
        _screen_fn = screen_job_with_ai
        _log.info("AI screening gate loaded from core.shared_modules.indeed package")
    except ImportError:
        # Fallback: try importing via the monorepo modules path
        import sys
        from pathlib import Path

        monorepo = _MONOREPO_ROOT
        shared = monorepo.parent / "jobbots" / "core" / "shared_modules"

        # Temporarily make 'jobbots.core.shared_modules.indeed.gates' importable
        if str(shared) not in sys.path:
            sys.path.insert(0, str(shared))

        try:
            from indeed import screen_job_with_ai, _init_ai_client
            _init_ai_client()
            _screen_fn = screen_job_with_ai
            _log.info("AI screening gate loaded via fallback path")
        except ImportError as exc:
            _log.error(
                "Cannot import screen_job_with_ai — "
                "AI screening will be unavailable: %s", exc,
            )
            raise


def screen_job(
    *,
    title: str,
    company: str,
    description: str,
    location: str = "",
    easy_apply: bool = False,
    profile: str = "it",
) -> tuple[bool, int, str]:
    """Screen a single job using the existing AI gate.

    Returns ``(passed, fit_score, reason)`` — same contract as
    ``screen_job_with_ai()`` in ``core.shared_jobbots.core.shared_modules.indeed.gates``.
    """
    _ensure_gate_loaded()

    # Set JOB_PROFILE so the gate uses the correct profile rules
    prev_profile = os.environ.get("JOB_PROFILE")
    os.environ["JOB_PROFILE"] = profile.upper()

    try:
        return _screen_fn(
            title, company, description,
            location=location,
            easy_apply=easy_apply,
        )
    finally:
        # Restore previous profile
        if prev_profile is not None:
            os.environ["JOB_PROFILE"] = prev_profile
        else:
            os.environ.pop("JOB_PROFILE", None)

"""Portal-agnostic application answers.

Share the Indeed IT answer brain without Indeed DOM/SmartApply:

  1. hard policy (``core.llm_backend.answer_policy``) — never invent identity
  2. curated QA bank (``find_answer`` / training JSON)
  3. deterministic profile/safe rules (name, country, cover letter, ...)
  4. DeepSeek via OpenRouter — same stack as Indeed
     (``deepseek_create_client`` + ``deepseek_answer_question``)

Greenhouse/Lever keep their own DOM adapters; only answers are shared.
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any, Iterable


@dataclass
class ResolvedAnswer:
    value: str
    source: str
    score: float = 1.0
    matched_question: str = ""


def _normalize(text: str) -> str:
    value = (text or "").lower().replace("\u00a0", " ")
    value = re.sub(r"\([^)]*duplicate[^)]*\)", " ", value)
    value = re.sub(r"\s*\*\s*$", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def load_profile() -> dict[str, Any]:
    profile = (os.getenv("JOB_PROFILE") or "IT").strip().upper()
    if profile not in {"IT", "GENERAL"}:
        profile = "IT"

    data: dict[str, Any] = {
        "first_name": "Jane",
        "last_name": "Doe",
        "full_name": "Jane Doe",
        "email": "user@example.com",
        "phone": "5550199",
        "city": "Surrey",
        "state": "BC",
        "zipcode": "V6B 1A1",
        "country": "Canada",
        "location": "Surrey, BC, Canada",
        "school": "Kwantlen Polytechnic University",
        "school_short": "KPU",
        "graduation_year": "2026",
        "graduation_month": "December",
        # KPU BTech IT — enrolled since Sep 2022 (not 2019).
        "education_start_year": "2022",
        "education_start_month": "September",
        "graduated": "No",
        "street": "100 Main Street",
        "linkedin": "https://linkedin.com/in/example",
        "website": "https://example.com/portfolio",
        "years_of_experience": "3",
        "require_visa": "No",
        "desired_salary": "70000",
        "cover_letter": "",
        "resume_path": "",
        "gender": "Male",
        # EEO defaults — never invent Hispanic/Latino or disability.
        "ethnicity": "Decline",
        "disability_status": "Decline",
        "veteran_status": "Decline",
        "how_heard": "Job board",
        "profile_summary": "",
        "why_company_answer": (
            "I'm excited about the opportunity to contribute to sustainable outdoor apparel and technology-driven solutions. "
            "My background in IT support, QA testing, and systems engineering aligns well with continuous improvement and reliability. "
            "The emphasis on quality craftsmanship and environmental responsibility resonates with my values. "
            "I'm based in Surrey/BC and available for the hybrid model near North Vancouver."
        ),
        "current_company": "Currently seeking opportunities",
        # ATS work-history blocks frequently label this field only "Title".
        # Keep the honest current identity deterministic instead of sending an
        # underspecified label through the generic Q&A fallback.
        "current_title": "IT Student",
    }

    try:
        if profile == "GENERAL":
            from config.general import personals as personals  # type: ignore
            from config.general import questions as questions  # type: ignore
        else:
            from config.it import personals as personals  # type: ignore
            from config.it import questions as questions  # type: ignore

        data["first_name"] = getattr(personals, "first_name", data["first_name"])
        data["last_name"] = getattr(personals, "last_name", data["last_name"])
        data["full_name"] = f"{data['first_name']} {data['last_name']}".strip()
        data["email"] = getattr(personals, "email_address", data["email"])
        phone = str(getattr(personals, "phone_number", data["phone"]) or "")
        data["phone"] = re.sub(r"\D+", "", phone)[-10:] or data["phone"]
        data["city"] = getattr(personals, "current_city", data["city"])
        data["state"] = getattr(personals, "state", data["state"])
        data["zipcode"] = getattr(personals, "zipcode", data["zipcode"])
        data["country"] = getattr(personals, "country", data["country"])
        data["street"] = getattr(personals, "street", data["street"])
        data["location"] = f"{data['city']}, {data['state']}, {data['country']}"
        data["school"] = getattr(questions, "school", data["school"])
        data["school_short"] = getattr(questions, "school_short", data["school_short"])
        data["years_of_experience"] = str(
            getattr(questions, "years_of_experience", data["years_of_experience"])
        )
        data["require_visa"] = getattr(questions, "require_visa", data["require_visa"])
        data["desired_salary"] = str(getattr(questions, "desired_salary", data["desired_salary"]))
        data["website"] = getattr(questions, "website", data["website"]) or data["website"]
        data["linkedin"] = (
            getattr(questions, "professional_profile_url", data["linkedin"]) or data["linkedin"]
        )
        data["cover_letter"] = (getattr(questions, "cover_letter", "") or "").strip()
        data["profile_summary"] = (getattr(questions, "profile_summary", "") or "").strip()
        data["gender"] = getattr(personals, "gender", data["gender"]) or data["gender"]
        # EEO / voluntary self-ID (Greenhouse, LinkedIn Easy Apply, etc.)
        data["ethnicity"] = getattr(personals, "ethnicity", data.get("ethnicity") or "Decline") or "Decline"
        data["disability_status"] = (
            getattr(personals, "disability_status", data.get("disability_status") or "Decline") or "Decline"
        )
        data["veteran_status"] = (
            getattr(personals, "veteran_status", data.get("veteran_status") or "Decline") or "Decline"
        )
        resume = getattr(questions, "default_resume_path", "") or ""
        if resume and Path(resume).is_file():
            data["resume_path"] = str(Path(resume).resolve())
    except Exception:
        pass

    for key in ("INDEED_TAILORED_RESUME_PATH", "ATS_RESUME_PATH", "TAILORED_RESUME_PATH"):
        cand = (os.getenv(key) or "").strip()
        if cand and Path(cand).is_file():
            data["resume_path"] = str(Path(cand).resolve())
            break

    monorepo = _MONOREPO_ROOT
    if not data["resume_path"]:
        for rel in ("all resumes/ls_resume_it.pdf", "all resumes/ls_resume_general.pdf"):
            p = monorepo / rel
            if p.is_file():
                data["resume_path"] = str(p)
                break

    # Cover letter PDF (never reuse resume here).
    for key in ("ATS_COVER_LETTER_PATH", "COVER_LETTER_PATH"):
        cand = (os.getenv(key) or "").strip()
        if cand and Path(cand).is_file():
            data["cover_letter_path"] = str(Path(cand).resolve())
            break
    if not data.get("cover_letter_path"):
        for rel in (
            "all resumes/cover_ls_it.pdf",
            "all resumes/cover_ls_general.pdf",
            "all resumes/cover_letter.pdf",
        ):
            p = monorepo / rel
            if p.is_file():
                data["cover_letter_path"] = str(p)
                break
    if "cover_letter_path" not in data:
        data["cover_letter_path"] = ""
    return data


def _ensure_master_modules_on_path() -> Path | None:
    """Allow importing ``modules.qa_answer_bank`` from the IT master tree."""
    monorepo = _MONOREPO_ROOT
    repo = monorepo.parent
    candidates = [
        repo / "master" / "it_indeed cwgeopy" / "Auto_indeed",
        repo / "master" / "gen_indeed" / "Auto_indeed",
    ]
    for root in candidates:
        modules = root / "modules"
        if modules.is_dir():
            s = str(root)
            if s not in sys.path:
                sys.path.insert(0, s)
            # monorepo for helpers/core if bank resolves project paths
            ms = str(monorepo)
            if ms not in sys.path:
                sys.path.insert(1, ms)
            return root
    return None


def _bank_find_answer(
    question: str,
    *,
    hint: str = "",
    options: Iterable[str] | None = None,
) -> ResolvedAnswer | None:
    root = _ensure_master_modules_on_path()
    if root is None:
        return None
    # Prefer IT bank when JOB_PROFILE is IT
    os.environ.setdefault("BOT_NAME", "indeed_it" if "it" in (os.getenv("JOB_PROFILE") or "it").lower() else "indeed_general")
    try:
        from modules.qa_answer_bank import find_answer  # type: ignore
    except Exception:
        return None
    try:
        match = find_answer(question=question, hint=hint, options=options)
    except Exception:
        return None
    if not match:
        return None
    return ResolvedAnswer(
        value=str(match.answer),
        source=str(getattr(match, "source", "qa_answer_bank")),
        score=float(getattr(match, "score", 1.0) or 1.0),
        matched_question=str(getattr(match, "matched_question", question) or question),
    )


def _is_female_option(opt: str) -> bool:
    on = _normalize(opt)
    if not on:
        return False
    # "female", "woman", "femme" — not "male".
    if on in {"female", "woman", "women", "femme", "f", "she her", "she/her"}:
        return True
    if "female" in on or "woman" in on or "femme" in on:
        return True
    return False


def _is_male_option(opt: str) -> bool:
    """True for binary Male/Man/Homme only — never Female or Transgender male."""
    on = _normalize(opt)
    if not on:
        return False
    if _is_female_option(opt):
        return False
    # Do not treat transgender variants as the profile's Male answer.
    if "trans" in on:
        return False
    if on in {"male", "man", "men", "homme", "m", "he him", "he/him"}:
        return True
    # Whole-word "male" / "man" only — never a substring of female/woman.
    if re.search(r"(?<![a-z])male(?![a-z])", on):
        return True
    if re.search(r"(?<![a-z])man(?![a-z])", on) and "woman" not in on:
        return True
    if "homme" in on and "femme" not in on:
        return True
    return False


def _is_yes_no_options(options: list[str] | None) -> bool:
    """True when the control is a binary Yes/No (or True/False) choice."""
    if not options:
        return False
    skip = {
        "select an option", "select", "please select", "choose", "choose one",
        "", "-", "--", "—",
    }
    real: list[str] = []
    for opt in options:
        on = _normalize(opt)
        if not on or on in {_normalize(s) for s in skip}:
            continue
        real.append(on)
    if len(real) < 2:
        return False
    allowed = {
        "yes", "no", "oui", "non", "true", "false", "y", "n",
        "i agree", "i do not agree", "agree", "disagree",
    }
    # All real options must be yes/no-ish (allow short labels)
    for on in real:
        if on in allowed:
            continue
        if on.startswith("yes") or on.startswith("no"):
            continue
        if on in {"true", "false"}:
            continue
        return False
    has_yes = any(on in {"yes", "oui", "true", "y"} or on.startswith("yes") for on in real)
    has_no = any(on in {"no", "non", "false", "n"} or on.startswith("no") for on in real)
    return has_yes and has_no


def _map_to_options(answer: str, options: list[str] | None) -> str:
    if not options:
        return answer
    ans = _normalize(answer)
    if not ans:
        return ""
    bool_yes = {"yes", "oui", "true", "1"}
    bool_no = {"no", "non", "false", "0", "never"}
    if ans in bool_yes | bool_no:
        want = bool_yes if ans in bool_yes else bool_no
        for opt in options:
            on = _normalize(opt)
            if on in want:
                return opt
        # Broaden: for "No", prefer options that *start* with No / state lack of trait.
        # Do NOT match bare substring "no" inside "not" / "Not Hispanic" / "one".
        if ans in bool_no:
            for opt in options:
                on = _normalize(opt)
                if re.match(r"^(no|non|false)\b", on) and "yes" not in on.split()[:1]:
                    return opt
            for opt in options:
                on = _normalize(opt)
                if re.search(r"\b(do not have|don't have|do not|i am not|i'm not|not a protected)\b", on):
                    if not re.match(r"^yes\b", on):
                        return opt
            for opt in options:
                on = _normalize(opt)
                if re.search(r"\b(prefer not|decline|do not want to answer|don't want to answer)\b", on):
                    return opt
        # Broaden: for "Yes", accept options that start with "yes" (not "yes" buried in prose)
        if ans in bool_yes:
            for opt in options:
                on = _normalize(opt)
                if re.match(r"^(yes|oui|true)\b", on) or on.startswith("i do "):
                    # Never treat "Yes, I have a disability" as a generic Yes unless caller wants it
                    return opt
    # Gender / sex — never let "male" map onto "Female" via substring/prefix.
    if ans in {"male", "man", "m", "homme", "he him"} or ans.startswith("male"):
        for opt in options:
            if _is_male_option(opt):
                return opt
        return ""
    if ans in {"female", "woman", "f", "femme", "she her"} or ans.startswith("female"):
        for opt in options:
            if _is_female_option(opt):
                return opt
        return ""
    # Exact normalized matches must win before any fuzzy token matching.  In
    # particular, ``3 - 6 months`` and ``< 3 months`` share ``months``;
    # doing fuzzy matching inside the same loop used to select the first
    # option before reaching the exact one.
    for opt in options:
        if ans == _normalize(opt):
            return opt
    for opt in options:
        on = _normalize(opt)
        if not on:
            continue
        # Never let the binary answer "male" map to the distinct identity
        # option "Transgender male".
        if ans in {"male", "female"} and "transgender" in on:
            continue
        # SUBSTRING only when both sides share the answer as a WHOLE WORD.
        # Use word-boundary check: 'male' matches 'Male' but NOT 'Female'.
        if len(ans) >= 3:  # only full words, not single chars like 'a'
            if re.search(r'(?<![a-zA-Z])' + re.escape(ans) + r'(?![a-zA-Z])', opt, re.I):
                # Block male↔female cross-hits even if regex is wrong on edge cases.
                if ans == "male" and _is_female_option(opt):
                    continue
                if ans == "female" and _is_male_option(opt) and not _is_female_option(opt):
                    continue
                return opt
            # Short answer (3-5 chars): also try as a WORD-STARTING prefix match
            # so 'pre' → 'Prefer', 'sta' → 'Start', etc.
            # Never prefix-match gender tokens (male ⊂ female).
            if 3 <= len(ans) <= 5 and ans not in {"male", "man", "men"}:
                if re.match(r'(?<![a-zA-Z])' + re.escape(ans), opt, re.I):
                    return opt
            # Multi-token answers (e.g. 'he him' from 'he/him'): check that ALL tokens
            # appear as whole words within the option text.
            if ' ' in ans:
                tokens = [t for t in ans.split() if len(t) >= 2]
                if tokens and all(re.search(r'(?<![a-zA-Z])' + re.escape(t) + r'(?![a-zA-Z])', opt, re.I) for t in tokens):
                    return opt
    # Token overlap (Jaccard) only as last resort — requires meaningful overlap.
    at = set(ans.split())
    best, best_score = "", 0.0
    for opt in options:
        ot = set(_normalize(opt).split())
        if not ot:
            continue
        # Require at least one shared token to avoid noise matches.
        intersection = at & ot
        if not intersection:
            continue
        union = at | ot
        score = len(intersection) / max(len(union), 1)
        # Raise threshold: require 0.6+ to accept (was 0.45).
        if score > 0.6 and len(intersection) >= 2:
            best, best_score = opt, score
    return best if best_score >= 0.6 else ""


def _safe_rules(question: str, profile: dict[str, Any], options: list[str] | None) -> ResolvedAnswer | None:
    """Deterministic high-value rules ported from Indeed IT question handling."""
    q = (question or "").lower()
    if not q.strip():
        return None

    def yes() -> ResolvedAnswer:
        mapped = _map_to_options("Yes", options) or "Yes"
        return ResolvedAnswer(mapped, "safe_rule_yes")

    def no() -> ResolvedAnswer:
        mapped = _map_to_options("No", options) or "No"
        return ResolvedAnswer(mapped, "safe_rule_no")

    def pick(*prefs: str, source: str = "safe_rule_pick") -> ResolvedAnswer | None:
        if options:
            for pref in prefs:
                mapped = _map_to_options(pref, options)
                if mapped:
                    return ResolvedAnswer(mapped, source)
            # decline-ish
            for opt in options:
                if "decline" in opt.lower() or "prefer not" in opt.lower():
                    return ResolvedAnswer(opt, source + "_decline")
        if prefs:
            return ResolvedAnswer(prefs[0], source)
        return None

    # Deterministic ATS knowledge check seen in the Chandos form. The answer
    # is Toronto's Raptors initial plus Canada's common coffee/donut chain;
    # choose the exact option because the portal exposes compact labels such
    # as ``Rtimhortons``.
    if (
        options
        and "uppercase first letter of toronto" in q
        and re.search(r"coffee\s*/\s*donut|coffee.*donut|donut.*coffee", q)
    ):
        for opt in options:
            if "timhortons" in re.sub(r"[^a-z]", "", str(opt).lower()):
                return ResolvedAnswer(opt, "safe_rule_anti_bot_knowledge_check")

    # Resume selection — prefer IT resume (ls_resume_it), never prefer generic upload names
    if "resume" in q or "cv" in q:
        if options:
            def _resume_rank(opt: str) -> tuple:
                o = (opt or "").lower()
                if "deselect" in o:
                    return (99, o)
                # Higher priority = lower rank number
                if "ls_resume_it" in o or "resume_it" in o or re.search(r"\bit\b.*resume|resume.*\bit\b", o):
                    return (0, o)
                if "ls_resume" in o and "general" not in o:
                    return (1, o)
                # Generic uploaded names (e.g. resume (1).pdf, Jane_Doe_Resume.pdf)
                if "jane" in o or "resume" in o or re.search(r"resume\s*\(\d+\)", o):
                    return (50, o)
                if "general" in o:
                    return (40, o)
                if "select" in o:
                    return (10, o)
                if "pdf" in o or "doc" in o:
                    return (15, o)
                return (20, o)

            ranked = sorted(options, key=_resume_rank)
            best = ranked[0] if ranked else None
            if best and "deselect" not in best.lower():
                src = "safe_rule_select_resume"
                bl = best.lower()
                if "ls_resume_it" in bl or "resume_it" in bl:
                    src = "safe_rule_select_resume_it"
                elif "resume" in bl or "jane" in bl:
                    src = "safe_rule_select_resume_generic_fallback"
                return ResolvedAnswer(best, src)
            for opt in options:
                if "deselect" not in opt.lower():
                    return ResolvedAnswer(opt, "safe_rule_resume_option")
            return ResolvedAnswer(options[0], "safe_rule_first_resume_option")

    # Contact / identity
    if re.search(r"\bfirst name\b", q) and "preferred" not in q:
        return ResolvedAnswer(profile["first_name"], "profile_first_name")
    if re.search(r"\blast name\b|\bsurname\b", q):
        return ResolvedAnswer(profile["last_name"], "profile_last_name")
    if re.search(r"\bpreferred name\b|\bpreferred first name\b", q):
        return ResolvedAnswer(profile["first_name"], "profile_preferred_name")
    if re.search(r"\bfull name\b|\byour name\b", q) and "company" not in q:
        return ResolvedAnswer(profile["full_name"], "profile_full_name")
    # Contact preference multi-select ("by email." / "by phone.") — not identity fields.
    if re.match(r"^\s*by\s+email\.?\s*$", q) or re.match(r"^\s*by\s+phone\.?\s*$", q):
        return yes()
    if any(
        k in q
        for k in (
            "how you would like to be contacted",
            "like to be contacted",
            "receive further communications",
            "contacted to receive",
        )
    ):
        if options:
            # Prefer checking both email + phone via multi-select labels.
            for opt in options:
                ol = (opt or "").lower()
                if "email" in ol or "phone" in ol:
                    return ResolvedAnswer(opt, "contact_pref_multi")
        return yes()
    # Identity email/phone only (not "by email" preference checkboxes).
    if (
        re.search(r"\b(email address|e-?mail)\b", q)
        or re.fullmatch(r"\s*email\s*", q)
    ) and "by email" not in q and "contacted" not in q:
        return ResolvedAnswer(profile["email"], "profile_email")
    if (
        re.search(r"\b(phone|mobile|telephone)\b", q)
        or re.fullmatch(r"\s*phone\s*", q)
    ) and "by phone" not in q and "contacted" not in q:
        return ResolvedAnswer(profile["phone"], "profile_phone")
    if "linkedin" in q:
        return ResolvedAnswer(profile["linkedin"], "profile_linkedin")
    if any(k in q for k in ("portfolio", "website", "github")) and "how did you" not in q:
        return ResolvedAnswer(profile["website"], "profile_website")

    if (
        re.search(r"\bcurrent\b.*\bcompany\b|\bcompany\b.*\bcurrent\b", q)
        or re.fullmatch(r"\s*current company\s*", q)
    ):
        return ResolvedAnswer(profile.get("current_company", "") or "Currently seeking opportunities", "profile_current_company")
    # MUST run before location rules: "right to work in the job location"
    # contains "location" but is a work auth question, not a geography question.
    #
    # Compound forms (SmartRecruiters / Greenhouse / LinkedIn):
    #   "Are you legally authorized to work in British Columbia without the
    #    need for visa sponsorship?"
    # This is YES for a Canadian citizen — NOT a "need sponsorship?" → No.
    # Detect "authorized/eligible … without … sponsorship" before bare sponsorship.
    _auth_without_sponsor = bool(
        re.search(
            r"(authorized|eligible|entitled|right to work|work authorization).{0,80}"
            r"without.{0,40}(visa\s+)?sponsorship",
            q,
        )
        or re.search(
            r"without.{0,40}(the need for\s+)?(visa\s+)?sponsorship.{0,40}"
            r"(authorized|eligible|entitled|right to work)",
            q,
        )
        or re.search(
            r"(can|able to)\s+work.{0,40}without.{0,40}(visa\s+)?sponsorship",
            q,
        )
    )
    if _auth_without_sponsor:
        # US still No (persona cannot work in US without sponsorship).
        if ("united states" in q or "u.s." in q or " usa " in f" {q} " or re.search(r"\bus\b", q)) and not any(
            k in q
            for k in (
                "canada",
                "canadian",
                "british columbia",
                "ontario",
                "alberta",
                "quebec",
                "toronto",
                "vancouver",
                "bc",
            )
        ):
            return no()
        return yes()

    # Pure "do you need/require sponsorship?" questions → No for Canada path.
    # Do not match "without the need for visa sponsorship" (handled above).
    if any(
        k in q
        for k in (
            "require sponsorship",
            "need sponsorship",
            "require visa sponsorship",
            "need visa sponsorship",
            "will you require sponsorship",
            "do you require sponsorship",
            "do you need sponsorship",
            "require visa",
            "need visa sponsorship",
        )
    ) or (
        "sponsorship" in q
        and not re.search(r"without.{0,40}sponsorship", q)
        and not _auth_without_sponsor
    ):
        return no()
    # The IT persona is not authorized to work in the United States.  Keep
    # this distinct from Canadian authorization; never let a generic
    # "legally authorized" rule turn a US question into Yes.
    if (
        ("united states" in q or "u.s." in q or " usa " in f" {q} " or re.search(r"\bus\b", q))
        and any(k in q for k in ("authorized", "eligible", "entitled", "right to work", "work permit", "work authorization"))
    ):
        return no()
    if any(
        k in q
        for k in (
            "legally entitled",
            "legally authorized",
            "authorized to work",
            "eligible to work",
            "work authorization",
            "work permit",
            "legal right to work",
            "work in canada",
            "work in british columbia",
            "work in bc",
            "citizen or permanent",
            "permanent resident",
            "right to work",
            # LinkedIn Easy Apply (Bosch etc.)
            "citizenship",
            "employment eligibility",
            "work eligibility",
            "employment status",
        )
    ):
        return pick(
            # Exact Bosch / LinkedIn-style option labels first
            "I am a Canadian Citizen",
            "I am a Canadian Permanent Resident",
            "Canadian Citizen",
            "Citizen or permanent resident where this role is listed",
            "Citizen of a country with an unlimited right to work where this role is listed",
            "Citizen",
            "Canadian Citizen / Permanent Resident",
            "Canadian Citizen/Permanent Resident",
            "Canadian citizen",
            "Permanent Resident",
            "PR",
            "Work Permit",
            "Yes",
            "Authorized",
            source="work_auth_yes",
        )

    # Former/current employee screeners (e.g. Capital One, Asana, Deloitte, government entity, Block).
    # Default No unless the question is about a company the candidate has worked for.
    if (
        "worked at" in q
        or "currently work" in q
        or "previously worked" in q
        or "former employee" in q
        or "current employee" in q
        or "ever worked for" in q
        or "ever worked" in q
        or "ever been employed" in q
        or "ever provided any contract work" in q
        or "employed" in q
        or "otherwise engaged" in q
        or "have you worked" in q
    ):
        return no()

    # Legal agreement single-option dropdowns ("I agree to these expectations").
    if options and len(options) == 1:
        only = options[0].lower()
        if any(k in only for k in ("i agree", "i accept", "i understand", "i consent")):
            return ResolvedAnswer(options[0], "safe_rule_agree_single")

    # Privacy notices, terms acknowledgment, data processing checkboxes/agreements.
    if any(k in q for k in ("privacy policy", "privacy notice", "data processing", "acknowledge", "consent", "terms and conditions", "terms of service")):
        if options:
            for opt in options:
                if any(k in opt.lower() for k in ("acknowledge", "agree", "accept", "consent", "yes")):
                    return ResolvedAnswer(opt, "safe_rule_privacy_acknowledge")
        return yes()

    # Open-ended essay prompts ("Why work for us", "Why Coalition", "What makes you excited").
    if any(k in q for k in ("why work for us", "why coalition", "why are you excited", "why this company", "why this role", "what makes you excited", "excited to join")) and not options:
        return ResolvedAnswer(
            "I am excited to apply my background in IT systems administration, network security, and technical support to contribute effectively to your engineering and operations teams.",
            "safe_rule_essay_prompt",
        )

    # Office location dropdowns (prefer Vancouver option when selecting office location).
    if options and any("vancouver" in opt.lower() for opt in options) and not any(k in q for k in ("currently located", "currently in vancouver", "reside in vancouver", "located in vancouver")):
        for opt in options:
            if "vancouver" in opt.lower() and "no" not in opt.lower():
                return ResolvedAnswer(opt, "safe_rule_location_vancouver")

    # Conditional free-text follow-ups when the parent was No / N/A.
    # Do NOT force N/A on multi-select "If yes, select …" (e.g. minority status)
    # — those need real options / AI, not a canned N/A.
    q0 = (question or "").lower()
    if any(k in q0 for k in ("if not applicable", "type n/a", "enter n/a", "write n/a")):
        return ResolvedAnswer("N/A", "safe_rule_parent_no_followup")
    if any(k in q0 for k in ("if you answered yes", "if yes", "if you answered")):
        is_select_followup = "select" in q0 or (options is not None and len(options) >= 2)
        if not is_select_followup:
            return ResolvedAnswer("N/A", "safe_rule_parent_no_followup")

    # Proximity / employment type are policy questions, not city fields.
    if "within 50km" in q or "within 50 km" in q:
        return yes()
    if "currently located in vancouver" in q or "currently in vancouver" in q:
        # Surrey is outside the question's Vancouver-only condition.  This
        # must remain deterministic on retries; never let a later AI pass
        # flip the answer after the DOM reveals its option labels.
        if str(profile.get("city") or "").strip().lower() != "vancouver":
            return pick("No, I am not located in Vancouver", "No", source="profile_not_in_vancouver")
        return yes()
    # Hybrid/onsite Metro Van roles — candidate is in Surrey (Metro Van) and
    # seeks hybrid work near Vancouver.
    if (
        ("vancouver" in q or "metro van" in q)
        and any(k in q for k in ("hybrid", "located in", "seeking", "based in", "office"))
        and "united states" not in q
    ):
        return yes()
    if "permanent full-time" in q or "permanent full time" in q or "incorporated contractor" in q:
        return pick("Permanent Full-time", "Permanent full-time", source="profile_employment_type")

    # Multi-select city / office preference labels (e.g. Greenhouse
    # "Vancouver, BC", "Calgary, AB", "North Vancouver, BC").
    # Require a comma before the 2-letter region — the old optional-comma
    # regex matched "Highest level of education" as city="…educati" st="on".
    raw_q = (question or "").strip()
    city_pref = re.match(
        r"^\s*([A-Za-z][A-Za-z .'\-]{1,40}),\s*([A-Za-z]{2})\s*$",
        raw_q,
    )
    if city_pref and "?" not in raw_q and len(raw_q.split()) <= 4:
        city_name = city_pref.group(1).strip().lower().rstrip(",")
        st = city_pref.group(2).strip().lower()
        known_st = {
            "ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt",
            "ca", "us",
        }
        metro_van = {
            "vancouver", "surrey", "burnaby", "richmond", "coquitlam",
            "new westminster", "north vancouver", "west vancouver",
            "langley", "delta", "port coquitlam", "port moody",
            "maple ridge", "white rock", "pitt meadows",
        }
        profile_city = str(profile.get("city") or "").strip().lower()
        if st in known_st and len(city_name) <= 40:
            if city_name in metro_van or city_name == profile_city:
                return yes()
            return no()
    # Some ATS checkbox labels omit the comma (for example ``Montreal QC``).
    # Only recognize a fixed set of actual city names here: accepting every
    # trailing two-letter token would misread prompts such as "Highest level
    # of education" as a city label ending in Ontario (``ON``).
    compact_city_pref = re.match(r"^\s*([A-Za-z][A-Za-z .'-]{1,40})\s+([A-Za-z]{2})\s*$", raw_q)
    if compact_city_pref and "?" not in raw_q and len(raw_q.split()) <= 4:
        city_name = compact_city_pref.group(1).strip().lower()
        st = compact_city_pref.group(2).strip().lower()
        known_st = {
            "ab", "bc", "mb", "nb", "nl", "ns", "nt", "nu", "on", "pe", "qc", "sk", "yt",
            "ca", "us",
        }
        metro_van = {
            "vancouver", "surrey", "burnaby", "richmond", "coquitlam",
            "new westminster", "north vancouver", "west vancouver",
            "langley", "delta", "port coquitlam", "port moody",
            "maple ridge", "white rock", "pitt meadows",
        }
        profile_city = str(profile.get("city") or "").strip().lower()
        known_cities = metro_van | {
            "calgary", "edmonton", "montreal", "quebec city", "toronto",
            "ottawa", "winnipeg", "halifax", "victoria", "kelowna",
            "regina", "saskatoon", "st johns", "fredericton", "charlottetown",
            "whitehorse", "yellowknife", "iqaluit",
        }
        if st in known_st and city_name in known_cities:
            if city_name in metro_van or city_name == profile_city:
                return yes()
            return no()
    # Group header for multi-location pickers — select-all / interested-in-group.
    if any(
        k in q
        for k in (
            "all locations",
            "tous les emplacements",
            "all emplacements",
            "any location",
        )
    ):
        return yes()

    # Location
    if "country" in q and "phone" not in q:
        return pick("Canada", "CA", profile["country"], source="profile_country")
    if any(k in q for k in ("province", "state")) and "united states" not in q:
        return pick("British Columbia", "BC", profile["state"], source="profile_province")
    # Word-boundary "city" so "Quebec City, QC" is not treated as a home-city field.
    if (
        re.search(r"\bcity\b", q)
        or "where do you live" in q
        or "current location" in q
        or re.search(r"\breside\b", q)
    ) and not re.search(r"\bcity,\s*[a-z]{2}\b", q):
        return pick(profile["city"], profile["location"], "Surrey", "Vancouver", "Canada", source="profile_city")
    if "postal" in q or "zip" in q:
        return ResolvedAnswer(profile["zipcode"], "profile_zip")
    if ("street" in q or "address" in q) and "addressed" not in q:
        return ResolvedAnswer(profile["street"], "profile_street")
    if "location" in q and "convenient" not in q and "all location" not in q:
        return pick(profile["location"], f"{profile['city']}, {profile['state']}", source="profile_location")

    # On-site / commute (Metro Van candidate)
    if "employment type" in q or "type of employment" in q:
        return pick("Permanent Full-time", "Permanent full-time", source="profile_employment_type")
    if any(
        k in q
        for k in (
            "on-site",
            "onsite",
            "on site",
            "able to work on-site",
            "commute",
            "in-person",
            "in person",
            "metro vancouver",
            "vancouver office",
        )
    ):
        return yes()

    # Salary
    if any(k in q for k in ("salary", "compensation", "pay expectation", "expected pay", "base salary")):
        return pick(
            profile["desired_salary"],
            f"${profile['desired_salary']}",
            "70000",
            "70,000",
            "CAD 70000",
            source="profile_salary",
        )

    # Experience years (overall + skill-specific LinkedIn/ATS prompts)
    if ("years" in q and "experience" in q) or "how many years" in q or re.search(r"\byoe\b", q):
        # Prefer profile YOE for overall; skill prompts still get a non-zero default.
        yoe = str(profile.get("years_of_experience") or "3").strip() or "3"
        skill_map = (
            (("manual testing", "test cases", "regression", "qa", "quality assurance"), "2"),
            (("selenium", "cypress", "playwright", "test automation"), "1"),
            (("python", "java", "javascript", "react", "node"), "2"),
            (("linux", "windows server", "active directory"), "2"),
            (("network", "cisco", "vlan", "firewall"), "2"),
            (("aws", "azure", "cloud", "docker", "kubernetes", "devops"), "1"),
            (("sql", "mysql", "mongodb"), "2"),
            (("help desk", "service desk", "desktop support"), "2"),
        )
        for keys, years in skill_map:
            if any(k in q for k in keys):
                return pick(years, yoe, "2", "3", source="profile_skill_yoe")
        return pick(yoe, "3", "2-3", "3-5", source="profile_yoe")

    # How heard
    if options and any("career page" in opt.lower() for opt in options):
        for opt in options:
            if "career page" in opt.lower() and "employee" not in opt.lower():
                return ResolvedAnswer(opt, "how_heard_company_career_page")
    if "hear about" in q or "how did you find" in q or "referral source" in q:
        # Some Lever forms combine the source question with a free-text
        # referral field.  The candidate was not referred by an employee;
        # choose the employer's career page when that is the available
        # truthful source option instead of putting a LinkedIn URL in a
        # select meant for a source label.
        if options:
            for opt in options:
                if "career page" in opt.lower() and "employee" not in opt.lower():
                    return ResolvedAnswer(opt, "how_heard_company_career_page")
        return pick(
            profile.get("how_heard") or "Job board",
            "Job Board",
            "Other",
            "LinkedIn",
            "Indeed",
            "Company website",
            "Internet search",
            source="how_heard",
        )

    # Kabam/game-specific questions.  These are factual profile defaults,
    # not open-ended questions: avoid spending the AI budget or guessing.
    if "username" in q and "marvel" in q:
        return ResolvedAnswer("N/A", "profile_game_username_none")
    if "marvel contest of champions" in q:
        if options:
            for opt in options:
                if "never played" in opt.lower():
                    return ResolvedAnswer(opt, "profile_game_not_played")
        return no()
    if "live games" in q or ("games" in q and "previously played" in q):
        return ResolvedAnswer("N/A", "profile_games_none")
    if "first co-op term" in q or "first coop term" in q:
        if options:
            for opt in options:
                if "first co-op term" in opt.lower() or "first coop term" in opt.lower():
                    return ResolvedAnswer(opt, "profile_first_coop_term")
        return ResolvedAnswer("This will be my first co-op term", "profile_first_coop_term")
    # Grade/GPA threshold (e.g. mThree 2.75+) — profile meets the grade bar.
    # French forms often say "obtenu un diplôme avec une note de 2,75+" which
    # is treated as the grade threshold, not "have you finished the degree".
    if any(k in q for k in ("2.75", "2,75", "2 75", "gpa")) and any(
        k in q for k in ("grade", "note", "graduat", "diplôme", "diplome")
    ):
        return yes()
    # Pure "have you graduated?" without a grade bar → still enrolled = No.
    if re.search(r"\bhave you graduated\b", q) and "grade" not in q and "gpa" not in q:
        return no()
    if "authorize" in q and ("sms" in q or "text message" in q or "mthree" in q):
        return yes()
    if "message and data rates" in q:
        return yes()
    # Training / start availability free-text (mThree and similar).
    if any(
        k in q
        for k in (
            "first available date to start training",
            "available date to start training",
            "première date disponible pour commencer la formation",
            "premiere date disponible pour commencer la formation",
            "date disponible pour commencer",
            "start training",
        )
    ):
        return ResolvedAnswer("Immediately", "profile_start_training_immediate")
    if "months" in q and ("graduate" in q or "graduat" in q):
        # Profile: expected graduation Dec 2026; current run date is Jul
        # 2026, so the truthful bucket is 3–6 months.
        if options:
            for opt in options:
                if "3 - 6 months" in opt.lower() or "3-6 months" in opt.lower():
                    return ResolvedAnswer(opt, "profile_months_to_graduation")
        return ResolvedAnswer("3 - 6 months", "profile_months_to_graduation")
    if "year of schooling" in q or "year of study" in q or "schooling have you completed" in q:
        # Expected Dec 2026 and the resume identifies the candidate as a
        # fourth-year B.Tech student.
        if options:
            for opt in options:
                if opt.strip().lower() in {"4th year", "fourth year", "year 4", "4"}:
                    return ResolvedAnswer(opt, "profile_academic_year")
        return ResolvedAnswer("4th year", "profile_academic_year")

    # Referral by employee — name fields need blank/N/A, not Yes/No or applicant name
    if any(
        k in q
        for k in (
            "referred by",
            "referral",
            "recommended by",
            "current employee",
            "if you were referred",
            "employee who referred",
            "name of the employee who referred",
            "referrer",
            "who referred you",
        )
    ):
        # Free-text "share the name" → no referrer
        if any(k in q for k in ("name", "share", "who referred", "referrer")):
            if options:
                for opt in options:
                    ot = opt.lower()
                    if any(k in ot for k in ("n/a", "none", "not applicable", "no one", "no")):
                        return ResolvedAnswer(opt, "no_referral_option")
            return ResolvedAnswer("N/A", "no_referral_name")
        return no()

    # Former employer at this company / any division (Bosch, etc.) → No
    if any(
        k in q
        for k in (
            "employed by any division",
            "employed by this company",
            "worked for this company",
            "worked for us before",
            "previously employed",
            "former employee",
            "ever been employed by",
            "ever worked for",
        )
    ):
        return no()

    # "Have you previously worked for [Company] / a subsidiary / competitor" → always No
    if any(k in q for k in ("worked for a", "worked for ", "worked for:")) and (
        "subsidiary" in q or "competitor" in q
    ):
        return no()

    # Relatives of employees — always No
    if "relative" in q and "works" in q:
        return no()

    # EEO / demographics — decline when possible
    if "age range" in q or "age bracket" in q:
        if options:
            for opt in options:
                if any(k in opt.lower() for k in ("prefer not", "decline", "don't wish")):
                    return ResolvedAnswer(opt, "eeo_age_decline")
        return None
    # Sexual orientation — prefer decline / prefer not to say (overnight: 8 unresolved).
    if "sexual orientation" in q or ("orientation" in q and "sexual" in q):
        return pick(
            "Prefer not to say",
            "Decline to self-identify",
            "Decline",
            "I don't wish to answer",
            "Prefer not to answer",
            "Je préfère ne pas répondre",
            source="eeo_orientation_decline",
        )
    # AI-tools usage self-description (StackAdapt/GH style multiple choice).
    if "use ai tools" in q or ("ai tools" in q and ("work" in q or "currently" in q or "statement" in q)):
        return pick(
            "I use AI tools regularly in my work",
            "I regularly use AI tools",
            "I use AI tools daily",
            "I use AI tools frequently",
            "Regularly",
            "Frequently",
            "Often",
            "Yes",
            source="ai_tools_regular",
        )
    # Gender / sex (EN + FR). Profile is Male — never pick Female/Woman/Femme.
    # Do not match race / minority / aboriginal questions that mention "sex" in
    # long EEO copy or that share option lists with demographics.
    if (
        re.search(r"\b(gender|sex|sexe)\b", q)
        and "orientation" not in q
        and not any(
            k in q
            for k in (
                "minority", "minorities", "race", "ethnic", "aboriginal",
                "indigenous", "disability", "veteran", "visible",
            )
        )
    ):
        if options:
            for opt in options:
                if _is_male_option(opt):
                    return ResolvedAnswer(opt, "eeo_gender_male")
            for pref in (
                profile.get("gender") or "Male",
                "Male",
                "Man",
                "Homme",
                "Decline to self-identify",
                "Prefer not to say",
                "Decline",
            ):
                mapped = _map_to_options(pref, options)
                if mapped and not _is_female_option(mapped):
                    return ResolvedAnswer(mapped, "eeo_gender")
            return None
        return ResolvedAnswer(str(profile.get("gender") or "Male"), "eeo_gender")
    if "veteran" in q:
        return pick("I am not a veteran", "No", "Decline to self-identify", "Decline", source="eeo_veteran")
    if any(k in q for k in ("race", "ethnicity", "hispanic", "latino", "visible minority")):
        if "if yes" in q or "select options" in q:
            # This is a conditional demographic follow-up.  The profile's
            # policy declines the parent question and does not infer an
            # ethnicity from a name or language.
            return None
        return pick("Decline to self-identify", "Decline", "Prefer not to say", source="eeo_race")
    if "disability" in q:
        return pick("No", "I don't wish to answer", "Decline", "Prefer not to say", source="eeo_disability")

    # Consent / certify / privacy notice (SmartRecruiters, Greenhouse, LinkedIn)
    if any(
        k in q
        for k in (
            "i agree",
            "certify",
            "attest",
            "true and complete",
            "acknowledge",
            "consent to",
            "i consent",
            "privacy notice",
            "privacy policy",
            "read and understand",
            "select checkbox to proceed",
        )
    ):
        return pick(
            "I consent",
            "Yes",
            "I agree",
            "I certify",
            "Agree",
            "Consent",
            source="consent_yes",
        )

    # Cover / about / culture free-text
    if any(
        k in q
        for k in (
            "cover letter",
            "why do you want",
            "tell us about yourself",
            "about yourself",
            "what do you value",
            "work culture",
            "work environment",
            "what motivates you",
            "motivates you to do",
            "why this role",
            "why our company",
            "letter of interest",
        )
    ):
        text = (
            profile.get("cover_letter")
            or profile.get("why_company_answer")
            or profile.get("profile_summary")
            or ""
        )
        if text:
            return ResolvedAnswer(text.strip()[:4000], "profile_cover_or_summary")

    # Education / school — pick first matching or default Canadian university
    # Enrollment/co-op eligibility is a yes/no policy question, not a school
    # picker.  Check it before the broad school/university keyword rule.
    if any(k in q for k in ("full 8-month", "8-month co-op", "8 month co-op", "aug 2026 to apr 2027")):
        return ResolvedAnswer(_map_to_options("Yes", options) or "Yes", "profile_coop_term_available")
    if any(k in q for k in ("currently enrolled", "enrolled in", "co-op program", "coop program", "co op program")):
        return ResolvedAnswer(_map_to_options("Yes", options) or "Yes", "profile_coop_eligible")
    # School name free-text (type the real school).  Dropdowns are handled below.
    school_name = str(profile.get("school") or "Kwantlen Polytechnic University")
    is_school_name_entry = any(
        k in q
        for k in (
            "selected \"other\"",
            "selected “other”",
            "selected other",
            "sélectionné \"autre\"",
            "selectionne \"autre\"",
            "sélectionné « autre »",
            "si vous avez sélectionné",
            "if you selected other",
            "if other",
            "please enter the name of your school",
            "please specify your school",
            "please specify",
            "name of your school",
            "school name",
            "enter your school",
            "type your school",
            "write the name",
            "nom de l'école",
            "nom de l ecole",
            "nom de votre école",
            "précisez le nom",
            "precisez le nom",
            "saisir le nom",
        )
    ) or (
        ("autre" in q and ("école" in q or "ecole" in q or "school" in q))
        or ("other" in q and "school" in q and any(k in q for k in ("name", "enter", "specify", "type")))
    )
    if is_school_name_entry and not options:
        return ResolvedAnswer(school_name, "profile_school_other_text")
    if is_school_name_entry and options:
        # Rare: free-text disguised as select — still type profile school if no Other.
        for opt in options:
            ot = (opt or "").strip().lower()
            if ot in {"other", "autre"} or "not listed" in ot:
                return ResolvedAnswer(opt, "smart_school_other")
        return ResolvedAnswer(school_name, "profile_school_other_text")

    # School / university DROPDOWNS → always Other (never pick a random university).
    if ("school" in q or "university" in q or "college" in q or "institution" in q or "école" in q or "ecole" in q) and "high school" not in q:
        school_name = str(profile.get("school") or "Kwantlen Polytechnic University")
        if options:
            year_like = sum(
                1 for o in options if re.fullmatch(r"(19|20)\d{2}", (o or "").strip())
            )
            if year_like >= max(2, len(options) // 2):
                return None
            for opt in options:
                ot = (opt or "").strip().lower()
                if ot in {"other", "autre", "n/a", "na", "none"}:
                    return ResolvedAnswer(opt, "smart_school_other")
                if any(
                    k in ot
                    for k in (
                        "not listed",
                        "not in list",
                        "school not listed",
                        "my school is not",
                        "prefer not",
                        "doesn't appear",
                        "does not appear",
                    )
                ):
                    return ResolvedAnswer(opt, "smart_school_other")
            return ResolvedAnswer("Other", "smart_school_other_prefer")
        return ResolvedAnswer("Other", "smart_school_other_prefer")

    # Degree / education level — map common formats
    if "degree" in q or "education level" in q or "highest level" in q:
        # LinkedIn often asks "Do you have a Bachelor's in X?" with Yes/No options.
        # Never return "Bachelor's Degree" text when the control is binary.
        if _is_yes_no_options(options):
            # Specialty degree we do not hold → No
            if any(
                k in q
                for k in (
                    "geology", "geoscience", "geophysic", "petroleum", "mining",
                    "nursing", "medicine", "law", "accounting", "finance",
                    "psychology", "biology", "chemistry", "physics",
                )
            ):
                return no()
            # "Have you completed … Bachelor's Degree?" / education level Yes/No
            if re.search(
                r"\b(have you completed|completed the following|do you have|hold a|"
                r"possess a|earned a|obtained a)\b",
                q,
            ):
                # BTech IT path → Yes for generic bachelor's completion questions
                if any(k in q for k in ("bachelor", "bachelors", "undergraduate", "degree")):
                    if any(k in q for k in ("computer", "information", "it ", "software", "engineering")):
                        return yes()
                    # Generic bachelor's Yes/No — still enrolled but forms often want Yes
                    # for "completed level of education: Bachelor's"
                    if "completed the following level" in q or "level of education" in q:
                        return yes()
                    if re.search(r"\bdo you have\b.*\bbachelor", q) and "closely related" not in q:
                        return yes()
                    # "degree in <unrelated specialty>" already handled above
                    return yes()
            return no()
        if options:
            for pref in (
                "Bachelor's Degree", "Bachelor of", "B.E/ B.Tech",
                "BCA/ BCCA/ B.Sc", "B.Sc",
                "Bachelor's Degree or Apprenticeship level 5-6",
            ):
                mapped = _map_to_options(pref, options)
                if mapped:
                    return ResolvedAnswer(mapped, "smart_degree_bachelor")
            for opt in options:
                ot = opt.lower()
                if any(k in ot for k in ("bach", "b.e", "b.sc", "b.tech", "bca", "bachelor")):
                    return ResolvedAnswer(opt, "smart_degree_mapped")
            if "other" in {o.lower() for o in options}:
                return ResolvedAnswer("Other", "smart_degree_other")
            # Options exist but nothing mapped — do not invent free text that
            # cannot be selected (causes form_stalled_validation).
            return None
        return ResolvedAnswer("Bachelor's Degree", "smart_degree_default")

    # Discipline / major
    if "discipline" in q or "major" in q or "field of study" in q:
        if options:
            for pref in (
                "Computer Science", "Information Technology", "STEM",
                "Computer Science/ Information Technology/ Computer Application",
                "STEM : (Science, Technology, Engineering & Mathematics)",
            ):
                mapped = _map_to_options(pref, options)
                if mapped:
                    return ResolvedAnswer(mapped, "smart_discipline_mapped")
            # Try substring matches for IT/CS
            for opt in options:
                ot = opt.lower()
                if any(k in ot for k in ("computer", "information", "stem", "engineering", "science")):
                    return ResolvedAnswer(opt, "smart_discipline_fallback")
        return ResolvedAnswer("Computer Science", "smart_discipline_default")

    # Start/end dates — KPU start Sep 2022; graduation from profile (Dec 2026).
    if any(k in q for k in ("start date", "start month", "start year", "start_date", "startdate")):
        start_year = str(profile.get("education_start_year") or "2022")
        start_month = str(profile.get("education_start_month") or "September")
        if "year" in q and "month" not in q:
            if options:
                for opt in options:
                    ot = opt.strip()
                    if ot == start_year or ot in {"2022", "2021", "2023", "2020"}:
                        return ResolvedAnswer(ot if ot == start_year else start_year, "profile_education_start_year")
            return ResolvedAnswer(start_year, "profile_education_start_year_default")
        if "month" in q and "year" not in q:
            if options:
                month_names = {
                    "january": "January", "february": "February", "march": "March",
                    "april": "April", "may": "May", "june": "June", "july": "July",
                    "august": "August", "september": "September", "october": "October",
                    "november": "November", "december": "December",
                }
                for opt in options:
                    if opt.lower().strip() in month_names:
                        return ResolvedAnswer(start_month, "profile_education_start_month")
            return ResolvedAnswer(start_month, "profile_education_start_month_default")
        if options:
            for pref in (start_month, f"{start_month} {start_year}", "Sep", start_year):
                mapped = _map_to_options(pref, options)
                if mapped:
                    return ResolvedAnswer(mapped, "profile_education_start")
        return ResolvedAnswer(f"{start_month} {start_year}", "profile_education_start_default")

    if any(k in q for k in ("end date", "graduation", "end month", "end year", "end_date", "enddate", "date de fin")):
        grad_year = str(profile.get("graduation_year") or "2026")
        grad_month = str(profile.get("graduation_month") or "December")
        if "year" in q and "month" not in q:
            if options:
                for opt in options:
                    ot = opt.strip()
                    if ot == grad_year or ot in {"2025", "2026", "2027"}:
                        return ResolvedAnswer(ot, "profile_graduation_year")
            return ResolvedAnswer(grad_year, "profile_graduation_year_default")
        if "month" in q and "year" not in q:
            if options:
                month_names = {
                    "january": "January", "february": "February", "march": "March",
                    "april": "April", "may": "May", "june": "June", "july": "July",
                    "august": "August", "september": "September", "october": "October",
                    "november": "November", "december": "December",
                }
                for opt in options:
                    if opt.lower().strip() in month_names:
                        return ResolvedAnswer(grad_month, "profile_graduation_month")
            return ResolvedAnswer(grad_month, "profile_graduation_month_default")
        if options:
            for pref in (grad_year, grad_month, f"{grad_month} {grad_year}"):
                mapped = _map_to_options(pref, options)
                if mapped:
                    return ResolvedAnswer(mapped, "profile_graduation_date")
        return ResolvedAnswer(f"{grad_month} {grad_year}", "profile_graduation_date_default")

    # Languages spoken
    if any(k in q for k in ("language", "proficient", "bilingual")):
        if options:
            for pref in ("English", "English/Anglais", "Bilingual"):
                mapped = _map_to_options(pref, options)
                if mapped:
                    return ResolvedAnswer(mapped, "smart_language")
        return ResolvedAnswer("English", "smart_language_default")

    # Notice period — keep aligned with Indeed training bank ("2 weeks" default).
    if "notice" in q:
        if options:
            for pref in ("2 weeks", "Two weeks", "2 Weeks", "2"):
                mapped = _map_to_options(pref, options)
                if mapped:
                    return ResolvedAnswer(mapped, "smart_notice")
        return ResolvedAnswer("2 weeks", "smart_notice_default")

    # Generic consent/acknowledgment questions with Yes/No options.
    if (
        any(k in q for k in (
            "i understand and agree",
            "you understand and agree",
            "by applying",
            "by submitting",
            "i acknowledge",
            "i agree",
            "i consent",
            "you consent",
            "with your permission",
            "we may use",
            "i certify that all",
        ))
        and any(k in q for k in ("acknowledge", "agree", "consent", "understand", "permission", "certify", "progression in the recruitment"))
    ):
        return yes()

    return None


def _policy_values(profile: dict[str, Any]):
    from jobbots.core.llm_backend.answer_policy import PolicyValues

    gender = str(profile.get("gender") or "Male").strip().lower()
    if gender.startswith("m"):
        gender_val = "male"
    elif gender.startswith("f"):
        gender_val = "female"
    else:
        gender_val = gender or "male"

    try:
        salary = int(re.sub(r"[^\d]", "", str(profile.get("desired_salary") or "70000")) or "70000")
    except Exception:
        salary = 70000
    try:
        yoe = int(re.sub(r"[^\d]", "", str(profile.get("years_of_experience") or "3")) or "3")
    except Exception:
        yoe = 3

    return PolicyValues(
        gender=gender_val,
        pronouns="he/him" if gender_val == "male" else "she/her",
        desired_salary=salary,
        years_experience=yoe,
        full_name=str(profile.get("full_name") or ""),
        authorized_canada=True,
        needs_canada_sponsorship=False,
        authorized_us=False,
        needs_us_sponsorship=True,
    )


def _policy_answer(
    question: str,
    *,
    options: list[str] | None,
    profile: dict[str, Any],
) -> ResolvedAnswer | None:
    """Hard-locked policy — same identity/eligibility rules as Indeed."""
    try:
        from jobbots.core.llm_backend.answer_policy import classify, map_intent_to_option
    except Exception:
        return None

    decision = classify(question, options=options, values=_policy_values(profile))
    if not decision.matched:
        return None

    if decision.intent in {"text", "numeric"} and decision.value:
        value = str(decision.value)
        if options:
            mapped = _map_to_options(value, options) or map_intent_to_option(value, options)
            if mapped:
                value = mapped
            elif decision.category == "gender":
                # Hard identity: only accept a male option label, never raw "male"
                # that the DOM might mis-map onto Female.
                for opt in options:
                    if _is_male_option(opt):
                        value = opt
                        break
                else:
                    return None
            elif decision.confidence == "hard":
                # keep raw numeric/text even if not in options
                pass
            else:
                return None
        q_low = (question or "").lower()
        if (
            re.search(r"\b(gender|sex|sexe)\b", q_low)
            and str(profile.get("gender") or "").lower().startswith("m")
            and (_is_female_option(value) or "transgender" in value.lower())
        ):
            return None
        return ResolvedAnswer(
            value=value,
            source=decision.source or f"policy_{decision.category}",
            score=1.0 if decision.confidence == "hard" else 0.9,
            matched_question=question,
        )

    if decision.intent in {"yes", "no", "decline"}:
        if options:
            mapped = map_intent_to_option(decision.intent, options) or _map_to_options(
                decision.intent, options
            )
            if not mapped:
                return None
            return ResolvedAnswer(
                value=mapped,
                source=decision.source or f"policy_{decision.category}",
                score=1.0,
                matched_question=question,
            )
        return ResolvedAnswer(
            value=decision.intent.title() if decision.intent != "decline" else "Decline",
            source=decision.source or f"policy_{decision.category}",
            score=1.0,
            matched_question=question,
        )
    return None


@lru_cache(maxsize=1)
def _deepseek_client():
    """Lazy DeepSeek/OpenRouter client — same factory Indeed uses."""
    # Ensure Infisical/.env secrets are cached (OPENROUTER often not in os.environ).
    try:
        import jobbots.core.secret_manager  # noqa: F401
    except Exception:
        pass
    try:
        from config.secrets import use_AI, ai_provider  # type: ignore
    except Exception:
        use_AI, ai_provider = True, "deepseek"
    if not use_AI:
        return None
    provider = (ai_provider or "deepseek").strip().lower()
    # Production Indeed path is deepseek + OpenRouter key in secret manager.
    if provider not in {"deepseek", "openai", ""}:
        # Still allow when OpenRouter/DeepSeek keys exist.
        pass
    try:
        from jobbots.core.llm_backend.ai.deepseekConnections import deepseek_create_client
        return deepseek_create_client()
    except Exception:
        try:
            # Master-bot path Indeed uses after modules bridge
            from modules.ai.deepseekConnections import deepseek_create_client  # type: ignore
            return deepseek_create_client()
        except Exception:
            return None


def _sanitize_ai_text(answer: Any, *, question: str = "", options: list[str] | None = None) -> str:
    if isinstance(answer, dict):
        if answer.get("error"):
            return ""
        answer = answer.get("content") or answer.get("answer") or ""
    text = str(answer or "").strip()
    if not text:
        return ""
    text = re.sub(r"^```(?:text|markdown|json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text).strip()
    explicit = re.findall(
        r"(?:final\s+answer|answer|response)\s*:\s*(.+)",
        text,
        flags=re.I | re.S,
    )
    if explicit:
        text = explicit[-1].strip()
    text = text.strip().strip('"').strip("'").strip()
    if options:
        for opt in options:
            if text.casefold() == opt.casefold():
                return opt
        matches = [
            opt
            for opt in options
            if re.search(rf"(?<!\w){re.escape(opt)}(?!\w)", text, flags=re.I)
        ]
        if len(matches) == 1:
            return matches[0]
        mapped = _map_to_options(text, options)
        if mapped:
            return mapped
    q = (question or "").lower()
    if any(k in q for k in ("yes or no", "yes/no", "(yes / no", "(yes/no")):
        yn = re.findall(r"\b(yes|no|prefer not to say)\b", text, flags=re.I)
        if yn:
            return yn[-1].title()
    # Keep free-text answers short for form fields.
    if len(text) > 500 and not options:
        text = text[:500].rsplit(" ", 1)[0].strip()
    return text


def _profile_dossier(profile: dict[str, Any] | None) -> str:
    """Compact candidate dossier for LLM prompts (DOM-aware form fill)."""
    p = profile or {}
    lines = [
        f"Full name: {p.get('full_name') or ''}",
        f"Email: {p.get('email') or ''}",
        f"Phone: {p.get('phone') or ''}",
        f"Location: {p.get('location') or p.get('city') or ''}, {p.get('state') or ''} {p.get('country') or ''}",
        f"Street: {p.get('street') or ''}",
        f"School: {p.get('school') or ''} (start {p.get('education_start_month') or ''} {p.get('education_start_year') or ''}; "
        f"expected grad {p.get('graduation_month') or ''} {p.get('graduation_year') or ''}; graduated={p.get('graduated') or 'No'})",
        f"Years of experience: {p.get('years_of_experience') or '3'}",
        f"Desired salary (CAD): {p.get('desired_salary') or '70000'}",
        f"Require visa sponsorship: {p.get('require_visa') or 'No'}",
        f"LinkedIn: {p.get('linkedin') or ''}",
        f"Website/portfolio: {p.get('website') or ''}",
        f"Gender: {p.get('gender') or 'Male'}",
        f"Current company: {p.get('current_company') or 'Seeking opportunities'}",
        "Work auth: Canadian citizen / permanent-resident path; authorized to work in Canada; no Canada sponsorship needed.",
        "US work: not authorized without sponsorship.",
        "Skills/focus: IT support, networking, cloud (AWS), security, QA, systems admin, help desk.",
        f"Summary: {(p.get('profile_summary') or p.get('why_company_answer') or '')[:500]}",
    ]
    try:
        from config.questions import user_information_all  # type: ignore

        extra = (user_information_all or "").strip()
        if extra:
            lines.append(f"Full profile notes:\n{extra[:2500]}")
    except Exception:
        pass
    return "\n".join(lines)


def _selectable_options(options: list[str] | None) -> list[str]:
    if not options:
        return []
    skip = {
        "select an option", "select", "please select", "choose", "choose one",
        "", "-", "--",
    }
    out: list[str] = []
    for o in options:
        t = (o or "").strip()
        if not t or t.lower() in skip or t.lower().startswith("select an"):
            continue
        out.append(t)
    return out


def _deepseek_answer(
    question: str,
    *,
    hint: str = "",
    options: list[str] | None = None,
    job_context: str = "",
    profile: dict[str, Any] | None = None,
) -> ResolvedAnswer | None:
    """DeepSeek / Akash ML — rich profile + DOM options for form fill."""
    # Allow explicit disable for dry unit tests / offline runs.
    if str(os.getenv("FORM_ANSWERS_DISABLE_AI") or "").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        return None
    client = _deepseek_client()
    if client is None:
        return None

    prof = profile or load_profile()
    dossier = _profile_dossier(prof)
    selectable = _selectable_options(options)
    qtype = "single_select" if selectable else "text"

    # Build a high-signal prompt: question + DOM options + candidate facts + job.
    parts = [
        f"QUESTION:\n{(question or '').strip()}",
    ]
    if hint and hint.strip():
        parts.append(f"DOM / FIELD CONTEXT (use this; it includes nearby labels):\n{hint.strip()[:1200]}")
    if selectable:
        parts.append(
            "DOM OPTIONS (you MUST answer with one EXACT option string from this list):\n"
            + "\n".join(f"- {o}" for o in selectable)
        )
        parts.append(
            "If the question is Yes/No, pick the exact Yes/No option label. "
            "Never invent free text that is not in the list."
        )
    else:
        parts.append(
            "This is a free-text field. Answer concisely for the candidate. "
            "Years → number only. Referral name if none → N/A. No fluff."
        )
    parts.append(f"CANDIDATE PROFILE:\n{dossier}")
    if job_context and job_context.strip():
        parts.append(f"JOB / APPLICATION CONTEXT:\n{job_context.strip()[:2000]}")
    q = "\n\n".join(parts)

    try:
        from jobbots.core.llm_backend.ai.deepseekConnections import deepseek_answer_question
    except Exception:
        try:
            from modules.ai.deepseekConnections import deepseek_answer_question  # type: ignore
        except Exception:
            return None

    try:
        raw = deepseek_answer_question(
            client,
            q,
            options=selectable or options,
            question_type=qtype,
            job_description=job_context or "",
            about_company="",
            user_information_all=dossier,
            stream=False,
        )
    except Exception:
        return None

    value = _sanitize_ai_text(raw, question=question, options=selectable or options)
    if not value:
        return None
    if selectable:
        mapped = _map_to_options(value, selectable)
        if not mapped:
            # Fuzzy: pick best option by token overlap
            vn = _normalize(value)
            best, best_s = "", 0.0
            for opt in selectable:
                on = _normalize(opt)
                if not on:
                    continue
                if vn == on or vn in on or on in vn:
                    mapped = opt
                    break
                inter = set(vn.split()) & set(on.split())
                score = len(inter) / max(len(set(vn.split()) | set(on.split())), 1)
                if score > best_s:
                    best, best_s = opt, score
            if not mapped and best_s >= 0.35:
                mapped = best
        if not mapped:
            return None
        value = mapped
    # Source tag reflects gateway (Akash ML preferred).
    try:
        from jobbots.core.llm_backend.ai.llm_gateway import resolve_llm_gateway

        _src = f"deepseek_{resolve_llm_gateway().provider or 'llm'}"
    except Exception:
        _src = "deepseek_akashml"
    return ResolvedAnswer(
        value=value,
        source=_src,
        score=0.75,
        matched_question=question,
    )


def resolve_answer(
    question: str,
    *,
    hint: str = "",
    options: Iterable[str] | None = None,
    profile: dict[str, Any] | None = None,
    job_context: str = "",
    allow_ai: bool = True,
) -> ResolvedAnswer | None:
    """Resolve an answer for any portal form question.

    Order:
      1. hard policy (identity / eligibility locks) — only if mappable to options
      2. deterministic profile/safe rules — only if mappable when options exist
      3. curated QA bank — only if mappable when options exist
      4. AI with full profile + DOM options (always used for unmapped / empty)

    When the control has real DOM options, any rule answer that cannot be
    mapped onto those options is discarded and AI fills the field.
    """
    prof = profile or load_profile()
    opt_list = [str(o).strip() for o in (options or []) if str(o).strip()]
    selectable = _selectable_options(opt_list)
    opts = opt_list or None
    # Policy + safe rules use the field question only.  DOM section dumps in
    # ``hint`` used to pollute matches (e.g. School → British Columbia).
    q_only = (question or "").strip()
    has_choice = len(selectable) >= 2

    def _coerce(ans: ResolvedAnswer | None, *, require_option: bool = False) -> ResolvedAnswer | None:
        """Map free-text onto DOM options; drop unmappable answers for selects."""
        if not ans or not (ans.value or "").strip():
            return None
        if not selectable:
            return ans
        mapped = _map_to_options(ans.value, selectable)
        if mapped:
            if mapped != ans.value:
                return ResolvedAnswer(
                    mapped, f"{ans.source}_mapped", ans.score, ans.matched_question
                )
            return ans
        # Unmappable on a real choice control → force later AI (or fail closed)
        if require_option or has_choice or _is_yes_no_options(opt_list):
            return None
        return ans

    # 1) Hard policy first — never invent identity, but must match DOM options.
    policy = _coerce(
        _policy_answer(q_only, options=selectable or opt_list or None, profile=prof),
        require_option=has_choice,
    )
    if policy and policy.value:
        return policy

    # 2) Profile / safe rules take priority over historic examples. Facts such
    # as location, graduation, identity, and current contact details must not
    # be overwritten by a stale answer in the QA bank.
    safe = _coerce(
        _safe_rules(q_only, prof, selectable or opt_list or None),
        require_option=has_choice,
    )
    if safe and safe.value:
        return safe

    # 3) Curated training bank for questions without a deterministic profile rule.
    bank = _bank_find_answer(question, hint=hint, options=opts)
    if bank and bank.value:
        bank = _coerce(bank, require_option=has_choice)
        # Identity lock: profile is Male — never accept Female / Transgender *
        # from a stale QA bank match when the DOM only offers those options.
        if bank:
            bv = (bank.value or "").lower()
            is_gender_q = bool(
                re.search(r"\b(gender|sex|sexe|identify as)\b", q_only.lower())
            )
            if is_gender_q and (
                "transgender" in bv
                or _is_female_option(bank.value)
            ) and str(prof.get("gender") or "Male").lower().startswith("m"):
                bank = None
        if bank:
            return bank

    # 4) AI — always for empty / unmapped choice fields with full profile + DOM
    if allow_ai:
        # Enrich hint with option list for the model even if deepseek gets options kw
        rich_hint = (hint or "").strip()
        if selectable:
            rich_hint = (
                f"{rich_hint}\n\nExact options:\n" + "\n".join(f"- {o}" for o in selectable)
            ).strip()
        ai = _coerce(
            _deepseek_answer(
                question,
                hint=rich_hint,
                options=selectable or opt_list or None,
                job_context=job_context,
                profile=prof,
            ),
            require_option=has_choice,
        )
        if ai and ai.value:
            return ai

    # 5) Last resort for binary Yes/No if AI failed
    if has_choice and _is_yes_no_options(selectable or opt_list):
        # Prefer No for "ever been / previously" style, else Yes for capability
        qn = q_only.lower()
        if any(k in qn for k in ("ever been", "previously", "criminal", "disability", "sponsor")):
            mapped = _map_to_options("No", selectable) or "No"
            return ResolvedAnswer(mapped, "fallback_yes_no_no")
        mapped = _map_to_options("Yes", selectable) or "Yes"
        return ResolvedAnswer(mapped, "fallback_yes_no_yes")

    return None


def resolve_text(
    question: str,
    *,
    hint: str = "",
    profile: dict[str, Any] | None = None,
    job_context: str = "",
    allow_ai: bool = True,
) -> str:
    ans = resolve_answer(
        question,
        hint=hint,
        profile=profile,
        job_context=job_context,
        allow_ai=allow_ai,
    )
    return ans.value if ans else ""


def resolve_choice(
    question: str,
    options: Iterable[str],
    *,
    hint: str = "",
    profile: dict[str, Any] | None = None,
    job_context: str = "",
    allow_ai: bool = True,
) -> str:
    ans = resolve_answer(
        question,
        hint=hint,
        options=options,
        profile=profile,
        job_context=job_context,
        allow_ai=allow_ai,
    )
    return ans.value if ans else ""

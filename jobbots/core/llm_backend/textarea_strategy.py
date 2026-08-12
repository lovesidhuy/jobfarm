"""
core.textarea_strategy — classify free-text textarea questions and build
short, role-aware prompts for the AI.

Why this exists
---------------
The original textarea path dumped a full `profile_summary` into any field
whose label contained "summary", "tell us", "describe" or "about yourself".
That is correct for a real "Tell us about yourself" prompt but disastrous
for behavioural questions like:

    "Tell us about a time you had to handle a difficult stakeholder"
    "Describe a challenging project and how you handled it"
    "Walk me through how you would approach X"

The summary-dump is what makes a behavioural answer look automated. This
module separates the two so:

    profile_summary path → only the genuine "introduce yourself" prompts
    behavioural path     → tight STAR-lite AI generation, role-aware

It is a *pure-function* module — no DOM, no I/O, no AI calls. The runtime
keeps owning *how* to feed the prompt to its provider.

Public API
----------
    classify_textarea(question, hint=None) -> TextareaCategory
    should_block_summary(question, hint=None) -> bool
    build_behavioral_prompt(question, role_context, profile_facts) -> str
    build_short_answer_prompt(question, role_context, profile_facts) -> str
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class TextareaCategory(str, Enum):
    BEHAVIORAL      = "behavioral"        # STAR-style situation/action/result
    SHORT_ANSWER    = "short_answer"      # 1-3 sentence factual / opinion
    PROFILE_SUMMARY = "profile_summary"   # genuine "tell us about yourself"
    COVER_LETTER    = "cover_letter"
    TECHNICAL_SKILL = "technical_skill"   # "describe your experience with X"
    CONTACT_INFO    = "contact_info"      # name / address / linkedin etc
    UNMATCHED       = "unmatched"


@dataclass(frozen=True)
class ProfileFacts:
    """Per-bot snapshot fed into the behavioural prompt.

    Each bot owns its own ProfileFacts. The two Indeed bots have *deliberately
    different* career narratives — the General bot has the full
    sales/customer-service/driver/porter history, the IT bot has IT-aligned
    porter + Bell experience only — so this struct keeps them separate at the
    prompt-construction layer.
    """

    name: str = ""
    role_focus: str = ""        # e.g. "IT support / system administration"
    years_experience: int = 0
    top_skills: tuple[str, ...] = ()
    notable_employers: tuple[str, ...] = ()
    industries: tuple[str, ...] = ()
    location: str = ""
    pronouns: str = ""

    def short_career_brief(self) -> str:
        bits = []
        if self.years_experience:
            bits.append(f"{self.years_experience}+ years")
        if self.role_focus:
            bits.append(self.role_focus)
        if self.notable_employers:
            bits.append("at " + ", ".join(self.notable_employers))
        return " ".join(bits)


# ---------------------------------------------------------------------------
# Keyword tables. Ordered by priority — earlier matches win.
# ---------------------------------------------------------------------------

_BEHAVIORAL_PHRASES = (
    "tell us about a time", "tell me about a time",
    "tell us about a project", "tell me about a project",
    "describe a time", "describe a situation", "describe an example",
    "describe a project", "tell us about an experience",
    "give an example", "give us an example", "give me an example",
    "share an experience", "share a time", "share an example",
    "walk me through a time", "walk us through a time",
    "how would you handle", "how would you approach",
    "how did you handle", "how did you approach",
    "how would you respond", "how do you handle",
    "what would you do if", "what did you do when",
    "describe a challenge", "describe a difficult",
    "tell us about a challenge", "tell us about a difficult",
    "challenging project", "difficult situation",
    "difficult customer", "difficult coworker", "difficult stakeholder",
    "conflict with", "disagreement with", "team conflict",
    "demonstrate your", "an instance where",
    "a situation where you", "a time when you",
    "describe how you", "tell us how you",
    "biggest accomplishment", "proudest moment", "most proud of",
    "your biggest weakness", "your biggest strength",
    "show me you read", "to show you read",
    "why are you a fit", "why should we hire",
    "why this role", "why this company",
)

_PROFILE_SUMMARY_PHRASES = (
    "tell us about yourself", "tell me about yourself",
    "introduce yourself", "describe yourself",
    "about yourself", "anything else about yourself",
    "anything else you'd like us to know", "anything else you would like us",
    "anything else about you",
    "professional summary", "summary of your experience",
    "your summary", "candidate summary",
)

_TECHNICAL_SKILL_PHRASES = (
    "describe your experience with", "describe your experience using",
    "describe your experience", "describe your skill",
    "your experience in", "your experience with", "your experience using",
    "experience with", "experience using",
    "what is your experience", "what's your experience",
    "knowledge of", "familiarity with", "proficiency in",
    "skills in", "technical skills",
    "tools and technologies", "tools you have used",
    "software you have used", "software experience",
    "telecommunication experience", "communication experience",
    "level of skill", "level of proficiency",
)

_COVER_LETTER_PHRASES = (
    "cover letter", "letter of motivation", "motivation letter",
    "lettre de motivation",
)

_CONTACT_PHRASES = (
    "linkedin profile", "linkedin url",
    "portfolio url", "github url", "personal website",
    "twitter handle", "current address", "mailing address",
)

_SHORT_ANSWER_PHRASES = (
    "what attracted you", "what excites you about", "why do you want",
    "why are you interested", "why this position",
    "what are your goals", "what are your career goals",
    "what motivates you", "what is your motivation",
    "describe your ideal", "ideal work environment",
    "what would you bring", "what skills do you bring",
    "any additional information", "anything additional",
    "anything else we should know about your application",
    "questions for the hiring manager", "questions for us",
)

_WORD_RE = re.compile(r"[a-z0-9']+")


def _norm(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def _matches_any(haystack: str, needles: tuple[str, ...]) -> Optional[str]:
    for n in needles:
        nn = _norm(n)
        if nn and nn in haystack:
            return n
    return None


def classify_textarea(question: str,
                      hint: Optional[str] = None) -> TextareaCategory:
    """
    Classify a textarea question.

    Order matters:
      1. behavioural keywords (catches "tell us about a *time*…")
      2. profile-summary keywords (catches the genuine "tell us about yourself")
      3. cover letter
      4. technical-skill description
      5. contact info
      6. short-answer (catch-all for "why this role" / "what motivates you")
      7. unmatched
    """
    haystack = _norm(f"{question or ''} {hint or ''}")
    if not haystack:
        return TextareaCategory.UNMATCHED

    if _matches_any(haystack, _BEHAVIORAL_PHRASES):
        return TextareaCategory.BEHAVIORAL
    if _matches_any(haystack, _PROFILE_SUMMARY_PHRASES):
        return TextareaCategory.PROFILE_SUMMARY
    if _matches_any(haystack, _COVER_LETTER_PHRASES):
        return TextareaCategory.COVER_LETTER
    if _matches_any(haystack, _TECHNICAL_SKILL_PHRASES):
        return TextareaCategory.TECHNICAL_SKILL
    if _matches_any(haystack, _CONTACT_PHRASES):
        return TextareaCategory.CONTACT_INFO
    if _matches_any(haystack, _SHORT_ANSWER_PHRASES):
        return TextareaCategory.SHORT_ANSWER
    return TextareaCategory.UNMATCHED


def should_block_summary(question: str, hint: Optional[str] = None) -> bool:
    """
    True iff the question is *not* a genuine "tell us about yourself" prompt.

    Used as a guard so the legacy `configured_summary` branch never fires on
    behavioural / technical-skill / cover-letter questions, which is what was
    causing the resume-dump anti-pattern in the corpus.
    """
    cat = classify_textarea(question, hint)
    return cat != TextareaCategory.PROFILE_SUMMARY


# ---------------------------------------------------------------------------
# Prompt builders. Compact, model-agnostic — they return a single string that
# fits the existing `_ai_answer(question=..., hint=...)` call surface.
# ---------------------------------------------------------------------------


_BEHAVIORAL_TEMPLATE = """You are answering a behavioural job-application question on behalf of the candidate.

CANDIDATE:
{candidate_brief}

ROLE CONTEXT:
{role_context}

QUESTION:
{question}

WRITE THE ANSWER FOLLOWING ALL OF THESE RULES:
- Answer in the FIRST PERSON ("I ...").
- 2 to 4 sentences total. NEVER more than 90 words.
- Use a STAR-lite shape: brief situation, the action you took, the result.
- Anchor the example in ONE of the candidate's real employers / industries
  if a relevant one exists; otherwise speak generally about a workplace.
- Do NOT recite a resume summary or list multiple jobs.
- Do NOT invent specific certifications, dates, or numeric metrics that
  are not in the candidate facts above.
- Do NOT start with "As a ..." or "I am a ...". Start with the situation.
- No bullet points, no headings, no newlines. Plain prose only.
- Match the candidate's role focus, not a generic professional voice.

Output ONLY the answer text."""


_SHORT_ANSWER_TEMPLATE = """You are answering a short open-text job-application question on behalf of the candidate.

CANDIDATE:
{candidate_brief}

ROLE CONTEXT:
{role_context}

QUESTION:
{question}

WRITE THE ANSWER FOLLOWING ALL OF THESE RULES:
- 1 to 3 sentences. NEVER more than 60 words.
- First person, plain prose, no bullets, no headings.
- Stay specific to this role and company; reference the candidate's
  background only if it is directly relevant to the question.
- Do NOT recite a resume summary.
- Do NOT invent specific certifications, dates, or metrics that are not
  in the candidate facts above.

Output ONLY the answer text."""


def _format_facts(facts: ProfileFacts) -> str:
    parts = []
    brief = facts.short_career_brief()
    if brief:
        parts.append(f"  - {brief}")
    if facts.top_skills:
        parts.append(f"  - core skills: {', '.join(facts.top_skills)}")
    if facts.industries:
        parts.append(f"  - industries: {', '.join(facts.industries)}")
    if facts.location:
        parts.append(f"  - based in: {facts.location}")
    if not parts:
        parts.append("  - (no candidate facts supplied)")
    return "\n".join(parts)


def build_behavioral_prompt(question: str,
                            role_context: str,
                            profile_facts: ProfileFacts) -> str:
    return _BEHAVIORAL_TEMPLATE.format(
        candidate_brief=_format_facts(profile_facts),
        role_context=(role_context or "(no specific role context)").strip(),
        question=(question or "").strip() or "(no question text)",
    )


def build_short_answer_prompt(question: str,
                              role_context: str,
                              profile_facts: ProfileFacts) -> str:
    return _SHORT_ANSWER_TEMPLATE.format(
        candidate_brief=_format_facts(profile_facts),
        role_context=(role_context or "(no specific role context)").strip(),
        question=(question or "").strip() or "(no question text)",
    )

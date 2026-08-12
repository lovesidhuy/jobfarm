"""Limited office / customer-service discovery terms for GENERAL profile only.

Indeed general discovery uses ``HERO_SEARCH_TERMS`` (short CS/office list).

LinkedIn production is ONE bot (``linkedin_general`` / authenticated session) that
applies to both IT and office/CS. ``LINKEDIN_HERO_TERMS`` is the full combined
list; the discover wrapper also dual-passes IT terms under profile=it so gates
stay correct (IT titles rejected by general gate, CSR rejected by IT gate).
"""

from __future__ import annotations

# Minor office + customer service Easy Apply farm (Metro Vancouver) — Indeed general.
HERO_SEARCH_TERMS: list[str] = [
    "Customer Service Representative",
    "Customer Service Associate",
    "Customer Service Agent",
    "Customer Care Representative",
    "Customer Experience Representative",
    "Client Service Representative",
    "Guest Services Associate",
    "Receptionist",
    "Front Desk Receptionist",
    "Office Assistant",
    "Office Clerk",
    "Administrative Assistant",
    "Admin Assistant",
    "Administrative Coordinator",
    "Office Coordinator",
    "Operations Assistant",
    "Data Entry Clerk",
    "Order Entry Clerk",
    "Call Centre Representative",
    "Call Center Agent",
    "Contact Centre Agent",
    "Member Services Representative",
    "Patient Service Representative",
    "Appointment Scheduler",
    "Scheduling Coordinator",
]

# Office/CS slice used by the single LinkedIn bot (combined with IT terms in discover).
LINKEDIN_OFFICE_TERMS: list[str] = list(HERO_SEARCH_TERMS)


def _combined_linkedin_terms() -> list[str]:
    """IT LinkedIn heroes ∪ office/CS — de-duped, order preserved."""
    try:
        from config.it.hero_terms import LINKEDIN_HERO_TERMS as _it_li
        it_list = list(_it_li)
    except Exception:
        it_list = []
    seen: set[str] = set()
    out: list[str] = []
    for term in it_list + LINKEDIN_OFFICE_TERMS:
        key = (term or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(term.strip())
    return out


# Full list for the single LinkedIn bot (documentation + env override default).
LINKEDIN_HERO_TERMS: list[str] = _combined_linkedin_terms()

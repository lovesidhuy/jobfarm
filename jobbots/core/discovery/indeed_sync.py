"""Indeed-family sync gate for Glassdoor / Workopolis discovery.

Skip enqueueing a Glassdoor or Workopolis job when the same role was already
seen/applied on Indeed (or already applied on Glassdoor), including:

  1. discovery ``source_refs`` already contain an Indeed platform ref
  2. canonical URL / soft company+title(+location) matches an Indeed queue row
  3. soft company+title match against Glassdoor ``applied`` / ``bookmarked`` rows
  4. Indeed applied / skipped history job ids (normalised), plus exact
     normalised company/title history keys for reposted Indeed listings
  5. ``email_applied_history`` title/company matches (IMAP confirmation emails)

Workopolis is an Indeed partner — many (not all) postings are Indeed twins.
Glassdoor Easy Apply → Indeed SmartApply also surfaces ``already applied``
at click time. This gate reduces wasted leases; false negatives are OK when
apply-time detection still works — but soft matching + email history should
catch the common cases (``Ltd.`` suffix, ``Vancouver`` vs ``Vancouver, BC, CA``).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from jobbots.core.discovery.contracts import NormalizedJob

_log = logging.getLogger("discovery.indeed_sync")

_PLATFORM_INDEED = "indeed"
_SYNC_SOURCE_PLATFORMS = frozenset({"glassdoor", "workopolis"})
_APPLIED_STATUSES = frozenset({"applied", "bookmarked"})

# Legal / noise suffixes that differ across Glassdoor vs Indeed company strings.
_COMPANY_SUFFIX_RE = re.compile(
    r"\b(ltd\.?|limited|inc\.?|incorporated|llc|corp\.?|corporation|co\.?|"
    r"company|plc|ulc|lp|llp)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s&/+-]+", re.UNICODE)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _soft_company(company: str) -> str:
    """Company key robust to Ltd/Inc and punctuation drift."""
    s = _normalize_text(company)
    s = _COMPANY_SUFFIX_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _soft_title(title: str) -> str:
    s = _normalize_text(title)
    # Drop trailing requisition codes like "#5157" / "MOVEUP-018-26"
    s = re.sub(r"\s[#-]?\d{3,}[a-z0-9-]*\s*$", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _soft_location(location: str) -> str:
    """City-only key: ``Vancouver, BC, CA`` → ``vancouver``."""
    s = _normalize_text(location)
    if not s:
        return ""
    city = s.split(",")[0].strip()
    city = re.sub(r"\s+(bc|ab|on|qc|canada|ca)$", "", city).strip()
    return city


def _ctl_key(company: str, title: str, location: str) -> str:
    """Soft company|title|city — preferred queue match key."""
    return "|".join([
        _soft_company(company),
        _soft_title(title),
        _soft_location(location),
    ])


def _ct_key(company: str, title: str) -> str:
    """Soft company|title — catches cross-portal location string drift."""
    return "|".join([_soft_company(company), _soft_title(title)])


def _companies_soft_match(a: str, b: str) -> bool:
    """Exact soft company or containment (Altimus vs Altimus Product Development)."""
    sa, sb = _soft_company(a), _soft_company(b)
    if not sa or not sb or sa in {"unknown", ""} or sb in {"unknown", ""}:
        return False
    if sa == sb:
        return True
    # Require short side ≥4 chars to avoid "co"/"it" false positives
    short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(short) < 4:
        return False
    return short in long or long.startswith(short + " ")


def _ct_key_in_set(keys: set[str], company: str, title: str) -> bool:
    """Exact CT key or same title + soft company containment."""
    exact = _ct_key(company, title)
    if exact.strip("|") and exact in keys:
        return True
    st = _soft_title(title)
    sc = _soft_company(company)
    if not st or not sc or st in {"unknown", ""}:
        return False
    for key in keys:
        if "|" not in key:
            continue
        other_c, other_t = key.split("|", 1)
        if other_t != st:
            continue
        if _companies_soft_match(sc, other_c):
            return True
    return False


def _ctl_key_in_set(keys: set[str], company: str, title: str, location: str) -> bool:
    """Exact CTL or same title+city with soft company containment."""
    exact = _ctl_key(company, title, location)
    if exact.strip("|") and exact in keys:
        return True
    st = _soft_title(title)
    sc = _soft_company(company)
    loc = _soft_location(location)
    if not st or not sc or st in {"unknown", ""}:
        return False
    for key in keys:
        parts = key.split("|")
        if len(parts) != 3:
            continue
        other_c, other_t, other_loc = parts
        if other_t != st:
            continue
        if loc and other_loc and loc != other_loc:
            continue
        if _companies_soft_match(sc, other_c):
            return True
    return False


def _canonical_url(url: str | None) -> str:
    if not url:
        return ""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().lstrip("www.")
        qs = parse_qs(parsed.query, keep_blank_values=False)
        for drop in (
            "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
            "ref", "src", "from", "trk", "refId", "trackingId",
        ):
            qs.pop(drop, None)
        clean_qs = urlencode(qs, doseq=True)
        return urlunparse(("", host, parsed.path.rstrip("/"), "", clean_qs, ""))
    except Exception:
        return (url or "").strip().lower()


def normalize_indeed_job_id(raw: str | None) -> str:
    """Strip common Indeed id prefixes (``in-``, ``jk=`` noise)."""
    s = (raw or "").strip()
    if not s:
        return ""
    if s.lower().startswith("in-"):
        s = s[3:]
    if s.lower().startswith("jk="):
        s = s[3:]
    return s.strip()


def source_refs_include_indeed(job: NormalizedJob) -> bool:
    for ref in job.source_refs or []:
        if (ref.get("platform") or "").strip().lower() == _PLATFORM_INDEED:
            return True
    return False


def _load_indeed_history_ids() -> set[str]:
    """Best-effort Indeed applied + skipped ids (empty on import failure)."""
    ids: set[str] = set()
    # Prefer Mongo job-record store (no Indeed bootstrap / modules path needed).
    try:
        from jobbots.core.portals.mongo_storage_legacy import get_job_ids
        for status in ("applied", "skipped"):
            for raw in get_job_ids("indeed", status) | get_job_ids("Indeed", status):
                nid = normalize_indeed_job_id(str(raw))
                if nid:
                    ids.add(nid)
    except Exception as exc:
        _log.debug("Indeed mongo history ids unavailable: %s", exc)

    try:
        from jobbots.core.shared_jobbots.core.shared_modules.indeed.persistence import (
            get_applied_indeed_job_ids,
            get_skipped_indeed_job_ids,
        )
        for raw in get_applied_indeed_job_ids() | get_skipped_indeed_job_ids():
            nid = normalize_indeed_job_id(str(raw))
            if nid:
                ids.add(nid)
    except Exception as exc:
        _log.debug("Indeed CSV history ids unavailable: %s", exc)
    return ids


def _load_indeed_history_rows() -> list[dict[str, Any]]:
    """Best-effort applied Indeed records for reposted-listing dedupe.

    Indeed can publish the same job under a new ``jk`` id.  IDs alone then
    cannot prevent a duplicate application, but an exact normalised
    company+title (and preferably city) can.  This deliberately reads only
    records explicitly stored as ``applied``; it does not learn from failed or
    merely saved listings.
    """
    try:
        from jobbots.core.portals.mongo_storage_legacy import list_jobs
        return list(list_jobs("indeed", "applied")) + list(list_jobs("Indeed", "applied"))
    except Exception as exc:
        _log.debug("Indeed mongo history rows unavailable: %s", exc)
        return []


def _iter_queue_rows(
    queue: Any,
    *,
    portals: Iterable[str],
    statuses: Iterable[str] | None = None,
) -> Iterable[dict]:
    if queue is None:
        return []
    try:
        coll = getattr(queue, "jobs", None)
        if coll is None:
            return []
        query: dict[str, Any] = {
            "portal": {"$in": [p.strip().lower() for p in portals]},
        }
        if statuses is not None:
            query["status"] = {"$in": [s.strip().lower() for s in statuses]}
        return coll.find(query, {
            "portal": 1, "source_job_id": 1, "title": 1, "company": 1,
            "location": 1, "url": 1, "result_url": 1, "status": 1,
        })
    except Exception as exc:
        _log.debug("Queue scan failed: %s", exc)
        return []


def _iter_indeed_queue_rows(queue: Any) -> Iterable[dict]:
    return _iter_queue_rows(queue, portals=("indeed",), statuses=None)


def _is_indeed_email(row: dict) -> bool:
    platform = (row.get("source_platform") or "").strip().lower()
    if platform == "indeed":
        return True
    sender = (row.get("sender") or "").strip().lower()
    if "indeed" in sender:
        return True
    subject = (row.get("subject") or "").strip().lower()
    if subject.startswith("indeed application") or "indeed application:" in subject:
        return True
    return False


def _iter_email_applied_rows(queue: Any) -> Iterable[dict]:
    """IMAP-synced application receipts from Indeed (ignore non-Indeed emails)."""
    if queue is None:
        return []
    try:
        db = getattr(queue, "db", None)
        if db is None:
            return []
        rows = db["email_applied_history"].find({}, {
            "company_name": 1, "job_title": 1, "subject": 1, "source_platform": 1, "sender": 1,
        })
        return [r for r in rows if _is_indeed_email(r)]
    except Exception as exc:
        _log.debug("email_applied_history scan failed: %s", exc)
        return []


def _index_soft_row(
    *,
    by_url: set[str],
    by_ctl: set[str],
    by_ct: set[str],
    by_source_id: set[str],
    row: dict,
    index_urls: bool,
    index_source_ids: bool,
) -> None:
    if index_source_ids:
        sid = normalize_indeed_job_id(row.get("source_job_id"))
        if sid:
            by_source_id.add(sid)
    if index_urls:
        for u in (row.get("url"), row.get("result_url")):
            cu = _canonical_url(u)
            if cu:
                by_url.add(cu)
    company = row.get("company") or ""
    title = row.get("title") or ""
    location = row.get("location") or ""
    ctl = _ctl_key(company, title, location)
    if ctl.strip("|"):
        by_ctl.add(ctl)
    ct = _ct_key(company, title)
    if ct.strip("|"):
        by_ct.add(ct)


class IndeedSyncIndex:
    """Precomputed Indeed / Glassdoor-applied / email lookup for sync skips."""

    def __init__(
        self,
        *,
        queue: Any = None,
        history_ids: set[str] | None = None,
        load_history: bool = True,
    ) -> None:
        self.by_url: set[str] = set()
        self.by_ctl: set[str] = set()
        self.by_ct: set[str] = set()
        self.by_glassdoor_applied_ctl: set[str] = set()
        self.by_glassdoor_applied_ct: set[str] = set()
        self.by_email_ct: set[str] = set()
        self.by_email_title: set[str] = set()
        self.by_email_company: set[str] = set()
        self.by_source_id: set[str] = set()
        self.history_ctl: set[str] = set()
        self.history_ct: set[str] = set()
        if history_ids is not None:
            self.history_ids = {normalize_indeed_job_id(x) for x in history_ids if x}
        elif load_history:
            self.history_ids = _load_indeed_history_ids()
        else:
            self.history_ids = set()

        if load_history:
            for row in _load_indeed_history_rows():
                company = row.get("Company") or row.get("company") or ""
                title = row.get("Title") or row.get("title") or ""
                location = row.get("Work Location") or row.get("work_location") or ""
                ctl = _ctl_key(company, title, location)
                ct = _ct_key(company, title)
                if ctl.strip("|"):
                    self.history_ctl.add(ctl)
                if ct.strip("|"):
                    self.history_ct.add(ct)

        for row in _iter_indeed_queue_rows(queue):
            _index_soft_row(
                by_url=self.by_url,
                by_ctl=self.by_ctl,
                by_ct=self.by_ct,
                by_source_id=self.by_source_id,
                row=row,
                index_urls=True,
                index_source_ids=True,
            )

        # Glassdoor already applied/bookmarked — catch Workopolis/Indeed twins
        # that never shared a source_job_id with Indeed.
        for row in _iter_queue_rows(
            queue,
            portals=("glassdoor",),
            statuses=_APPLIED_STATUSES,
        ):
            company = row.get("company") or ""
            title = row.get("title") or ""
            location = row.get("location") or ""
            ctl = _ctl_key(company, title, location)
            if ctl.strip("|"):
                self.by_glassdoor_applied_ctl.add(ctl)
            ct = _ct_key(company, title)
            if ct.strip("|"):
                self.by_glassdoor_applied_ct.add(ct)

        for row in _iter_email_applied_rows(queue):
            company = row.get("company_name") or ""
            title = row.get("job_title") or ""
            subject = row.get("subject") or ""
            # Recover title from "Indeed Application: <title>" when job_title is Unknown.
            if (not title or title.strip().lower() in {"unknown", ""}) and subject:
                m = re.match(r"(?i)indeed application:\s*(.+)$", subject.strip())
                if m:
                    title = m.group(1).strip()
                else:
                    m = re.match(r"(?i)thank you for applying(?: to| at)?\s+(.+)$", subject.strip())
                    if m and (not company or company.strip().lower() in {"unknown", ""}):
                        company = m.group(1).strip()
            sc = _soft_company(company)
            st = _soft_title(title)
            if sc and sc not in {"unknown", ""}:
                self.by_email_company.add(sc)
            if st and st not in {"unknown", ""}:
                self.by_email_title.add(st)
            if sc and st and sc not in {"unknown", ""} and st not in {"unknown", ""}:
                self.by_email_ct.add(f"{sc}|{st}")

    def match_reason(self, job: NormalizedJob) -> str | None:
        """Return skip reason string, or ``None`` if job is clear to enqueue."""
        platform = (job.source_platform or "").strip().lower()
        if platform not in _SYNC_SOURCE_PLATFORMS:
            return None

        if source_refs_include_indeed(job):
            return "indeed_source_ref"

        for ref in job.source_refs or []:
            if (ref.get("platform") or "").strip().lower() == _PLATFORM_INDEED:
                return "indeed_source_ref"
            rid = normalize_indeed_job_id(ref.get("job_id"))
            if rid and (rid in self.by_source_id or rid in self.history_ids):
                return "indeed_source_id"

        for u in (job.destination_url, job.listing_url):
            cu = _canonical_url(u)
            if cu and cu in self.by_url:
                return "indeed_queue_url"

        ctl = _ctl_key(job.company_name, job.job_title, job.location)
        if _ctl_key_in_set(self.by_ctl, job.company_name, job.job_title, job.location or ""):
            return "indeed_queue_ctl"

        ct = _ct_key(job.company_name, job.job_title)
        if _ct_key_in_set(self.by_ct, job.company_name, job.job_title):
            return "indeed_queue_ct"

        if _ctl_key_in_set(self.by_glassdoor_applied_ctl, job.company_name, job.job_title, job.location or ""):
            return "glassdoor_applied_ctl"
        if _ct_key_in_set(self.by_glassdoor_applied_ct, job.company_name, job.job_title):
            return "glassdoor_applied_ct"

        if ct.strip("|") and ct in self.by_email_ct:
            return "indeed_email_ct"
        # Email CT with company containment (same title, Ltd. vs short name)
        st = _soft_title(job.job_title)
        sc = _soft_company(job.company_name)
        if st and sc and st not in {"unknown", ""}:
            for ect in self.by_email_ct:
                if "|" not in ect:
                    continue
                ec, et = ect.split("|", 1)
                if et == st and _companies_soft_match(sc, ec):
                    return "indeed_email_ct"

        # Title-only email match (exact only). Substring containment caused false
        # skips e.g. email "service technician" / "junior it service technician (tier 1)"
        # ⊂ Glassdoor "senior it service technician (tier 2)" at Smartt.
        # Common titles ("IT Support Technician") + company Unknown still risk
        # over-skip; prefer CT when company is known.
        if st and st not in {"unknown", ""} and st in self.by_email_title:
            # If job has a real company, require email CT (or company match) —
            # bare title match only when company is missing/unknown on the job.
            if not sc or sc in {"unknown", ""}:
                return "indeed_email_title"
            # Job has company: only skip if some email row has same title AND
            # unknown company (Indeed often omits employer) — still risky for
            # generic titles; require title length uniqueness (>= 28 chars) or
            # distinctive punctuation/parentheses (tier/level codes).
            if len(st) >= 28 or "(" in st or "#" in st:
                return "indeed_email_title"

        sid = normalize_indeed_job_id(job.source_job_id)
        if sid and sid in self.history_ids:
            return "indeed_history_id"
        # Indeed commonly rotates the listing id when it reposts a role.  The
        # legacy applied ledger gives us an exact, normalised company+title
        # match to close that gap.  Do not use containment/fuzzy matching here:
        # this guard runs before a real application attempt.
        if (_soft_location(job.location)
                and _ctl_key(job.company_name, job.job_title, job.location) in self.history_ctl):
            return "indeed_history_ctl"
        if (not _soft_location(job.location)
                and _ct_key(job.company_name, job.job_title) in self.history_ct):
            return "indeed_history_ct"
        if sid and sid in self.by_source_id:
            return "indeed_source_id"

        return None

    def match_known_indeed(self, job: NormalizedJob) -> str | None:
        """Return a pre-screen skip reason for a direct Indeed listing.

        Discovery must not spend an AI screening call on a listing already
        recorded in the candidate's queue, Indeed application history, or
        confirmation-email ledger.  This deliberately avoids the
        ``source_refs_include_indeed`` check used for cross-portal sync: every
        direct Indeed listing naturally has an Indeed source ref.
        """
        if (job.source_platform or "").strip().lower() != _PLATFORM_INDEED:
            return None

        sid = normalize_indeed_job_id(job.source_job_id)
        if sid and sid in self.history_ids:
            return "indeed_history_id"
        if (_soft_location(job.location)
                and _ctl_key(job.company_name, job.job_title, job.location) in self.history_ctl):
            return "indeed_history_ctl"
        if (not _soft_location(job.location)
                and _ct_key(job.company_name, job.job_title) in self.history_ct):
            return "indeed_history_ct"
        if sid and sid in self.by_source_id:
            return "indeed_queue_source_id"

        for u in (job.destination_url, job.listing_url):
            cu = _canonical_url(u)
            if cu and cu in self.by_url:
                return "indeed_queue_url"

        ctl = _ctl_key(job.company_name, job.job_title, job.location)
        if _ctl_key_in_set(self.by_ctl, job.company_name, job.job_title, job.location or ""):
            return "indeed_queue_ctl"
        ct = _ct_key(job.company_name, job.job_title)
        if _ct_key_in_set(self.by_ct, job.company_name, job.job_title):
            return "indeed_queue_ct"

        if ct.strip("|") and ct in self.by_email_ct:
            return "indeed_email_ct"
        st = _soft_title(job.job_title)
        sc = _soft_company(job.company_name)
        if st and sc and st not in {"unknown", ""}:
            for ect in self.by_email_ct:
                if "|" not in ect:
                    continue
                ec, et = ect.split("|", 1)
                if et == st and _companies_soft_match(sc, ec):
                    return "indeed_email_ct"

        st = _soft_title(job.job_title)
        if st and st not in {"unknown", ""} and st in self.by_email_title:
            # Same-portal Indeed: exact title only (no substring — see match_reason).
            return "indeed_email_title"
        return None


def already_synced_with_indeed_family(
    job: NormalizedJob,
    *,
    index: IndeedSyncIndex | None = None,
    queue: Any = None,
) -> tuple[bool, str]:
    """Return ``(True, reason)`` when Glassdoor/Workopolis job should not enqueue."""
    if (job.source_platform or "").strip().lower() not in _SYNC_SOURCE_PLATFORMS:
        return False, ""
    idx = index or IndeedSyncIndex(queue=queue)
    reason = idx.match_reason(job)
    if reason:
        return True, reason
    return False, ""


def glassdoor_already_on_indeed(
    job: NormalizedJob,
    *,
    index: IndeedSyncIndex | None = None,
    queue: Any = None,
) -> tuple[bool, str]:
    """Return ``(True, reason)`` when this Glassdoor job should not be enqueued."""
    if (job.source_platform or "").strip().lower() != "glassdoor":
        return False, ""
    return already_synced_with_indeed_family(job, index=index, queue=queue)


def workopolis_already_on_indeed(
    job: NormalizedJob,
    *,
    index: IndeedSyncIndex | None = None,
    queue: Any = None,
) -> tuple[bool, str]:
    """Return ``(True, reason)`` when this Workopolis job should not be enqueued."""
    if (job.source_platform or "").strip().lower() != "workopolis":
        return False, ""
    return already_synced_with_indeed_family(job, index=index, queue=queue)

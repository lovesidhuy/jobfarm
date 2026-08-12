"""Dedupe Greenhouse/Lever leads against prior successful applies + IMAP.

Sources (best-effort, offline-first):
  1. ``artifacts/wave-google-ats/ats_apply_results_*.json`` (local canary truth)
  2. Mongo ``application_queue`` status applied/already_applied for GH/Lever URLs
  3. IMAP confirmation history (CSV + Mongo ``email_applied_history``)
  4. Optional explicit extra URL sets

Email rules (emails = truth; avoid false skips):
  * Prefer soft ``company|title`` when both are known.
  * For Greenhouse/Lever receipts with unknown title: soft **company** match only
    (never title-substring alone — Smartt-style false skips).
  * Company match allows Ltd/Inc stripping + short containment (≥4 chars).

Canonical URL form matches ``google_cdp_provider.canonicalize_ats_url`` when available.
"""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any, Iterable
from urllib.parse import urlparse

_SUCCESS_REASON_RE = re.compile(
    r"(submitted|already applied|already confirmed|imap code|application received|"
    r"received your application|thanks for applying)",
    re.IGNORECASE,
)

_ATS_HOST_HINT = re.compile(
    r"(greenhouse\.io|jobs\.lever\.co|lever\.co|grnh\.se|gh\.io|ashbyhq\.com|bamboohr\.com)",
    re.IGNORECASE,
)

_COMPANY_SUFFIX_RE = re.compile(
    r"\b(ltd\.?|limited|inc\.?|incorporated|llc|corp\.?|corporation|co\.?|"
    r"company|plc|ulc|lp|llp)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s&/+-]+", re.UNICODE)
_UNKNOWN = frozenset({"", "unknown", "n/a", "none", "null", "do not reply", "noreply"})

_ATS_EMAIL_PLATFORMS = frozenset({"greenhouse", "lever", "ashby", "bamboohr"})
_ATS_SENDER_HINT = re.compile(
    r"(greenhouse-mail\.io|hire\.lever\.co|boards\.greenhouse|jobs\.lever|ashbyhq\.com|bamboohr\.com)",
    re.IGNORECASE,
)


def _repo_root() -> Path:
    return _MONOREPO_ROOT


def canonicalize_ats_url(url: str | None) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    try:
        from jobbots.core.discovery.providers.google_cdp_provider import (
            canonicalize_ats_url as _canon,
        )

        out = _canon(raw)
        if out:
            return out
    except Exception:
        pass
    try:
        p = urlparse(raw.split("#", 1)[0].split("?", 1)[0])
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = (p.path or "").rstrip("/")
        if not host:
            return ""
        return f"https://{host}{path}"
    except Exception:
        return raw.rstrip("/")


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def soft_company(company: str) -> str:
    s = _normalize_text(company)
    s = _COMPANY_SUFFIX_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Drop pure email-looking company fields from bad parsers.
    if "@" in s:
        return ""
    return s


def soft_title(title: str) -> str:
    s = _normalize_text(title)
    s = re.sub(r"\s[#-]?\d{3,}[a-z0-9-]*\s*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def ct_key(company: str, title: str) -> str:
    return f"{soft_company(company)}|{soft_title(title)}"


def companies_soft_match(a: str, b: str) -> bool:
    sa, sb = soft_company(a), soft_company(b)
    if not sa or not sb or sa in _UNKNOWN or sb in _UNKNOWN:
        return False
    if sa == sb:
        return True
    short, long = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
    if len(short) < 4:
        return False
    return short in long or long.startswith(short + " ")


def _job_url(job: dict[str, Any]) -> str:
    for k in ("apply_url", "url", "result_url", "final", "destination_url", "listing_url"):
        v = (job.get(k) or "").strip()
        if v:
            return v
    return ""


def is_success_result(row: dict[str, Any]) -> bool:
    """True when a prior apply attempt should block re-apply."""
    if bool(row.get("ok")):
        return True
    reason = str(row.get("reason") or row.get("status") or "")
    if _SUCCESS_REASON_RE.search(reason):
        return True
    status = str(row.get("status") or "").lower()
    if status in {"applied", "already_applied", "submitted"}:
        return True
    if row.get("already") is True:
        return True
    return False


def load_applied_urls_from_artifacts(
    artifacts_dir: Path | None = None,
) -> set[str]:
    root = artifacts_dir or (_repo_root() / "artifacts" / "wave-google-ats")
    applied: set[str] = set()
    if not root.is_dir():
        return applied
    for path in root.glob("ats_apply_results_*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for row in data.get("results") or []:
            if not isinstance(row, dict):
                continue
            if not is_success_result(row):
                continue
            for key in ("apply_url", "url", "result_url"):
                canon = canonicalize_ats_url(row.get(key))
                if canon:
                    applied.add(canon)
    overnight = root / "overnight_open_leads.json"
    if overnight.is_file():
        try:
            rows = json.loads(overnight.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            rows = []
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                if row.get("already") is True or is_success_result(row):
                    for key in ("url", "final", "apply_url"):
                        canon = canonicalize_ats_url(row.get(key))
                        if canon:
                            applied.add(canon)
    return applied


def load_applied_urls_from_mongo() -> set[str]:
    """Best-effort: GH/Lever URLs already applied in application_queue."""
    applied: set[str] = set()
    try:
        from jobbots.core.job_queue import JobQueue  # type: ignore

        q = JobQueue()
        coll = getattr(q, "col", None) or getattr(q, "collection", None)
        if coll is None and hasattr(q, "db"):
            coll = q.db.get_collection("application_queue")
        if coll is None:
            return applied
        cursor = coll.find(
            {
                "status": {"$in": ["applied", "already_applied", "bookmarked"]},
                "$or": [
                    {"url": {"$regex": "greenhouse|lever\\.co|grnh\\.se|ashbyhq|bamboohr", "$options": "i"}},
                    {"apply_url": {"$regex": "greenhouse|lever\\.co|grnh\\.se|ashbyhq|bamboohr", "$options": "i"}},
                    {"result_url": {"$regex": "greenhouse|lever\\.co|grnh\\.se|ashbyhq|bamboohr", "$options": "i"}},
                ],
            },
            {"url": 1, "apply_url": 1, "result_url": 1, "status": 1},
        ).limit(5000)
        for row in cursor:
            for key in ("apply_url", "url", "result_url"):
                canon = canonicalize_ats_url(row.get(key))
                if canon and _ATS_HOST_HINT.search(canon):
                    applied.add(canon)
    except Exception:
        return applied
    return applied


def load_applied_ats_urls(
    *,
    artifacts_dir: Path | None = None,
    include_mongo: bool = True,
    extra: Iterable[str] | None = None,
) -> set[str]:
    applied = load_applied_urls_from_artifacts(artifacts_dir)
    if include_mongo:
        applied |= load_applied_urls_from_mongo()
    if extra:
        for u in extra:
            c = canonicalize_ats_url(u)
            if c:
                applied.add(c)
    return applied


def _clean_email_company(company: str, subject: str = "", sender: str = "") -> str:
    c = (company or "").strip()
    if not c or c.lower() in _UNKNOWN or "@" in c:
        # Recover from subject when parser put sender into company.
        m = re.search(
            r"(?:thank(?:s)? you for (?:your )?application(?: to)?|"
            r"thank(?:s)? for applying(?: to| at)?|"
            r"application to)\s+(.+)$",
            subject or "",
            re.I,
        )
        if m:
            c = m.group(1).strip().strip("!")
        else:
            # "X - Thanks for Applying!"
            m = re.search(r"^(.+?)\s*[-:]\s*thanks for applying", subject or "", re.I)
            if m:
                c = m.group(1).strip()
    # Sender "Company <no-reply@hire.lever.co>"
    if (not c or c.lower() in _UNKNOWN or "@" in c) and sender:
        m = re.match(r"^([^<@]+)\s*<", sender.strip())
        if m:
            cand = m.group(1).strip().strip('"')
            if cand and "@" not in cand and cand.lower() not in _UNKNOWN:
                c = cand
    # Strip bilingual / noise tails
    c = re.split(r"\s*\|\s*", c)[0].strip()
    c = re.sub(r"[\U0001F300-\U0001FAFF]+", "", c).strip()
    return c


def _is_ats_email_row(platform: str, sender: str, subject: str) -> bool:
    p = (platform or "").strip().lower()
    if p in _ATS_EMAIL_PLATFORMS:
        return True
    blob = f"{sender} {subject}"
    return bool(_ATS_SENDER_HINT.search(blob))


@dataclass
class EmailAppliedIndex:
    """IMAP confirmation index for ATS lead skips."""

    by_ct: set[str] = field(default_factory=set)
    by_company_ats: set[str] = field(default_factory=set)  # GH/Lever company-only
    by_company_any: set[str] = field(default_factory=set)  # all platforms, company known
    row_count: int = 0
    ats_row_count: int = 0
    sources: list[str] = field(default_factory=list)

    def add_row(
        self,
        *,
        company: str,
        title: str = "",
        platform: str = "",
        sender: str = "",
        subject: str = "",
    ) -> None:
        self.row_count += 1
        company = _clean_email_company(company, subject=subject, sender=sender)
        sc = soft_company(company)
        st = soft_title(title)
        if sc and sc not in _UNKNOWN:
            self.by_company_any.add(sc)
            if st and st not in _UNKNOWN:
                self.by_ct.add(f"{sc}|{st}")
            if _is_ats_email_row(platform, sender, subject):
                self.ats_row_count += 1
                self.by_company_ats.add(sc)

    def match_reason(self, company: str, title: str) -> str | None:
        sc = soft_company(company)
        st = soft_title(title)
        if not sc or sc in _UNKNOWN:
            return None
        # 1) Exact company|title
        if st and st not in _UNKNOWN:
            key = f"{sc}|{st}"
            if key in self.by_ct:
                return "email_company_title"
            # Same title + company containment
            for other in self.by_ct:
                if "|" not in other:
                    continue
                oc, ot = other.split("|", 1)
                if ot == st and companies_soft_match(sc, oc):
                    return "email_company_title"
        # 2) GH/Lever company-only (title often Unknown in IMAP)
        for oc in self.by_company_ats:
            if companies_soft_match(sc, oc):
                return "email_company_ats"
        return None


def load_email_applied_index(
    *,
    csv_path: Path | None = None,
    include_mongo: bool = True,
) -> EmailAppliedIndex:
    idx = EmailAppliedIndex()
    path = csv_path or (_repo_root() / "all excels" / "email_applied_history.csv")
    if path.is_file():
        idx.sources.append(str(path))
        try:
            with path.open(encoding="utf-8", errors="replace", newline="") as fh:
                reader = csv.DictReader(fh)
                for row in reader:
                    idx.add_row(
                        company=row.get("company_name") or "",
                        title=row.get("job_title") or "",
                        platform=row.get("source_platform") or "",
                        sender=row.get("sender") or "",
                        subject=row.get("subject") or "",
                    )
        except Exception:
            pass

    if include_mongo:
        try:
            from jobbots.core.job_queue import JobQueue  # type: ignore

            q = JobQueue()
            db = getattr(q, "db", None)
            if db is not None:
                idx.sources.append("mongo:email_applied_history")
                for row in db["email_applied_history"].find(
                    {},
                    {
                        "company_name": 1,
                        "job_title": 1,
                        "source_platform": 1,
                        "sender": 1,
                        "subject": 1,
                    },
                ).limit(20000):
                    idx.add_row(
                        company=row.get("company_name") or "",
                        title=row.get("job_title") or "",
                        platform=row.get("source_platform") or "",
                        sender=row.get("sender") or "",
                        subject=row.get("subject") or "",
                    )
        except Exception:
            pass
    return idx


def refresh_email_applied_from_imap(*, days: int = 30) -> dict[str, Any]:
    """Best-effort live IMAP sync into CSV/Mongo via existing script helpers."""
    stats: dict[str, Any] = {"ok": False, "days": days}
    try:
        # Prefer discovery helper if present (already used in planner).
        from jobbots.core.discovery.email_history_refresh import refresh_email_applied_history

        out = refresh_email_applied_history(days=days)
        stats["ok"] = True
        stats["via"] = "email_history_refresh"
        stats["result"] = out if isinstance(out, dict) else {"raw": str(out)[:200]}
        return stats
    except Exception as exc:
        stats["refresh_error"] = f"{type(exc).__name__}: {exc}"
    # Fallback: run sync script main path lightly if importable.
    try:
        import scripts.sync_imap_applied_data as sync  # type: ignore

        if hasattr(sync, "main"):
            # Don't hijack CLI; just report fallback available.
            stats["fallback"] = "scripts.sync_imap_applied_data available"
    except Exception as exc:
        stats["fallback_error"] = f"{type(exc).__name__}: {exc}"
    return stats


# Lightweight fallback only if hard gates cannot import (tests / broken path).
_IT_TITLE_KEEP_RE = re.compile(
    r"(?i)(?:"
    r"\bqa\b|\bsdet\b|quality assurance|test engineer|software test|test analyst|"
    r"it support|help\s*desk|service desk|desktop support|technical support|"
    r"systems?\s+admin(?:istrator)?s?|\bsysadmin\b|"
    r"network\s+(?:support|tech(?:nician)?|admin(?:istrator)?s?)|"
    r"\bit\s+(?:analyst|specialist|coordinator|administrator|technician|intern|co-?op|student|operations)\b|"
    r"junior\s+(?:it|software|devops|data|sys|developer|engineer)\b|"
    r"cloud support|soc analyst|security analyst|"
    r"support specialist|support analyst|support engineer|support technician|"
    r"\bdesktop\b|\bendpoint\b|\bhelpdesk\b|"
    r"software developer intern|software engineer co-?op"
    r")"
)
_IT_TITLE_REJECT_RE = re.compile(
    r"\b("
    r"sales|account manager|\btam\b|threat engineer|staff software|principal|"
    r"director|vp\b|vice president|brand and communications|material handler|"
    r"customer support representative|\bcsr\b|production controller|materials planning|"
    r"qc equipment|product manager|solutions engineer|"
    r"electrical|power engineer|civil engineer|mechanical engineer|"
    r"marketing|finance|financial analyst|recruiter|people ops|"
    r"veterinary|registered vet|mandarin|bilingual french"
    r")\b",
    re.I,
)


def is_it_persona_title(title: str) -> bool:
    """Fallback IT title check (prefer ``hard_screen_job`` via gates)."""
    t = title or ""
    if not t.strip():
        return False
    if _IT_TITLE_REJECT_RE.search(t) and not _IT_TITLE_KEEP_RE.search(t):
        return False
    if not _IT_TITLE_KEEP_RE.search(t):
        return False
    if re.search(r"\b(staff|principal)\b", t, re.I) and not re.search(
        r"\b(qa|sdet|support|it |help|service desk|admin)\b", t, re.I
    ):
        return False
    return True


def _job_location_blob(job: dict[str, Any]) -> str:
    parts = [
        job.get("location") or "",
        job.get("title") or "",
        (job.get("description") or job.get("snippet") or "")[:400],
    ]
    return " ".join(str(p) for p in parts if p)


def passes_metro_van_policy(job: dict[str, Any]) -> tuple[bool, str]:
    """Metro-Vancouver-first geo policy for GH/Lever company-site leads.

    Keep:
      * Metro Van (any work mode) — GH/Lever fill is company-site APPLY path
      * Outside metro only if location is *confirmed* fully remote (Canada remote OK)

    Reject:
      * Outside metro hybrid/onsite/unknown
      * Explicit foreign cities (Sydney, etc.) in title
    """
    title = job.get("title") or ""
    location = (job.get("location") or "").strip()
    description = (job.get("description") or job.get("snippet") or "")[:1500]
    blob = f"{title} {location} {description}"

    # Hard foreign / exclusive out-of-area city in title (Tavily / Google SERP
    # often stamps the search centre as location while the title is exclusive).
    if re.search(
        r"\b("
        r"sydney|melbourne|dublin|london|singapore|tokyo|bangalore|bengaluru|"
        r"mexico\s+only|mexico\s+city|guadalajara|monterrey|"
        r"quebec\s+city|province\s+of\s+quebec|montr[eé]al|"
        r"toronto|ottawa|calgary|edmonton|winnipeg|halifax"
        r")\b",
        title,
        re.I,
    ) and not re.search(
        r"\b(vancouver|burnaby|surrey|richmond|coquitlam|metro\s+vancouver)\b",
        title,
        re.I,
    ):
        return False, "outside_metro_foreign_city_title"

    # Finance / non-IT discipline hard rejects even when "analyst/systems" present
    if re.search(
        r"\b("
        r"equity|finance systems?|financial|accounting|treasury|audit|"
        r"investment|portfolio|revenue|billing|accounts payable|accounts receivable|"
        r"mandarin|cantonese|bilingual french|french required"
        r")\b",
        f"{title} {description[:400]}",
        re.I,
    ) and not re.search(r"\b(qa|sdet|help desk|service desk|it support|sysadmin)\b", title, re.I):
        return False, "non_it_finance_or_language"

    try:
        from jobbots.core.discovery.classification.location_policy import (
            ACTION_REJECT,
            REGION_METRO_VAN,
            REGION_OTHER,
            WORK_HYBRID,
            WORK_REMOTE,
            classify_region,
            detect_work_mode,
            decide_job_policy,
        )
        from jobbots.core.discovery.contracts import NormalizedJob
    except Exception as exc:
        # Fallback: require Metro Van token in location/title
        if re.search(
            r"\b(vancouver|burnaby|surrey|richmond|coquitlam|north vancouver|"
            r"new westminster|langley|delta|lower mainland|metro vancouver|,?\s*bc\b|canada)\b",
            blob,
            re.I,
        ):
            return True, f"metro_fallback:{exc.__class__.__name__}"
        return False, f"metro_fallback_reject:{exc.__class__.__name__}"

    # Prefer explicit location field. Search-centre alone is OK only as last resort
    # after title/content extraction in tavily_ats; still require not foreign title.
    loc = location or "Vancouver, BC"
    region = classify_region(loc)
    if region != REGION_METRO_VAN:
        region_blob = classify_region(blob)
        if region_blob == REGION_METRO_VAN:
            region = REGION_METRO_VAN
            loc = blob[:120]

    work_mode = detect_work_mode(loc, description, is_remote_hint=bool(job.get("is_remote")))
    # GH/Lever company-site ATS — not Easy Apply.
    # Use decide_job_policy for outside-metro remote rules; metro company-site → keep.
    nj = NormalizedJob(
        source_platform="google",
        source_job_id=canonicalize_ats_url(job.get("apply_url") or job.get("url") or "") or "unknown",
        discovery_engine="tavily_ats",
        query_id="ats_lead",
        job_title=title or "Unknown",
        company_name=job.get("company") or "Unknown",
        location=loc,
        description=description,
        date_posted=None,
        listing_url=job.get("apply_url") or job.get("url") or "",
        destination_url=job.get("apply_url") or job.get("url") or "",
        apply_type="COMPANY_APPLY",
        apply_type_source="ats_url",
        apply_type_confidence=1.0,
        verification_required=False,
        apply_type_confirmed=True,
        is_remote_hint=work_mode == WORK_REMOTE,
    )
    decision = decide_job_policy(nj)
    if decision.action == ACTION_REJECT:
        # Company-site outside metro is REJECT under Indeed-family policy — keep that.
        return False, f"geo_policy:{decision.reason}"
    # Metro company-site is SAVE for Indeed-family; for ATS we still keep (fill+submit).
    if region == REGION_METRO_VAN or decision.keep:
        return True, f"geo_policy:{decision.reason}"
    if region == REGION_OTHER and work_mode == WORK_HYBRID:
        return False, "geo_policy:outside_metro_hybrid"
    return decision.keep, f"geo_policy:{decision.reason}"


def passes_it_hard_gate(job: dict[str, Any]) -> tuple[bool, str, int]:
    """Run Phase-I ``hard_screen_job`` (Indeed IT gates) on an ATS lead."""
    title = job.get("title") or ""
    company = job.get("company") or ""
    location = job.get("location") or ""
    description = job.get("description") or job.get("snippet") or ""

    # Pre-gate discipline rejects (finance/language) — same persona as hard gates.
    if re.search(
        r"\b("
        r"equity analyst|finance systems?|financial analyst|accounting|"
        r"investment analyst|portfolio|mandarin|cantonese|bilingual french"
        r")\b",
        title,
        re.I,
    ):
        return False, "hard gate: non-IT finance/language title", 0
    if re.search(
        r"\b(engine programmer|gameplay programmer|graphics programmer)\b",
        title,
        re.I,
    ):
        return False, "hard gate: non-software programmer domain", 0

    try:
        from jobbots.core.discovery._gate_adapter import hard_screen_job, is_ambiguous_title_reason

        ok, score, reason = hard_screen_job(
            title=title,
            company=company,
            description=description,
            location=location,
            easy_apply=False,  # GH/Lever = company-site path gates
        )
        if ok:
            return True, reason, score
        # Ambiguous titles: keep only if soft IT persona matches (then optional batch AI later)
        if is_ambiguous_title_reason(reason) and is_it_persona_title(title):
            return True, f"ambiguous_kept_soft_it:{reason}", score
        return False, reason, score
    except Exception as exc:
        # Fallback soft title if gates cannot load
        if is_it_persona_title(title):
            return True, f"soft_it_fallback:{exc.__class__.__name__}", 50
        return False, f"soft_it_fallback_reject:{exc.__class__.__name__}", 0


def filter_it_persona_jobs(
    jobs: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(kept, rejected)`` using soft title regex (legacy/tests)."""
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        title = job.get("title") or ""
        if is_it_persona_title(title):
            kept.append(job)
        else:
            rejected.append({**job, "filter_skip": "not_it_title"})
    return kept, rejected


def screen_ats_leads_with_gates(
    jobs: Iterable[dict[str, Any]],
    *,
    use_hard_gate: bool = True,
    use_metro_policy: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Metro Van policy + IT hard gates for GH/Lever leads."""
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        out = dict(job)
        if use_metro_policy:
            ok_geo, geo_reason = passes_metro_van_policy(out)
            out["geo_reason"] = geo_reason
            if not ok_geo:
                out["filter_skip"] = "geo_policy"
                out["dedupe_skip"] = f"geo_policy:{geo_reason}"
                rejected.append(out)
                continue
        if use_hard_gate:
            ok_it, it_reason, score = passes_it_hard_gate(out)
            out["gate_reason"] = it_reason
            out["gate_score"] = score
            if not ok_it:
                out["filter_skip"] = "hard_gate"
                out["dedupe_skip"] = f"hard_gate:{it_reason}"
                rejected.append(out)
                continue
        elif not is_it_persona_title(out.get("title") or ""):
            out["filter_skip"] = "not_it_title"
            out["dedupe_skip"] = "not_it_title"
            rejected.append(out)
            continue
        kept.append(out)
    return kept, rejected


def finalize_ats_leads(
    jobs: Iterable[dict[str, Any]],
    *,
    artifacts_dir: Path | None = None,
    include_mongo: bool = False,
    include_email: bool = True,
    refresh_imap: bool = False,
    it_only: bool = True,
    use_gates: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """URL/IMAP dedupe + Metro Van policy + IT hard gates.

    Returns ``(fresh, skipped, stats)``.
    """
    fresh, skipped, applied = filter_fresh_jobs(
        jobs,
        artifacts_dir=artifacts_dir,
        include_mongo=include_mongo,
        include_email=include_email,
        refresh_imap=refresh_imap,
    )
    gate_rejected: list[dict[str, Any]] = []
    if it_only:
        if use_gates:
            fresh, gate_rejected = screen_ats_leads_with_gates(fresh)
        else:
            fresh, gate_rejected = filter_it_persona_jobs(fresh)
            for row in gate_rejected:
                row.setdefault("dedupe_skip", row.get("filter_skip") or "not_it_title")
        skipped = list(skipped) + list(gate_rejected)
    stats = {
        "applied_index": len(applied),
        "fresh": len(fresh),
        "skipped": len(skipped),
        "skipped_title": len(gate_rejected),
        "skipped_geo": sum(1 for r in gate_rejected if str(r.get("filter_skip")) == "geo_policy"),
        "skipped_hard_gate": sum(1 for r in gate_rejected if str(r.get("filter_skip")) == "hard_gate"),
        "it_only": bool(it_only),
        "use_gates": bool(use_gates and it_only),
    }
    return fresh, skipped, stats


def filter_fresh_jobs(
    jobs: Iterable[dict[str, Any]],
    *,
    applied_urls: set[str] | None = None,
    artifacts_dir: Path | None = None,
    include_mongo: bool = True,
    include_email: bool = True,
    email_index: EmailAppliedIndex | None = None,
    refresh_imap: bool = False,
    imap_days: int = 30,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[str]]:
    """Return ``(fresh, skipped, applied_url_set)``.

    Skip reasons:
      * ``already_applied`` — prior ATS apply URL / queue URL
      * ``duplicate_in_batch``
      * ``email_company_title`` / ``email_company_ats`` — IMAP confirmation
      * ``empty_url``
    """
    if refresh_imap:
        refresh_email_applied_from_imap(days=imap_days)

    applied = applied_urls if applied_urls is not None else load_applied_ats_urls(
        artifacts_dir=artifacts_dir,
        include_mongo=include_mongo,
    )
    email_idx = email_index
    if include_email and email_idx is None:
        email_idx = load_email_applied_index(include_mongo=include_mongo)

    fresh: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    seen_fresh: set[str] = set()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        raw = _job_url(job)
        canon = canonicalize_ats_url(raw)
        company = job.get("company") or ""
        title = job.get("title") or ""
        if not canon:
            skipped.append(
                {
                    **job,
                    "dedupe_skip": "empty_url",
                    "canonical_url": "",
                }
            )
            continue
        if canon in applied:
            skipped.append(
                {
                    **job,
                    "dedupe_skip": "already_applied",
                    "canonical_url": canon,
                }
            )
            continue
        if email_idx is not None:
            why = email_idx.match_reason(company, title)
            if why:
                skipped.append(
                    {
                        **job,
                        "dedupe_skip": why,
                        "canonical_url": canon,
                    }
                )
                continue
        if canon in seen_fresh:
            skipped.append(
                {
                    **job,
                    "dedupe_skip": "duplicate_in_batch",
                    "canonical_url": canon,
                }
            )
            continue
        seen_fresh.add(canon)
        out = dict(job)
        out["canonical_url"] = canon
        if not out.get("apply_url"):
            out["apply_url"] = raw or canon
        fresh.append(out)
    return fresh, skipped, applied

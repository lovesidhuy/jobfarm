"""Job Bank (jobbank.gc.ca) email apply — screening Qs answered via form_answers + AI.

Discover stores leads in ``scraper_leads``. Apply enqueues them as
``portal=jobbank`` and this module:

  1. Ensures custom screening questions have answers (bank → profile → DeepSeek)
  2. Optionally re-fetches the Job Bank page when answers are missing
  3. Sends the application email with resume/cover + screening block
"""
from __future__ import annotations

import os
import re
import smtplib
import sys
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT
from typing import Any

ROOT = _MONOREPO_ROOT
REPO = ROOT.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(REPO / "scrapers") not in sys.path:
    sys.path.insert(0, str(REPO / "scrapers"))


def _log(msg: str) -> None:
    print(f"[jobbank] {msg}", flush=True)


def screening_block_incomplete(block: str | None) -> bool:
    """True when a screening block lists questions but has blank answers.

    The Job Bank scraper sometimes stores shells like ``Answer:`` with no
    text after AI fails. That non-empty string used to skip regeneration.
    """
    text = (block or "").strip()
    if not text:
        return True
    # Any "Answer:" line with no non-space content after the colon.
    blank_answer = re.search(
        r"(?im)^[ \t]*Answer:[ \t]*(?:\r?\n|$)",
        text,
    )
    if blank_answer:
        return True
    # Question numbered list with zero filled answers.
    if re.search(r"(?im)^\d+\.\s+\S", text) and not re.search(
        r"(?im)^[ \t]*Answer:[ \t]*\S",
        text,
    ):
        return True
    return False


def generate_screening_answers(questions: list[str], job_context: str = "") -> str:
    """Answer Job Bank screening questions using shared form brain (bank + AI)."""
    if not questions:
        return ""
    try:
        from jobbots.core.shared_modules.form_answers import resolve_text
    except Exception as exc:
        _log(f"form_answers unavailable ({exc}); screening answers skipped")
        return ""

    lines: list[str] = []
    for idx, raw_q in enumerate(questions, 1):
        q = (raw_q or "").strip()
        if not q:
            continue
        try:
            ans = resolve_text(
                q,
                hint="Job Bank Canada employer screening question",
                job_context=(job_context or "")[:6000],
                allow_ai=True,
            )
        except Exception as exc:
            _log(f"AI answer failed for Q{idx}: {exc}")
            ans = ""
        ans = (ans or "").strip() or "Please see my resume for details."
        lines.append(f"{idx}. {q}\n   Answer: {ans}")
    return "\n".join(lines)


def fetch_jobbank_screening_questions(url: str) -> tuple[list[str], str]:
    """Headless fetch of Job Bank apply panel questions + page text as context."""
    url = (url or "").strip()
    if not url or "jobbank.gc.ca" not in url.lower():
        return [], ""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        _log(f"playwright missing for re-fetch: {exc}")
        return [], ""

    questions: list[str] = []
    job_desc = ""
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            )
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                job_desc = page.locator("body").inner_text(timeout=10000) or ""
            except Exception:
                job_desc = ""
            if page.locator("#applynowbutton").count() > 0:
                page.click("#applynowbutton", timeout=8000)
                try:
                    page.wait_for_selector("#howtoapply", timeout=8000)
                except Exception:
                    pass
            try:
                from bs4 import BeautifulSoup

                hta = page.locator("#howtoapply").inner_html(timeout=5000)
                soup = BeautifulSoup(hta, "html.parser")
                for li in soup.find_all("li"):
                    if "screening questions" in li.get_text().lower():
                        nested = li.find_all("li")
                        questions = [
                            n.get_text(strip=True)
                            for n in nested
                            if n.get_text(strip=True)
                        ]
                        break
            except Exception as exc:
                _log(f"parse howtoapply failed: {exc}")
            browser.close()
    except Exception as exc:
        _log(f"fetch job page failed: {exc}")
        return [], job_desc
    return questions, job_desc


def ensure_screening_answers(
    *,
    existing: str | None,
    url: str = "",
    title: str = "",
    company: str = "",
    force_refresh: bool = False,
) -> str:
    """Return screening answer block; generate via AI if missing or incomplete."""
    block = (existing or "").strip()
    incomplete = screening_block_incomplete(block)
    if block and not incomplete and not force_refresh:
        return block
    if incomplete and block:
        _log(
            f"screening block incomplete for {title!r} @ {company!r} "
            f"({len(block)} chars) — regenerating via form_answers/AI"
        )

    questions, job_desc = fetch_jobbank_screening_questions(url)
    if not questions:
        # Try to recover questions from the incomplete shell itself.
        if incomplete and block:
            recovered = re.findall(r"(?im)^\d+\.\s+(.+)$", block)
            questions = [q.strip() for q in recovered if q.strip() and not q.strip().lower().startswith("answer:")]
        if not questions:
            # No custom questions on posting — empty block is fine.
            return "" if incomplete else block

    ctx = job_desc or f"{title} at {company}\n{url}"
    generated = generate_screening_answers(questions, ctx)
    if generated and not screening_block_incomplete(generated):
        return generated
    # Last resort: fill recovered questions with safe defaults so we never
    # ship blank "Answer:" lines to employers.
    if questions:
        safe = []
        for idx, q in enumerate(questions, 1):
            safe.append(
                f"{idx}. {q}\n   Answer: Yes — please see my attached resume for details."
            )
        return "\n".join(safe)
    return block if not incomplete else ""


def _resume_paths() -> tuple[Path, Path]:
    data = (os.environ.get("JOBBOTS_DATA_DIR") or "").strip()
    if data:
        base = Path(data) / "all resumes"
    else:
        base = ROOT / "all resumes"
    return base / "ls_resume_it.pdf", base / "cover_ls_it.pdf"


def _attach(msg: MIMEMultipart, path: Path) -> None:
    if not path.is_file():
        _log(f"attachment missing: {path}")
        return
    with path.open("rb") as f:
        part = MIMEBase("application", "octet-stream")
        part.set_payload(f.read())
    encoders.encode_base64(part)
    part.add_header("Content-Disposition", f"attachment; filename={path.name}")
    msg.attach(part)


def build_email_body(
    *,
    role: str,
    company: str,
    source: str = "jobbank",
    screening_answers: str = "",
) -> str:
    try:
        import lss_helper

        body = lss_helper.get_tailored_body(role, company, source)
    except Exception:
        body = (
            f"Dear Hiring Manager,\n\n"
            f"I am writing to apply for the {role} position at {company}. "
            f"Please find my resume and cover letter attached.\n\n"
            f"Best regards,\nJane Doe"
        )
    if screening_answers.strip():
        body += f"\n\nAnswers to screening questions:\n{screening_answers.strip()}"
    return body


def send_application_email(
    *,
    to_email: str,
    role: str,
    company: str,
    subject: str = "",
    screening_answers: str = "",
    source: str = "jobbank",
    dry_run: bool = False,
) -> tuple[bool, str]:
    """Send one Job Bank application email. Returns (ok, reason)."""
    from jobbots.core.secret_manager import get_secret

    to_email = (to_email or "").strip().lower()
    if not to_email or "@" not in to_email:
        return False, "missing_recipient_email"

    sender = (get_secret("IMAP_EMAIL_IT") or "").strip()
    password = (get_secret("IMAP_APP_PASSWORD_IT") or "").strip()
    if not sender or not password:
        return False, "missing_imap_credentials"

    subj = (subject or f"Application for {role}").strip()
    body = build_email_body(
        role=role, company=company, source=source, screening_answers=screening_answers
    )
    if dry_run:
        _log(f"DRY-RUN to={to_email} subject={subj!r} screening_chars={len(screening_answers)}")
        return True, "dry_run"

    msg = MIMEMultipart()
    msg["From"] = f"Jane Doe <{sender}>"
    msg["To"] = to_email
    msg["Subject"] = subj
    msg.attach(MIMEText(body, "plain"))
    resume, cover = _resume_paths()
    _attach(msg, resume)
    _attach(msg, cover)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=60) as server:
            server.starttls()
            server.login(sender, password)
            server.sendmail(sender, to_email, msg.as_string())
    except Exception as exc:
        return False, f"smtp_failed:{exc}"

    try:
        import lss_helper

        lss_helper.update_lead_status(to_email, "Sent")
    except Exception as exc:
        _log(f"lead status update failed: {exc}")
    return True, "sent"


def apply_jobbank_queue_job(job: dict[str, Any], *, dry_run: bool = False) -> tuple[bool, str]:
    """Full apply path for one application_queue job (portal=jobbank)."""
    meta = dict(job.get("metadata") or {})
    to_email = (
        meta.get("to_email")
        or meta.get("email")
        or job.get("email")
        or ""
    ).strip()
    role = (job.get("title") or meta.get("role") or "IT role").strip()
    company = (job.get("company") or "Hiring Team").strip()
    url = (job.get("url") or "").strip()
    source = (meta.get("scraper_source") or meta.get("source") or "jobbank").strip()
    subject = (meta.get("subject") or f"Application for {role}").strip()

    existing = (meta.get("screening_answers") or "").strip()
    force = str(os.environ.get("JOBBANK_REFRESH_SCREENING") or "").lower() in {
        "1", "true", "yes", "on",
    }
    screening = ensure_screening_answers(
        existing=existing,
        url=url,
        title=role,
        company=company,
        force_refresh=force or not existing,
    )
    if screening and screening != existing:
        meta["screening_answers"] = screening
        meta["screening_answers_generated"] = True
        try:
            from jobbots.core.job_queue import JobQueue

            JobQueue().jobs.update_one(
                {"_id": job.get("id") or job.get("_id")},
                {"$set": {
                    "metadata.screening_answers": screening,
                    "metadata.screening_answers_generated": True,
                }},
            )
        except Exception as exc:
            _log(f"could not persist screening answers: {exc}")

    ok, reason = send_application_email(
        to_email=to_email,
        role=role,
        company=company,
        subject=subject,
        screening_answers=screening,
        source=source,
        dry_run=dry_run,
    )
    return ok, reason

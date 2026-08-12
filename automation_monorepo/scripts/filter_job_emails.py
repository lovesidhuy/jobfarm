#!/usr/bin/env python3
"""Filter raw IMAP email dumps down to job-related messages."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
DEFAULT_ARCHIVE_DIR = REPO_ROOT / "email_archive"
ACCOUNTS = ("it", "general")

# Domains or domain fragments that strongly indicate job/ATS mail.
JOB_PLATFORM_DOMAIN_FRAGMENTS = (
    "indeed.com",
    "indeedemail.com",
    "glassdoor.com",
    "linkedin.com",
    "greenhouse-mail.io",
    "greenhouse-jobs.com",
    "myworkday.com",
    "otp.workday.com",
    "workday.",
    "ashbyhq.com",
    "icims.com",
    "lever.co",
    "jobvite.com",
    "workablemail.com",
    "smartrecruiters.com",
    "smartrecruiters.app",
    "bamboohr.com",
    "rippling.com",
    "adp.com",
    "dayforce.com",
    "applytojob.com",
    "jobrapido",
    "whatjobs.com",
    "careerbeacon.com",
    "naukri.com",
    "jobleads.com",
    "gethired.com",
    "applicantemails.com",
    "applicant-tracking.com",
    "talexahr.com",
    "successfactors",
    "teamtailor-mail.com",
    "trakstar.com",
    "recruitee.com",
    "brainhunter.com",
    "jobs2web.com",
    "jobalerts.",
    "amazon.jobs",
    "raisejobs.com",
    "drivehrisnotifications.com",
    "paylocity.com",
    "employmenthero.com",
    "talentsphere.ca",
    "bcjobs.ca",
    "pitchnhire.com",
    "centiro.",
    "varicent.com",
    "covergo.com",
    "applytoeducation.com",
    "careers.",
    "recruiting.",
    "hire.",
    "talent.",
    "jobgether.com",
    "digitalrecruiters.com",
    "obrajobs.com",
    "refer.io",
    "experis.ca",
    "cfp-psc.gc.ca",
    "seaspan.com",
    "hatch.com",
    "tucows.com",
    "telusdigital.com",
    "lightspeedhq.com",
    "asana.com",
    "lululemon.com",
    "tesla.com",
    "rivian",
    "point.com",
    "recooty.com",
    "medmehealth.",
    "go-lifted.com",
    "owlco.",
    "postmedia.com",
    "bioscriptsolutions.com",
    "npaworldwide.com",
    "gem.com",
    "boomi.com",
    "cloudbeds.com",
    "prolific.com",
    "hibob.com",
    "careerplug.com",
    "fasken.com",
    "cima.ca",
    "ml6.ca",
    "creyos.com",
    "cimicgrouprecruiting.com",
    "westlandinsurance.ca",
    "axelon.com",
    "jsheld.com",
    "calderwoodsearch.com",
    "aventyrsecurity.com",
    "veriswap.com",
    "pitchnhire.com",
    "workspacerecruit.com",
    "fnha.ca",
    "bccfe.ca",
    "metrovancouver.org",
    "hr.tetratech.com",
    "delta.ca",
    "surrey.ca",
    "invalidemail.com",
    "clientconnections.com",
    "iemfg.com",
    "mmc.com",
)

# Exact domains that are never job mail (retail, social, dev tooling, etc.).
DROP_DOMAINS = {
    "github.com",
    "travis-ci.com",
    "deepnote.com",
    "digital.costco.ca",
    "e.walmart.ca",
    "offers.pizzahut.ca",
    "newsletter.artlist.io",
    "webshare.io",
    "zyte.com",
    "doordash.com",
    "hello.klarna.com",
    "e.thenorthface.com",
    "emails.underarmour.com",
    "beauty.sephora.com",
    "e.affirm.ca",
    "instantink.hpsmart.com",
    "m.parallels.com",
    "dominos.co.in",
    "msg.amazonmusic.com",
    "em.shopify.com",
    "paddle.com",
    "paymentus.com",
    "bchydro.com",
    "paybyphone.com",
    "scotiabank.com",
    "communications.sbi.co.in",
    "alerts.sbi.bank.in",
    "canadalife.com",
    "mail.canadalife.com",
    "e-news.bmo.com",
    "amu.bmo.com",
    "info.bell.ca",
    "info.luckymobile.ca",
    "sjrb.ca",
    "vancity.com",
    "surveys.vancity.com",
    "accounts.google.com",
    "priority.instagram.com",
    "verify.snapchat.com",
    "mail.instagram.com",
    "m.plex.tv",
    "boathousestores.com",
    "joeyrestaurants.com",
    "transdev.ca",
    "discord.com",
    "mongodb.com",
    "messages.mongodb.com",
    "ixbrowser.com",
    "gridmail.mostlogin.com",
    "webmail.adspower.net",
    "dolphin-anty.com",
    "infisical.com",
    "doppler.com",
    "updates.notion.so",
    "amazon.ca",
    "jetbrains.com",
    "info.digitalocean.com",
    "digitalocean.com",
    "cloudns.net",
    "clerk.com",
    "mermaid.ai",
    "email.ynab.com",
    "updates.ynab.com",
    "admin.manus.im",
    "news.kilocode.ai",
    "love.decodo.com",
    "brightdata.com",
    "pulumi.com",
    "codescene.io",
    "mail.langchain.com",
    "langchain.dev",
    "apify.com",
    "docker.com",
    "trypinecone.com",
    "supabase.com",
    "vultr.com",
    "getvultr.com",
    "windsurf.com",
    "microsoft.com",
    "accountprotection.microsoft.com",
    "notificationmail.microsoft.com",
    "infomail.microsoft.com",
    "microsoftstore.microsoft.com",
    "email.microsoft.com",
    "md.getsentry.com",
    "sentry.io",
    "dtdg.co",
    "learn.termius.com",
    "todoist.com",
    "devpost.com",
    "beautiful.ai",
    "mail.internshala.com",
    "jobscan.co",
    "info.studentbeans.com",
    "heu.org",
    "carecantwait.ca",
    "mail.fiscal.ai",
    "zennolab.com",
    "instilefloors.ca",
    "dbu.edu",
    "mycare.telus.com",
    "legal.spotify.com",
    "sunsetgrown.com",
    "3257235.brevosend.com",
    "notification.canada.ca",
    "e.lucid.co",
    "ehsanalytics.com",
    "example.com",
    "parl.gc.ca",
    "forces.gc.ca",
    "cra-arc.gc.ca",
    "mail.csnpe-nslsc.canada.ca",
    "notification.gov.bc.ca",
    "advanis.net",
    "alerts.foundit.in",
    "recruiting.ups.com",
    "otp.workday.com",
    "ibm.com",
    "em.linkedin.com",
}

KEEP_SUBJECT_KEYWORDS = (
    "application received",
    "application submitted",
    "application update",
    "application confirmation",
    "application outcome",
    "thank you for applying",
    "thanks for applying",
    "thank you for your application",
    "we received your application",
    "your application for",
    "your application has been",
    "your recent job application",
    "you have applied",
    "applied to the following",
    "applied with success",
    "applied successfully",
    "status of your application",
    "update on your application",
    "follow up to your application",
    "follow up: application",
    "interview",
    "rejection",
    "not moving forward",
    "regret to inform",
    "unfortunately",
    "offer letter",
    "job offer",
    "recruiter",
    "hiring manager",
    "new message from",
    "new jobs",
    "jobs matching",
    "jobs found for you",
    "job alert",
    "job posting",
    "job application",
    "vacancies added",
    "roles in",
    "position has been",
    "opportunity ref",
    "your interest in",
    "thank you for your interest",
    "gc jobs",
    "ongoing student recruitment",
    "demande d'emploi",
    "demande d’emploi",
)

KEEP_BODY_KEYWORDS = (
    "thank you for applying",
    "application has been received",
    "we received your application",
    "your application to",
    "schedule an interview",
    "invite you to interview",
    "not selected",
    "will not be moving forward",
    "unable to offer",
    "job alert",
    "matching your search",
    "new jobs posted",
)

OTP_SUBJECT_KEYWORDS = (
    "verification code",
    "one-time password",
    "one-time code",
    "single-use code",
    "login code",
    "password reset",
    "reset your password",
    "reset code",
    "security alert",
    "successful login",
    "new device logged",
    "verify email",
    "confirm your email",
    "temporary login",
    "multi-factor authentication",
    "authentication code",
    "access code for",
    "candidate account",
)

DROP_SUBJECT_KEYWORDS = (
    "unsubscribe",
    "newsletter",
    "% off",
    "promo",
    "sale ends",
    "limited time",
    *OTP_SUBJECT_KEYWORDS,
    "your order",
    "pickup order",
    "bill is ready",
    "invoice",
    "receipt",
    "payment confirmation",
    "payment failed",
    "credit card",
    "banking at your fingertips",
    "gift your friends",
    "premium",
    "wow your audience",
    "boost your productivity",
    "canceled:",
    "build failed",
    "weekly report",
    "new alerts since",
    "ticket closed",
    "ticket resolved",
    "welcome to",
    "getting started",
    "setup checklist",
    "student developer pack",
    "opted out",
    "marketing",
    "shop",
    "treasure hunt",
    "clearance",
    "summer deals",
    "black friday",
    "free trial is over",
    "insufficient balance",
    "overdue fees",
    "e-statement",
    "estatement",
    "password recovery",
    "account locked",
    "win tickets",
    "giveaway",
)

DROP_BODY_KEYWORDS = (
    "unsubscribe",
    "list-unsubscribe",
    "manage your preferences",
    "no longer wish to receive",
    "verification code",
    "one-time password",
    "otp",
    "reset your password",
    "click here to verify",
    "confirm your email address",
)

DROP_LABEL_KEYWORDS = (
    "promotions",
    "social",
    "forums",
    "spam",
    "trash",
)


def _extract_domain(from_addr: str) -> str:
    match = re.search(r"@([\w.-]+)", (from_addr or "").lower())
    return match.group(1) if match else ""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _is_job_platform_domain(domain: str) -> bool:
    if not domain:
        return False
    return any(fragment in domain for fragment in JOB_PLATFORM_DOMAIN_FRAGMENTS)


def _is_bare_otp_or_login(subject: str, body: str) -> bool:
    if not _contains_any(subject, OTP_SUBJECT_KEYWORDS):
        return False
    # Application-related OTP noise still drops; real application mail uses other subjects.
    job_context = (
        "indeed application:",
        "application received",
        "application submitted",
        "application update",
        "thank you for applying",
        "your application for",
        "you have applied",
    )
    return not _contains_any(subject, job_context)


def classify_email(record: dict) -> tuple[bool, str]:
    """Return (keep, reason)."""
    subject = _normalize(record.get("subject"))
    body = _normalize((record.get("body_text") or "")[:4000])
    labels = _normalize(record.get("labels"))
    from_addr = _normalize(record.get("from"))
    domain = _extract_domain(from_addr)
    combined = f"{subject} {body}"

    if _is_bare_otp_or_login(subject, body):
        return False, "drop_otp_login"

    if domain in DROP_DOMAINS:
        if _contains_any(subject, KEEP_SUBJECT_KEYWORDS):
            return True, "keep_keyword_overrides_drop_domain"
        return False, "drop_domain"

    if _contains_any(labels, DROP_LABEL_KEYWORDS) and not _is_job_platform_domain(domain):
        if not _contains_any(subject, KEEP_SUBJECT_KEYWORDS):
            return False, "drop_label"

    score = 0
    reasons: list[str] = []

    if _is_job_platform_domain(domain):
        score += 4
        reasons.append("job_platform_domain")

    if _contains_any(subject, KEEP_SUBJECT_KEYWORDS):
        score += 3
        reasons.append("keep_subject")
    elif _contains_any(body, KEEP_BODY_KEYWORDS):
        score += 2
        reasons.append("keep_body")

    if _contains_any(subject, DROP_SUBJECT_KEYWORDS):
        score -= 3
        reasons.append("drop_subject")
    if _contains_any(body, DROP_BODY_KEYWORDS):
        score -= 2
        reasons.append("drop_body")

    if "unsubscribe" in combined and score < 3:
        score -= 2
        reasons.append("bulk_unsubscribe")

    # LinkedIn/Indeed promos that slipped through domain match.
    if domain.endswith("linkedin.com") and _contains_any(
        subject,
        ("gift your friends", "premium", "get noticed by recruiters with premium"),
    ):
        score -= 4
        reasons.append("linkedin_promo")

    if score >= 2:
        return True, "+".join(reasons) or "keep_score"
    if score <= -2:
        return False, "+".join(reasons) or "drop_score"

    # Job-platform domains with neutral subjects (e.g. bare alerts) still lean keep.
    if _is_job_platform_domain(domain):
        return True, "job_platform_default"

    return False, "+".join(reasons) or "default_drop"


def _latest_run_dir(archive_dir: Path) -> Path | None:
    runs = sorted(archive_dir.glob("run_*"), key=lambda p: p.name, reverse=True)
    return runs[0] if runs else None


def _resolve_input_run(archive_dir: Path, input_run: str | None) -> Path:
    if input_run:
        run_dir = archive_dir / input_run if not input_run.startswith("run_") else archive_dir / input_run
        if not run_dir.is_absolute():
            run_dir = archive_dir / Path(input_run).name
        if not run_dir.exists():
            raise FileNotFoundError(f"Input run not found: {run_dir}")
        return run_dir

    latest = _latest_run_dir(archive_dir)
    if latest is None:
        raise FileNotFoundError(f"No run_* folders under {archive_dir}")
    return latest


def filter_account(
    input_jsonl: Path,
    output_jsonl: Path,
    *,
    dry_run: bool,
) -> dict:
    kept_records: list[dict] = []
    drop_reasons: Counter[str] = Counter()
    total = 0

    with input_jsonl.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            total += 1
            record = json.loads(line)
            keep, reason = classify_email(record)
            if keep:
                kept_records.append(record)
            else:
                drop_reasons[reason] += 1

    kept_records.sort(key=lambda r: r.get("date_iso") or r.get("date") or "", reverse=True)

    if not dry_run:
        output_jsonl.parent.mkdir(parents=True, exist_ok=True)
        with output_jsonl.open("w", encoding="utf-8") as handle:
            for record in kept_records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    return {
        "account": input_jsonl.parent.name,
        "input_jsonl": str(input_jsonl),
        "output_jsonl": str(output_jsonl),
        "total": total,
        "kept": len(kept_records),
        "dropped": total - len(kept_records),
        "drop_reasons": dict(drop_reasons.most_common(20)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Filter email dumps to job-related messages.")
    parser.add_argument(
        "--input-run",
        help="Run folder name (e.g. run_20260614_160001) or path; default: latest run_*",
    )
    parser.add_argument(
        "--archive-dir",
        default=str(DEFAULT_ARCHIVE_DIR),
        help=f"Email archive root (default: {DEFAULT_ARCHIVE_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute counts without writing cleaned output",
    )
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)
    input_run_dir = _resolve_input_run(archive_dir, args.input_run)
    output_run_dir = archive_dir / "cleaned" / input_run_dir.name

    summaries: list[dict] = []
    for account in ACCOUNTS:
        input_jsonl = input_run_dir / account / "emails.jsonl"
        if not input_jsonl.exists():
            print(f"[skip] Missing {input_jsonl}")
            continue
        output_jsonl = output_run_dir / account / "emails.jsonl"
        summary = filter_account(input_jsonl, output_jsonl, dry_run=args.dry_run)
        summaries.append(summary)

        if not args.dry_run:
            account_summary_path = output_run_dir / account / "summary.json"
            account_summary_path.parent.mkdir(parents=True, exist_ok=True)
            account_summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        print(
            f"[{account}] total={summary['total']} kept={summary['kept']} "
            f"dropped={summary['dropped']}"
        )

    if not summaries:
        print("No account dumps processed.")
        return 1

    run_summary = {
        "filtered_at": datetime.now(timezone.utc).isoformat(),
        "input_run": str(input_run_dir),
        "output_run": str(output_run_dir),
        "dry_run": args.dry_run,
        "accounts": summaries,
        "total": sum(s["total"] for s in summaries),
        "kept": sum(s["kept"] for s in summaries),
        "dropped": sum(s["dropped"] for s in summaries),
    }

    if not args.dry_run:
        output_run_dir.mkdir(parents=True, exist_ok=True)
        (output_run_dir / "summary.json").write_text(
            json.dumps(run_summary, indent=2),
            encoding="utf-8",
        )
        print(f"Cleaned output: {output_run_dir}")
    else:
        print("Dry run — no files written.")

    print(
        f"Overall: total={run_summary['total']} kept={run_summary['kept']} "
        f"dropped={run_summary['dropped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

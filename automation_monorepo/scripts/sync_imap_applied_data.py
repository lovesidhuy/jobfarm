#!/usr/bin/env python3
"""Sync job application confirmation emails from IMAP accounts to MongoDB and CSV.

This pulls recent emails from both IT and GENERAL IMAP accounts, parses them for
application confirmations (Company Name, Job Title, Platform, Date), and inserts/upserts
them into MongoDB (jobbots.email_applied_history) and all excels/email_applied_history.csv.
"""
from __future__ import annotations

import argparse
import csv
import email
import imaplib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

# Insert project root to sys.path
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from core.secret_manager import get_secret

ACCOUNTS = (
    ("it", "IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT"),
    ("general", "IMAP_EMAIL_GENERAL", "IMAP_APP_PASSWORD_GENERAL"),
)

# Email subject keywords indicating application confirmations
CONFIRMATION_SUBJECTS = (
    "application confirmation",
    "application received",
    "application submitted",
    "thank you for applying",
    "thanks for applying",
    "thank you for your application",
    "we received your application",
    "we have received your application",
    "indeed application:",
    "your application for",
    "your application to",
    "applied successfully",
    "applied to",
    "demande d'emploi",
    "demande de candidature",
)


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    try:
        parts = decode_header(value)
        out = []
        for frag, enc in parts:
            if isinstance(frag, bytes):
                out.append(frag.decode(enc or "utf-8", errors="replace"))
            else:
                out.append(frag)
        return "".join(out)
    except Exception:
        return str(value)


def _parse_date(msg: email.message.Message) -> str:
    date_raw = msg.get("Date", "")
    try:
        return parsedate_to_datetime(date_raw).astimezone(timezone.utc).isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def parse_email_for_job(subject: str, from_addr: str) -> dict:
    """Extract company_name, job_title, and platform from subject & sender."""
    subject_clean = subject.strip()
    from_lower = from_addr.strip().lower()

    company = "Unknown"
    title = "Unknown"
    platform = "unknown"

    # 1. Identify platform from sender domain
    if "indeed" in from_lower:
        platform = "indeed"
    elif "glassdoor" in from_lower:
        platform = "glassdoor"
    elif "linkedin" in from_lower:
        platform = "linkedin"
    elif "workopolis" in from_lower:
        platform = "workopolis"
    elif "greenhouse" in from_lower:
        platform = "greenhouse"
    elif "lever.co" in from_lower:
        platform = "lever"
    elif "ashbyhq.com" in from_lower or "ashby" in from_lower:
        platform = "ashby"
    elif "bamboohr.com" in from_lower or "bamboohr" in from_lower:
        platform = "bamboohr"
    elif "workday" in from_lower:
        platform = "workday"

    # 2. Heuristic regex matches on subject
    # "Your application for [Job Title] at [Company]"
    m = re.search(r"application for\s+(.+?)\s+at\s+(.+)$", subject_clean, re.I)
    if m:
        title = m.group(1).strip()
        company = m.group(2).strip()
    else:
        # "Your application to [Company] for [Job Title]"
        m = re.search(r"application to\s+(.+?)\s+for\s+(.+)$", subject_clean, re.I)
        if m:
            company = m.group(1).strip()
            title = m.group(2).strip()
        else:
            # "Thank you for applying to [Company]" / "Thanks for applying to [Company]"
            m = re.search(r"thank(?:s)?\s+you\s+for\s+applying\s+to\s+(.+)$", subject_clean, re.I)
            if m:
                company = m.group(1).strip()
            else:
                # "Application received: [Job Title] - [Company]"
                m = re.search(r"application received:\s+(.+?)\s*-\s*(.+)$", subject_clean, re.I)
                if m:
                    title = m.group(1).strip()
                    company = m.group(2).strip()
                else:
                    # "Indeed Application Received: [Job Title]"
                    m = re.search(r"indeed application received:\s+(.+)$", subject_clean, re.I)
                    if m:
                        title = m.group(1).strip()
                        platform = "indeed"
                    else:
                        # "Indeed Application: [Job Title]"
                        m = re.search(r"indeed application:\s+(.+)$", subject_clean, re.I)
                        if m:
                            title = m.group(1).strip()
                            platform = "indeed"
                        else:
                            # "We have received your application, [Name]!"
                            m = re.search(r"we have received your application", subject_clean, re.I)
                            if m:
                                # Try extracting company from from_addr domain prefix
                                if "@" in from_lower:
                                    prefix = from_lower.split("@")[0].strip("<>")
                                    if ".hr" in prefix:
                                        company = prefix.split(".hr")[0].upper()
                                    elif "recruiting" in prefix:
                                        company = prefix.replace("recruiting", "").upper()
                                    else:
                                        company = prefix.upper()
                            else:
                                # "Applied to [Company]: [Job Title]"
                                m = re.search(r"applied to\s+(.+?):\s+(.+)$", subject_clean, re.I)
                                if m:
                                    company = m.group(1).strip()
                                    title = m.group(2).strip()

    # Cleanup trailing punctuation
    company = company.rstrip("! .").strip()
    title = title.rstrip("! .").strip()

    # Fallback to sender display name for company if company remains Unknown
    if company == "Unknown":
        m_sender = re.search(r"^([^<]+)", from_addr)
        if m_sender:
            sender_name = m_sender.group(1).replace('"', '').strip()
            if sender_name and not any(board in sender_name.lower() for board in ("indeed", "glassdoor", "linkedin", "workopolis")):
                company = sender_name

    return {"company_name": company, "job_title": title, "source_platform": platform}


def fetch_confirmations_for_account(
    label: str,
    email_addr: str,
    app_password: str,
    imap_server: str,
    days: int,
) -> list[dict]:
    """Fetch recent confirmation emails from the IMAP account."""
    print(f"[{label}] Connecting to {imap_server} for {email_addr}...")
    try:
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_addr, app_password)
    except Exception as e:
        print(f"[{label}] Connection/Login failed: {e}")
        return []

    try:
        # Check INBOX and Gmail All Mail
        folders = ["INBOX", '"[Gmail]/All Mail"']
        all_records = []
        seen_message_ids = set()

        for folder in folders:
            try:
                status, _ = mail.select(folder, readonly=True)
                if status != "OK":
                    continue
            except Exception:
                continue

            since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%d-%b-%Y")
            status, data = mail.search(None, "SINCE", since)
            if status != "OK" or not data or not data[0]:
                continue

            ids = data[0].split()
            print(f"[{label}] Folder {folder}: found {len(ids)} emails in last {days} days.")
            if not ids:
                continue

            # Fetch in batches
            batch_size = 50
            for i in range(0, len(ids), batch_size):
                chunk = ids[i : i + batch_size]
                chunk_set = b",".join(chunk)
                status, msg_data = mail.fetch(chunk_set, "(BODY[HEADER.FIELDS (SUBJECT FROM DATE MESSAGE-ID)])")
                if status != "OK" or not msg_data:
                    continue

                for response_part in msg_data:
                    if not isinstance(response_part, tuple):
                        continue
                    raw_bytes = response_part[1]
                    if not raw_bytes:
                        continue
                    try:
                        msg = email.message_from_bytes(raw_bytes)
                        msg_id = (msg.get("Message-ID") or "").strip()
                        if not msg_id or msg_id in seen_message_ids:
                            continue
                        seen_message_ids.add(msg_id)

                        subject = _decode_header_value(msg.get("Subject", ""))
                        from_addr = _decode_header_value(msg.get("From", ""))

                        # Match confirmation keywords
                        subj_lower = subject.lower()
                        if any(k in subj_lower for k in CONFIRMATION_SUBJECTS):
                            job_data = parse_email_for_job(subject, from_addr)
                            date_iso = _parse_date(msg)

                            all_records.append({
                                "message_id": msg_id,
                                "email_account": label,
                                "sender": from_addr,
                                "subject": subject,
                                "company_name": job_data["company_name"],
                                "job_title": job_data["job_title"],
                                "source_platform": job_data["source_platform"],
                                "applied_at": date_iso,
                                "synced_at": datetime.now(timezone.utc).isoformat(),
                            })
                    except Exception as e:
                        print(f"[{label}] Error parsing message: {e}")

        mail.logout()
        return all_records

    except Exception as e:
        print(f"[{label}] IMAP transaction failed: {e}")
        try:
            mail.logout()
        except Exception:
            pass
        return []


def save_to_mongodb(records: list[dict]) -> int:
    """Insert/Upsert records into MongoDB jobbots.email_applied_history."""
    if not records:
        return 0

    from core.job_queue import safe_mongo_uri
    mongodb_uri = safe_mongo_uri(get_secret("MONGODB_URI", "mongodb://localhost:27017"))
    db_name = get_secret("JOBBOTS_MONGO_DATABASE", "jobbots")
    coll_name = "email_applied_history"

    try:
        from pymongo import MongoClient, ReplaceOne
        try:
            client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=2000)
            # Try to ping the database to verify connection
            client.admin.command('ping')
        except Exception as conn_err:
            print(f"[MongoDB] Failed connecting to primary URI ({conn_err}). Falling back to localhost...")
            mongodb_uri = "mongodb://localhost:27017"
            client = MongoClient(mongodb_uri, serverSelectionTimeoutMS=2000)
        db = client[db_name]
        coll = db[coll_name]

        # Ensure index on message_id
        coll.create_index("message_id", unique=True)
        coll.create_index("email_account")

        operations = [
            ReplaceOne({"message_id": r["message_id"]}, r, upsert=True)
            for r in records
        ]

        res = coll.bulk_write(operations)
        inserted_or_upserted = (res.upserted_count or 0) + (res.modified_count or 0)
        print(f"[MongoDB] Upserted/Synced {inserted_or_upserted} records into {db_name}.{coll_name}")

        # Correlate confirmation emails with queue records
        try:
            queue_coll = db["application_queue"]
            # Fetch all applied jobs for correlation
            applied_jobs = list(queue_coll.find({
                "status": "applied"
            }))

            for r in records:
                try:
                    from core.training_capture import record_training_event
                    record_training_event(
                        "application_status_observed", portal=r.get("source_platform", ""),
                        job_url=r.get("job_url", ""), title=r.get("job_title", ""),
                        company=r.get("company_name", ""), status="confirmation_received",
                        applied_at=r.get("applied_at", ""), evidence_sender=r.get("sender", ""),
                        evidence_subject=r.get("subject", ""), message_id=r.get("message_id", ""),
                    )
                except Exception:
                    pass
                matched_job = None
                for job in applied_jobs:
                    # check portal match (unless unknown)
                    if job.get("portal") and r["source_platform"] != "unknown":
                        if r["source_platform"] not in (job.get("portal") or ""):
                            continue

                    # Normalize titles
                    j_title = re.sub(r"[^\w]", "", (job.get("title") or "")).lower()
                    e_title = re.sub(r"[^\w]", "", r["job_title"]).lower()

                    # Normalize companies
                    j_comp = re.sub(r"[^\w]", "", (job.get("company") or "")).lower()
                    e_comp = re.sub(r"[^\w]", "", r["company_name"]).lower()

                    # Fuzzy match title and company
                    title_match = (e_title in j_title) or (j_title in e_title) or (e_title == "unknown")
                    comp_match = (e_comp in j_comp) or (j_comp in e_comp) or (e_comp == "unknown")

                    # If company matches, and title has significant overlap
                    if comp_match and (title_match or "support" in j_title and "support" in e_title):
                        matched_job = job
                        break

                if matched_job:
                    queue_coll.update_one(
                        {"_id": matched_job["_id"]},
                        {"$set": {
                            "confirmation_message_id": r["message_id"],
                            "confirmation_evidence": {
                                "subject": r["subject"],
                                "sender": r["sender"],
                                "received_at": r["applied_at"]
                            }
                        }}
                    )
                    print(f"[Correlation] Successfully linked Message-ID {r['message_id']} to job {matched_job['_id']} ({matched_job['title']} at {matched_job['company']})")
        except Exception as corr_err:
            print(f"[Correlation] Error correlating emails: {corr_err}")

        return inserted_or_upserted
    except Exception as e:
        print(f"[MongoDB] Sync error: {e}")
        return 0


def save_to_csv(records: list[dict], csv_path: Path) -> None:
    """Write/merge records into the local applied history CSV file."""
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}

    if csv_path.exists():
        try:
            with csv_path.open("r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get("message_id"):
                        existing[row["message_id"]] = row
        except Exception as e:
            print(f"[CSV] Reading error: {e}")

    # Merge new records
    for r in records:
        existing[r["message_id"]] = {
            "message_id": r["message_id"],
            "email_account": r["email_account"],
            "sender": r["sender"],
            "subject": r["subject"],
            "company_name": r["company_name"],
            "job_title": r["job_title"],
            "source_platform": r["source_platform"],
            "applied_at": r["applied_at"],
            "synced_at": r["synced_at"],
        }

    fields = [
        "message_id",
        "email_account",
        "sender",
        "subject",
        "company_name",
        "job_title",
        "source_platform",
        "applied_at",
        "synced_at",
    ]

    try:
        with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            for k in sorted(existing.keys()):
                writer.writerow(existing[k])
        print(f"[CSV] Written {len(existing)} total records to {csv_path}")
    except Exception as e:
        print(f"[CSV] Write error: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync IMAP confirmation emails to MongoDB and CSV.")
    parser.add_argument("--days", type=int, default=180, help="Days back to fetch (default: 180)")
    parser.add_argument(
        "--csv-file",
        default="all excels/email_applied_history.csv",
        help="Local CSV path to save data",
    )
    args = parser.parse_args()

    imap_server = get_secret("IMAP_SERVER", "imap.gmail.com").strip() or "imap.gmail.com"
    all_records = []

    for label, email_key, password_key in ACCOUNTS:
        email_addr = get_secret(email_key, "").strip()
        app_password = get_secret(password_key, "").strip()
        if not email_addr or not app_password:
            print(f"[skip] Missing credentials for {label} ({email_key})")
            continue

        records = fetch_confirmations_for_account(
            label, email_addr, app_password, imap_server, args.days
        )
        print(f"[{label}] Found {len(records)} job confirmations.")
        all_records.extend(records)

    if not all_records:
        print("No job confirmations found to sync.")
        return 0

    # Save to MongoDB
    save_to_mongodb(all_records)

    # Save to CSV
    csv_path = _ROOT / args.csv_file
    save_to_csv(all_records, csv_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Send job application via direct email with resume and cover letter using Infisical credentials."""

import os
import sys
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path

# Add monorepo path to access secret manager
MONOREPO_PATH = Path(__file__).resolve().parents[1]  # automation_monorepa
sys.path.insert(0, str(MONOREPO_PATH))
from core.secret_manager import get_secret

# Get credentials from Infisical (or fallback to defaults/env vars)
SENDER_EMAIL = os.getenv("SMTP_SENDER", get_secret("SMTP_EMAIL") or get_secret("IMAP_EMAIL_IT", "user@example.com"))
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or get_secret("SMTP_PASSWORD") or get_secret("IMAP_APP_PASSWORD_IT")
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", "db-admin-jobs@vancouver-healthtech.ca")
SUBJECT = os.getenv("SUBJECT", "Job Application - Database Administrator Position")

# Use absolute paths for resume files based on monorepo root
RESUME_PATH = os.getenv("RESUME_PATH", str(MONOREPO_PATH / "all resumes" / "ls_resume_it.pdf"))
COVER_LETTER_PATH = os.getenv("COVER_LETTER_PATH", str(MONOREPO_PATH / "all resumes" / "cover_ls_it.pdf"))

# Craft the email body tailored to the DBA role - adjusted for Jane Doe's IT profile (4th year student)
EMAIL_BODY = """Dear Hiring Manager,

I'm applying for the Database Administrator position at Vancouver Health-Tech. As a 4th-year IT student with experience in database management and system administration, I'm eager to contribute my skills to your team.

My background includes database design, optimization, backup/recovery procedures, and ensuring data integrity and security—skills I've developed through academic projects and hands-on IT work.

Please find my resume and cover letter attached. I'd welcome the opportunity to discuss how my training can support your health-tech initiatives.

Thank you for your consideration.

Sincerely,
Jane Doe
"""

def attach_file(msg, file_path):
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            f"attachment; filename={os.path.basename(file_path)}",
        )
        msg.attach(part)
        print(f"Attached: {file_path}")
    else:
        print(f"Warning: File not found - {file_path}")

def send_email(dry_run=False):
    if not SMTP_PASSWORD:
        print("ERROR: SMTP_PASSWORD is not set. Please provide your app password.")
        return

    # Create message
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = RECIPIENT_EMAIL
    msg['Subject'] = SUBJECT
    msg.attach(MIMEText(EMAIL_BODY, 'plain'))

    # Attach files
    attach_file(msg, RESUME_PATH)
    attach_file(msg, COVER_LETTER_PATH)

    if dry_run:
        print(f"\n📧 DRY RUN - Would send email:")
        print(f"   From: {SENDER_EMAIL}")
        print(f"   To: {RECIPIENT_EMAIL}")
        print(f"   Subject: {SUBJECT}")
        print(f"   Attachments: {os.path.basename(RESUME_PATH)}, {os.path.basename(COVER_LETTER_PATH)}")
        print(f"   Body preview: {EMAIL_BODY[:200]}...")
        return

    try:
        # Connect to Gmail SMTP server
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)
        print("\n✅ Email sent successfully to " + RECIPIENT_EMAIL + "!")
    except Exception as e:
        print(f"\n❌ Failed to send email: {e}")

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Send job application email with resume and cover letter")
    parser.add_argument("--dry-run", action="store_true", help="Preview email without sending")
    parser.add_argument("--recipient", type=str, default=None, help="Override recipient email")
    parser.add_argument("--subject", type=str, default=None, help="Override subject line")
    
    args = parser.parse_args()
    
    if args.recipient:
        RECIPIENT_EMAIL = args.recipient
    if args.subject:
        SUBJECT = args.subject
    
    send_email(dry_run=args.dry_run)

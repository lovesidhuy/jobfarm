#!/usr/bin/env python3
"""Batch job application email sender - sends applications to multiple job postings."""

import os
import sys
import time
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

# Get credentials from Infisical
SENDER_EMAIL = os.getenv("SMTP_SENDER", get_secret("SMTP_EMAIL") or get_secret("IMAP_EMAIL_IT", "user@example.com"))
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or get_secret("SMTP_PASSWORD") or get_secret("IMAP_APP_PASSWORD_IT")

# Resume paths
RESUME_PATH = str(MONOREPO_PATH / "all resumes" / "ls_resume_it.pdf")
COVER_LETTER_PATH = str(MONOREPO_PATH / "all resumes" / "cover_ls_it.pdf")

# Job list - categorized by position type for tailored messaging
JOBS = [
    # Position #1
    {"position": "Junior Systems Administrator", "email": "jobs@burnaby-it-solutions.ca", "subject": "Application – Junior Systems Administrator"},
    # Position #2
    {"position": "Helpdesk Support Technician (Tier 1/2)", "email": "hr@richmond-tech-retail.com", "subject": "Helpdesk Application"},
    # Position #3
    {"position": "Full-Stack Software Engineer", "email": "careers@vancouver-web-creatives.com", "subject": "Application – Full-Stack Software Engineer"},
    # Position #4
    {"position": "Network Infrastructure Specialist", "email": "network-jobs@surrey-telecom-ops.net", "subject": "Application – Network Infrastructure Specialist"},
    # Position #5
    {"position": "IT Project Coordinator", "email": "talent@northvan-software-group.org", "subject": "Application – IT Project Coordinator"},
    # Position #6
    {"position": "DevOps Engineer (Contract)", "email": "cloud-ops@vancouver-cloud-ninjas.io", "subject": "Application – DevOps Engineer"},
    # Position #7
    {"position": "Cybersecurity Analyst", "email": "security-careers@coquitlam-fintech.com", "subject": "Application – Cybersecurity Analyst"},
    # Position #8
    {"position": "Data Analyst", "email": "recruitment@delta-logistics-tech.ca", "subject": "Application – Data Analyst"},
    # Position #9
    {"position": "IT Support Specialist", "email": "jobs@newwest-edtech.org", "subject": "Application – IT Support Specialist"},
    # Position #10
    {"position": "Software QA Tester", "email": "careers@vancouver-app-testers.com", "subject": "Application – Software QA Tester"},
    # Position #11
    {"position": "Database Administrator", "email": "db-admin-jobs@vancouver-healthtech.ca", "subject": "Application – Database Administrator"},
    # Position #12
    {"position": "IT Operations Manager", "email": "careers@poco-manufacturing-tech.com", "subject": "Application – IT Operations Manager"},
    # Position #13
    {"position": "Frontend Web Developer", "email": "creative-jobs@gastown-web-studio.net", "subject": "Application – Frontend Web Developer"},
    # Position #14
    {"position": "Systems Integrator", "email": "tech-hiring@richmond-logistics-sys.org", "subject": "Application – Systems Integrator"},
    # Position #15
    {"position": "Python Developer", "email": "ai-talent@vancouver-neural-dev.io", "subject": "Application – Python Developer"},
    # Position #16
    {"position": "IT Helpdesk Tier 2 Lead", "email": "jobs@surrey-edu-it.ca", "subject": "Application – IT Helpdesk Tier 2 Lead"},
    # Position #17
    {"position": "Cloud Solutions Architect", "email": "cloud-careers@burnaby-consulting-group.com", "subject": "Application – Cloud Solutions Architect"},
    # Position #18
    {"position": "Linux Systems Administrator", "email": "linux-admin@northvan-isp-ops.net", "subject": "Application – Linux Systems Administrator"},
    # Position #19
    {"position": "UI/UX Designer & IT Liaison", "email": "design-jobs@vancouver-interactive-media.com", "subject": "Application – UI/UX Designer & IT Liaison"},
    # Position #20
    {"position": "Information Security Officer", "email": "compliance-jobs@langley-tech-secure.org", "subject": "Application – Information Security Officer"},
    # Position #21
    {"position": "Network Security Engineer", "email": "security-jobs@vancouver-fintech-sec.ca", "subject": "Application – Network Security Engineer"},
    # Position #22
    {"position": "Technical Support Specialist", "email": "support-careers@burnaby-ecommerce-tech.com", "subject": "Application – Technical Support Specialist"},
    # Position #23
    {"position": "Software Developer in Test (SDET)", "email": "careers@richmond-gaming-labs.io", "subject": "Application – SDET"},
    # Position #24
    {"position": "Cloud Migration Consultant", "email": "cloud-jobs@surrey-enterprise-it.net", "subject": "Application – Cloud Migration Consultant"},
    # Position #25
    {"position": "Junior Database Analyst", "email": "hr@vancouver-health-research.org", "subject": "Application – Junior Database Analyst"},
    # Position #26
    {"position": "Systems Administrator (Windows/Linux)", "email": "sysadmin-jobs@delta-logistics-tech.ca", "subject": "Application – Systems Administrator"},
    # Position #27
    {"position": "Front-End React Developer", "email": "web-jobs@northvan-digital-media.com", "subject": "Application – Front-End React Developer"},
    # Position #28
    {"position": "IT Infrastructure Coordinator", "email": "infrastructure@newwest-transport-tech.org", "subject": "Application – IT Infrastructure Coordinator"},
    # Position #29
    {"position": "Cyber Threat Intelligence Analyst", "email": "threat-intel@coquitlam-security-group.net", "subject": "Application – Cyber Threat Intelligence Analyst"},
    # Position #30
    {"position": "Mobile Application Developer (iOS/Android)", "email": "mobile-dev@vancouver-startup-hub.io", "subject": "Application – Mobile Application Developer"},
]

def get_body_for_position(position):
    """Generate tailored email body based on job position category."""
    position_lower = position.lower()
    
    # Systems/Infrastructure category
    if any(kw in position_lower for kw in ["systems administrator", "it support", "helpdesk", "operations", "infrastructure"]):
        return f"""Dear Hiring Manager,

I'm applying for the {position} position at your organization. As a 4th-year IT student with strong foundational skills in system administration, technical support, and infrastructure management, I'm eager to contribute my knowledge to your team.

My experience includes troubleshooting, system maintenance, user support, and working across diverse IT environments—skills directly relevant to this role. I'm proficient with both Windows and Linux systems and thrive in fast-paced technical settings.

Please find my resume and cover letter attached. They detail my qualifications and projects. I welcome the opportunity to discuss how my training can support your operations.

Thank you for your consideration.

Sincerely,
Jane Doe"""
    
    # Development category (Full-stack, Python, Frontend, Mobile, SDET)
    elif any(kw in position_lower for kw in ["software engineer", "developer", "python", "frontend", "react", "mobile", "ios", "android", "sdet", "qa tester"]):
        return f"""Dear Hiring Manager,

I'm writing to apply for the {position} position. As a 4th-year IT student with hands-on development experience in modern web technologies and software engineering practices, I'm excited about the opportunity to contribute to your development team.

My background includes full-stack development, API integration, database interactions, and testing methodologies. I'm comfortable with both front-end and back-end technologies and enjoy building robust, scalable solutions.

Please see my attached resume and cover letter for more details on my projects and skills. I'd appreciate the chance to discuss how my technical abilities align with your team's needs.

Thank you for your review.

Sincerely,
Jane Doe"""
    
    # Security category (Cybersecurity, Security Officer, Threat Intel, Network Security)
    elif any(kw in position_lower for kw in ["security", "cybersecurity", "threat intelligence", "information security", "forensics"]):
        return f"""Dear Hiring Manager,

I'm interested in the {position} position at your organization. As a 4th-year IT student focused on security principles, network protection, and risk assessment, I'm keen to bring my analytical mindset to your security team.

My studies and projects have covered network security fundamentals, threat analysis, compliance frameworks, and secure system design—I'm particularly drawn to the challenging work in cybersecurity and defense.

My resume and cover letter (attached) provide further detail on my academic background and security-related coursework. I would welcome the opportunity to discuss how my preparation can contribute to your security initiatives.

Best regards,
Jane Doe"""
    
    # Data/Analytics category (Data Analyst, Database Admin, Database Analyst)
    elif any(kw in position_lower for kw in ["data analyst", "database administrator", "dba", "database analyst", "analytics"]):
        return f"""Dear Hiring Manager,

I'm applying for the {position} position. As a 4th-year IT student with experience in database design, SQL queries, data analysis, and performance optimization, I'm eager to support your data operations.

My background includes database management, data integrity assurance, reporting, and using analytics tools to extract insights from structured data. I'm comfortable working with various database systems and understanding data relationships.

Please find my resume and cover letter attached. They outline my database-related coursework and practical experience. I'd be glad to discuss how my training can benefit your organization's data initiatives.

Thank you for your time.

Sincerely,
Jane Doe"""
    
    # Cloud category (DevOps, Cloud Architect, Migration Consultant)
    elif any(kw in position_lower for kw in ["devops", "cloud solutions", "cloud migration", "cloud architect", "aws", "azure", "docker", "kubernetes"]):
        return f"""Dear Hiring Manager,

I'm writing to apply for the {position} role. As a 4th-year IT student with emerging expertise in cloud platforms, CI/CD pipelines, and infrastructure-as-code concepts, I'm excited about the opportunity to contribute to your cloud operations.

My knowledge spans cloud deployment strategies, containerization, automation scripts, and monitoring systems. I'm quick to learn new technologies and thrive in dynamic, tech-forward environments.

Please review my attached resume and cover letter for more details on my cloud-related projects and certifications. I look forward to discussing how my skills can support your cloud transformation efforts.

Best regards,
Jane Doe"""
    
    # Project Management/Coordination category
    elif any(kw in position_lower for kw in ["project coordinator", "project management", "coordination", "integrator"]):
        return f"""Dear Hiring Manager,

I'm interested in the {position} position. As a 4th-year IT student with experience coordinating technical projects, bridging communication between teams, and managing deliverables, I'm eager to bring my organizational skills to your project coordination role.

My background includes requirements gathering, stakeholder communication, timeline management, and documentation—skills that translate well into IT project coordination and systems integration work.

My resume and cover letter (attached) describe my project experience and technical foundation. I welcome the opportunity to discuss how my coordination abilities can support your team's objectives.

Thank you for considering my application.

Sincerely,
Jane Doe"""
    
    # UI/UX Design category
    elif "ui/ux" in position_lower:
        return f"""Dear Hiring Manager,

I'm applying for the {position} position. As a 4th-year IT student with an intersection of design sensibilities and technical understanding, I'm excited about bridging design and development at your organization.

My background includes user interface principles, design tool familiarity, and translating visual concepts into implementable solutions. I understand development constraints and can collaborate effectively with engineering teams.

Please see my attached resume and cover letter for examples of my design-work and technical projects. I'd be delighted to discuss how my hybrid skills can enhance your product team.

Thank you for your consideration.

Sincerely,
Jane Doe"""
    
    # Fallback generic template
    else:
        return f"""Dear Hiring Manager,

I'm writing to express interest in the {position} position at your organization. As a 4th-year IT student with broad technical foundations across multiple domains—including system administration, software development, and data management—I'm eager to contribute my energy and learning potential to your team.

My academic background includes practical projects covering diverse IT areas, and I bring a strong work ethic, adaptability, and commitment to continuous growth. I'm excited about the opportunity to learn from experienced professionals and add value to your operations.

Please find my resume and cover letter attached. They provide further detail on my education, projects, and skills. I welcome the chance to discuss how my training aligns with your team's needs.

Thank you for your time and consideration.

Sincerely,
Jane Doe"""


def attach_file(msg, file_path):
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(file_path)}")
        msg.attach(part)
        print(f"  Attached: {os.path.basename(file_path)}")
    else:
        print(f"  WARNING: File not found - {file_path}")


def send_single_job(job, dry_run=False, delay_between_emails=2):
    """Send a single application email."""
    position = job["position"]
    recipient = job["email"]
    subject = job["subject"]
    body = get_body_for_position(position)

    if dry_run:
        print(f"\n📧 DRY RUN - Job #{JOBS.index(job)+1}: {position}")
        print(f"   To: {recipient}")
        print(f"   Subject: {subject}")
        print(f"   Body preview: {body[:200]}...")
        return
    
    # Create message
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))

    # Attach files
    attach_file(msg, RESUME_PATH)
    attach_file(msg, COVER_LETTER_PATH)

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"✅ Sent: {position} to {recipient}")
    except Exception as e:
        print(f"❌ Failed: {position} - {e}")
        return False
    
    time.sleep(delay_between_emails)  # Be respectful between sends
    return True


def main():
    if not SMTP_PASSWORD:
        print("ERROR: SMTP_PASSWORD not set. Please configure via environment or Infisical.")
        sys.exit(1)

    print(f"=== Batch Job Application Sender ===")
    print(f"Sender: {SENDER_EMAIL}")
    print(f"Total jobs: {len(JOBS)}")
    print()

    # Check dry-run flag
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("--- DRY RUN MODE ---")
        print("No emails will be sent. This is a simulation only.\n")
    
    success_count = 0
    fail_count = 0

    for i, job in enumerate(JOBS, 1):
        print(f"\nProcessing {i}/{len(JOBS)}...")
        if send_single_job(job, dry_run):
            success_count += 1
        else:
            fail_count += 1

    print(f"\n{'='*50}")
    print(f"Results: {success_count} successful, {fail_count} failed")
    if dry_run:
        print("\n(Note: Dry run did not send any actual emails)")


if __name__ == "__main__":
    main()

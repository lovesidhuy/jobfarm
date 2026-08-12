#!/usr/bin/env python3
"""Batch job application email sender with dynamic resume tailoring."""

import os
import sys
import time
import smtplib
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

# Add monorepo path to access secret manager (for credentials)
MONOREPO_PATH = Path(__file__).resolve().parents(  # automation_monorepa
sys.path.insert(0, str(MONOREPO_PATH))
from core.secret_manager import get_secret

# Get credentials from Infisical
SENDER_EMAIL = os.getenv("SMTP_SENDER", get_secret("SMTP_EMAIL") or get_secret("IMAP_EMAIL_IT", "user@example.com"))
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD") or get_secret("SMTP_PASSWORD") or get_secret("IMAP_APP_PASSWORD_IT")

# Resume tailoring server configuration
TAILOR_SERVER_URL = "http://localhost:3001"  # resume_workflow server
DEFAULT_RESUME_PATH = None  # Fallback if tailoring server unavailable

# Jobs list (same as before)
JOBS = [
    {"position": "Junior Systems Administrator", "email": "jobs@burnaby-it-solutions.ca", "subject": "Application – Junior Systems Administrator"},
    {"position": "Helpdesk Support Technician (Tier 1/2)", "email": "hr@richmond-tech-retail.com", "subject": "Helpdesk Application"},
    {"position": "Full-Stack Software Engineer", "email": "careers@vancouver-web-creatives.com", "subject": "Application – Full-Stack Software Engineer"},
    {"position": "Network Infrastructure Specialist", "email": "network-jobs@surrey-telecom-ops.net", "subject": "Application – Network Infrastructure Specialist"},
    {"position": "IT Project Coordinator", "email": "talent@northvan-software-group.org", "subject": "Application – IT Project Coordinator"},
    {"position": "DevOps Engineer (Contract)", "email": "cloud-ops@vancouver-cloud-ninjas.io", "subject": "Application – DevOps Engineer"},
    {"position": "Cybersecurity Analyst", "email": "security-careers@coquitlam-fintech.com", "subject": "Application – Cybersecurity Analyst"},
    {"position": "Data Analyst", "email": "recruitment@delta-logistics-tech.ca", "subject": "Application – Data Analyst"},
    {"position": "IT Support Specialist", "email": "jobs@newwest-edtech.org", "subject": "Application – IT Support Specialist"},
    {"position": "Software QA Tester", "email": "careers@vancouver-app-testers.com", "subject": "Application – Software QA Tester"},
    {"position": "Database Administrator", "email": "db-admin-jobs@vancouver-healthtech.ca", "subject": "Application – Database Administrator"},
    {"position": "IT Operations Manager", "email": "careers@poco-manufacturing-tech.com", "subject": "Application – IT Operations Manager"},
    {"position": "Frontend Web Developer", "email": "creative-jobs@gastown-web-studio.net", "subject": "Application – Frontend Web Developer"},
    {"position": "Systems Integrator", "email": "tech-hiring@richmond-logistics-sys.org", "subject": "Application – Systems Integrator"},
    {"position": "Python Developer", "email": "ai-talent@vancouver-neural-dev.io", "subject": "Application – Python Developer"},
    {"position": "IT Helpdesk Tier 2 Lead", "email": "jobs@surrey-edu-it.ca", "subject": "Application – IT Helpdesk Tier 2 Lead"},
    {"position": "Cloud Solutions Architect", "email": "cloud-careers@burnaby-consulting-group.com", "subject": "Application – Cloud Solutions Architect"},
    {"position": "Linux Systems Administrator", "email": "linux-admin@northvan-isp-ops.net", "subject": "Application – Linux Systems Administrator"},
    {"position": "UI/UX Designer & IT Liaison", "email": "design-jobs@vancouver-interactive-media.com", "subject": "Application – UI/UX Designer & IT Liaison"},
    {"position": "Information Security Officer", "email": "compliance-jobs@langley-tech-secure.org", "subject": "Application – Information Security Officer"},
    {"position": "Network Security Engineer", "email": "security-jobs@vancouver-fintech-sec.ca", "subject": "Application – Network Security Engineer"},
    {"position": "Technical Support Specialist", "email": "support-careers@burnaby-ecommerce-tech.com", "subject": "Application – Technical Support Specialist"},
    {"position": "Software Developer in Test (SDET)", "email": "careers@richmond-gaming-labs.io", "subject": "Application – SDET"},
    {"position": "Cloud Migration Consultant", "email": "cloud-jobs@surrey-enterprise-it.net", "subject": "Application – Cloud Migration Consultant"},
    {"position": "Junior Database Analyst", "email": "hr@vancouver-health-research.org", "subject": "Application – Junior Database Analyst"},
    {"position": "Systems Administrator (Windows/Linux)", "email": "sysadmin-jobs@delta-logistics-tech.ca", "subject": "Application – Systems Administrator"},
    {"position": "Front-End React Developer", "email": "web-jobs@northvan-digital-media.com", "subject": "Application – Front-End React Developer"},
    {"position": "IT Infrastructure Coordinator", "email": "infrastructure@newwest-transport-tech.org", "subject": "Application – IT Infrastructure Coordinator"},
    {"position": "Cyber Threat Intelligence Analyst", "email": "threat-intel@coquitlam-security-group.net", "subject": "Application – Cyber Threat Intelligence Analyst"},
    {"position": "Mobile Application Developer (iOS/Android)", "email": "mobile-dev@vancouver-startup-hub.io", "subject": "Application – Mobile Application Developer"},
]


def generate_tailored_resume(position, company, location="Vancouver"):
    """Contact the tailoring server to generate a tailored resume for this job."""
    try:
        # Prepare payload
        payload = {
            "jobTitle": position,
            "companyName": company,
            "jobDescription": f"Position at {company} in {location} requiring {position.split()[0]} skills.",
            "location": location
        }
        
        # POST to /api/tailor
        req = f"{TAILOR_SERVER_URL}/api/tailor"
        data = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        
        print(f"  Generating tailored resume for '{position}' at {company}...")
        resp = urlopen(req, data=data, headers=headers, timeout=30)
        result = json.loads(resp.read().decode('utf-8'))
        
        execution_id = result.get('executionId', '')
        if not execution_id:
            print(f"  ❌ No execution ID received")
            return None
        
        # Poll for status
        max_wait = 60  # seconds
        poll_interval = 2
        waited = 0
        
        while waited < max_wait:
            time.sleep(poll_interval)
            waited += poll_interval
            
            status_resp = urlopen(f"{TAILOR_SERVER_URL}/api/status/{execution_id}", timeout=10)
            status_data = json.loads(status_resp.read().decode('utf-8'))
            
            status = status_data.get('result', {}).get('status', '')
            if status == 'success':
                print(f"  ✓ Tailored resume generated for '{position}'")
                # Get the PDF path from the result
                result_info = status_data.get('result', {})
                local_path = result_info.get('localPdfPath', '')
                if local_path and os.path.exists(local_path):
                    return local_path
                else:
                    # Try to get resume URL
                    resume_url = result_info.get('resumeUrl', '')
                    if resume_url:
                        # Download the docx
                        docx_resp = urlopen(f"{TAILOR_SERVER_URL}{resume_url}", timeout=30)
                        filename = f"tailored_resume_{position.replace(' ', '_')}.docx"
                        with open(filename, 'wb') as f:
                            f.write(docx_resp.read())
                        print(f"  Saved tailored docx to {filename}")
                        return filename
                return execution_id  # Return execution ID as fallback
            elif status in ['error', 'failed']:
                print(f"  ❌ Tailoring failed: {status}")
                return None
            else:
                print(f"  Waiting... ({waited}/{max_wait}s)")
        
        print(f"  ❌ Timeout waiting for tailoring to complete")
        return None
        
    except HTTPError as e:
        print(f"  ❌ HTTP Error: {e.code} - {e.reason}")
        return None
    except URLError as e:
        print(f"  ❌ URL Error: {e.reason}")
        return None
    except Exception as e:
        print(f"  ❌ Error generating tailored resume: {e}")
        return None


def get_body_for_position(position):
    """Generate tailored email body based on job position category."""
    position_lower = position.lower()
    
    if any(kw in position_lower for kw in ["systems administrator", "it support", "helpdesk", "operations", "infrastructure"]):
        return f"""Dear Hiring Manager,

I'm applying for the {position} position at your organization. As a 4th-year IT student with strong foundational skills in system administration, technical support, and infrastructure management, I'm eager to contribute my knowledge to your team.

My experience includes troubleshooting, system maintenance, user support, and working across diverse IT environments—skills directly relevant to this role. I'm proficient with both Windows and Linux systems and thrive in fast-paced technical settings.

Please find my tailored resume and cover letter attached. They detail my qualifications and how they align with this specific role. I welcome the opportunity to discuss how my training can support your operations.

Thank you for your consideration.

Sincerely,
Jane Doe"""
    
    elif any(kw in position_lower for kw in ["software engineer", "developer", "python", "frontend", "react", "mobile", "ios", "android", "sdet", "qa tester"]):
        return f"""Dear Hiring Manager,

I'm writing to apply for the {position} position. As a 4th-year IT student with hands-on development experience in modern web technologies and software engineering practices, I'm excited about the opportunity to contribute to your development team.

My background includes full-stack development, API integration, database interactions, and testing methodologies. I'm comfortable with both front-end and back-end technologies and enjoy building robust, scalable solutions.

Please see my attached tailored resume and cover letter for more details on my projects and skills specifically relevant to this role. I'd appreciate the chance to discuss how my technical abilities align with your team's needs.

Thank you for your review.

Sincerely,
Jane Doe"""
    
    elif any(kw in position_lower for kw in ["security", "cybersecurity", "threat intelligence", "information security", "forensics"]):
        return f"""Dear Hiring Manager,

I'm interested in the {position} position at your organization. As a 4th-year IT student focused on security principles, network protection, and risk assessment, I'm keen to bring my analytical mindset to your security team.

My studies and projects have covered network security fundamentals, threat analysis, compliance frameworks, and secure system design—I'm particularly drawn to the challenging work in cybersecurity and defense.

My tailored resume and cover letter provide further detail on my academic background and security-related coursework specifically suited to this role. I would welcome the opportunity to discuss how my preparation can contribute to your security initiatives.

Best regards,
Jane Doe"""
    
    elif any(kw in position_lower for kw in ["data analyst", "database administrator", "dba", "database analyst", "analytics"]):
        return f"""Dear Hiring Manager,

I'm applying for the {position} position. As a 4th-year IT student with experience in database design, SQL queries, data analysis, and performance optimization, I'm eager to support your data operations.

My background includes database management, data integrity assurance, reporting, and using analytics tools to extract insights from structured data. I'm comfortable working with various database systems and understanding data relationships.

Please find my tailored resume and cover letter attached. They outline my database-related coursework and practical experience customized for this specific role. I'd be glad to discuss how my training can benefit your organization's data initiatives.

Thank you for your time.

Sincerely,
Jane Doe"""
    
    elif any(kw in position_lower for kw in ["devops", "cloud solutions", "cloud migration", "cloud architect", "aws", "azure", "docker", "kubernetes"]):
        return f"""Dear Hiring Manager,

I'm writing to apply for the {position} role. As a 4th-year IT student with emerging expertise in cloud platforms, CI/CD pipelines, and infrastructure-as-code concepts, I'm excited about the opportunity to contribute to your cloud operations.

My knowledge spans cloud deployment strategies, containerization, automation scripts, and monitoring systems. I'm quick to learn new technologies and thrive in dynamic, tech-forward environments.

Please review my attached tailored resume and cover letter for more details on my cloud-related projects and certifications relevant to this position. I look forward to discussing how my skills can support your cloud transformation efforts.

Best regards,
Jane Doe"""
    
    elif any(kw in position_lower for kw in ["project coordinator", "project management", "coordination", "integrator"]):
        return f"""Dear Hiring Manager,

I'm interested in the {position} position. As a 4th-year IT student with experience coordinating technical projects, bridging communication between teams, and managing deliverables, I'm eager to bring my organizational skills to your project coordination role.

My background includes requirements gathering, stakeholder communication, timeline management, and documentation—skills that translate well into IT project coordination and systems integration work.

My tailored resume and cover letter describe my project experience and technical foundation specifically aligned with this opportunity. I welcome the opportunity to discuss how my coordination abilities can support your team's objectives.

Thank you for considering my application.

Sincerely,
Jane Doe"""
    
    elif "ui/ux" in position_lower:
        return f"""Dear Hiring Manager,

I'm applying for the {position} position. As a 4th-year IT student with an intersection of design sensibilities and technical understanding, I'm excited about bridging design and development at your organization.

My background includes user interface principles, design tool familiarity, and translating visual concepts into implementable solutions. I understand development constraints and can collaborate effectively with engineering teams.

Please see my attached tailored resume and cover letter for examples of my design-work and technical projects relevant to this role. I'd be delighted to discuss how my hybrid skills can enhance your product team.

Thank you for your consideration.

Sincerely,
Jane Doe"""
    
    else:
        return f"""Dear Hiring Manager,

I'm writing to express interest in the {position} position at your organization. As a 4th-year IT student with broad technical foundations across multiple domains—including system administration, software development, and data management—I'm eager to contribute my energy and learning potential to my team.

My academic background includes practical projects covering diverse IT areas, and I bring a strong work ethic, adaptability, and commitment to continuous growth. I'm excited about the opportunity to learn from experienced professionals and add value to your operations.

Please find my tailored resume and cover letter attached. They provide further detail on my education, projects, and skills customized for this opportunity. I welcome the chance to discuss how my training aligns with your team's needs.

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
        # Fallback to default resume
        default_resume = Path("all resumes/ls_resume_it.pdf")
        if default_resume.exists():
            attach_file(msg, str(default_resume))
        else:
            print("  No fallback resume available.")


def send_single_job(job, dry_run=False, delay_between_emails=2):
    """Send a single application email with potentially tailored resume."""
    position = job["position"]
    recipient = job["email"]
    subject = job["subject"]
    body = get_body_for_position(position)
    
    # Determine resume to use
    resume_path = None
    
    # First try to generate a tailored resume if we're not in dry-run mode
    if not dry_run:
        # Extract company name from email (simple approach)
        company_name = recipient.split('@')[0].replace('-', ' ').title()
        if company_name == 'Jobs' or company_name == 'Hr':
            # More detailed company name extraction from the job data
            company_name = extract_company_from_email(recipient)
        
        resume_path = generate_tailored_resume(position, company_name)
        
        if not resume_path or not os.path.exists(resume_path):
            print(f"  ⚠️ Using fallback resume for {position}")
            resume_path = Path("all resumes/ls_resume_it.pdf") if Path("all resumes/ls_resume_it.pdf").existselse Path("all resumes/ls_resume_general.pdf")
            if resume_path and resume_path.exists():
                resume_path = str(resume_path)
            else:
                resume_path = None
    
    if dry_run:
        print(f"\n📧 DRY RUN - Job #{JOBS.index(job)+1}: {position}")
        print(f"   To: {recipient}")
        print(f"   Subject: {subject}")
        print(f"   Resume to use: {resume_path or 'FAILED TO LOAD'}")
        print(f"   Body preview: {body[:200]}...")
        return
    
    # Create message
    msg = MIMEMultipart()
    msg['From'] = SENDER_EMAIL
    msg['To'] = recipient
    msg['Subject'] = subject
    msg.attach(MIMEText(body, 'plain'))
    
    # Attach files
    if resume_path and os.path.exists(resume_path):
        attach_file(msg, resume_path)
    else:
        # Always attach cover letter
        cover_letter = Path("all resumes/cover_ls_it.pdf")
        if cover_letter.exists():
            attach_file(msg, str(cover_letter))
        else:
            print("  WARNING: Cover letter not found!")
    
    # Try to attach cover letter separately if needed
    cover_letter_path = "all resumes/cover_ls_it.pdf"
    if os.path.exists(cover_letter_path):
        with open(cover_letter_path, "rb") as f:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header("Content-Disposition", f"attachment; filename={os.path.basename(cover_letter_path)}")
        msg.attach(part)
        print(f"  Attached: cover_ls_it.pdf")
    
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


def extract_company_from_email(email):
    """Extract company name from email address (heuristic)."""
    parts = email.split('@')[0].replace('.', '-').replace('_', '-')
    # Try to make it readable
    return parts.replace('-', ' ').title()


def main():
    if not SMTP_PASSWORD:
        print("ERROR: SMTP_PASSWORD not set. Please configure via environment or Infisical.")
        sys.exit(1)

    print(f"=== Batch Job Application Sender with Tailored Resumes ===")
    print(f"Sender: {SENDER_EMAIL}")
    print(f"Total jobs: {len(JOBS)}")
    print(f"Tailoring Server: {TAILOR_SERVER_URL}")
    print()

    # Check dry-run flag
    dry_run = "--dry-run" in sys.argv

    if dry_run:
        print("--- DRY RUN MODE ---")
        print("No emails will be sent. This is a simulation only.\n")
    
    success_count = 0
    fail_count = 0

    for i, job in enumerate(JOBS, 1):
        print(f"\n{'='*60}")
        print(f"Processing {i}/{len(JOBS)}: {job['position']}")
        print(f"{'='*60}")
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

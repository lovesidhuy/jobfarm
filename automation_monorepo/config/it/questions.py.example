"""Default answers for application forms and employer questions (IT Profile)."""
from pathlib import Path

# Paths to resume and cover letter PDF
default_resume_path = str(Path(__file__).resolve().parents[2] / "profiles" / "resumes" / "sample_resume_it.pdf")
cover_letter_pdf_path = str(Path(__file__).resolve().parents[2] / "profiles" / "resumes" / "sample_cover_letter_it.pdf")

# Years of experience for numeric fields
years_of_experience = "3"          # A number in quotes Eg: "0","1","2","3","4", etc.

# Visa sponsorship requirement
require_visa = "No"               # "Yes" or "No"

# Portfolio & profile URLs
website = "https://example.com/portfolio"                        # "https://example.com/portfolio" or ""
professional_profile_url = "https://linkedin.com/in/example"     # "https://linkedin.com/in/example" or ""

# Citizenship status
us_citizenship = "Canadian Citizen/Permanent Resident"

## Employer-specific questions ##
desired_salary = 70000          # Numbers only. 70000, 80000, etc.
current_ctc = 50000             # Numbers only.
notice_period = 30              # Any number >= 0 without quotes.

# Professional Headline
profile_headline = "IT Systems & Security Specialist | AWS Certified Solutions Architect | Technical Support & Systems Administration"

# Professional Summary
profile_summary = """
Information Technology professional and AWS Certified Solutions Architect with hands-on experience in enterprise networking, cloud infrastructure, systems administration, and security across Linux and Windows Server environments. Skilled in deploying RADIUS/EAP-TLS authentication, network segmentation (VLANs, VPNs), SIEM logging (Splunk, Wazuh), and containerized cloud services on AWS. Proven track record in customer-facing technical support, diagnosing device connectivity, operating systems, and network configurations under SLA-driven environments.
"""

# Cover Letter
cover_letter = """
Dear Hiring Manager,

I am writing to express my strong interest in joining your team. As an IT professional with an AWS Certified Solutions Architect credential and practical experience across cloud infrastructure, networking, and systems administration, I bring both technical proficiency and a customer-focused troubleshooting approach.

Through my hands-on project and support experience, I have configured and maintained secure network environments (Cisco IOS, VLANs, VPNs, 802.1X, firewalls), deployed AWS cloud services (VPC, EC2, S3, IAM, CloudFormation), and automated tasks using Python and Bash. Additionally, my experience in technical support has honed my ability to rapidly triage hardware, software, and network issues while communicating clearly with both technical and non-technical stakeholders.

I am eager to contribute my technical foundation, problem-solving skills, and dedication to your organization. Thank you for your time and consideration.

Sincerely,
Jane Doe
"""

# Master user information passed to AI answer model / LLM gate
user_information_all = """
=== RESUME ===
Name: Jane Doe
Email: jane.doe@example.com
Phone: +1-555-0199
Location: Vancouver, BC, Canada
Portfolio: https://example.com/portfolio
LinkedIn: https://linkedin.com/in/example

Summary of Qualifications:
Information Technology professional and AWS Certified Solutions Architect with hands-on experience in enterprise networking, cloud infrastructure, systems administration, and security across Linux and Windows Server environments. Skilled in deploying RADIUS/EAP-TLS authentication, network segmentation (VLANs, VPNs), SIEM logging (Splunk, Wazuh), and containerized cloud services on AWS. Proven track record in customer-facing technical support, diagnosing device connectivity, operating systems, and network configurations under SLA-driven environments.

Technical Skills:
Networking & Security: Cisco IOS, VLANs, VPNs, OSPF, BGP, 802.1X, WPA3, Wi-Fi 6, Firewall Policy, Nmap, Wireshark.
Security Operations: Splunk, Wazuh, OSSEC, SIEM, Autopsy, FTK, Endpoint Hardening.
Cloud & Virtualization: AWS (VPC, EC2, S3, IAM, CloudFormation), Docker, Terraform, VMware, Hyper-V.
Systems & Automation: Windows Server (AD DS, GPO, NPS), Linux (Ubuntu/CentOS), Python (Boto3), Bash, Ansible.
Dev & Tools: Java, SQL (MySQL/MongoDB), RESTful APIs, Git, Postman, Technical Documentation.

Education:
Bachelor of Technology in Information Technology | Specialization: Network Administration & Security.
Relevant Coursework: Cloud Computing, System Security, Networking Technologies, Digital Forensics, IoT Systems.
Certifications: AWS Certified Solutions Architect – Associate; AWS Certified Cloud Practitioner.

Projects:
Cloud-Based Smart Storage Drive | AWS, Docker, Python, Spring Boot
• Architected a secure AWS VPC with public/private subnets, NAT gateways, and bastion hosts; integrated S3 pre-signed URLs with containerized services.
• Automated file lifecycle management and cloud operations using Python (Boto3).

Identity Infrastructure & Enterprise Authentication Lab | Windows Server, RADIUS, Wireshark
• Deployed enterprise RADIUS authentication (Windows Server NPS, Active Directory, EAP-TLS certificate-based auth); validated end-to-end authentication flows via packet analysis.
• Configured Host-based Intrusion Detection (HIDS) on Linux VMs for real-time file integrity monitoring and centralized alerting.

Experience:
Technical Support Specialist | Technology Services Group | 2022 – Present | Vancouver, BC.
• Diagnose and resolve enterprise hardware, OS, Wi-Fi/Bluetooth, and network configuration issues across 20+ ticketed interactions daily.
• Maintain resolution documentation and escalate complex anomalies to tier-3 engineering teams.

IT Operations Coordinator | Regional Health Network | 2020 – 2022 | Vancouver, BC.
• Coordinated medical technology assets and digital inventory logs across acute care facilities.

=== BOT-ONLY APPLICATION METADATA ===
Work authorization: Canadian Citizen/Permanent Resident (No sponsorship required).
Notice period: ~30 days (flexible).
Earliest start: ~1 week from offer.
Desired compensation: ,000 CAD base salary.
Work preferences: Open to full-time, contract, internship, and co-op IT roles (QA, IT Support, Service Desk, Desktop Support, Systems Administration, Cloud Support, junior Security Operations).

=== SPECIFIC TECHNICAL PROBLEM SOLVED (STAR METHOD) ===
Question: Describe a technically challenging problem you solved.
Situation: During an enterprise wireless security deployment using WPA3-Enterprise EAP-TLS Authentication, client devices repeatedly failed to authenticate against the RADIUS server.
Task: Diagnose and resolve the authentication failure to establish certificate-based mutual authentication.
Action:
1. Inspected security event logs on the Network Policy Server and identified Event ID 6273 with handshake failure.
2. Performed network packet capture using Wireshark during client connection attempts.
3. Observed that the client terminated the TLS tunnel with an 'Unknown CA' alert after certificate presentation.
4. Determined root cause: The client devices lacked the Trusted Root Certification Authority certificate in their local trust stores.
5. Resolved by deploying Group Policy certificate auto-enrollment and importing the Root CA certificate.
Outcome: Mutual TLS handshakes succeeded, the RADIUS server issued Access-Accept packets, and secure enterprise network connectivity was restored.
"""

recent_employer = "Technology Services Group"
confidence_level = "8"

# Related Settings
pause_before_submit = False
pause_at_failed_question = False
overwrite_previous_answers = False

# Hard requirement auto-answers
meets_minimum_work_age = True
has_legal_work_documents = True
can_work_in_person = True
can_work_evenings = True
can_work_weekends = True
can_work_full_time_40_hours = True
can_travel_between_local_locations = True
can_commute_up_to_one_hour = True
has_valid_drivers_license = True
has_reliable_vehicle = True
can_stand_for_long_periods = True
can_lift_up_to_70_lbs = True
is_vaccinated_against_covid = True
has_health_office_reception_experience = False
has_dental_reception_experience = False
weekly_work_availability = "Available Monday to Friday during daytime and evening hours, with flexible scheduling as required."
can_freely_travel_to_us = False

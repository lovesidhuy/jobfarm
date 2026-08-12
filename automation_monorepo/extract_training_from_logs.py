#!/usr/bin/env python3
"""
Extract training data from main logs for Indeed IT bot
"""
import json
import re
from datetime import datetime
from pathlib import Path

# Input and output paths
input_file = Path("logs/indeed_it/log.txt")
output_dir = Path("data/training/it_data")
output_dir.mkdir(parents=True, exist_ok=True)

# Today's date
today = datetime.now().strftime("%Y-%m-%d")
output_file = output_dir / f"indeed_it_training_{today}_from_logs.jsonl"

print(f"Extracting training data from main logs for {today}")
print(f"Output will be saved to: {output_file}")

training_data = []

# Read the main log file
with open(input_file, 'r') as f:
    log_content = f.read()

# Find today's log section
today_pattern = rf"Indeed Bot — Cycle.*\|.*{today}.*"
today_sections = re.findall(today_pattern, log_content)

# Extract job applications
applied_jobs = []
job_pattern = r"✓ Applied: (.+?)\s+→\s+(https?://[^\s]+)"
for match in re.finditer(job_pattern, log_content):
    job_title = match.group(1).strip()
    job_url = match.group(2).strip()
    applied_jobs.append({
        "title": job_title,
        "url": job_url
    })

print(f"Found {len(applied_jobs)} job applications")

# Extract question-answer attempts
qa_attempts = []
qa_pattern = r"\[SmartApply\] Answering employer questions…"
for match in re.finditer(qa_pattern, log_content):
    # Get context around this match
    start_pos = match.start()
    context_start = max(0, start_pos - 500)
    context_end = min(len(log_content), start_pos + 1000)
    context = log_content[context_start:context_end]
    
    # Look for job title in context
    job_match = re.search(r"Job: '(.+?)'\s*\|\s*'(.+?)'", context)
    if job_match:
        job_title = job_match.group(1).strip()
        company = job_match.group(2).strip()
        
        # Look for errors or successful answers
        if "Could not get answer: HTTP Error 404" in context:
            qa_attempts.append({
                "job_title": job_title,
                "company": company,
                "status": "failed",
                "error": "HTTP Error 404 - Ollama not available"
            })
        elif "AI answer failed" in context:
            qa_attempts.append({
                "job_title": job_title,
                "company": company,
                "status": "failed",
                "error": "AI answer failed - technical issue"
            })

print(f"Found {len(qa_attempts)} question-answer attempts")

# Create training data entries
for i, job in enumerate(applied_jobs):
    # Create a training data entry for each job application
    entry = {
        "bot_id": "indeed_it",
        "event_type": "job_application",
        "job": {
            "title": job["title"],
            "url": job["url"],
            "application_date": today
        },
        "ts": f"{today}T{i:02d}:00:00",
        "run_id": f"run_{today}_{i:03d}"
    }
    
    # Check if this job had question attempts
    for qa in qa_attempts:
        if qa["job_title"] in job["title"]:
            entry["question_attempt"] = {
                "status": qa["status"],
                "error": qa.get("error", ""),
                "company": qa["company"]
            }
            break
    
    training_data.append(entry)

# Write training data to file
with open(output_file, 'w') as f:
    for entry in training_data:
        f.write(json.dumps(entry) + '\n')

print(f"\nTraining data extraction complete!")
print(f"- Total entries: {len(training_data)}")
print(f"- Successful applications: {len(applied_jobs)}")
print(f"- Question attempts: {len(qa_attempts)}")
print(f"- Saved to: {output_file}")

# Show summary
print(f"\nJob applications processed:")
for i, job in enumerate(applied_jobs[:5], 1):
    print(f"  {i}. {job['title']}")
if len(applied_jobs) > 5:
    print(f"  ... and {len(applied_jobs) - 5} more")

print(f"\nQuestion attempts summary:")
failed_count = len([qa for qa in qa_attempts if qa["status"] == "failed"])
print(f"  - Failed attempts: {failed_count}")
print(f"  - Main error: HTTP Error 404 - Ollama service unavailable")

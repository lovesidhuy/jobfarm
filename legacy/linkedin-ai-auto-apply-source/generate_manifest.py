#!/usr/bin/env python3
"""Generate linkedin_profile_manifest.json from Python config files."""
import sys, json, os
from pathlib import Path

# Navigate up 2 levels: legacy/linkedin-ai-auto-apply-source → automation_monorepo/
_monorepo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_monorepo))
os.environ.setdefault("JOB_PROFILE", "IT")
print(f"[Manifest] Monorepo root: {_monorepo}")

try:
    from config.it.personals import *
    from config.it.questions import *
    from core.shared_modules.form_answers import load_profile
    
    prof = load_profile()
except Exception as e:
    print(f"[Manifest] Import error: {e}")
    # Fallback with hard-coded values from personals.py + questions.py reading
    import re
    personals_content = (_monorepo / "config" / "it" / "personals.py").read_text()
    questions_content = (_monorepo / "config" / "it" / "questions.py").read_text()
    
    def extract_var(content, varname):
        m = re.search(rf'^{varname}\s*=\s*["\'"]?([^#\n"\'\'\"]*)["\'"]?', content, re.MULTILINE)
        return (m.group(1).strip().strip(""'").strip('"') if m else "")
    
    class _P: pass
    p = _P()
    for attr in ["email_address","phone_number","current_city","street","state","zipcode","country",
                 "ethnicity","gender","disability_status","veteran_status",
                 "require_visa","desired_salary","current_ctc","notice_period","cover_letter",
                 "profile_summary","user_information_all","recent_employer","confidence_level","profile_headline","website","us_citizenship"]:
        val = extract_var(questions_content, attr) or extract_var(personals_content, attr)
        setattr(p, attr, val)
    
    prof = {"email": "user@example.com", "first_name": "Jane", "last_name": "Doe",
            "full_name": "Jane Doe", "phone": "5550199", "city": "Vancouver", "state": "BC",
            "zipcode": "V6B 1A1", "country": "Canada", "location": "Vancouver, BC, Canada",
            "years_of_experience": "3", "gender": getattr(p,"gender","Male"),
            "ethnicity": getattr(p,"ethnicity","Decline"),
            "disability_status": getattr(p,"disability_status","Decline"),
            "veteran_status": getattr(p,"veteran_status","Decline"),
            "us_citizenship": getattr(p,"us_citizenship","Canadian Citizen/Permanent Resident")}
    
    years_exp = extract_var(questions_content, "years_of_experience") or "3"
    desired_sal = extract_var(questions_content, "desired_salary") or "70000"
    try: desired_sal = int(desired_sal)
    except: desired_sal = 70000
    recent_emp = extract_var(questions_content, "recent_employer") or "Company"

manifest = {
    "first_name": prof.get("first_name", "Jane"),
    "last_name": prof.get("last_name", "Doe"),
    "full_name": prof.get("full_name", "Jane Doe"),
    "email": prof.get("email", "user@example.com"),
    "phone": prof.get("phone", "5550199"),
    "city": prof.get("city", "Vancouver"),
    "state": prof.get("state", "BC"),
    "zipcode": prof.get("zipcode", "V6B 1A1"),
    "country": prof.get("country", "Canada"),
    "location": prof.get("location", "Vancouver, BC, Canada"),
    "street": getattr(p,"street",""),
    "website": getattr(p,"website","https://example.com/portfolio"),
    "linkedin": getattr(p,"professional_profile_url","https://www.linkedin.com/in/example-user/"),
    "years_of_experience": years_exp if 'years_exp' in dir() else "3",
    "require_visa": getattr(p,"require_visa","No"),
    "desired_salary": desired_sal,
    "current_ctc": getattr(p,"current_ctc",50000),
    "notice_period": getattr(p,"notice_period",30),
    "cover_letter": getattr(p,"cover_letter",""),
    "profile_summary": getattr(p,"profile_summary",""),
    "user_information_all": getattr(p,"user_information_all",""),
    "resume_path": getattr(p,"default_resume_path",""),
    "gender": prof.get("gender", "Male"),
    "ethnicity": prof.get("ethnicity", "Decline"),
    "disability_status": prof.get("disability_status", "Decline"),
    "veteran_status": prof.get("veteran_status", "Decline"),
    "us_citizenship": prof.get("us_citizenship", "Canadian Citizen/Permanent Resident"),
    "recent_employer": recent_emp if 'recent_emp' in dir() else "Vancouver Coastal Health",
    "confidence_level": getattr(p,"confidence_level","7"),
    "profile_headline": getattr(p,"profile_headline",""),
}

outpath = str(Path(__file__).resolve().parent / "linkedin_profile_manifest.json")
with open(outpath, "w") as f:
    json.dump(manifest, f, indent=2)

print(f"Generated {outpath}")
for key in ["email","years_of_experience","desired_salary","phone","linkedin","city","recent_employer"]:
    print(f"  {key}: {manifest.get(key)}")

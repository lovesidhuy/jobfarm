"""General resume configuration."""
import os
from pathlib import Path
from personals import *

_repo_root = Path(__file__).resolve().parents[2]
_env_resume = os.getenv("RESUME_PATH") or os.getenv("CANDIDATE_RESUME_PATH")
if _env_resume and Path(_env_resume).is_file():
    default_resume_path = str(Path(_env_resume).resolve())
elif (_repo_root / "profiles" / "resumes" / "sample_resume_general.pdf").is_file():
    default_resume_path = str(_repo_root / "profiles" / "resumes" / "sample_resume_general.pdf")
else:
    default_resume_path = str(_repo_root / "profiles" / "resumes" / "sample_resume_general.pdf")

import os

profile = os.environ.get("JOB_PROFILE", "IT").upper()

if profile == "GENERAL":
    from .general.resume import *
else:
    from .it.resume import *

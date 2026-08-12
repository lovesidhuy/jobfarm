import os

profile = os.environ.get("JOB_PROFILE", "IT").upper()

if profile == "GENERAL":
    from .general.questions import *
else:
    from .it.questions import *

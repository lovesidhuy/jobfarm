import os

profile = os.environ.get("JOB_PROFILE", "IT").upper()

if profile == "GENERAL":
    from .general.personals import *
else:
    from .it.personals import *

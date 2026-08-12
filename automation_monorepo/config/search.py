import os

profile = os.environ.get("JOB_PROFILE", "IT").upper()

if profile == "GENERAL":
    from .general.search import *
else:
    from .it.search import *

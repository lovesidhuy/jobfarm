"""MongoDB-backed discovery/application queue for the unified jobbots database."""
from __future__ import annotations
import os,socket,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from typing import Any
try:
    from pymongo import ASCENDING, DESCENDING, ReturnDocument, MongoClient
    from pymongo.errors import DuplicateKeyError, PyMongoError
except ImportError:
    ASCENDING = 1
    DESCENDING = -1
    ReturnDocument = None
    MongoClient = None
    DuplicateKeyError = Exception
    PyMongoError = Exception

# Terminal outcomes — "applied" means a *new* on-platform submit with **page
# confirmation** (success banner / thank-you / confirmation URL). Application-
# receipt email is secondary (dedupe/history) and is not required to mark applied.
# already_applied / skipped are also terminal so the queue does not thrash, but
# metrics must not count them as fresh applications.
TERMINAL={
    "applied",
    "already_applied",
    "bookmarked",
    "skipped",
    "rejected",
    "dead",
    "manual_review",
}
# Per-status wall-clock fields (UTC datetime). ``terminal_at`` is always set with these.
_TERMINAL_AT_FIELD = {
    "applied": "applied_at",
    "already_applied": "already_applied_at",
    "bookmarked": "bookmarked_at",
    "skipped": "skipped_at",
    "rejected": "rejected_at",
    "dead": "dead_at",
    "manual_review": "manual_review_at",
}
RETRYABLE={"queued","retry"}
def _now(): return datetime.now(timezone.utc)
def _day_key(dt: datetime | None = None) -> str:
    """UTC calendar day ``YYYY-MM-DD`` for day-level metrics (no timezone guesswork)."""
    d = dt or _now()
    if getattr(d, "tzinfo", None) is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).strftime("%Y-%m-%d")
def unified_database_name(): return (os.getenv("JOBBOTS_MONGO_DATABASE") or os.getenv("MONGODB_DB_NAME") or "jobbots").strip()
def safe_mongo_uri(uri: str) -> str:
    if not uri.startswith("mongodb://") and not uri.startswith("mongodb+srv://"):
        return uri
    try:
        from urllib.parse import quote_plus, unquote
        prefix, rest = uri.split("://", 1)
        if "@" not in rest:
            return uri
        creds, hosts = rest.rsplit("@", 1)
        if ":" not in creds:
            username = quote_plus(unquote(creds))
            return f"{prefix}://{username}@{hosts}"
        username, password = creds.split(":", 1)
        username = quote_plus(unquote(username))
        password = quote_plus(unquote(password))
        return f"{prefix}://{username}:{password}@{hosts}"
    except Exception:
        return uri
def mongo_uri():
    raw = (os.getenv("MONGODB_URI") or os.getenv("MONGO_URI") or "mongodb://127.0.0.1:27017").strip()
    return safe_mongo_uri(raw)

class JobQueue:
    def __init__(self,path: str|Path|None=None,*,uri:str|None=None,database:str|None=None):
        # path is retained only for old tests/callers; production never selects a file.
        if path is not None:
            try:
                import tinymongo
                self.database = database or f"jobbots_test_{uuid.uuid5(uuid.NAMESPACE_URL, str(path)).hex[:12]}"
                self.client = tinymongo.TinyMongoClient(str(path))
                self.db = self.client[self.database]
                self.jobs = self.db["application_queue"]
                self.events = self.db["queue_events"]
                self.health = self.db["worker_health"]
                return
            except ImportError:
                pass
        if path is not None and database is None:
            database=f"jobbots_test_{uuid.uuid5(uuid.NAMESPACE_URL,str(path)).hex[:12]}"
        self.database=database or unified_database_name(); self.client=MongoClient(uri or mongo_uri(),serverSelectionTimeoutMS=2500) if MongoClient else None
        if self.client is None:
            raise RuntimeError("MongoClient is not available (pymongo not installed)")
        try: self.client.admin.command("ping")
        except PyMongoError as exc: raise RuntimeError(f"MongoDB is required for the authoritative job queue: {exc}") from exc
        self.db=self.client[self.database]; self.jobs=self.db["application_queue"]; self.events=self.db["queue_events"]; self.health=self.db["worker_health"]
        self.jobs.create_index("unique_key",unique=True,name="queue_unique_job")
        # Claim order: priority then queue_rank_at (FIFO). discovered_at stays immutable.
        self.jobs.create_index(
            [("status",ASCENDING),("portal",ASCENDING),("profile",ASCENDING),
             ("next_attempt_at",ASCENDING),("priority",ASCENDING),("queue_rank_at",ASCENDING)],
            name="queue_claim_v2",
        )
        # Day / time-range metrics (today applied, today dead, etc.)
        self.jobs.create_index([("applied_at", ASCENDING)], name="queue_applied_at")
        self.jobs.create_index([("terminal_at", ASCENDING)], name="queue_terminal_at")
        self.jobs.create_index([("discovered_at", ASCENDING)], name="queue_discovered_at")
        self.jobs.create_index([("discovered_day", ASCENDING), ("portal", ASCENDING)], name="queue_discovered_day")
        self.jobs.create_index([("applied_day", ASCENDING), ("portal", ASCENDING)], name="queue_applied_day")
        self.jobs.create_index([("terminal_day", ASCENDING), ("status", ASCENDING)], name="queue_terminal_day")
        self.events.create_index([("job_id",ASCENDING),("created_at",ASCENDING)])
        self.health.create_index("heartbeat_at")

    @staticmethod
    def key(portal,source_job_id,profile): return f"{portal.strip().lower()}:{profile.strip().lower()}:{source_job_id.strip()}"
    def _event(self,job_id,event,worker="",details=None):
        now=_now()
        self.events.insert_one({
            "job_id":job_id,"event":event,"worker":worker,"details":details or {},
            "created_at":now,"event_day":_day_key(now),
        })
    # Rediscovery may re-open dead/retry jobs when the prior failure looks transient.
    # Permanent outcomes (applied, already applied, non-EA, expired, non-IT) stay closed.
    _REENQUEUE_STATUSES = frozenset({"dead", "retry"})
    # 2h cooldown — portals (LinkedIn/Greenhouse) go idle when dead rows never reopen.
    # Override via JOBBOTS_REENQUEUE_COOLDOWN_SECONDS (seconds).
    try:
        _REENQUEUE_COOLDOWN_SECONDS = max(
            0, int(os.getenv("JOBBOTS_REENQUEUE_COOLDOWN_SECONDS", str(2 * 3600)) or str(2 * 3600))
        )
    except ValueError:
        _REENQUEUE_COOLDOWN_SECONDS = 2 * 3600
    _REENQUEUE_RECOVERABLE = (
        "timeout", "timed out", "proxy", "browser launch", "nst", "cdp",
        "login", "auth", "session", "network", "connection", "rate limit",
        "cloudflare", "captcha", "terminated before", "exit 1", "exit 0",
        "uncaught", "bot exited",
        "form stall", "form_stalled", "modal", "navigation", "502", "503", "429",
        "no outcome", "empty result", "webSocketDebuggerUrl", "retrieving browser",
        "produced no application", "no application outcome",
        "direct queue application failed", "direct queue job produced",
        "verification code", "no application confirmation", "no confirmation",
        "no page confirmation", "submit clicked but no confirmation",
        "submit clicked but no page confirmation", "failed to click submit",
        "smartapply failed", "smartapply form automation failed",
        "form automation failed", "no apply flow",
        # ATS jobs wrongly killed when worker required NST profiles that do not exist
        # (Playwright-only path does not need them).
        "missing existing nst", "nstbrowser_profile_id_greenhouse",
        "nstbrowser_profile_id_lever", "nstbrowser_profile_id_ashby",
        "nstbrowser_profile_id_bamboohr", "nstbrowser_profile_id_google",
        "refusing to open",
    )
    _REENQUEUE_PERMANENT = (
        "already applied", "not easy apply", "plain apply", "company site",
        "company website", "external apply", "job expired", "job closed",
        "hotpatch_reject", "non-it", "title mismatch", "apply button not found",
        "easy apply button not found", "employer website",
        "bad title", "title hard reject", "company_rate_limit",
    )

    def enqueue(self,*,portal,profile,source_job_id,title,company,url,location="",description="",gate_score=None,gate_reason="",resume_policy="default",priority=100,metadata=None,initial_status="queued",date_posted=None):
        key=self.key(portal,str(source_job_id),profile); jid=str(uuid.uuid4()); now=_now(); day=_day_key(now)
        meta=dict(metadata or {})
        # Optional job-board posting date (string or datetime) for "when was this job listed".
        if date_posted is not None and "date_posted" not in meta:
            meta["date_posted"]=date_posted
        doc={
            "_id":jid,"unique_key":key,"portal":portal.lower(),"profile":profile.lower(),
            "source_job_id":str(source_job_id),"title":title,"company":company,"location":location,
            "url":url,"description":description,"gate_status":"approved","gate_score":gate_score,
            "gate_reason":gate_reason,"resume_policy":resume_policy,"resume_path":"",
            "status":initial_status,"priority":priority,"attempts":0,"max_attempts":3,
            "next_attempt_at":0.0,"lease_owner":None,"lease_expires_at":None,
            "last_error":"","result_url":"",
            # Lifecycle timestamps (all UTC datetime unless noted)
            "discovered_at":now,          # immutable first discovery
            "enqueued_at":now,            # first time entered queue
            "queue_rank_at":now,          # claim FIFO key (may move on captcha requeue)
            "claimed_at":None,            # last claim
            "first_claimed_at":None,      # first claim ever
            "terminal_at":None,           # when left active pipeline
            "applied_at":None,
            "already_applied_at":None,
            "dead_at":None,
            "skipped_at":None,
            "bookmarked_at":None,
            "rejected_at":None,
            "manual_review_at":None,
            "reenqueued_at":None,
            "updated_at":now,
            # Day-level keys (UTC YYYY-MM-DD) for cheap "today" rollups
            "discovered_day":day,
            "enqueued_day":day,
            "applied_day":None,
            "terminal_day":None,
            "metadata":meta,
        }
        try: self.jobs.insert_one(doc); self._event(jid,"enqueued",details={"source":portal}); return jid,True
        except DuplicateKeyError:
            old=self.jobs.find_one({"unique_key":key})
            if not old:
                return "", False
            reopened = self._maybe_reenqueue_dead(old, now=now, title=title, company=company, url=url,
                                                 location=location, description=description,
                                                 gate_score=gate_score, gate_reason=gate_reason,
                                                 metadata=metadata, portal=portal)
            return str(old["_id"]), reopened

    def _maybe_reenqueue_dead(self, old, *, now, title, company, url, location, description,
                              gate_score, gate_reason, metadata, portal) -> bool:
        """Re-open recoverable dead/retry rows when rediscovered. Returns True if reopened."""
        status = (old.get("status") or "").lower()
        if status not in self._REENQUEUE_STATUSES:
            return False
        err = (
            (old.get("last_error") or "")
            or (old.get("outcome_reason") or "")
            or str((old.get("metadata") or {}).get("last_outcome_reason") or "")
        ).lower()
        if any(m in err for m in self._REENQUEUE_PERMANENT):
            return False
        if err and not any(m in err for m in self._REENQUEUE_RECOVERABLE):
            return False
        # Empty error on dead: only reopen after cooldown (unknown ATS/portal failures).
        updated = old.get("updated_at") or old.get("terminal_at")
        if updated is not None:
            try:
                if getattr(updated, "tzinfo", None) is None:
                    updated = updated.replace(tzinfo=timezone.utc)
                age = (now - updated).total_seconds()
                if age < self._REENQUEUE_COOLDOWN_SECONDS:
                    return False
            except Exception:
                pass
        meta = dict(old.get("metadata") or {})
        if metadata:
            meta.update(metadata)
        meta["rediscovered"] = True
        meta["reenqueue_prev_error"] = (old.get("last_error") or "")[:500]
        meta["reenqueue_prev_terminal_at"] = (
            old.get("terminal_at").isoformat() if hasattr(old.get("terminal_at"), "isoformat") else old.get("terminal_at")
        )
        day = _day_key(now)
        patch = {
            "status": "queued",
            "attempts": 0,
            "next_attempt_at": 0.0,
            "lease_owner": None,
            "lease_expires_at": None,
            "last_error": "",
            "updated_at": now,
            "reenqueued_at": now,
            "queue_rank_at": now,  # re-enter FIFO at end of current priority band
            # Clear terminal stamps so "today dead" does not keep counting this row.
            "terminal_at": None,
            "terminal_day": None,
            "dead_at": None,
            "title": title or old.get("title"),
            "company": company or old.get("company"),
            "url": url or old.get("url"),
            "location": location or old.get("location") or "",
            "description": description or old.get("description") or "",
            "gate_score": gate_score if gate_score is not None else old.get("gate_score"),
            "gate_reason": gate_reason or old.get("gate_reason") or "",
            "metadata": meta,
        }
        # discovered_at / enqueued_at / discovered_day stay as original first-seen.
        res = self.jobs.update_one(
            {"_id": old["_id"], "status": {"$in": list(self._REENQUEUE_STATUSES)}},
            {"$set": patch},
        )
        if res.modified_count:
            self._event(str(old["_id"]), "reenqueued", details={"source": portal, "prev_error": err[:200]})
            return True
        return False
    def claim(self,*,worker,portals=None,profile=None,lease_seconds=900):
        # next_attempt_at is normally a unix float, but some requeue paths wrote
        # datetime. Accept either so LinkedIn/Indeed don't sit forever unclaimable.
        now_ts=time.time()
        now=_now()
        query={
            "status":{"$in":["queued","retry"]},
            "$or":[
                {"next_attempt_at":{"$lte":now_ts}},
                {"next_attempt_at":{"$lte":now}},
                {"next_attempt_at":{"$exists":False}},
                {"next_attempt_at":None},
            ],
        }
        if portals: query["portal"]={"$in":[p.lower() for p in portals]}
        if profile: query["profile"]=profile.lower()
        token=f"{worker}:{uuid.uuid4().hex[:10]}"
        # priority ASC then queue_rank_at ASC = FIFO within priority.
        # Fall back sort key: older docs without queue_rank_at still use discovered_at via
        # missing-field sort behavior (nulls first) — new enqueues always set queue_rank_at.
        row=self.jobs.find_one_and_update(
            query,
            {"$set":{
                "status":"leased",
                "lease_owner":token,
                "lease_expires_at":time.time()+lease_seconds,
                "updated_at":now,
                "claimed_at":now,
            },"$inc":{"attempts":1}},
            sort=[("priority",ASCENDING),("queue_rank_at",ASCENDING),("discovered_at",ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if not row:return None
        # first_claimed_at: set once (portable — TinyMongo tests lack $min)
        if row.get("first_claimed_at") is None:
            self.jobs.update_one(
                {"_id":row["_id"],"first_claimed_at":None},
                {"$set":{"first_claimed_at":now}},
            )
            row["first_claimed_at"]=now
        self._event(row["_id"],"claimed",worker,{"lease":token}); row["id"]=row["_id"]; row.pop("_id",None); return row
    def _finish(self,job_id,lease_owner,status,result_url="",*,reason="",outcome_detail=None):
        now=_now(); day=_day_key(now)
        patch={
            "status":status,"result_url":result_url,"updated_at":now,
            "lease_owner":None,"lease_expires_at":None,
            "terminal_at":now,"terminal_day":day,
        }
        at_field=_TERMINAL_AT_FIELD.get(status)
        if at_field:
            patch[at_field]=now
        if status=="applied":
            patch["applied_day"]=day
        if reason:
            patch["last_error"]=str(reason)[:2000]
            patch["outcome_reason"]=str(reason)[:2000]
        if outcome_detail:
            for k,v in outcome_detail.items():
                patch[f"metadata.{k}"]=v
        res=self.jobs.update_one({"_id":str(job_id),"status":"leased","lease_owner":lease_owner},{"$set":patch})
        if res.modified_count:
            self._event(str(job_id),status,lease_owner,{"result_url":result_url,"reason":reason or ""})
        return bool(res.modified_count)
    def complete(self,job_id,lease_owner,result_url="",*,reason=""):
        """New Easy Apply / SmartApply submission (or email-grade success)."""
        return self._finish(job_id,lease_owner,"applied",result_url,reason=reason)
    def already_applied(self,job_id,lease_owner,result_url="",*,reason=""):
        """Job was already applied on the portal — terminal, not a new win."""
        return self._finish(
            job_id,lease_owner,"already_applied",result_url,
            reason=reason or "already applied",
            outcome_detail={"outcome":"already_applied"},
        )
    def skipped(self,job_id,lease_owner,result_url="",*,reason=""):
        """Intentional skip (cover letter, policy) — terminal for metrics."""
        return self._finish(
            job_id,lease_owner,"skipped",result_url,
            reason=reason or "skipped",
            outcome_detail={"outcome":"skipped"},
        )
    def bookmarked(self,job_id,lease_owner,result_url="",*,reason=""):
        return self._finish(
            job_id,lease_owner,"bookmarked",result_url,
            reason=reason or "company_site bookmarked",
            outcome_detail={"outcome":"bookmarked","application_method":"company_site"},
        )
    def manual_review(self,job_id,lease_owner,result_url="",reason=""):
        # Terminal, non-retryable outcome for jobs whose apply type could not be
        # resolved after visiting (Metro-Van lease-and-verify). Prevents endless retry.
        return self._finish(job_id,lease_owner,"manual_review",result_url,reason=reason)
    def set_application_method(self,job_id,method,*,lease_owner=None):
        # Persist the *resolved* apply method after a lease-and-verify visit so an
        # ``unverified`` record becomes ``easy_apply`` or ``company_site`` on the record.
        q={"_id":str(job_id)}
        if lease_owner is not None:q["lease_owner"]=lease_owner
        res=self.jobs.update_one(q,{"$set":{"metadata.application_method":method,"updated_at":_now()}})
        if res.modified_count:self._event(str(job_id),"apply_method_resolved",lease_owner or "",{"application_method":method})
        return bool(res.modified_count)
    def renew(self,job_id,lease_owner,lease_seconds=900):
        return bool(self.jobs.update_one({"_id":str(job_id),"status":"leased","lease_owner":lease_owner},{"$set":{"lease_expires_at":time.time()+lease_seconds,"updated_at":_now()}}).modified_count)
    def fail(self,job_id,lease_owner,error,*,retryable=True,base_delay_seconds=60):
        row=self.jobs.find_one({"_id":str(job_id),"lease_owner":lease_owner},{"attempts":1,"max_attempts":1})
        if not row:return "lost_lease"
        retry=retryable and row.get("attempts",0)<row.get("max_attempts",3); status="retry" if retry else "dead"; delay=base_delay_seconds*(2**max(0,row.get("attempts",1)-1)) if retry else 0
        now=_now(); day=_day_key(now)
        patch={
            "status":status,"last_error":str(error)[:2000],"next_attempt_at":time.time()+delay,
            "updated_at":now,"lease_owner":None,"lease_expires_at":None,
        }
        if status=="dead":
            patch.update({"terminal_at":now,"terminal_day":day,"dead_at":now})
        self.jobs.update_one({"_id":str(job_id),"lease_owner":lease_owner},{"$set":patch})
        self._event(str(job_id),status,lease_owner,{"error":str(error),"delay":delay}); return status
    def requeue_captcha_cf(self,job_id,lease_owner,error,*,priority_floor=1000):
        """Track a CAPTCHA/Cloudflare failure and push the job to the end of its portal+profile queue.

        Claim order is ``priority ASC, queue_rank_at ASC``. Bumping priority above
        the floor and refreshing ``queue_rank_at`` (not ``discovered_at``) makes this
        job claimable only after fresher work — while preserving original discovery time.
        Respects ``max_attempts`` — exhausted jobs go ``dead`` like ``fail()``.
        """
        row=self.jobs.find_one({"_id":str(job_id),"lease_owner":lease_owner},{"attempts":1,"max_attempts":1,"priority":1,"metadata":1})
        if not row:return "lost_lease"
        attempts=int(row.get("attempts",0) or 0); max_attempts=int(row.get("max_attempts",3) or 3)
        now=_now(); day=_day_key(now)
        if attempts>=max_attempts:
            self.jobs.update_one({"_id":str(job_id),"lease_owner":lease_owner},{"$set":{
                "status":"dead","last_error":str(error)[:2000],"next_attempt_at":0.0,
                "updated_at":now,"lease_owner":None,"lease_expires_at":None,
                "terminal_at":now,"terminal_day":day,"dead_at":now,
            }})
            self._event(str(job_id),"dead",lease_owner,{"error":str(error),"cause":"captcha_cf_exhausted"}); return "dead"
        meta=row.get("metadata") or {}; prev=int(meta.get("captcha_cf_retry_count") or meta.get("captcha_cf_failures") or 0)
        retry_count=prev+1
        # Each captcha/CF bounce gets a higher priority so it stays behind new work.
        new_priority=max(int(row.get("priority") or 100),int(priority_floor))+retry_count
        patch={
            "status":"queued","priority":new_priority,"last_error":str(error)[:2000],
            "next_attempt_at":0.0,
            # Do NOT overwrite discovered_at — metrics need original first-seen time.
            "queue_rank_at":now,
            "updated_at":now,"lease_owner":None,"lease_expires_at":None,
            "metadata.captcha_cf_retry_count":retry_count,
            "metadata.captcha_cf_failures":retry_count,
            "metadata.captcha_cf_last_reason":str(error)[:2000],
            "metadata.captcha_cf_last_at":now.isoformat() if hasattr(now,"isoformat") else now,
        }
        self.jobs.update_one({"_id":str(job_id),"lease_owner":lease_owner},{"$set":patch})
        self._event(str(job_id),"captcha_cf_requeued",lease_owner,{"error":str(error),"priority":new_priority,"captcha_cf_retry_count":retry_count})
        return "queued"
    def release_expired(self):
        rows=list(self.jobs.find({"status":"leased","lease_expires_at":{"$lt":time.time()}},{"_id":1,"lease_owner":1}))
        now=_now()
        for row in rows:
            self.jobs.update_one(
                {"_id":row["_id"],"status":"leased"},
                {"$set":{
                    "status":"retry","next_attempt_at":time.time(),
                    "last_error":"worker lease expired","lease_owner":None,
                    "lease_expires_at":None,"updated_at":now,
                    "lease_expired_at":now,
                }},
            )
            self._event(row["_id"],"lease_expired",row.get("lease_owner") or "")
        return len(rows)
    def heartbeat(self,worker,role,*,portal="",profile="",status="healthy",current_job_id=None,error=""):
        self.health.update_one({"_id":worker},{"$set":{"role":role,"portal":portal,"profile":profile,"status":status,"current_job_id":current_job_id,"last_error":error[:2000],"heartbeat_at":time.time(),"updated_at":_now()}},upsert=True)
    def counts(self):return {r["_id"]:r["n"] for r in self.jobs.aggregate([{"$group":{"_id":"$status","n":{"$sum":1}}}])}
    def drop_test_database(self):
        if self.database.startswith("jobbots_test_"):self.client.drop_database(self.database)

def runtime_worker_name(role):return os.getenv("WORKER_ID") or f"{socket.gethostname()}:{role}:{os.getpid()}"

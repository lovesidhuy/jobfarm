from core.job_queue import JobQueue, _day_key


def test_enqueue_deduplicates_and_claims(tmp_path):
    q=JobQueue(tmp_path/"q.db")
    args=dict(portal="indeed",profile="it",source_job_id="abc",title="Support",company="Acme",url="https://x")
    first,created=q.enqueue(**args); second,created2=q.enqueue(**args)
    assert created is True and created2 is False and first==second
    row=q.jobs.find_one({"_id":first})
    assert row["discovered_at"] is not None
    assert row["enqueued_at"] is not None
    assert row["queue_rank_at"] is not None
    assert row["discovered_day"] == _day_key()
    assert row["applied_at"] is None
    job=q.claim(worker="w1",portals=["indeed"],profile="it")
    assert job["id"]==first and job["attempts"]==1
    claimed=q.jobs.find_one({"_id":first})
    assert claimed["claimed_at"] is not None
    assert claimed["first_claimed_at"] is not None
    assert q.complete(first,job["lease_owner"],"https://result") is True
    done=q.jobs.find_one({"_id":first})
    assert done["status"]=="applied"
    assert done["applied_at"] is not None
    assert done["terminal_at"] is not None
    assert done["applied_day"] == _day_key()
    assert done["terminal_day"] == _day_key()
    assert q.counts()=={"applied":1}
    q.drop_test_database()


def test_dead_and_already_applied_get_terminal_timestamps(tmp_path):
    q=JobQueue(tmp_path/"q-term.db")
    jid,_=q.enqueue(portal="linkedin",profile="it",source_job_id="t1",title="T",company="C",url="https://x/1")
    job=q.claim(worker="w",portals=["linkedin"],profile="it")
    assert q.fail(jid,job["lease_owner"],"boom",retryable=False)=="dead"
    dead=q.jobs.find_one({"_id":jid})
    assert dead["dead_at"] is not None and dead["terminal_at"] is not None

    jid2,_=q.enqueue(portal="linkedin",profile="it",source_job_id="t2",title="T2",company="C",url="https://x/2")
    job2=q.claim(worker="w",portals=["linkedin"],profile="it")
    assert q.already_applied(jid2,job2["lease_owner"]) is True
    aa=q.jobs.find_one({"_id":jid2})
    assert aa["already_applied_at"] is not None and aa["terminal_at"] is not None
    q.drop_test_database()


def test_captcha_requeue_preserves_discovered_at(tmp_path):
    q=JobQueue(tmp_path/"q-captcha.db")
    jid,_=q.enqueue(portal="indeed",profile="it",source_job_id="c1",title="T",company="C",url="https://x/c")
    before=q.jobs.find_one({"_id":jid})
    disc=before["discovered_at"]
    job=q.claim(worker="w",portals=["indeed"],profile="it")
    assert q.requeue_captcha_cf(jid,job["lease_owner"],"cf challenge")=="queued"
    after=q.jobs.find_one({"_id":jid})
    assert after["discovered_at"]==disc
    assert after["queue_rank_at"]>=disc or after["queue_rank_at"] is not None
    assert after["status"]=="queued"
    q.drop_test_database()


def test_enqueue_reopens_recoverable_dead_after_cooldown(tmp_path):
    """Rediscovery re-opens dead jobs whose last_error looks transient."""
    from datetime import datetime, timedelta, timezone
    q = JobQueue(tmp_path / "q-reopen.db")
    jid, created = q.enqueue(
        portal="linkedin", profile="it", source_job_id="li-1",
        title="IT Support", company="Acme", url="https://li/1",
    )
    assert created is True
    job = q.claim(worker="w", portals=["linkedin"], profile="it")
    assert q.fail(jid, job["lease_owner"], "browser launch failed: proxy hang", base_delay_seconds=0) in (
        "retry", "dead",
    )
    # Force terminal dead + aged updated_at so cooldown has elapsed.
    old_ts = datetime.now(timezone.utc) - timedelta(hours=5)
    q.jobs.update_one(
        {"_id": jid},
        {"$set": {"status": "dead", "last_error": "browser launch failed: proxy hang", "updated_at": old_ts, "attempts": 3}},
    )
    again, reopened = q.enqueue(
        portal="linkedin", profile="it", source_job_id="li-1",
        title="IT Support", company="Acme", url="https://li/1",
    )
    assert again == jid and reopened is True
    row = q.jobs.find_one({"_id": jid})
    assert row["status"] == "queued"
    assert row["attempts"] == 0
    assert row["metadata"].get("rediscovered") is True
    # Applied jobs must never reopen.
    claimed = q.claim(worker="w2", portals=["linkedin"], profile="it")
    assert q.complete(jid, claimed["lease_owner"], "https://done") is True
    _, reopened2 = q.enqueue(
        portal="linkedin", profile="it", source_job_id="li-1",
        title="IT Support", company="Acme", url="https://li/1",
    )
    assert reopened2 is False
    assert q.jobs.find_one({"_id": jid})["status"] == "applied"
    q.drop_test_database()


def test_enqueue_reopens_form_stalled_and_submit_confirm_misses(tmp_path):
    """LinkedIn form stalls and ATS submit-confirm misses must re-enter the queue."""
    from datetime import datetime, timedelta, timezone

    q = JobQueue(tmp_path / "q-reopen-stall.db")
    cases = [
        ("linkedin", "li-stall", "form_stalled_validation: Select an option Yes No"),
        ("greenhouse", "gh-confirm", "Submit clicked but no confirmation detected"),
        ("indeed", "in-outcome", "Indeed direct queue job produced no application outcome"),
        ("workopolis", "wp-smart", "SmartApply form automation failed"),
    ]
    old_ts = datetime.now(timezone.utc) - timedelta(hours=3)
    for portal, sid, err in cases:
        jid, _ = q.enqueue(
            portal=portal, profile="it", source_job_id=sid,
            title="IT Support", company="Acme", url=f"https://x/{sid}",
        )
        q.jobs.update_one(
            {"_id": jid},
            {"$set": {
                "status": "dead",
                "last_error": err,
                "updated_at": old_ts,
                "attempts": 3,
            }},
        )
        again, reopened = q.enqueue(
            portal=portal, profile="it", source_job_id=sid,
            title="IT Support", company="Acme", url=f"https://x/{sid}",
        )
        assert again == jid and reopened is True, f"{portal}/{sid} should reopen for: {err}"
        assert q.jobs.find_one({"_id": jid})["status"] == "queued"
    q.drop_test_database()


def test_enqueue_does_not_reopen_permanent_dead(tmp_path):
    from datetime import datetime, timedelta, timezone
    q = JobQueue(tmp_path / "q-perm.db")
    jid, _ = q.enqueue(
        portal="glassdoor", profile="it", source_job_id="gd-1",
        title="Dev", company="Co", url="https://gd/1",
    )
    old_ts = datetime.now(timezone.utc) - timedelta(hours=5)
    q.jobs.update_one(
        {"_id": jid},
        {"$set": {
            "status": "dead",
            "last_error": "Apply button not found on detail page",
            "updated_at": old_ts,
            "attempts": 3,
        }},
    )
    _, reopened = q.enqueue(
        portal="glassdoor", profile="it", source_job_id="gd-1",
        title="Dev", company="Co", url="https://gd/1",
    )
    assert reopened is False
    assert q.jobs.find_one({"_id": jid})["status"] == "dead"
    q.drop_test_database()


def test_retry_and_dead_letter(tmp_path):
    q=JobQueue(tmp_path/"q.db")
    jid,_=q.enqueue(portal="glassdoor",profile="it",source_job_id="1",title="T",company="C",url="u")
    for expected in ("retry","retry","dead"):
        job=q.claim(worker="w",profile="it")
        assert q.fail(jid,job["lease_owner"],"boom",base_delay_seconds=0)==expected
    assert q.counts()=={"dead":1}
    q.drop_test_database()

def test_company_site_lead_can_finish_as_bookmarked(tmp_path):
    q=JobQueue(tmp_path/"q.db")
    jid,_=q.enqueue(portal="indeed",profile="it",source_job_id="site-1",title="T",company="C",url="u",metadata={"application_method":"company_site"})
    job=q.claim(worker="w")
    assert q.bookmarked(jid,job["lease_owner"],"u") is True
    assert q.counts()=={"bookmarked":1}
    q.drop_test_database()

def test_active_lease_can_be_renewed(tmp_path):
    q=JobQueue(tmp_path/"q.db")
    jid,_=q.enqueue(portal="indeed",profile="it",source_job_id="renew",title="T",company="C",url="u")
    job=q.claim(worker="w",lease_seconds=1)
    assert q.renew(jid,job["lease_owner"],lease_seconds=60) is True
    assert q.release_expired()==0
    q.drop_test_database()


def test_captcha_cf_requeue_goes_to_end_of_same_platform_queue(tmp_path):
    """CAPTCHA/CF failures are tracked and claimed after fresher same-portal work."""
    q=JobQueue(tmp_path/"q.db")
    blocked,_=q.enqueue(portal="indeed",profile="it",source_job_id="cf-1",title="Blocked",company="A",url="u1",priority=100)
    fresh,_=q.enqueue(portal="indeed",profile="it",source_job_id="fresh-1",title="Fresh",company="B",url="u2",priority=100)
    # Other platform stays independent — same profile, different portal.
    other,_=q.enqueue(portal="glassdoor",profile="it",source_job_id="gd-1",title="Other",company="C",url="u3",priority=100)

    job=q.claim(worker="w",portals=["indeed"],profile="it")
    assert job["id"]==blocked
    assert q.requeue_captcha_cf(blocked,job["lease_owner"],"cloudflare challenge blocked")=="queued"

    row=q.jobs.find_one({"_id":blocked})
    assert row["status"]=="queued"
    assert row["lease_owner"] is None
    assert row["priority"]>=1000
    assert row["metadata"]["captcha_cf_retry_count"]==1
    assert row["metadata"]["captcha_cf_failures"]==1
    assert "cloudflare" in row["metadata"]["captcha_cf_last_reason"]
    assert row["metadata"]["captcha_cf_last_at"] is not None
    assert q.events.find_one({"job_id":blocked,"event":"captcha_cf_requeued"}) is not None

    # Next claim on indeed/it must be the fresh job, not the requeued captcha one.
    next_job=q.claim(worker="w",portals=["indeed"],profile="it")
    assert next_job["id"]==fresh
    assert q.complete(fresh,next_job["lease_owner"],"u2") is True

    # Only then is the captcha/CF job claimed again (still indeed/it).
    again=q.claim(worker="w",portals=["indeed"],profile="it")
    assert again["id"]==blocked
    assert again["attempts"]==2

    # Glassdoor job is untouched by indeed claims.
    gd=q.claim(worker="w",portals=["glassdoor"],profile="it")
    assert gd["id"]==other
    q.drop_test_database()


def test_captcha_cf_requeue_respects_max_attempts(tmp_path):
    q=JobQueue(tmp_path/"q.db")
    jid,_=q.enqueue(portal="indeed",profile="it",source_job_id="cf-max",title="T",company="C",url="u")
    for expected in ("queued","queued","dead"):
        job=q.claim(worker="w",portals=["indeed"],profile="it")
        assert q.requeue_captcha_cf(jid,job["lease_owner"],"captcha challenge")==expected
    row=q.jobs.find_one({"_id":jid})
    assert row["status"]=="dead"
    assert row["metadata"]["captcha_cf_retry_count"]==2
    assert q.counts()=={"dead":1}
    q.drop_test_database()

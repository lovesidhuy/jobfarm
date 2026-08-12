# Two-stage job pipeline

The platform now has an explicit discovery stage and a durable application queue.

## Discovery

Run one bot or several bots in parallel with `--stage discover`. Portal loops search,
scrape descriptions, run their gates, and enqueue approved jobs. They do not submit.

```bash
cd automation_monorepo
python supervisor.py --stage discover --parallel --once
python supervisor.py --stage discover --only indeed_it --once
```

The authoritative queue is stored in MongoDB collections inside the unified
`jobbots` database. Local and server runs use the same schema; configure only
`MONGODB_URI` and `JOBBOTS_MONGO_DATABASE`.

```bash
python scripts/job_queue_admin.py stats
python scripts/job_queue_admin.py recover
```

Queue states are `queued`, `leased`, `retry`, `applied`, and `dead`. Claims are
transactional. Expired worker leases return to retry. Every enqueue, claim, retry,
expiry and completion is written to `job_events`; worker liveness is stored in
`worker_health`.

### Policy screening before queueing (Phase I-B)

All screening is part of discovery. Before a job is written to the queue,
`core/discovery/planner.py::_screen_and_enqueue` runs, in order:

1. **Geo / work-mode / apply-type policy**
   (`core/discovery/classification/location_policy.py`), the candidate is in
   Metro Vancouver:
   - Metro Van + Easy Apply → **apply** (`easy_apply`).
   - Metro Van + company-site → **save** (`company_site` → bookmark).
   - Metro Van + unknown apply-type → visit & auto-route.
   - Outside Metro Van → require **confirmed remote + confirmed Easy Apply**.
     Hybrid, on-site, remote-on-company-site, and **unknown/unverified
     apply-type** jobs are rejected (`outside_metro_apply_type_unverified`) and
     never queued.
   - Metro Van + unknown apply-type → **not** converted to easy-apply. Kept as
     `application_method="unverified"` and routed to the Phase II verification
     path (visit → Easy Apply → apply; external → bookmark).
2. **IT-fit AI screening** (`screen_job_with_ai`).

Jobs rejected by either step never reach the queue, so invalid postings
(out-of-province hybrid/on-site, remote company-site, etc.) are never applied to.
Discovery scrapes the empty/`Remote` location passes as remote-only +
easy-apply-only so company-site postings are excluded at the source. Set
`DISCOVERY_GEO_POLICY=0` to disable the geo policy.

**The application stage does not re-screen for job fit, geography, or remote
status** — it only performs defensive final validation (already-applied,
external→bookmark, title mismatch) before applying or bookmarking.

### Global location intent (all portals)

Equivalent rules for Indeed, Glassdoor, LinkedIn, and Workopolis:

- Metro Vancouver on-platform/easy application → apply.
- Metro Vancouver external/company-site → bookmark/save.
- Metro Vancouver unknown method → verify safely or save; never assume Easy Apply.
- Outside Metro Vancouver → confirmed fully remote **and** confirmed on-platform/easy only.
- Reject outside-Metro hybrid, on-site, external/company-site, and unknown/unverified.

Portal-specific “on-platform” meaning:

- Indeed/Glassdoor: Indeed Easy Apply / SmartApply.
- LinkedIn: LinkedIn Easy Apply (confirmed remote + Easy Apply outside Metro Van is allowed).
- Workopolis: bot can submit without redirecting to an external ATS.

### Metro-Van lease-and-verify (unverified apply type)

Metro-Vancouver jobs whose apply type could not be classified at discovery are
queued as `status="queued"` + `metadata.application_method="unverified"` (a
leasable status, *not* `status="unverified"`, which is not leasable). Phase II
resolves them safely:

1. **Metro-Van only.** The worker re-checks `metadata.region`; a non-Metro-Van
   verify job is degraded to bookmark-only and never submitted (safeguard #1).
2. **Bookmark first.** `JOB_QUEUE_BOOKMARK_FIRST=1` saves the lead before any
   apply attempt.
3. **Submit only on Easy Apply / SmartApply.** With `JOB_QUEUE_VERIFY_APPLY_TYPE=1`
   the applier submits only after positively detecting an Indeed Easy Apply /
   SmartApply flow; an external/company-site page is saved, never submitted.
4. **Resolved method is persisted.** After the visit the record's
   `application_method` is rewritten to `easy_apply` or `company_site`.
5. **Terminal states (never endless retry).**
   - submission → `applied`
   - external/company-site → `bookmarked`
   - unresolved apply type → `manual_review` (non-retryable); only transient
     errors (CAPTCHA, network) retry, and only within the attempt budget.
6. **Per-job env isolation.** Verify/bookmark flags are cleared before every
   dispatch so a stale flag cannot leak into the next job (safeguard #7).

## Resume policy

IT jobs are queued with `tailored`; General jobs use `default`. Resume generation
belongs to the application stage, immediately before the portal form is opened.
This prevents generating documents for jobs that never reach an application worker.

## Company-site leads

Discovery is scrape-only and never changes portal bookmark state. Approved Easy Apply
and company-site jobs are queued with an `application_method`. During the execution
stage, the exact-job worker opens and bookmarks the job first. Easy Apply records then
continue through submission; company-site records finish with queue status
`bookmarked` without entering the employer form.

LinkedIn discovery can save non-Easy-Apply/company-site jobs by setting:

```bash
LINKEDIN_SAVE_COMPANY_SITE_JOBS=true
```

LinkedIn writes deduplicated JSONL records to
`legacy/linkedin-ai-auto-apply-source/company_site_jobs.jsonl`. Override that path
with `LINKEDIN_COMPANY_SITE_LEADS_PATH` when storing it on the server data volume.

The LinkedIn hybrid runner also honors `JOBBOT_MODE=discover`: locally approved
Easy Apply jobs are inserted directly into the shared queue and are not submitted
during that run.

## Safety

- Uniqueness is portal + profile + source job ID.
- A job can be leased by only one worker.
- Retry count is bounded; exhausted jobs become `dead`.
- Application workers must complete or fail using their lease token.
- `recover` releases jobs held by crashed workers after lease expiry.

## Application consumers

Application workers claim one exact record, open its saved URL, and invoke the
matching Indeed, Glassdoor, Workopolis, or LinkedIn direct-job adapter. They write a
result file which the worker uses to complete, retry, or dead-letter the leased job.

```bash
# One queued job, then exit
python supervisor.py --stage apply --once

# Four continuous consumers across all portals
python supervisor.py --stage apply --workers 4

# Portal/profile constrained pool
python supervisor.py --stage apply --portal indeed --profile it --workers 2
```

LinkedIn jobs can be imported from an approved discovery export with:

```bash
python scripts/job_queue_admin.py enqueue-json jobs.json --portal linkedin --profile it
```

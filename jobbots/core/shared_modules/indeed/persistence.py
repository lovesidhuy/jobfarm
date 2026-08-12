from ._bootstrap import *  # noqa: F403
from jobbots.core.utils import truncate_for_csv


def _ensure_dirs() -> None:
    make_directories([INDEED_APPLIED_FILE, INDEED_FAILED_FILE, INDEED_SKIPPED_FILE,
                      logs_folder_path + "/screenshots"])
    try:
        os.makedirs(resolve_project_path("data/extracted_jobs"), exist_ok=True)
    except Exception:
        os.makedirs("data/extracted_jobs", exist_ok=True)


def get_applied_indeed_job_ids() -> set:
    ids: set = set()
    ids.update(get_job_ids(INDEED_PLATFORM_TAG, "applied"))
    try:
        with open(INDEED_APPLIED_FILE, 'r', encoding='utf-8') as f:
            for row in csv.reader(f):
                if row and row[0] != 'Job ID':
                    ids.add(row[0])
    except FileNotFoundError:
        pass
    return ids


def get_skipped_indeed_job_ids() -> set:
    ids: set = set()
    ids.update(get_job_ids(INDEED_PLATFORM_TAG, "skipped"))
    try:
        with open(INDEED_SKIPPED_FILE, 'r', encoding='utf-8') as f:
            for row in csv.reader(f):
                if row and row[0] != 'Job ID':
                    ids.add(row[0])
    except FileNotFoundError:
        pass
    return ids


def _save_skipped(job_id, title, company, location, reason, date_skipped=None, job_link=""):
    if not date_skipped:
        date_skipped = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        record = {
            'Job ID': truncate_for_csv(job_id), 'Title': truncate_for_csv(title),
            'Company': truncate_for_csv(company), 'Work Location': truncate_for_csv(location),
            'Reason': truncate_for_csv(reason), 'Date Skipped': truncate_for_csv(date_skipped),
            'Job Link': truncate_for_csv(job_link),
        }
        with open(INDEED_SKIPPED_FILE, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['Job ID', 'Title', 'Company', 'Work Location',
                          'Reason', 'Date Skipped', 'Job Link']
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                w.writeheader()
            w.writerow(record)
        save_job_record(INDEED_PLATFORM_TAG, "skipped", record)
        log_training_event(
            "job_record_saved",
            status="skipped",
            job={"job_id": job_id, "title": title, "company": company,
                 "location": location, "job_link": job_link},
            reason=reason,
        )
    except Exception as e:
        print_lg(f"[Indeed] Failed to save skipped job: {e}")


def _save_applied(job_id, title, company, location, description,
                  experience, skills, date_applied, job_link):
    try:
        record = {
            'Job ID': truncate_for_csv(job_id), 'Title': truncate_for_csv(title),
            'Company': truncate_for_csv(company), 'Work Location': truncate_for_csv(location),
            'About Job': truncate_for_csv(description), 'Experience Required': truncate_for_csv(experience),
            'Skills Required': truncate_for_csv(skills), 'Date Applied': truncate_for_csv(date_applied),
            'Job Link': truncate_for_csv(job_link),
        }
        with open(INDEED_APPLIED_FILE, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['Job ID', 'Title', 'Company', 'Work Location',
                          'About Job', 'Experience Required', 'Skills Required',
                          'Date Applied', 'Job Link']
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                w.writeheader()
            w.writerow(record)
        save_job_record(INDEED_PLATFORM_TAG, "applied", record)
        log_training_event(
            "job_record_saved",
            status="applied_or_saved",
            job={"job_id": job_id, "title": title, "company": company,
                 "location": location, "job_link": job_link},
        )
    except Exception as e:
        print_lg(f"[Indeed] Failed to save applied job: {e}")
        log_training_event("job_record_save_error", status="applied_or_saved",
                           job={"job_id": job_id, "title": title, "company": company,
                                "job_link": job_link},
                           error=f"{type(e).__name__}: {e}")


def _save_failed(job_id, title, company, job_link, reason):
    try:
        record = {
            'Job ID': truncate_for_csv(job_id), 'Title': truncate_for_csv(title),
            'Company': truncate_for_csv(company), 'Job Link': truncate_for_csv(job_link),
            'Reason': truncate_for_csv(reason), 'Date Tried': datetime.now(),
        }
        with open(INDEED_FAILED_FILE, 'a', newline='', encoding='utf-8') as f:
            fieldnames = ['Job ID', 'Title', 'Company', 'Job Link', 'Reason', 'Date Tried']
            w = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                w.writeheader()
            w.writerow(record)
        save_job_record(INDEED_PLATFORM_TAG, "failed", record)
        log_training_event(
            "job_record_saved",
            status="failed",
            reason=reason,
            job={"job_id": job_id, "title": title, "company": company,
                 "job_link": job_link},
        )
    except Exception as e:
        print_lg(f"[Indeed] Failed to save failed job: {e}")
        log_training_event("job_record_save_error", status="failed",
                           reason=reason,
                           job={"job_id": job_id, "title": title, "company": company,
                                "job_link": job_link},
                           error=f"{type(e).__name__}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot helper  (Playwright API)
# ─────────────────────────────────────────────────────────────────────────────

def _screenshot(page, job_id: str, failed_at: str) -> str:
    try:
        name = f"{job_id} - {failed_at} - {datetime.now()}.png"
        path = (logs_folder_path + "/screenshots/" + name).replace("//", "/")
        path = re.sub(r'[<>:"|?*]', '-', path)
        page.screenshot(path=path)
        print_lg(f"[Indeed] Screenshot saved: {name}")
        log_training_event("screenshot_saved", job={**_current_job_meta, "job_id": job_id},
                           failed_at=failed_at, screenshot=name,
                           page=page_dom_snapshot(page, limit=35))
        return name
    except Exception as e:
        print_lg(f"[Indeed] Could not save screenshot: {e}")
        return "screenshot_failed"



# ─────────────────────────────────────────────────────────────────────────────
# Status stream JSONL helper
# ─────────────────────────────────────────────────────────────────────────────

def log_job_status_event(event_type: str, job_id: str, title: str, company: str, url: str, reason: str = None, source: str = "indeed") -> None:
    import json
    from datetime import datetime
    import os
    
    try:
        status_file = resolve_project_path("data/job_status_stream.jsonl")
    except Exception:
        status_file = "data/job_status_stream.jsonl"
        
    try:
        os.makedirs(os.path.dirname(status_file), exist_ok=True)
        event = {
            "event_type": event_type,
            "job_id": job_id,
            "title": title,
            "company": company,
            "url": url,
            "timestamp": datetime.now().isoformat(),
            "reason": reason,
            "source": source
        }
        with open(status_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        print_lg(f"[Status Stream] {event_type} - {job_id} ({title} at {company})")
    except Exception as e:
        print_lg(f"[Status Stream Error] Failed to log {event_type} for {job_id}: {e}")


def log_job_status_event_from_meta(event_type: str, reason: str = None) -> None:
    try:
        import sys
        meta = None
        boot = sys.modules.get("jobbots.core.shared_modules.indeed._bootstrap")
        if boot and boot.__dict__.get("_current_job_meta"):
            meta = boot.__dict__.get("_current_job_meta")
        if not meta:
            for mod_name in ("jobbots.core.shared_modules.indeed.loop", "jobbots.core.shared_modules.indeed.smartapply", "jobbots.core.shared_modules.indeed.questions"):
                m = sys.modules.get(mod_name)
                if m and m.__dict__.get("_current_job_meta"):
                    meta = m.__dict__.get("_current_job_meta")
                    break
        if meta:
            job_id = meta.get("job_id") or "Unknown"
            title = meta.get("title") or "Unknown"
            company = meta.get("company") or "Unknown"
            url = meta.get("job_link") or meta.get("job_href") or "Unknown"
            log_job_status_event(event_type, job_id, title, company, url, reason=reason, source="indeed")
    except Exception as e:
        print_lg(f"[Status Stream Error] log_job_status_event_from_meta error: {e}")


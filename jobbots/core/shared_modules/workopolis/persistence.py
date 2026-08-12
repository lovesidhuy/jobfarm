from ._bootstrap import *  # noqa: F403

def _ensure_dirs() -> None:
    make_directories([WORKOPOLIS_APPLIED_FILE, WORKOPOLIS_FAILED_FILE, WORKOPOLIS_SKIPPED_FILE,
                      logs_folder_path + "/screenshots"])


def get_applied_workopolis_job_ids() -> set:
    ids: set = set()
    ids.update(get_job_ids(_bot_name, "applied"))
    try:
        with open(WORKOPOLIS_APPLIED_FILE, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if row and row[0] != "Job ID":
                    ids.add(row[0])
    except FileNotFoundError:
        pass
    return ids


def get_skipped_workopolis_job_ids() -> set:
    ids: set = set()
    ids.update(get_job_ids(_bot_name, "skipped"))
    try:
        with open(WORKOPOLIS_SKIPPED_FILE, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                if row and row[0] != "Job ID":
                    ids.add(row[0])
    except FileNotFoundError:
        pass
    return ids


def _save_skipped(job_id, title, company, location, reason, date_skipped=None, job_link=""):
    if not date_skipped:
        date_skipped = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        record = {
            "Job ID":            truncate_for_csv(job_id),
            "Title":             truncate_for_csv(title),
            "Company":           truncate_for_csv(company),
            "Work Location":     truncate_for_csv(location),
            "Reason":            truncate_for_csv(reason),
            "Date Skipped":      truncate_for_csv(date_skipped),
            "Job Link":          truncate_for_csv(job_link),
        }
        with open(WORKOPOLIS_SKIPPED_FILE, "a", newline="", encoding="utf-8") as f:
            fieldnames = ["Job ID", "Title", "Company", "Work Location", "Reason", "Date Skipped", "Job Link"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(record)
        save_job_record(_bot_name, "skipped", record)
        log_training_event("workopolis_job_record_saved", status="skipped",
                           job={"job_id": job_id, "title": title, "company": company,
                                "location": location, "job_link": job_link},
                           reason=reason)
    except Exception as e:
        print_lg(f"[Workopolis] Failed to save skipped job: {e}")


def _save_applied(job_id, title, company, location, description,
                  experience, skills, date_applied, job_link):
    try:
        record = {
            "Job ID":            truncate_for_csv(job_id),
            "Title":             truncate_for_csv(title),
            "Company":           truncate_for_csv(company),
            "Work Location":     truncate_for_csv(location),
            "About Job":         truncate_for_csv(description),
            "Experience Required": truncate_for_csv(experience),
            "Skills Required":   truncate_for_csv(skills),
            "Date Applied":      truncate_for_csv(date_applied),
            "Job Link":          truncate_for_csv(job_link),
        }
        with open(WORKOPOLIS_APPLIED_FILE, "a", newline="", encoding="utf-8") as f:
            fieldnames = ["Job ID", "Title", "Company", "Work Location",
                          "About Job", "Experience Required", "Skills Required",
                          "Date Applied", "Job Link"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(record)
        save_job_record(_bot_name, "applied", record)
        log_training_event("workopolis_job_record_saved", status="applied",
                           job={"job_id": job_id, "title": title, "company": company,
                                "location": location, "job_link": job_link})
    except Exception as e:
        print_lg(f"[Workopolis] Failed to save applied job: {e}")


def _save_failed(job_id, title, company, job_link, reason):
    try:
        record = {
            "Job ID":    truncate_for_csv(job_id),
            "Title":     truncate_for_csv(title),
            "Company":   truncate_for_csv(company),
            "Job Link":  truncate_for_csv(job_link),
            "Reason":    truncate_for_csv(reason),
            "Date Tried": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        with open(WORKOPOLIS_FAILED_FILE, "a", newline="", encoding="utf-8") as f:
            fieldnames = ["Job ID", "Title", "Company", "Job Link", "Reason", "Date Tried"]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if f.tell() == 0:
                writer.writeheader()
            writer.writerow(record)
        save_job_record(_bot_name, "failed", record)
    except Exception as e:
        print_lg(f"[Workopolis] Failed to save failed job: {e}")


def _save_manual_link(job_id, title, company, job_link, reason):
    _save_failed(job_id, title, company, job_link, f"Manual apply required: {reason}")


def load_resume_state(bot_name: str, default_terms: list[str]) -> tuple[list[str], str | None]:
    import json
    import os
    from datetime import datetime
    state_file = "data/resume_state.json"
    if not os.path.exists(state_file):
        return default_terms, None
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
        today = datetime.now().strftime("%Y-%m-%d")
        if state.get("date") == today and state.get("bot_name") == bot_name:
            remaining = state.get("remaining_terms", [])
            filtered = [t for t in remaining if t in default_terms]
            if filtered:
                print(f"[ResumeState] Resuming '{bot_name}' with remaining terms: {filtered}")
                return filtered, state.get("location_query")
    except Exception as e:
        print_lg(f"[ResumeState] Error loading resume state: {e}")
    return default_terms, None


def save_resume_state(
    bot_name: str,
    remaining_terms: list[str],
    location_query: str | None = None,
) -> None:
    import json
    import os
    from datetime import datetime
    state_file = "data/resume_state.json"
    try:
        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        with open(state_file, "w") as f:
            json.dump({
                "date": today,
                "bot_name": bot_name,
                "remaining_terms": remaining_terms,
                "location_query": location_query,
            }, f, indent=2)
        print_lg(f"[ResumeState] Saved remaining terms: {remaining_terms}")
    except Exception as e:
        print_lg(f"[ResumeState] Error saving resume state: {e}")


def clear_resume_state() -> None:
    import os
    state_file = "data/resume_state.json"
    if os.path.exists(state_file):
        try:
            os.remove(state_file)
            print_lg("[ResumeState] Cleared resume state file.")
        except Exception as e:
            print_lg(f"[ResumeState] Error clearing resume state: {e}")


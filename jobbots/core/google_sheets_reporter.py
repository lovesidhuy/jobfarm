"""Google Sheets and Google Drive reporter for daily job application statistics.

Queries MongoDB for today's stats (discovered, applied, bookmarked, failed) and
logs them into a specified Google Sheet, uploading raw reports to Google Drive.
"""

from __future__ import annotations

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from jobbots.paths import MONOREPO_ROOT as _MONOREPO_ROOT

# Add project root to path
base_dir = _MONOREPO_ROOT
if str(base_dir) not in sys.path:
    sys.path.insert(0, str(base_dir))

from jobbots.core.secret_manager import get_secret
from jobbots.core.alerts import send_telegram_alert
from jobbots.core.job_queue import JobQueue

# Google API modules
GOOGLE_SHEETS_SUPPORT = False
try:
    from googleapiclient.discovery import build
    from google.oauth2 import service_account
    from google.oauth2.credentials import Credentials as UserCredentials
    from google.auth.transport.requests import Request as GoogleAuthRequest
    GOOGLE_SHEETS_SUPPORT = True
except ImportError:
    pass

# Prefer OAuth (user My Drive quota). Service account still works for Sheets.
_GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _oauth_token_path() -> Path:
    raw = (get_secret("GOOGLE_OAUTH_TOKEN_FILE", "") or os.getenv("GOOGLE_OAUTH_TOKEN_FILE") or "").strip()
    if raw:
        return Path(raw)
    return base_dir / "token.json"


def _oauth_client_secrets_path() -> Path | None:
    """Locate OAuth Desktop client JSON (client_secret.json style)."""
    candidates = [
        (get_secret("GOOGLE_OAUTH_CLIENT_FILE", "") or os.getenv("GOOGLE_OAUTH_CLIENT_FILE") or "").strip(),
        str(base_dir / "client_secret.json"),
        str(base_dir / "google_oauth_client.json"),
        "/etc/jobbots/client_secret.json",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return Path(c)
    # Inline JSON from Infisical
    return None


def _write_client_secrets_from_env() -> Path | None:
    """Materialize client secrets file from GOOGLE_OAUTH_CLIENT_JSON if needed."""
    raw = (get_secret("GOOGLE_OAUTH_CLIENT_JSON", "") or os.getenv("GOOGLE_OAUTH_CLIENT_JSON") or "").strip()
    if not raw:
        return None
    path = base_dir / "client_secret.json"
    try:
        info = json.loads(raw)
        path.write_text(json.dumps(info, indent=2), encoding="utf-8")
        path.chmod(0o600)
        return path
    except Exception as e:
        print(f"[Reporter] Failed to write OAuth client secrets: {e}")
        return None


def _get_oauth_user_credentials(*, interactive: bool = False):
    """User OAuth credentials (personal Drive quota). Refresh token.json when possible."""
    if not GOOGLE_SHEETS_SUPPORT:
        return None
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        if interactive:
            print("[Reporter] google-auth-oauthlib not installed")
        return None

    token_path = _oauth_token_path()
    creds = None
    if token_path.is_file():
        try:
            creds = UserCredentials.from_authorized_user_file(str(token_path), _GOOGLE_SCOPES)
        except Exception as e:
            print(f"[Reporter] Failed to load token.json: {e}")
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(GoogleAuthRequest())
            token_path.write_text(creds.to_json(), encoding="utf-8")
            token_path.chmod(0o600)
            print("[Reporter] Refreshed Google OAuth access token")
            return creds
        except Exception as e:
            print(f"[Reporter] OAuth token refresh failed: {e}")

    if not interactive:
        return None

    client_path = _oauth_client_secrets_path() or _write_client_secrets_from_env()
    if not client_path or not client_path.is_file():
        print(
            "[Reporter] No OAuth client secrets. Place client_secret.json in project root "
            "or set GOOGLE_OAUTH_CLIENT_JSON / GOOGLE_OAUTH_CLIENT_FILE."
        )
        return None

    print(f"[Reporter] Starting OAuth browser login (client={client_path.name})…", flush=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(client_path), _GOOGLE_SCOPES)
    # port=0 → free port; open system browser for the user to approve.
    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        authorization_prompt_message=(
            "\nIf a browser did not open, paste this URL into Chrome/Safari:\n{url}\n"
        ),
        success_message="Google OAuth OK — you can close this tab and return to the terminal.",
    )
    token_path.write_text(creds.to_json(), encoding="utf-8")
    token_path.chmod(0o600)
    print(f"[Reporter] Saved OAuth token → {token_path}", flush=True)
    return creds


def _get_service_account_credentials():
    """Service account — good for Sheets; cannot upload to personal My Drive."""
    if not GOOGLE_SHEETS_SUPPORT:
        return None

    json_str = get_secret("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if json_str:
        try:
            info = json.loads(json_str)
            return service_account.Credentials.from_service_account_info(
                info, scopes=_GOOGLE_SCOPES
            )
        except Exception as e:
            print(f"[Reporter] Failed to parse GOOGLE_SERVICE_ACCOUNT_JSON string: {e}")

    file_path = get_secret("GOOGLE_SERVICE_ACCOUNT_FILE", "").strip()
    if not file_path:
        default_path = Path("/etc/jobbots/google_service_account.json")
        if default_path.exists():
            file_path = str(default_path)
        else:
            default_path_local = base_dir / "google_service_account.json"
            if default_path_local.exists():
                file_path = str(default_path_local)

    if file_path and os.path.exists(file_path):
        try:
            return service_account.Credentials.from_service_account_file(
                file_path, scopes=_GOOGLE_SCOPES
            )
        except Exception as e:
            print(f"[Reporter] Failed to load credentials from file {file_path}: {e}")

    return None


def _get_google_credentials(*, prefer_oauth: bool = True, interactive: bool = False):
    """Resolve Google credentials.

    Order (prefer_oauth=True, default):
      1. OAuth user token (token.json) — uses **your** Drive quota
      2. Service account — Sheets OK; Drive My Drive uploads fail

    Set ``GOOGLE_AUTH_MODE=service_account`` to force SA only.
    Set ``GOOGLE_AUTH_MODE=oauth`` to require OAuth (no SA fallback).
    """
    if not GOOGLE_SHEETS_SUPPORT:
        return None

    mode = (get_secret("GOOGLE_AUTH_MODE", "") or os.getenv("GOOGLE_AUTH_MODE") or "auto").strip().lower()
    if mode in {"service_account", "sa"}:
        return _get_service_account_credentials()

    if prefer_oauth and mode in {"auto", "oauth", "user", ""}:
        oauth = _get_oauth_user_credentials(interactive=interactive)
        if oauth:
            return oauth
        if mode in {"oauth", "user"}:
            return None

    return _get_service_account_credentials()


def get_daily_stats() -> dict:
    """Query MongoDB to compile statistics for today's run."""
    stats = {
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "discovered_total": 0,
        "discovered_portals": {"indeed": 0, "glassdoor": 0, "linkedin": 0, "workopolis": 0},
        "applied_total": 0,
        "applied_portals": {"indeed": 0, "glassdoor": 0, "linkedin": 0, "workopolis": 0},
        "bookmarked": 0,
        "failed": 0,
        "queued": 0,
    }

    try:
        jq = JobQueue()
        # Get start of today (UTC)
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 1. Count Discovered (enqueued today) — prefer discovered_day / discovered_at
        today_key = today_start.strftime("%Y-%m-%d")
        discovered_docs = jq.jobs.find({
            "$or": [
                {"discovered_day": today_key},
                {"discovered_at": {"$gte": today_start}},
            ]
        })
        for doc in discovered_docs:
            stats["discovered_total"] += 1
            portal = doc.get("portal", "").lower()
            if portal in stats["discovered_portals"]:
                stats["discovered_portals"][portal] += 1

        # 2. Count Applied (applied today)
        # New submits only — exclude already_applied / skipped terminals.
        applied_docs = jq.jobs.find({
            "status": "applied",
            "$or": [
                {"applied_day": today_key},
                {"applied_at": {"$gte": today_start}},
            ],
        })
        for doc in applied_docs:
            stats["applied_total"] += 1
            portal = doc.get("portal", "").lower()
            if portal in stats["applied_portals"]:
                stats["applied_portals"][portal] += 1

        # 3. Count Bookmarked (terminal today)
        stats["bookmarked"] = jq.jobs.count_documents({
            "status": "bookmarked",
            "$or": [
                {"terminal_day": today_key},
                {"bookmarked_at": {"$gte": today_start}},
                {"terminal_at": {"$gte": today_start}},
                {"updated_at": {"$gte": today_start}},
            ],
        })

        # 4. Count Failed (dead terminal today — not last updated_at thrash)
        stats["failed"] = jq.jobs.count_documents({
            "status": "dead",
            "$or": [
                {"terminal_day": today_key},
                {"dead_at": {"$gte": today_start}},
                {"terminal_at": {"$gte": today_start}},
                {"updated_at": {"$gte": today_start}},
            ],
        })

        # 5. Count Remaining Queued
        counts = jq.counts()
        stats["queued"] = counts.get("queued", 0) + counts.get("retry", 0)

    except Exception as e:
        print(f"[Reporter] Warning: Failed to query MongoDB counts: {e}")
        # Return partial empty stats so we don't crash
        
    return stats


def get_failed_applications() -> list[dict]:
    """Return today's terminal failures with enough detail for manual follow-up."""
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    today_key = today_start.strftime("%Y-%m-%d")
    try:
        jq = JobQueue()
        docs = jq.jobs.find({
            "status": "dead",
            "$or": [
                {"terminal_day": today_key},
                {"dead_at": {"$gte": today_start}},
                {"terminal_at": {"$gte": today_start}},
                {"updated_at": {"$gte": today_start}},
            ],
        }).sort("terminal_at", -1)
        rows = []
        for doc in docs:
            metadata = doc.get("metadata") or {}
            rows.append({
                "failed_at": doc.get("dead_at") or doc.get("terminal_at") or doc.get("updated_at") or "",
                "portal": doc.get("portal", ""),
                "profile": doc.get("profile", ""),
                "title": doc.get("title", ""),
                "company": doc.get("company", ""),
                "location": doc.get("location", ""),
                "job_url": doc.get("url", ""),
                "result_url": doc.get("result_url", ""),
                "failure_reason": doc.get("last_error") or doc.get("outcome_reason") or metadata.get("last_error", ""),
                "attempts": doc.get("attempts", 0),
                "source_job_id": doc.get("source_job_id", ""),
                "job_id": str(doc.get("_id", "")),
            })
        return rows
    except Exception as e:
        print(f"[Reporter] Warning: Failed to query failed applications: {e}")
        return []


def generate_text_report(stats: dict) -> str:
    """Generate a clean ASCII text report of the daily metrics."""
    t = datetime.now().strftime("%B %d, %Y %I:%M %p")
    report = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📊 Daily Job Automation Report — {stats['date']}",
        f"Generated: {t}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🔍 Discovery: {stats['discovered_total']} jobs found",
        f"   Indeed: {stats['discovered_portals']['indeed']} | Glassdoor: {stats['discovered_portals']['glassdoor']} | LinkedIn: {stats['discovered_portals']['linkedin']} | Workopolis: {stats['discovered_portals']['workopolis']}",
        "",
        f"✅ Applied: {stats['applied_total']} jobs submitted",
        f"   Indeed: {stats['applied_portals']['indeed']} | Glassdoor: {stats['applied_portals']['glassdoor']} | LinkedIn: {stats['applied_portals']['linkedin']} | Workopolis: {stats['applied_portals']['workopolis']}",
        "",
        f"🔖 Bookmarked: {stats['bookmarked']}",
        f"❌ Failed: {stats['failed']} (dead outcomes)",
        f"📋 Current Queue Depth: {stats['queued']} remaining in queue",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    ]
    return "\n".join(report)


_SHEET_TAB = "Daily Report"
_SHEET_HEADERS = [
    "date",
    "discovered_total",
    "discovered_indeed",
    "discovered_glassdoor",
    "discovered_linkedin",
    "discovered_workopolis",
    "applied_total",
    "applied_indeed",
    "applied_glassdoor",
    "applied_linkedin",
    "applied_workopolis",
    "bookmarked",
    "failed",
    "queued",
]


def _ensure_daily_report_tab(sheets, spreadsheet_id: str) -> str:
    """Return a writable tab title, creating ``Daily Report`` if missing."""
    meta = sheets.get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties(sheetId,title)",
    ).execute()
    titles = [
        (s.get("properties") or {}).get("title") or ""
        for s in meta.get("sheets") or []
    ]
    preferred = (get_secret("GOOGLE_SHEET_TAB", _SHEET_TAB) or _SHEET_TAB).strip() or _SHEET_TAB
    if preferred in titles:
        return preferred
    # Create preferred tab
    try:
        sheets.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={
                "requests": [
                    {
                        "addSheet": {
                            "properties": {"title": preferred},
                        }
                    }
                ]
            },
        ).execute()
        print(f"[Reporter] Created sheet tab {preferred!r}")
        # Seed header row
        sheets.values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{preferred}'!A1:N1",
            valueInputOption="USER_ENTERED",
            body={"values": [_SHEET_HEADERS]},
        ).execute()
        return preferred
    except Exception as exc:
        print(f"[Reporter] Could not create tab {preferred!r}: {exc}")
        # Fall back to first existing tab (e.g. Sheet1)
        if titles:
            print(f"[Reporter] Falling back to existing tab {titles[0]!r}")
            return titles[0]
        raise


_FAILED_SHEET_TAB = "Failed Applications"
_FAILED_SHEET_HEADERS = [
    "failed_at", "portal", "profile", "title", "company", "location",
    "job_url", "result_url", "failure_reason", "attempts", "source_job_id", "job_id",
]


def _ensure_failed_applications_tab(sheets, spreadsheet_id: str) -> str:
    """Create the manual-review tab and its headers when needed."""
    meta = sheets.get(
        spreadsheetId=spreadsheet_id,
        fields="sheets.properties(title)",
    ).execute()
    titles = [
        (s.get("properties") or {}).get("title") or ""
        for s in meta.get("sheets") or []
    ]
    if _FAILED_SHEET_TAB not in titles:
        sheets.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": _FAILED_SHEET_TAB}}}]},
        ).execute()
        print(f"[Reporter] Created sheet tab {_FAILED_SHEET_TAB!r}")
    existing = sheets.values().get(
        spreadsheetId=spreadsheet_id,
        range=f"'{_FAILED_SHEET_TAB}'!A1:L1",
    ).execute()
    if not existing.get("values"):
        sheets.values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{_FAILED_SHEET_TAB}'!A1:L1",
            valueInputOption="USER_ENTERED",
            body={"values": [_FAILED_SHEET_HEADERS]},
        ).execute()
    return _FAILED_SHEET_TAB


def write_to_google_sheet(stats: dict) -> bool:
    """Append the daily metrics to the configured Google Sheet."""
    if not GOOGLE_SHEETS_SUPPORT:
        print("[Reporter] Google Sheets API not installed. Skipping spreadsheet append.")
        return False

    creds = _get_google_credentials()
    if not creds:
        print("[Reporter] Google Service Account credentials not found. Skipping spreadsheet append.")
        return False

    spreadsheet_id = get_secret("GOOGLE_SPREADSHEET_ID", "").strip()
    if not spreadsheet_id:
        print("[Reporter] GOOGLE_SPREADSHEET_ID not configured. Skipping spreadsheet append.")
        return False

    try:
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheets = service.spreadsheets()
        tab = _ensure_daily_report_tab(sheets, spreadsheet_id)

        # Ensure header if tab is empty
        existing = sheets.values().get(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A1:N1",
        ).execute()
        if not existing.get("values"):
            sheets.values().update(
                spreadsheetId=spreadsheet_id,
                range=f"'{tab}'!A1:N1",
                valueInputOption="USER_ENTERED",
                body={"values": [_SHEET_HEADERS]},
            ).execute()

        row_data = [
            stats["date"],
            stats["discovered_total"],
            stats["discovered_portals"]["indeed"],
            stats["discovered_portals"]["glassdoor"],
            stats["discovered_portals"]["linkedin"],
            stats["discovered_portals"]["workopolis"],
            stats["applied_total"],
            stats["applied_portals"]["indeed"],
            stats["applied_portals"]["glassdoor"],
            stats["applied_portals"]["linkedin"],
            stats["applied_portals"]["workopolis"],
            stats["bookmarked"],
            stats["failed"],
            stats["queued"],
        ]

        result = sheets.values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A:N",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": [row_data]},
        ).execute()

        print(f"[Reporter] Successfully appended daily summary row to Google Sheet tab={tab!r}: {result.get('updates')}")
        return True
    except Exception as e:
        print(f"[Reporter] Failed to write to Google Sheet: {e}")
        return False


def write_failed_applications_to_google_sheet(rows: list[dict]) -> bool:
    """Append today's failed applications to a separate manual-review tab."""
    if not rows:
        print("[Reporter] No failed applications to add to Google Sheets.")
        return True
    if not GOOGLE_SHEETS_SUPPORT:
        print("[Reporter] Google Sheets API not installed. Skipping failed-application details.")
        return False
    creds = _get_google_credentials()
    spreadsheet_id = get_secret("GOOGLE_SPREADSHEET_ID", "").strip()
    if not creds or not spreadsheet_id:
        print("[Reporter] Google credentials or spreadsheet ID missing; skipping failed-application details.")
        return False
    try:
        service = build("sheets", "v4", credentials=creds, cache_discovery=False)
        sheets = service.spreadsheets()
        tab = _ensure_failed_applications_tab(sheets, spreadsheet_id)
        values = [[row.get(header, "") for header in _FAILED_SHEET_HEADERS] for row in rows]
        result = sheets.values().append(
            spreadsheetId=spreadsheet_id,
            range=f"'{tab}'!A:L",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": values},
        ).execute()
        print(f"[Reporter] Added {len(values)} failed applications to Google Sheet tab={tab!r}: {result.get('updates')}")
        return True
    except Exception as e:
        print(f"[Reporter] Failed to write failed-application details: {e}")
        return False


def upload_to_google_drive(report_text: str, filename: str) -> str | None:
    """Upload the text report into your Google Drive folder.

    Prefers **OAuth user credentials** (``token.json``) so files land in **your**
    My Drive and use **your** storage. Service-account uploads to personal Drive
    fail with zero quota — run ``python scripts/google_oauth_login.py`` once.

    Set ``GOOGLE_DRIVE_UPLOAD=0`` to skip Drive (Sheets-only).
    """
    if not GOOGLE_SHEETS_SUPPORT:
        print("[Reporter] Google Drive API not installed. Skipping Drive upload.")
        return None

    if (get_secret("GOOGLE_DRIVE_UPLOAD", "1") or "1").strip().lower() in {
        "0", "false", "no", "off",
    }:
        print("[Reporter] GOOGLE_DRIVE_UPLOAD disabled — Sheets-only mode.")
        return None

    creds = _get_google_credentials(prefer_oauth=True, interactive=False)
    if not creds:
        print(
            "[Reporter] No Google credentials for Drive. "
            "Run: python scripts/google_oauth_login.py"
        )
        return None

    folder_id = get_secret("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    if not folder_id:
        print("[Reporter] GOOGLE_DRIVE_FOLDER_ID not set — skip Drive upload.")
        return None

    try:
        service = build("drive", "v3", credentials=creds, cache_discovery=False)

        file_metadata = {
            "name": filename,
            "mimeType": "text/plain",
            "parents": [folder_id],
        }

        from googleapiclient.http import MediaInMemoryUpload
        media = MediaInMemoryUpload(report_text.encode("utf-8"), mimetype="text/plain")

        file_result = service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id, webViewLink",
            supportsAllDrives=True,
        ).execute()

        view_link = file_result.get("webViewLink")
        print(f"[Reporter] Successfully uploaded report file to Google Drive: {view_link}")
        return view_link
    except Exception as e:
        err = str(e)
        if "storageQuotaExceeded" in err or "storage quota" in err.lower():
            print(
                "[Reporter] Drive upload blocked (likely service-account quota). "
                "Run once: python scripts/google_oauth_login.py "
                "— then uploads use your personal Drive storage."
            )
        else:
            print(f"[Reporter] Failed to upload report to Google Drive: {e}")
        return None


def run_daily_reporting() -> str:
    """Compile statistics, write to Google Sheet, upload text report to Drive, and alert via Telegram."""
    print("[Reporter] Compiling daily report metrics...")
    
    stats = get_daily_stats()
    failed_applications = get_failed_applications()
    report_text = generate_text_report(stats)
    
    # Save local backup of the text report
    reports_dir = base_dir / "logs" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    local_file = reports_dir / f"daily_report_{stats['date']}.txt"
    try:
        local_file.write_text(report_text, encoding="utf-8")
        print(f"[Reporter] Saved local report file to: {local_file}")
    except Exception as e:
        print(f"[Reporter] Failed to write local report file: {e}")

    # Write to Google Sheet
    sheet_success = write_to_google_sheet(stats)
    failed_sheet_success = write_failed_applications_to_google_sheet(failed_applications)
    
    # Upload to Google Drive
    drive_link = upload_to_google_drive(report_text, f"daily_report_{stats['date']}.txt")
    
    # Send Telegram alert with the summary
    telegram_msg = report_text
    if drive_link:
        telegram_msg += f"\n📂 View full report on Google Drive:\n{drive_link}"
    elif not sheet_success or not failed_sheet_success:
        telegram_msg += "\n⚠️ Note: Google Sheets/Drive reporting was skipped (no credentials/packages)."
        
    send_telegram_alert(telegram_msg, bot_name="reporter", alert_type="daily_summary", force=True)
    
    return report_text


if __name__ == "__main__":
    run_daily_reporting()

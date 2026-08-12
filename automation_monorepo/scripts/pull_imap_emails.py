#!/usr/bin/env python3
"""Pull recent emails from both IMAP accounts (IT + GENERAL) and save locally."""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
DEFAULT_OUTPUT_DIR = REPO_ROOT / "email_archive"
sys.path.insert(0, str(ROOT))

from core.secret_manager import get_secret  # noqa: E402

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore[misc, assignment]


ACCOUNTS = (
    ("it", "IMAP_EMAIL_IT", "IMAP_APP_PASSWORD_IT"),
    ("general", "IMAP_EMAIL_GENERAL", "IMAP_APP_PASSWORD_GENERAL"),
)

GMAIL_ALL_MAIL = '"[Gmail]/All Mail"'
FOLDER_CANDIDATES = (
    GMAIL_ALL_MAIL,
    "INBOX",
    '"[Gmail]/Sent Mail"',
)


def _decode_header_value(value: str) -> str:
    if not value:
        return ""
    parts = decode_header(value)
    out: list[str] = []
    for frag, enc in parts:
        if isinstance(frag, bytes):
            out.append(frag.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(frag)
    return "".join(out)


def _html_to_text(raw: str) -> str:
    if BeautifulSoup:
        return BeautifulSoup(raw, "html.parser").get_text(separator=" ")
    return re.sub(r"<[^>]+>", " ", raw)


def _extract_body(msg: email.message.Message) -> tuple[str, str]:
    text_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        text_parts.append(payload.decode(errors="replace"))
                except Exception:
                    pass
            elif ctype == "text/html":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        html_parts.append(payload.decode(errors="replace"))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                raw = payload.decode(errors="replace")
                if msg.get_content_type() == "text/html":
                    html_parts.append(raw)
                else:
                    text_parts.append(raw)
        except Exception:
            pass

    body_text = "\n".join(text_parts).strip()
    body_html = "\n".join(html_parts).strip()
    if not body_text and body_html:
        body_text = _html_to_text(body_html).strip()
    return body_text, body_html


def _message_to_record(msg: email.message.Message, uid: str, folder: str) -> dict:
    body_text, body_html = _extract_body(msg)
    date_raw = msg.get("Date", "") or ""
    try:
        date_iso = parsedate_to_datetime(date_raw).astimezone(timezone.utc).isoformat()
    except Exception:
        date_iso = ""

    return {
        "uid": uid,
        "folder": folder,
        "message_id": (msg.get("Message-ID") or "").strip(),
        "date": date_raw,
        "date_iso": date_iso,
        "from": _decode_header_value(msg.get("From", "") or ""),
        "to": _decode_header_value(msg.get("To", "") or ""),
        "cc": _decode_header_value(msg.get("Cc", "") or ""),
        "subject": _decode_header_value(msg.get("Subject", "") or ""),
        "body_text": body_text,
        "body_html": body_html,
        "labels": _decode_header_value(msg.get("X-Gmail-Labels", "") or ""),
    }


def _since_date(days: int) -> str:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return since.strftime("%d-%b-%Y")


def _search_ids(
    mail: imaplib.IMAP4_SSL,
    *,
    use_gmail_raw: bool,
    days: int,
) -> list[bytes]:
    if use_gmail_raw:
        criterion = f'X-GM-RAW "newer_than:{days}d"'
        status, data = mail.search(None, criterion)
        if status == "OK" and data and data[0]:
            return data[0].split()
        return []

    since = _since_date(days)
    status, data = mail.search(None, "SINCE", since)
    if status == "OK" and data and data[0]:
        return data[0].split()
    return []


def _chunked(items: list[bytes], size: int) -> list[list[bytes]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _fetch_batch(
    mail: imaplib.IMAP4_SSL,
    ids: list[bytes],
    *,
    folder: str,
    save_raw: bool,
    raw_dir: Path | None,
    jsonl_handle,
) -> int:
    if not ids:
        return 0

    id_set = b",".join(ids)
    status, msg_data = mail.fetch(id_set, "(RFC822)")
    if status != "OK" or not msg_data:
        return 0

    saved = 0
    for response_part in msg_data:
        if not isinstance(response_part, tuple):
            continue
        meta = response_part[0]
        raw_bytes = response_part[1]
        if not raw_bytes:
            continue
        uid_match = re.search(rb"UID (\d+)", meta if isinstance(meta, bytes) else str(meta).encode())
        uid = uid_match.group(1).decode() if uid_match else "unknown"
        msg = email.message_from_bytes(raw_bytes)
        record = _message_to_record(msg, uid=uid, folder=folder)
        jsonl_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        saved += 1
        if save_raw and raw_dir is not None:
            safe_name = re.sub(r"[^\w\-.]+", "_", record["message_id"] or uid)[:120]
            (raw_dir / f"{uid}_{safe_name}.eml").write_bytes(raw_bytes)
    return saved


def _select_folder(mail: imaplib.IMAP4_SSL, folder: str) -> bool:
    try:
        status, _ = mail.select(folder, readonly=True)
        return status == "OK"
    except imaplib.IMAP4.error:
        return False


def _fetch_folder(
    mail: imaplib.IMAP4_SSL,
    folder: str,
    *,
    use_gmail_raw: bool,
    days: int,
    save_raw: bool,
    raw_dir: Path | None,
    jsonl_handle,
    batch_size: int,
    label: str,
) -> tuple[int, bool]:
    if not _select_folder(mail, folder):
        return 0, False

    ids = _search_ids(mail, use_gmail_raw=use_gmail_raw, days=days)
    if not ids:
        return 0, True

    print(f"[{label}] {folder}: {len(ids)} message(s) in last {days} day(s)", flush=True)
    saved = 0
    batches = _chunked(ids, batch_size)
    for idx, batch in enumerate(batches, start=1):
        saved += _fetch_batch(
            mail,
            batch,
            folder=folder,
            save_raw=save_raw,
            raw_dir=raw_dir,
            jsonl_handle=jsonl_handle,
        )
        print(f"[{label}] {folder}: batch {idx}/{len(batches)} ({saved} saved)", flush=True)
    return saved, True


def _dedupe_jsonl(path: Path) -> int:
    if not path.exists():
        return 0
    seen: set[str] = set()
    records: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            key = rec.get("message_id") or f"{rec.get('folder')}:{rec.get('uid')}"
            if key in seen:
                continue
            seen.add(key)
            records.append(rec)
    records.sort(key=lambda r: r.get("date_iso") or r.get("date") or "", reverse=True)
    with path.open("w", encoding="utf-8") as handle:
        for rec in records:
            handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def pull_account(
    label: str,
    email_addr: str,
    app_password: str,
    *,
    imap_server: str,
    days: int,
    output_dir: Path,
    save_raw: bool,
    batch_size: int,
) -> dict:
    print(f"[{label}] Connecting to {imap_server} as {email_addr}...", flush=True)
    mail = imaplib.IMAP4_SSL(imap_server)
    try:
        mail.login(email_addr, app_password)
    except imaplib.IMAP4.error as exc:
        raise RuntimeError(f"[{label}] Login failed: {exc}") from exc

    use_gmail_raw = "gmail.com" in imap_server.lower()
    account_dir = output_dir / label
    account_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = account_dir / "raw" if save_raw else None
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)

    folders_used: list[str] = []
    jsonl_path = account_dir / "emails.jsonl"
    total_saved = 0
    try:
        with jsonl_path.open("w", encoding="utf-8") as jsonl_handle:
            for folder in FOLDER_CANDIDATES:
                saved, selected = _fetch_folder(
                    mail,
                    folder,
                    use_gmail_raw=use_gmail_raw,
                    days=days,
                    save_raw=save_raw,
                    raw_dir=raw_dir,
                    jsonl_handle=jsonl_handle,
                    batch_size=batch_size,
                    label=label,
                )
                if selected and saved:
                    folders_used.append(folder)
                    total_saved += saved
                    if folder == GMAIL_ALL_MAIL:
                        break
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    message_count = _dedupe_jsonl(jsonl_path)

    summary = {
        "account": label,
        "email": email_addr,
        "imap_server": imap_server,
        "days": days,
        "folders_used": folders_used,
        "message_count": message_count,
        "output_jsonl": str(jsonl_path),
    }
    (account_dir / "summary.json").write_text(
        json.dumps(summary, indent=2),
        encoding="utf-8",
    )
    print(f"[{label}] Saved {message_count} message(s) to {jsonl_path}", flush=True)
    return summary


def _load_accounts() -> list[tuple[str, str, str]]:
    loaded: list[tuple[str, str, str]] = []
    for label, email_key, password_key in ACCOUNTS:
        email_addr = get_secret(email_key, "").strip()
        app_password = get_secret(password_key, "").strip()
        if not email_addr or not app_password:
            print(f"[skip] Missing credentials for {label} ({email_key})")
            continue
        loaded.append((label, email_addr, app_password))
    return loaded


def main() -> int:
    parser = argparse.ArgumentParser(description="Pull IMAP emails from IT and GENERAL accounts.")
    parser.add_argument("--days", type=int, default=30, help="How many days back to fetch (default: 30)")
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Base output directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--save-raw",
        action="store_true",
        help="Also save raw .eml files under each account's raw/ folder",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        help="Number of messages to fetch per IMAP request (default: 50)",
    )
    args = parser.parse_args()

    accounts = _load_accounts()
    if not accounts:
        print("No IMAP accounts configured. Set IMAP_EMAIL_IT/IMAP_APP_PASSWORD_IT and/or "
              "IMAP_EMAIL_GENERAL/IMAP_APP_PASSWORD_GENERAL in Infisical or .env.")
        return 1

    imap_server = get_secret("IMAP_SERVER", "imap.gmail.com").strip() or "imap.gmail.com"
    run_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output_dir) / f"run_{run_stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest: dict = {
        "pulled_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "imap_server": imap_server,
        "accounts": [],
    }

    errors: list[str] = []
    for label, email_addr, app_password in accounts:
        try:
            summary = pull_account(
                label,
                email_addr,
                app_password,
                imap_server=imap_server,
                days=args.days,
                output_dir=output_dir,
                save_raw=args.save_raw,
                batch_size=max(1, args.batch_size),
            )
            manifest["accounts"].append(summary)
        except Exception as exc:
            errors.append(str(exc))
            print(exc)

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Manifest written to {manifest_path}")

    if errors and not manifest["accounts"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

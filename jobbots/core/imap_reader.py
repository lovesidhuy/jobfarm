import imaplib
import email
import email.message  # noqa: F401 — `import email` alone does not bind email.message
import os
import re
import time
from email.header import decode_header

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None  # type: ignore[misc, assignment]


def extract_otp_from_text(text: str) -> str:
    """
    Look for a one-time code in email body or subject.
    Indeed e.g. subject 'Sign in to Indeed with code: 119599' or body 'Use this code... 119599'.
    """
    if not text:
        return ""
    for pattern in (
        r"(?:code|passcode)\s*[: ]\s*(\d{6})\b",
        r"\b(\d{6})\b",
        r"\b(\d{3})[-\s](\d{3})\b",
    ):
        m = re.search(pattern, text, flags=re.I)
        if m:
            code = "".join(g for g in m.groups() if g)
            if code.isdigit() and len(code) == 6:
                return code
    return ""


def extract_greenhouse_code_from_text(text: str) -> str:
    """Greenhouse email human-check codes are 8 alphanumeric chars (e.g. 9mAF6dL2)."""
    if not text:
        return ""
    # Prefer codes near verification language / "copy and paste this code".
    for pattern in (
        r"(?:security|verification|confirmation)\s*code[^\n]{0,40}?([A-Za-z0-9]{8})\b",
        r"copy and paste this code[^\n]{0,80}?([A-Za-z0-9]{8})\b",
        r"(?:enter|use)\s+(?:this\s+)?(?:code|security code)[^\n]{0,40}?([A-Za-z0-9]{8})\b",
        r"\bcode\s*[:#-]?\s*([A-Za-z0-9]{8})\b",
    ):
        m = re.search(pattern, text, flags=re.I | re.S)
        if m:
            code = m.group(1).strip()
            if _looks_like_greenhouse_code(code):
                return code
    # Fallback: standalone mixed alnum 8-char tokens with at least one digit.
    for m in re.finditer(r"\b([A-Za-z0-9]{8})\b", text):
        code = m.group(1)
        if _looks_like_greenhouse_code(code):
            return code
    return ""


def _looks_like_greenhouse_code(code: str) -> bool:
    if not code or len(code) != 8:
        return False
    if not code.isalnum():
        return False
    has_letter = any(c.isalpha() for c in code)
    has_digit = any(c.isdigit() for c in code)
    # Real GH codes are 8-char alnum. Most mix letters+digits; some are mixed-case letters only
    # (e.g. KpLidqwZ, ryfkWHYO). Reject pure dictionary-ish words / title-case English words.
    low = code.lower()
    if low in {
        "password", "security", "verified", "continue", "account", "required",
        "resubmit", "greenhouse", "application", "received", "position",
        "candidate", "thankyou",
    }:
        return False
    # Title-case English words like "Received" / "Security" are never GH codes.
    if code[0].isupper() and code[1:].islower() and not has_digit:
        return False
    if has_letter and has_digit:
        return True
    # All-letter codes: require mixed case beyond simple Titlecase.
    if has_letter and not has_digit:
        uppers = sum(1 for c in code if c.isupper())
        lowers = sum(1 for c in code if c.islower())
        return uppers >= 2 and lowers >= 2
    return False


def _decode_maybe_header(value: str) -> str:
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


def _message_text_body(msg: email.message.Message) -> str:
    """Collect plain text; for HTML parts strip tags when BeautifulSoup is available."""
    chunks: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    payload = part.get_payload(decode=True)
                    if payload:
                        chunks.append(payload.decode(errors="replace"))
                except Exception:
                    pass
            elif ctype == "text/html":
                try:
                    payload = part.get_payload(decode=True)
                    if not payload:
                        continue
                    raw = payload.decode(errors="replace")
                    if BeautifulSoup:
                        soup = BeautifulSoup(raw, "html.parser")
                        chunks.append(soup.get_text(separator=" "))
                    else:
                        chunks.append(re.sub(r"<[^>]+>", " ", raw))
                except Exception:
                    pass
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                raw = payload.decode(errors="replace")
                ctype = msg.get_content_type()
                if ctype == "text/html" and BeautifulSoup:
                    soup = BeautifulSoup(raw, "html.parser")
                    chunks.append(soup.get_text(separator=" "))
                else:
                    chunks.append(raw)
        except Exception:
            pass

    subj = _decode_maybe_header(msg.get("Subject", "") or "")
    return subj + "\n" + "\n".join(chunks)


def _gmail_search_raw(mail: imaplib.IMAP4_SSL, sender_domain: str) -> list[bytes]:
    """Gmail-specific: search by From (Indeed OTP often from login@indeed.com)."""
    queries = [f"from:{sender_domain} newer_than:2d"]
    low = (sender_domain or "").lower()
    if "indeed" in low:
        queries.extend(
            [
                "from:login@indeed.com newer_than:2d",
                "from:noreply@indeed.com newer_than:2d",
            ]
        )
    if "glassdoor" in low:
        # One-login emails often come from Indeed even when the flow started on Glassdoor.
        queries.extend(
            [
                "from:login@indeed.com newer_than:2d",
                "from:noreply@indeed.com newer_than:2d",
            ]
        )
    seen: set[bytes] = set()
    out: list[bytes] = []
    for q in dict.fromkeys(queries):
        criterion = f'X-GM-RAW "{q}"'
        try:
            status, data = mail.search(None, criterion)
            if status == "OK" and data and data[0]:
                for eid in data[0].split():
                    if eid not in seen:
                        seen.add(eid)
                        out.append(eid)
        except imaplib.IMAP4.error:
            continue
    return out


def _standard_search(mail: imaplib.IMAP4_SSL, sender_domain: str) -> list[bytes]:
    ids: list[bytes] = []
    short = sender_domain.split(".")[0] if "." in sender_domain else sender_domain
    for criterion in (
        f'(FROM "@{sender_domain}")',
        f'(FROM "{sender_domain}")',
        f'(FROM "{short}")',
    ):
        try:
            status, data = mail.search(None, criterion)
            if status == "OK" and data and data[0]:
                ids.extend(data[0].split())
        except imaplib.IMAP4.error:
            continue
    # de-dupe preserving order
    seen: set[bytes] = set()
    out: list[bytes] = []
    for eid in ids:
        if eid not in seen:
            seen.add(eid)
            out.append(eid)
    return out


def get_latest_otp(
    email_addr: str,
    app_password: str,
    sender_domain: str,
    max_wait_seconds: int | None = None,
) -> str:
    """
    Connect to Gmail via IMAP and wait for an OTP email from `sender_domain`.
    """
    if max_wait_seconds is None:
        try:
            max_wait_seconds = int(os.environ.get("IMAP_OTP_MAX_WAIT_SECONDS", "120"))
        except ValueError:
            max_wait_seconds = 120

    print(
        f"[IMAP] Connecting to Gmail for {email_addr} "
        f"looking for OTP from {sender_domain} (wait up to {max_wait_seconds}s)..."
    )

    imap_server = os.environ.get("IMAP_SERVER", "imap.gmail.com")
    mail = imaplib.IMAP4_SSL(imap_server)

    try:
        mail.login(email_addr, app_password)
    except imaplib.IMAP4.error as e:
        print(f"[IMAP] Login failed: {e}")
        return ""

    use_gmail_raw = "gmail.com" in imap_server.lower()

    start_time = time.time()
    tried_ids: set[bytes] = set()

    try:
        while time.time() - start_time < max_wait_seconds:
            mail.select("inbox")

            if use_gmail_raw:
                email_ids = _gmail_search_raw(mail, sender_domain)
            else:
                email_ids = _standard_search(mail, sender_domain)

            if not email_ids:
                print("[IMAP] No messages from sender yet. Waiting 5 seconds...")
                time.sleep(5)
                continue

            # Newest last in IMAP search; check the tail for a fresh OTP
            for eid in reversed(email_ids[-15:]):
                if eid in tried_ids:
                    continue
                tried_ids.add(eid)
                status, msg_data = mail.fetch(eid, "(RFC822)")
                if status != "OK":
                    continue
                for response_part in msg_data:
                    if not isinstance(response_part, tuple):
                        continue
                    msg = email.message_from_bytes(response_part[1])
                    blob = _message_text_body(msg)
                    otp = extract_otp_from_text(blob)
                    if otp:
                        print("[IMAP] Successfully extracted OTP.")
                        return otp

            print("[IMAP] No OTP in recent messages yet. Waiting 5 seconds...")
            time.sleep(5)

        print("[IMAP] Timed out waiting for OTP email.")
        return ""
    finally:
        try:
            mail.logout()
        except Exception:
            pass


def _greenhouse_search(mail: imaplib.IMAP4_SSL) -> list[bytes]:
    """Find recent Greenhouse verification emails (greenhouse-mail.io senders).

    Returns message ids newest-first (sorted by IMAP id / UID ascending then reversed).
    """
    seen: set[bytes] = set()
    out: list[bytes] = []
    imap_server = os.environ.get("IMAP_SERVER", "imap.gmail.com")
    use_gmail_raw = "gmail.com" in imap_server.lower()

    if use_gmail_raw:
        queries = [
            # Live GH senders use us.greenhouse-mail.io (not greenhouse.io).
            'from:greenhouse-mail.io subject:"Security code for your application" newer_than:2d',
            'from:greenhouse-mail.io subject:"Security code" newer_than:2d',
            'from:greenhouse-mail.io "security code" newer_than:2d',
            'from:no-reply@us.greenhouse-mail.io newer_than:2d',
            'from:greenhouse-mail.io newer_than:2d',
            'from:greenhouse.io newer_than:2d',
        ]
        for q in queries:
            try:
                status, data = mail.search(None, f'X-GM-RAW "{q}"')
                if status == "OK" and data and data[0]:
                    for eid in data[0].split():
                        if eid not in seen:
                            seen.add(eid)
                            out.append(eid)
            except imaplib.IMAP4.error:
                continue
    for criterion in (
        '(FROM "greenhouse-mail.io")',
        '(FROM "us.greenhouse-mail.io")',
        '(FROM "no-reply@us.greenhouse-mail.io")',
        '(FROM "greenhouse.io")',
        '(SUBJECT "Security code for your application")',
    ):
        try:
            status, data = mail.search(None, criterion)
            if status == "OK" and data and data[0]:
                for eid in data[0].split():
                    if eid not in seen:
                        seen.add(eid)
                        out.append(eid)
        except imaplib.IMAP4.error:
            continue
    # Newest first: IMAP sequence/UID numbers increase over time.
    def _id_key(eid: bytes) -> int:
        try:
            return int(eid)
        except Exception:
            return 0
    out.sort(key=_id_key, reverse=True)
    return out


def get_sent_recipients(
    email_addr: str,
    app_password: str,
    max_age_days: int = 90,
) -> set[str]:
    """
    Connect to Gmail IMAP, select the Sent Mail folder, and return a set of
    all unique lowercase ``To:`` recipient email addresses from sent mail
    (capped by ``max_age_days``).

    Used by the Job Bank pipeline to avoid re-sending emails that were
    already delivered in previous worker lifecycles.
    """
    import datetime as _datetime

    imap_server = os.environ.get("IMAP_SERVER", "imap.gmail.com")
    recipients: set[str] = set()
    try:
        mail = imaplib.IMAP4_SSL(imap_server)
        mail.login(email_addr, app_password)
    except imaplib.IMAP4.error as e:
        print(f"[IMAP Sent] Login failed: {e}")
        return recipients

    # Gmail Sent folder; works for both @gmail.com and Workspace accounts.
    for sent_label in ("[Gmail]/Sent Mail", "Sent", "Sent Items"):
        try:
            status, _ = mail.select(sent_label)
            if status == "OK":
                break
        except imaplib.IMAP4.error:
            continue
    else:
        try:
            mail.logout()
        except Exception:
            pass
        return recipients

    since = (_datetime.datetime.now() - _datetime.timedelta(days=max_age_days)).strftime(
        "%d-%b-%Y"
    )
    # Gmail does not support the standard SINCE/OLDER in all configs.
    # Use Gmail RAW search which is reliable for Gmail accounts.
    is_gmail = "gmail.com" in imap_server.lower()
    search_criteria = f'X-GM-RAW "in:sent newer_than:{max_age_days}d"' if is_gmail else f'(SINCE "{since}")'
    try:
        status, data = mail.search(None, search_criteria)
    except imaplib.IMAP4.error:
        # Fallback: fetch recent N messages without date filter.
        try:
            status, data = mail.search(None, "ALL")
        except imaplib.IMAP4.error:
            try:
                mail.logout()
            except Exception:
                pass
            return recipients
    if status != "OK" or not data or not data[0]:
        try:
            mail.logout()
        except Exception:
            pass
        return recipients

    eids = data[0].split()
    # Sample across the range to keep this fast (don't fetch 10k messages).
    step = max(1, len(eids) // 200)
    sample = list(eids[::-step])[:200]
    for eid in sample:
        try:
            eid_s = eid.decode() if isinstance(eid, (bytes, bytearray)) else str(eid)
            s, msg_data = mail.fetch(eid_s, "(BODY.PEEK[HEADER.FIELDS (TO)])")
            if s != "OK":
                continue
            for part in msg_data:
                if not isinstance(part, tuple):
                    continue
                header_bytes = part[1]
                if not header_bytes:
                    continue
                msg = email.message_from_bytes(header_bytes)
                for to in msg.get_all("To", []):
                    addr = _decode_maybe_header(to or "")
                    found = re.findall(r"[\w.+-]+@[\w.-]+", addr.lower())
                    for a in found:
                        recipients.add(a.strip())
        except Exception:
            continue
    try:
        mail.logout()
    except Exception:
        pass
    return recipients


def get_latest_greenhouse_code(
    email_addr: str,
    app_password: str,
    max_wait_seconds: int | None = None,
    *,
    not_before: float | None = None,
) -> str:
    """Wait for a Greenhouse 8-character verification code via IMAP."""
    if max_wait_seconds is None:
        try:
            max_wait_seconds = int(os.environ.get("IMAP_GH_CODE_MAX_WAIT_SECONDS", "150"))
        except ValueError:
            max_wait_seconds = 150

    print(
        f"[IMAP] Connecting for Greenhouse code @ {email_addr} "
        f"(wait up to {max_wait_seconds}s)..."
    )
    imap_server = os.environ.get("IMAP_SERVER", "imap.gmail.com")
    mail = imaplib.IMAP4_SSL(imap_server)
    try:
        mail.login(email_addr, app_password)
    except imaplib.IMAP4.error as e:
        print(f"[IMAP] Login failed: {e}")
        return ""

    baseline_id: bytes | None = None
    try:
        mail.select("inbox")
        baseline_search = _greenhouse_search(mail)
        if baseline_search:
            baseline_id = baseline_search[0]
            print(f"[IMAP] Baseline latest code email ID found: {baseline_id.decode() if isinstance(baseline_id, bytes) else str(baseline_id)}")
    except Exception as exc:
        print(f"[IMAP] Baseline fetch warning: {exc}")

    start_time = time.time()
    tried_ids: set[bytes] = set()
    try:
        while time.time() - start_time < max_wait_seconds:
            try:
                mail.select("inbox")
            except Exception:
                try:
                    print("[IMAP] Connection lost, reconnecting...")
                    mail = imaplib.IMAP4_SSL(imap_server)
                    mail.login(email_addr, app_password)
                    mail.select("inbox")
                except Exception as exc:
                    print(f"[IMAP] Reconnect failed: {exc}")
                    time.sleep(3)
                    continue
            email_ids = _greenhouse_search(mail)
            if not email_ids:
                print("[IMAP] No Greenhouse verification mail yet. Waiting 5s...")
                time.sleep(5)
                continue

            # Already newest-first; check a wider recent window.
            for eid in email_ids[:30]:
                if eid in tried_ids:
                    continue
                tried_ids.add(eid)
                if baseline_id is not None:
                    def _id_val(x: bytes) -> int:
                        try:
                            return int(x)
                        except Exception:
                            return 0
                    if _id_val(eid) <= _id_val(baseline_id):
                        continue
                # imaplib accepts str message set; decode bytes ids.
                eid_s = eid.decode() if isinstance(eid, (bytes, bytearray)) else str(eid)
                status, msg_data = mail.fetch(eid_s, "(RFC822)")
                if status != "OK":
                    continue
                for response_part in msg_data:
                    if not isinstance(response_part, tuple):
                        continue
                    msg = email.message_from_bytes(response_part[1])
                    # Skip very old mails if not_before provided.
                    if not_before is not None:
                        try:
                            from email.utils import parsedate_to_datetime
                            dt = parsedate_to_datetime(msg.get("Date") or "")
                            if dt is not None and dt.timestamp() < (not_before - 120):
                                continue
                        except Exception:
                            pass
                    blob = _message_text_body(msg)
                    low = blob.lower()
                    frm = (msg.get("From") or "").lower()
                    subj = (msg.get("Subject") or "").lower()
                    is_gh = (
                        "greenhouse" in low
                        or "greenhouse" in frm
                        or "greenhouse-mail" in frm
                        or "security code for your application" in subj
                    )
                    if not is_gh:
                        continue
                    # Skip pure "application received" receipts — no code.
                    if "application received" in subj and "security code" not in subj and "security code" not in low:
                        continue
                    if "security code" not in subj and "security code" not in low and "verification code" not in low:
                        continue
                    code = extract_greenhouse_code_from_text(blob)
                    if code:
                        print(f"[IMAP] Greenhouse verification code extracted ({code[:2]}******).")
                        return code

            print("[IMAP] No Greenhouse code in recent mail yet. Waiting 5s...")
            time.sleep(5)

        print("[IMAP] Timed out waiting for Greenhouse verification code.")
        return ""
    finally:
        try:
            mail.logout()
        except Exception:
            pass

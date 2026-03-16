from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path

from django.conf import settings


def _load_creds(scopes=None):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    scopes = scopes or ["https://www.googleapis.com/auth/gmail.send"]
    token_file = Path(settings.GMAIL_OAUTH_TOKEN_FILE)
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return creds


def gmail_ready() -> bool:
    creds = _load_creds(["https://www.googleapis.com/auth/gmail.send"])
    return bool(creds and creds.valid)


def gmail_inbox_ready() -> bool:
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    creds = _load_creds(scopes)
    return bool(creds and creds.valid)


def send_gmail(
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> str:
    from googleapiclient.discovery import build

    creds = _load_creds()
    if not creds or not creds.valid:
        raise RuntimeError("Gmail OAuth no configurado. Ejecuta autorizar_gmail_oauth.")

    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = settings.GMAIL_OAUTH_SENDER
    msg["Subject"] = subject
    msg.set_content(body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    for att in (attachments or []):
        try:
            fname, data, mime = att
            maintype, subtype = (mime or "application/octet-stream").split("/", 1)
            msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=fname)
        except Exception:
            continue

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service = build("gmail", "v1", credentials=creds)
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent.get("id", "")


def fetch_gmail_attachments_for_compra(compra_numero: int, max_messages: int = 20):
    from googleapiclient.discovery import build

    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]
    creds = _load_creds(scopes)
    if not creds or not creds.valid:
        raise RuntimeError("Gmail OAuth sin scope de lectura. Reautoriza Gmail con gmail.readonly.")

    service = build("gmail", "v1", credentials=creds)
    query = f'is:unread has:attachment "#{int(compra_numero)}"'
    listing = service.users().messages().list(userId="me", q=query, maxResults=max_messages).execute()
    msgs = listing.get("messages", []) or []

    out = []
    for m in msgs:
        msg = service.users().messages().get(userId="me", id=m["id"], format="full").execute()
        payload = msg.get("payload", {})
        parts = payload.get("parts", []) or []
        for p in parts:
            filename = (p.get("filename") or "").strip()
            if not filename:
                continue
            body = p.get("body") or {}
            att_id = body.get("attachmentId")
            if not att_id:
                continue
            if not (filename.lower().endswith(".xml") or filename.lower().endswith(".pdf")):
                continue
            att = service.users().messages().attachments().get(userId="me", messageId=m["id"], id=att_id).execute()
            data = att.get("data") or ""
            raw = base64.urlsafe_b64decode(data.encode("utf-8")) if data else b""
            out.append({"message_id": m["id"], "filename": filename, "bytes": raw})
    return out

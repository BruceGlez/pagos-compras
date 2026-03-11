from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path

from django.conf import settings


def _load_creds():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    token_file = Path(settings.GMAIL_OAUTH_TOKEN_FILE)
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), ["https://www.googleapis.com/auth/gmail.send"])
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
    return creds


def gmail_ready() -> bool:
    creds = _load_creds()
    return bool(creds and creds.valid)


def send_gmail(to_email: str, subject: str, body: str, html_body: str | None = None) -> str:
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

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service = build("gmail", "v1", credentials=creds)
    sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
    return sent.get("id", "")

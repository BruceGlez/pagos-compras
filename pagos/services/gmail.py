from __future__ import annotations

import base64
import json
from email.message import EmailMessage
from pathlib import Path

from django.conf import settings


SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send"
MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def _token_path(*, inbox: bool) -> Path:
    if inbox:
        return Path(getattr(settings, "GMAIL_OAUTH_INBOX_TOKEN_FILE", settings.GMAIL_OAUTH_TOKEN_FILE))
    return Path(settings.GMAIL_OAUTH_TOKEN_FILE)


def _load_creds(scopes=None, *, inbox: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    scopes = scopes or [SEND_SCOPE]

    # Primary token (separate inbox token when inbox=True)
    token_candidates = [_token_path(inbox=inbox)]

    # Backward-compat fallback: if inbox token missing, allow legacy shared token.
    if inbox:
        legacy = _token_path(inbox=False)
        if legacy not in token_candidates:
            token_candidates.append(legacy)

    selected_file = None
    creds = None

    for token_file in token_candidates:
        if not token_file.exists():
            continue

        try:
            payload = json.loads(token_file.read_text())
            token_scopes = set(payload.get("scopes") or [])
            if token_scopes and not set(scopes).issubset(token_scopes):
                continue
        except Exception:
            continue

        try:
            creds = Credentials.from_authorized_user_file(str(token_file), scopes)
            selected_file = token_file
            break
        except Exception:
            continue

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            if selected_file:
                selected_file.write_text(creds.to_json())
        except Exception:
            return None

    return creds


def gmail_ready() -> bool:
    creds = _load_creds([SEND_SCOPE], inbox=False)
    return bool(creds and creds.valid)


def gmail_inbox_ready() -> bool:
    creds = _load_creds([MODIFY_SCOPE, SEND_SCOPE], inbox=True)
    return bool(creds and creds.valid)


def send_gmail(
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
) -> str:
    from googleapiclient.discovery import build

    creds = _load_creds([SEND_SCOPE], inbox=False)
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

    scopes = [MODIFY_SCOPE, SEND_SCOPE]
    creds = _load_creds(scopes, inbox=True)
    if not creds or not creds.valid:
        raise RuntimeError(
            "Gmail inbox OAuth sin scopes requeridos (gmail.send + gmail.modify). "
            "Ejecuta: python manage.py autorizar_gmail_oauth"
        )

    service = build("gmail", "v1", credentials=creds)
    n = int(compra_numero)
    query_strict = f'is:unread has:attachment ("#{n}" OR "compra {n}" OR "compra {n:05d}" OR "{n:05d}")'
    listing_strict = service.users().messages().list(userId="me", q=query_strict, maxResults=max_messages).execute()
    msgs_strict = listing_strict.get("messages", []) or []

    # Fallback amplio siempre (merge) para evitar falsos positivos en query estricta.
    query_fallback = "is:unread has:attachment newer_than:30d"
    listing_fallback = service.users().messages().list(userId="me", q=query_fallback, maxResults=max_messages).execute()
    msgs_fallback = listing_fallback.get("messages", []) or []

    # Dedup preserving priority: strict primero, luego fallback.
    msgs = []
    seen = set()
    for m in (msgs_strict + msgs_fallback):
        mid = str(m.get("id") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        msgs.append(m)

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


def mark_gmail_message_processed(message_id: str, label_name: str = "pagos-processed"):
    from googleapiclient.discovery import build

    scopes = [MODIFY_SCOPE, SEND_SCOPE]
    creds = _load_creds(scopes, inbox=True)
    if not creds or not creds.valid:
        raise RuntimeError(
            "Gmail inbox OAuth sin scopes requeridos (gmail.send + gmail.modify). "
            "Ejecuta: python manage.py autorizar_gmail_oauth"
        )

    service = build("gmail", "v1", credentials=creds)

    labels = service.users().labels().list(userId="me").execute().get("labels", []) or []
    label_id = next((x.get("id") for x in labels if (x.get("name") or "").strip().lower() == label_name.lower()), None)
    if not label_id:
        created = service.users().labels().create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        ).execute()
        label_id = created.get("id")

    body = {
        "addLabelIds": [label_id] if label_id else [],
        "removeLabelIds": ["UNREAD"],
    }
    service.users().messages().modify(userId="me", id=message_id, body=body).execute()
    return {"message_id": message_id, "label_id": label_id, "label_name": label_name}

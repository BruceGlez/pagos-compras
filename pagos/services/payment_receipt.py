from __future__ import annotations

import io
import re
from datetime import datetime
from decimal import Decimal

from pypdf import PdfReader


def extract_pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        parts = []
        for p in reader.pages:
            parts.append(p.extract_text() or "")
        return "\n".join(parts)
    except Exception:
        return ""


def _find(pattern: str, text: str):
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return (m.group(1).strip() if m else "")


def parse_payment_receipt_text(text: str) -> dict:
    t = (text or "").replace("\u00a0", " ")

    amount_raw = _find(r"Importe de la operaci[oó]n:\s*([0-9,]+\.?[0-9]{0,2})", t)
    currency = _find(r"Importe de la operaci[oó]n:[^\n]*\s([A-Z]{3})\b", t) or "MXP"
    from_account = _find(r"Cuenta de retiro:\s*([0-9]{6,30})", t)
    to_account = _find(r"Cuenta de dep[oó]sito:\s*([0-9]{6,40})", t)
    beneficiary = _find(r"Titular de la cuenta:\s*([^\n]+)", t)
    concept = _find(r"Concepto de pago:\s*([^\n]+)", t)
    ref_num = _find(r"Referencia num[eé]rica:\s*([^\n]+)", t)
    tracking = _find(r"Clave de rastreo:\s*([^\n]+)", t)
    folio = _find(r"Folio interbancario:\s*([^\n]+)", t)
    apply_date = _find(r"Fecha de aplicaci[oó]n:\s*([0-9]{2}/[0-9]{2}/[0-9]{4})", t)

    amount = None
    if amount_raw:
        amount = Decimal(amount_raw.replace(",", ""))

    fecha_pago = None
    if apply_date:
        try:
            fecha_pago = datetime.strptime(apply_date, "%d/%m/%Y").date()
        except Exception:
            fecha_pago = None

    return {
        "amount": amount,
        "currency": currency.upper(),
        "from_account": from_account,
        "to_account": to_account,
        "beneficiary": beneficiary,
        "concept": concept,
        "reference": ref_num or folio,
        "tracking": tracking,
        "apply_date": fecha_pago,
        "raw_text": t,
    }

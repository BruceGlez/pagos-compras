from __future__ import annotations

import io
import re
from decimal import Decimal

from pypdf import PdfReader


def _pdf_text(pdf_bytes: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join((p.extract_text() or "") for p in reader.pages)
    except Exception:
        return ""


def _norm_name(s: str) -> str:
    s = (s or "").upper()
    s = s.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return " ".join(s.split())


def parse_compra_pdf_fields(pdf_bytes: bytes) -> dict:
    txt = _pdf_text(pdf_bytes)
    up = (txt or "").upper()

    out = {
        "numero_compra": None,
        "productor_nombre": "",
        "fecha_transaccion": "",
        "total_usd": None,
        "raw_text": txt,
    }

    if not up:
        return out

    # Compra suele venir como 00001/00002 cerca de "LIBRA COMPRA".
    m_nro = re.search(r"\b(\d{5})\b", up)
    if m_nro:
        try:
            out["numero_compra"] = int(m_nro.group(1))
        except Exception:
            pass

    # Productor en este layout aparece entre "LIBRA COMPRA :" y "1.0000 0000X".
    one_line = re.sub(r"\s+", " ", up)
    m_prod = re.search(r"LIBRA\s+COMPRA\s*:\s*([A-Z\s]+?)\s+1\.0000\s+0*\d{1,6}", one_line)
    if m_prod:
        out["productor_nombre"] = " ".join(m_prod.group(1).split()).strip()
    else:
        # fallback clásico "Productor:"
        m_prod2 = re.search(r"PRODUCTOR\s*:\s*([^\n]+)", txt, flags=re.IGNORECASE)
        if m_prod2:
            out["productor_nombre"] = m_prod2.group(1).strip()

    m_fecha = re.search(r"FECHA DE TRANSACCI[OÓ]N\s*:\s*([^\n]+)", txt, flags=re.IGNORECASE)
    if m_fecha:
        out["fecha_transaccion"] = m_fecha.group(1).strip()

    # Para este formato, tomar montos antes de "RETENCIÓN" y elegir el penúltimo si el último es 0.00.
    pre_ret = up.split("RETENCI", 1)[0]
    vals = re.findall(r"\b\d{1,3}(?:,\d{3})*\.\d{2}\b", pre_ret)
    if vals:
        try:
            cand = Decimal(vals[-1].replace(",", ""))
            if cand <= Decimal("0.01") and len(vals) >= 2:
                cand = Decimal(vals[-2].replace(",", ""))
            out["total_usd"] = cand
        except Exception:
            pass

    return out


def validate_compra_pdf(
    pdf_bytes: bytes,
    *,
    numero_compra: int,
    expected_total: Decimal | None = None,
    expected_productor: str = "",
) -> tuple[bool, str]:
    txt = (_pdf_text(pdf_bytes) or "").upper()
    if not txt:
        return False, "No se pudo leer texto del PDF"

    # Estructura mínima esperada para compra PDF de temporada.
    if "PRODUCTOR" not in txt or "LIBRA COMPRA" not in txt:
        return False, "PDF no parece ser formato de compra esperado"

    # Debe contener número de compra (con o sin ceros a la izquierda).
    nro = str(int(numero_compra))
    if not re.search(rf"\b0*{re.escape(nro)}\b", txt):
        return False, f"PDF no contiene número de compra {numero_compra}"

    # Debe contener productor esperado (coincidencia por tokens).
    if expected_productor:
        prod = _norm_name(expected_productor)
        txt_norm = _norm_name(txt)
        tokens = [t for t in prod.split() if len(t) > 2]
        overlap = sum(1 for t in tokens if t in txt_norm)
        if tokens and overlap < max(2, len(tokens) // 2):
            return False, "PDF no coincide con el productor esperado"

    # Regla de total: buscar monto cercano al total de compra (ej. 2,473.90).
    if expected_total is not None:
        vals = re.findall(r"\b\d{1,3}(?:,\d{3})*\.\d{2}\b", txt)
        found_close = False
        for v in vals:
            try:
                d = Decimal(v.replace(",", ""))
                if abs(d - Decimal(str(expected_total))) <= Decimal("5"):
                    found_close = True
                    break
            except Exception:
                continue
        if not found_close:
            return False, "PDF no contiene un monto cercano al total de la compra"

    return True, "PDF compra validado"

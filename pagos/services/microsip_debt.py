from __future__ import annotations

from decimal import Decimal
from pathlib import Path
import unicodedata
import re
import time

from pagos.models import Compra, DebtSnapshot, TipoCambio


SUMMARY_SQL_FILTERED = """
WITH CARGOS AS (
  SELECT d.DOCTO_CC_ID, d.CLIENTE_ID, cl.NOMBRE AS CLIENTE, cl.MONEDA_ID,
         SUM(i.IMPORTE + i.IMPUESTO - i.IVA_RETENIDO - i.ISR_RETENIDO) AS CARGO_NETO
  FROM DOCTOS_CC d
  JOIN CONCEPTOS_CC c ON c.CONCEPTO_CC_ID = d.CONCEPTO_CC_ID
  JOIN CLIENTES cl ON cl.CLIENTE_ID = d.CLIENTE_ID
  JOIN IMPORTES_DOCTOS_CC i ON i.DOCTO_CC_ID = d.DOCTO_CC_ID
  WHERE d.CANCELADO = 'N' AND d.ESTATUS = 'N' AND i.CANCELADO = 'N' AND i.ESTATUS = 'N'
    AND i.TIPO_IMPTE = 'C' AND c.NOMBRE IN ('Venta', 'Venta en mostrador')
  GROUP BY d.DOCTO_CC_ID, d.CLIENTE_ID, cl.NOMBRE, cl.MONEDA_ID
),
ABONOS AS (
  SELECT i.DOCTO_CC_ACR_ID AS DOCTO_CC_ID,
         SUM(i.IMPORTE + i.IMPUESTO - i.IVA_RETENIDO - i.ISR_RETENIDO) AS ABONO_NETO
  FROM IMPORTES_DOCTOS_CC i
  WHERE i.CANCELADO = 'N' AND i.ESTATUS = 'N' AND i.TIPO_IMPTE = 'R' AND i.DOCTO_CC_ACR_ID IS NOT NULL
  GROUP BY i.DOCTO_CC_ACR_ID
),
SALDO_CLIENTE AS (
  SELECT ca.CLIENTE_ID, ca.CLIENTE, ca.MONEDA_ID,
         SUM(ca.CARGO_NETO - COALESCE(ab.ABONO_NETO, 0)) AS SALDO_PENDIENTE
  FROM CARGOS ca
  LEFT JOIN ABONOS ab ON ab.DOCTO_CC_ID = ca.DOCTO_CC_ID
  WHERE (ca.CARGO_NETO - COALESCE(ab.ABONO_NETO, 0)) > 0
  GROUP BY ca.CLIENTE_ID, ca.CLIENTE, ca.MONEDA_ID
),
REM_CLIENTE AS (
  SELECT v.CLIENTE_ID, cl.NOMBRE AS CLIENTE, cl.MONEDA_ID,
         SUM(COALESCE(v.IMPORTE_NETO, 0)+COALESCE(v.TOTAL_IMPUESTOS, 0)-COALESCE(v.TOTAL_RETENCIONES, 0)
            +COALESCE(v.FLETES, 0)+COALESCE(v.OTROS_CARGOS, 0)-COALESCE(v.TOTAL_ANTICIPOS, 0)) AS REMISION_PENDIENTE
  FROM DOCTOS_VE v
  JOIN CLIENTES cl ON cl.CLIENTE_ID = v.CLIENTE_ID
  WHERE v.TIPO_DOCTO = 'R' AND v.ESTATUS = 'P'
  GROUP BY v.CLIENTE_ID, cl.NOMBRE, cl.MONEDA_ID
),
COMBINADO AS (
  SELECT COALESCE(s.CLIENTE_ID, r.CLIENTE_ID) AS CLIENTE_ID,
         COALESCE(s.CLIENTE, r.CLIENTE) AS CLIENTE,
         COALESCE(s.MONEDA_ID, r.MONEDA_ID) AS MONEDA_ID,
         COALESCE(s.SALDO_PENDIENTE, 0) AS SALDO_PENDIENTE,
         COALESCE(r.REMISION_PENDIENTE, 0) AS REMISION_PENDIENTE
  FROM SALDO_CLIENTE s
  FULL JOIN REM_CLIENTE r ON r.CLIENTE_ID = s.CLIENTE_ID AND r.MONEDA_ID = s.MONEDA_ID
)
SELECT COALESCE(c.CLIENTE_ID, 0) AS CLIENTE_ID, TRIM(c.CLIENTE) AS CLIENTE, c.MONEDA_ID,
       COALESCE(c.SALDO_PENDIENTE, 0) AS SALDO_PENDIENTE,
       COALESCE(c.REMISION_PENDIENTE, 0) AS REMISION_PENDIENTE,
       (COALESCE(c.SALDO_PENDIENTE,0)+COALESCE(c.REMISION_PENDIENTE,0)) AS TOTAL
FROM COMBINADO c
WHERE (COALESCE(c.SALDO_PENDIENTE,0)+COALESCE(c.REMISION_PENDIENTE,0)) > 0
  AND __CLIENT_FILTER__
ORDER BY CLIENTE
"""

_CACHE_TTL = 300
_cache_at = 0.0
_cache_rows: list[dict] = []


def _json_safe(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


def _norm_name(value: str) -> str:
    s = (value or "").upper().strip()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^A-Z0-9 ()]+", " ", s)
    return " ".join(s.split())


def _client_base(value: str) -> str:
    n = _norm_name(value)
    if n.startswith("1 ") or n.startswith("2 "):
        return n[2:].strip()
    return n


def _safe_like_token(value: str) -> str:
    n = _norm_name(value)
    tokens = [t for t in n.split(" ") if len(t) >= 3]
    return (tokens[0] if tokens else n)[:40]


def _fetch(sql: str):
    import fdb

    candidates = [
        (Path(__file__).resolve().parents[3] / "data" / "ALGODONERA.FDB"),
        (Path(__file__).resolve().parents[2] / "data" / "ALGODONERA.FDB"),
    ]
    db_file = next((p for p in candidates if p.exists()), candidates[0])
    con = fdb.connect(dsn=str(db_file.resolve()), user="SYSDBA", password="masterkey", charset="ISO8859_1")
    try:
        cur = con.cursor()
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        con.close()


def _rows_all_cached(force: bool = False):
    global _cache_at, _cache_rows
    now = time.time()
    if force or not _cache_rows or (now - _cache_at) > _CACHE_TTL:
        _cache_rows = _fetch(SUMMARY_SQL_FILTERED.replace("__CLIENT_FILTER__", "1=1"))
        _cache_at = now
    return _cache_rows


def _aggregate_clients(rows):
    by_client = {}
    for r in rows:
        raw = r["CLIENTE"]
        key = _client_base(raw)
        obj = by_client.setdefault(
            key,
            {
                "cliente": key,
                "cliente_ids": set(),
                "aliases": set(),
                "usd": Decimal("0"),
                "mxn": Decimal("0"),
            },
        )
        obj["cliente_ids"].add(str(r.get("CLIENTE_ID") or ""))
        obj["aliases"].add(raw)
        total = Decimal(str(r.get("TOTAL") or 0))
        if int(r.get("MONEDA_ID") or 0) == 620:
            obj["usd"] += total
        elif int(r.get("MONEDA_ID") or 0) == 1:
            obj["mxn"] += total

    out = []
    for v in by_client.values():
        out.append(
            {
                "cliente": v["cliente"],
                "cliente_id": "|".join(sorted([x for x in v["cliente_ids"] if x])),
                "aliases": sorted(v["aliases"]),
                "usd": v["usd"],
                "mxn": v["mxn"],
            }
        )
    return sorted(out, key=lambda x: (x["usd"] + x["mxn"]), reverse=True)


def find_microsip_candidates_for_productor(productor_name: str, limit: int = 12):
    token = _safe_like_token(productor_name)
    all_clients = _aggregate_clients(_rows_all_cached())
    filtered = [c for c in all_clients if token in _norm_name(c["cliente"]) or any(token in _norm_name(a) for a in c["aliases"])]
    return filtered[:limit]


def list_all_microsip_debt_clients(search: str = "", limit: int = 100):
    all_clients = _aggregate_clients(_rows_all_cached())
    if (search or "").strip():
        token = _norm_name(search)
        all_clients = [c for c in all_clients if token in _norm_name(c["cliente"]) or any(token in _norm_name(a) for a in c["aliases"])]
    return all_clients[:limit]


def sync_microsip_debt_for_compra(compra: Compra):
    mapped_name = (compra.productor.microsip_cliente_nombre or "").strip()
    all_rows = _rows_all_cached()

    if mapped_name:
        base = _client_base(mapped_name)
        rows = [r for r in all_rows if _client_base(r.get("CLIENTE", "")) == base]
        match_mode = "exact_mapped_base"
        token_used = base
    else:
        token = _safe_like_token(compra.productor.nombre)
        rows = [r for r in all_rows if token in _norm_name(r.get("CLIENTE", ""))]
        match_mode = "fuzzy"
        token_used = token

    total_mxn, total_usd = Decimal("0"), Decimal("0")
    for r in rows:
        moneda = int(r.get("MONEDA_ID") or 0)
        total = Decimal(str(r.get("TOTAL") or 0))
        if moneda == 1:
            total_mxn += total
        elif moneda == 620:
            total_usd += total

    snap = DebtSnapshot.objects.create(
        compra=compra,
        fuente="microsip",
        total_usd=total_usd,
        total_mxn=total_mxn,
        detalle_json=_json_safe({"match_mode": match_mode, "match_token": token_used, "rows": rows}),
    )

    compra.retencion_deudas_usd = total_usd
    compra.retencion_deudas_mxn = total_mxn

    tc = compra.tipo_cambio_valor or Decimal("0")
    if tc <= 0:
        tc_row = None
        if compra.fecha_liq:
            tc_row = TipoCambio.objects.filter(fecha=compra.fecha_liq).first()
        if not tc_row:
            tc_row = TipoCambio.objects.order_by("-fecha").first()
        if tc_row:
            compra.tipo_cambio = tc_row
            compra.tipo_cambio_valor = tc_row.tc
            tc = tc_row.tc

    if tc > 0:
        compra.total_deuda_en_dls = total_usd + (total_mxn / tc)
    else:
        compra.total_deuda_en_dls = total_usd

    compra.saldo_pendiente = (compra.compra_en_libras or Decimal("0")) - (compra.total_deuda_en_dls or Decimal("0"))
    compra.save(update_fields=["retencion_deudas_usd", "retencion_deudas_mxn", "tipo_cambio", "tipo_cambio_valor", "total_deuda_en_dls", "saldo_pendiente", "updated_at"])
    return snap

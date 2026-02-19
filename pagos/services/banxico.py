from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import requests


def _parse_banxico_date(value: str) -> date:
    return datetime.strptime(value, "%d/%m/%Y").date()


def _parse_banxico_decimal(value: str):
    cleaned = (value or "").strip().replace(",", "")
    if cleaned in {"", "N/E"}:
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def fetch_tipo_cambio(token: str, serie_id: str, start: date, end: date, timeout: int = 20):
    if not token:
        raise ValueError("BANXICO_TOKEN no configurado.")
    url = (
        "https://www.banxico.org.mx/SieAPIRest/service/v1/series/"
        f"{serie_id}/datos/{start:%Y-%m-%d}/{end:%Y-%m-%d}"
    )
    response = requests.get(
        url,
        headers={"Bmx-Token": token, "Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    series = payload.get("bmx", {}).get("series", [])
    if not series:
        return []

    output = []
    for row in series[0].get("datos", []):
        fecha = _parse_banxico_date(row.get("fecha", ""))
        valor = _parse_banxico_decimal(row.get("dato", ""))
        if valor is not None:
            output.append((fecha, valor))
    return output

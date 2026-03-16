from __future__ import annotations

from decimal import Decimal

from pagos.models import Compra, DebtSnapshot, Deduccion, MonedaChoices


def payable_breakdown(compra: Compra) -> dict:
    purchase_total = compra.compra_en_libras or Decimal("0")
    anticipos = compra.total_aplicado_anticipos or Decimal("0")

    # Regla operativa: SOLO usar montos explícitos capturados en la compra.
    # Si usuario pone 0, no se descuenta deuda aunque haya snapshot histórico.
    debt_usd = compra.retencion_deudas_usd or Decimal("0")
    debt_mxn = compra.retencion_deudas_mxn or Decimal("0")

    tc = compra.tipo_cambio_valor or Decimal("0")
    debt_mxn_in_usd = (debt_mxn / tc) if tc and tc > 0 else Decimal("0")

    resico = compra.retencion_resico or Decimal("0")

    manual_usd = Decimal("0")
    for d in compra.deducciones.all():
        if d.moneda == MonedaChoices.DOLARES:
            manual_usd += d.monto
        elif d.moneda == MonedaChoices.PESOS and tc and tc > 0:
            manual_usd += (d.monto / tc)

    saldo = purchase_total - anticipos - debt_usd - debt_mxn_in_usd - resico - manual_usd
    return {
        "purchase_total": purchase_total,
        "anticipos": anticipos,
        "debt_usd": debt_usd,
        "debt_mxn": debt_mxn,
        "debt_mxn_in_usd": debt_mxn_in_usd,
        "resico": resico,
        "manual_usd": manual_usd,
        "saldo_a_pagar": saldo,
    }


def calculate_payable(compra: Compra) -> Decimal:
    return payable_breakdown(compra)["saldo_a_pagar"]


def register_debt_snapshot(compra: Compra, total_usd: Decimal, total_mxn: Decimal, detalle: dict | None = None):
    return DebtSnapshot.objects.create(
        compra=compra,
        fuente="microsip",
        total_usd=total_usd,
        total_mxn=total_mxn,
        detalle_json=detalle or {},
    )


def add_manual_deduction(compra: Compra, *, concepto: str, monto: Decimal, moneda: str, notas: str = ""):
    return Deduccion.objects.create(
        compra=compra,
        concepto=concepto,
        monto=monto,
        moneda=moneda,
        fuente="manual",
        notas=notas,
    )

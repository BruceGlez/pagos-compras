from __future__ import annotations

from decimal import Decimal

from django.core.management.base import BaseCommand

from pagos.models import Compra, WorkflowStateChoices
from pagos.services import transition_compra


class Command(BaseCommand):
    help = "Backfill de workflow_state para compras con drift (dry-run por defecto)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true", help="Aplicar cambios")
        parser.add_argument("--numero-compra", type=int, help="Limitar a un numero_compra")

    def _advance(self, compra: Compra, target: str, actor: str):
        chain = [
            WorkflowStateChoices.IMPORTED,
            WorkflowStateChoices.DEBT_CALCULATED,
            WorkflowStateChoices.WAITING_INVOICE,
            WorkflowStateChoices.INVOICE_RECEIVED,
            WorkflowStateChoices.INVOICE_VALID,
            WorkflowStateChoices.WAITING_BANK_CONFIRMATION,
            WorkflowStateChoices.READY_TO_PAY,
            WorkflowStateChoices.PAID,
        ]
        if compra.workflow_state not in chain or target not in chain:
            raise ValueError("Estado fuera de cadena soportada")
        if chain.index(target) < chain.index(compra.workflow_state):
            raise ValueError("No se permite retroceso en este backfill")
        for t in chain[chain.index(compra.workflow_state) + 1 : chain.index(target) + 1]:
            transition_compra(compra, t, actor=actor, reason="Backfill workflow state drift")
            compra.refresh_from_db()

    def _suggest_target(self, compra: Compra):
        # Caso 1: ya pagada efectivamente pero estado no PAID.
        try:
            saldo = Decimal(str(compra.saldo_por_pagar or "0"))
        except Exception:
            saldo = Decimal("999999")
        paid_like = compra.total_pagado_vigente > Decimal("0") and abs(saldo) <= Decimal("3")
        if paid_like and compra.workflow_state != WorkflowStateChoices.PAID:
            return WorkflowStateChoices.PAID, f"paid_like total_pagado={compra.total_pagado_vigente} saldo={saldo}"

        # Caso 2: UI ya va en pasos avanzados pero estado temprano.
        step = compra.flujo_step_default
        if step in {"solicitar_factura", "revisar_factura", "pago"} and compra.workflow_state in {
            WorkflowStateChoices.IMPORTED,
            WorkflowStateChoices.DEBT_CALCULATED,
        }:
            return WorkflowStateChoices.WAITING_INVOICE, f"advanced_step={step} with early_state={compra.workflow_state}"

        return None, ""

    def handle(self, *args, **options):
        do_apply = bool(options.get("apply"))
        numero_compra = options.get("numero_compra")
        actor = "backfill_workflow_states"

        qs = Compra.objects.filter(cancelada=False).order_by("id")
        if numero_compra is not None:
            qs = qs.filter(numero_compra=numero_compra)

        total = qs.count()
        planned = 0
        applied = 0
        failed = 0

        self.stdout.write(self.style.WARNING(f"Modo: {'APPLY' if do_apply else 'DRY-RUN'} | compras={total}"))

        for compra in qs:
            target, reason = self._suggest_target(compra)
            if not target:
                continue

            planned += 1
            line = f"compra_id={compra.id} nro={compra.numero_compra} {compra.workflow_state} -> {target} | {reason}"

            if not do_apply:
                self.stdout.write(f"[PLAN] {line}")
                continue

            try:
                self._advance(compra, target, actor=actor)
                applied += 1
                self.stdout.write(self.style.SUCCESS(f"[APPLY] {line}"))
            except Exception as e:
                failed += 1
                self.stdout.write(self.style.ERROR(f"[FAIL] {line} | error={e}"))

        self.stdout.write("-" * 60)
        self.stdout.write(f"planned={planned} applied={applied} failed={failed}")
        if not do_apply:
            self.stdout.write(self.style.SUCCESS("Dry-run completado. Usa --apply para ejecutar."))

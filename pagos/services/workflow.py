from __future__ import annotations

from dataclasses import dataclass

from pagos.models import Compra, WorkflowStateChoices


@dataclass(frozen=True)
class TransitionRule:
    frm: str
    to: str


ALLOWED_TRANSITIONS = {
    TransitionRule(WorkflowStateChoices.IMPORTED, WorkflowStateChoices.DEBT_CALCULATED),
    TransitionRule(WorkflowStateChoices.DEBT_CALCULATED, WorkflowStateChoices.WAITING_INVOICE),
    TransitionRule(WorkflowStateChoices.WAITING_INVOICE, WorkflowStateChoices.INVOICE_RECEIVED),
    TransitionRule(WorkflowStateChoices.INVOICE_RECEIVED, WorkflowStateChoices.INVOICE_VALID),
    TransitionRule(WorkflowStateChoices.INVOICE_RECEIVED, WorkflowStateChoices.INVOICE_BLOCKED),
    TransitionRule(WorkflowStateChoices.INVOICE_BLOCKED, WorkflowStateChoices.INVOICE_RECEIVED),
    TransitionRule(WorkflowStateChoices.INVOICE_VALID, WorkflowStateChoices.WAITING_BANK_CONFIRMATION),
    TransitionRule(WorkflowStateChoices.WAITING_BANK_CONFIRMATION, WorkflowStateChoices.READY_TO_PAY),
    TransitionRule(WorkflowStateChoices.READY_TO_PAY, WorkflowStateChoices.PAID),
    TransitionRule(WorkflowStateChoices.PAID, WorkflowStateChoices.ARCHIVED),
}


def _required_expediente_present(compra: Compra) -> bool:
    etapas = set(compra.documentos.values_list("etapa", flat=True))
    required = {"solicitud_factura", "factura", "pago"}
    return required.issubset(etapas)


def _precondition_error(compra: Compra, to_state: str) -> str | None:
    latest_validation = compra.invoice_validations.first()

    if to_state in {WorkflowStateChoices.WAITING_BANK_CONFIRMATION, WorkflowStateChoices.READY_TO_PAY, WorkflowStateChoices.PAID}:
        if not latest_validation or not latest_validation.valid:
            return "Factura no validada: no se puede avanzar sin XML válido."

    if to_state in {WorkflowStateChoices.READY_TO_PAY, WorkflowStateChoices.PAID}:
        if not compra.bank_account_confirmed:
            return "Cuenta bancaria no confirmada."

    if to_state == WorkflowStateChoices.ARCHIVED:
        if not _required_expediente_present(compra):
            return "Expediente incompleto: faltan documentos requeridos."

    return None


def transition_compra(compra: Compra, to_state: str, *, actor: str = "system", reason: str = ""):
    if compra.cancelada and to_state != WorkflowStateChoices.ARCHIVED:
        raise ValueError("Compra cancelada: no se permiten transiciones operativas.")

    frm = compra.workflow_state
    if frm == to_state:
        return compra

    rule = TransitionRule(frm, to_state)
    if rule not in ALLOWED_TRANSITIONS:
        raise ValueError(f"Invalid transition: {frm} -> {to_state}")

    err = _precondition_error(compra, to_state)
    if err:
        raise ValueError(err)

    compra.set_workflow_state(to_state, reason=reason, actor=actor)
    return compra

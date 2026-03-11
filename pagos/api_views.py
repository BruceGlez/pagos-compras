from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from .models import Compra, WorkflowStateChoices


@login_required
def api_queue_summary(request):
    base = Compra.objects.filter(cancelada=False)
    data = {
        "WAITING_INVOICE": base.filter(workflow_state=WorkflowStateChoices.WAITING_INVOICE).count(),
        "INVOICE_BLOCKED": base.filter(workflow_state=WorkflowStateChoices.INVOICE_BLOCKED).count(),
        "WAITING_BANK_CONFIRMATION": base.filter(workflow_state=WorkflowStateChoices.WAITING_BANK_CONFIRMATION).count(),
        "READY_TO_PAY": base.filter(workflow_state=WorkflowStateChoices.READY_TO_PAY).count(),
        "PAID": base.filter(workflow_state=WorkflowStateChoices.PAID).count(),
    }
    return JsonResponse({"ok": True, "summary": data})


@login_required
def api_compra_detail(request, compra_id: int):
    c = Compra.objects.select_related("productor", "tipo_cambio").filter(pk=compra_id).first()
    if not c:
        return JsonResponse({"ok": False, "error": "not_found"}, status=404)
    return JsonResponse(
        {
            "ok": True,
            "compra": {
                "id": c.id,
                "numero_compra": c.numero_compra,
                "productor": c.productor.nombre,
                "workflow_state": c.workflow_state,
                "cancelada": c.cancelada,
                "compra_en_libras": float(c.compra_en_libras or 0),
                "total_deuda_en_dls": float(c.total_deuda_en_dls or 0),
                "saldo_por_pagar": float(c.saldo_por_pagar or 0),
                "tipo_cambio_valor": float(c.tipo_cambio_valor or 0),
            },
        }
    )

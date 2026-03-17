from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.mail import send_mail, EmailMultiAlternatives
from django.core.files.base import ContentFile
from django.core.paginator import Paginator
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET

from django.utils import timezone
from django.views.generic import ListView

from .forms import (
    AnticipoForm,
    AplicacionAnticipoForm,
    CompraDivisionCreateForm,
    CompraRegistrarFacturaForm,
    CompraSolicitarFacturaForm,
    CompraFiltroForm,
    CompraFlujo1Form,
    CompraFlujo2Form,
    CompraFlujo3Form,
    CompraFlujoAnticiposForm,
    CompraOperativaForm,
    CompraForm,
    CancelarCompraForm,
    CompraBankConfirmationForm,
    ContadorForm,
    DeduccionForm,
    BeneficiaryValidationExceptionForm,
    DocumentoCompraForm,
    EmailTemplateForm,
    ImportAnticiposExcelForm,
    ImportComprasExcelForm,
    PagoCompraForm,
    PersonaFacturaQuickForm,
    ProductorForm,
    ProductorCuentaBancariaForm,
    FacturadorCuentaBancariaForm,
    TipoCambioForm,
    XmlValidationConfigForm,
)
from .models import Anticipo, AplicacionAnticipo, BeneficiaryValidationException, Compra, Contador, Deduccion, DocumentoCompra, EmailOutboxLog, EmailTemplate, FacturadorCuentaBancaria, ImportRun, PagoCompra, PersonaFactura, Productor, ProductorCuentaBancaria, TipoCambio, WorkflowStateChoices, XmlValidationConfig
from .services import (
    build_invoice_request_email,
    build_invoice_request_message,
    render_invoice_email_html,
    create_invoice_validation_for_compra,
    detect_compras_conflicts,
    gmail_ready,
    gmail_inbox_ready,
    fetch_gmail_attachments_for_compra,
    import_anticipos_excel,
    import_compras_excel,
    send_gmail,
    payable_breakdown,
    find_microsip_candidates_for_productor,
    list_all_microsip_debt_clients,
    list_microsip_clients_by_rfc,
    preview_anticipos_excel,
    preview_compras_excel,
    sync_microsip_debt_for_compra,
    transition_compra,
    extract_pdf_text,
    parse_payment_receipt_text,
)


def _can_write(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=["Admin", "Operador"]).exists()


def _attach_email_proof_to_expediente(
    compra: Compra,
    *,
    to_email: str,
    subject: str,
    body: str,
    provider: str,
    provider_msg_id: str = "",
    template_code: str = "",
):
    ts = timezone.now()
    stamp = ts.strftime("%Y%m%d_%H%M%S")
    filename = f"acuse_envio_solicitud_compra_{compra.id}_{stamp}.txt"
    content = (
        "ACUSE DE ENVIO - SOLICITUD DE FACTURA\n"
        f"Fecha/Hora: {ts.isoformat()}\n"
        f"Compra ID: {compra.id}\n"
        f"Numero compra: {compra.numero_compra}\n"
        f"Destinatario: {to_email}\n"
        f"Provider: {provider}\n"
        f"Provider Message ID: {provider_msg_id or '-'}\n"
        f"Template: {template_code or '-'}\n"
        f"Asunto: {subject}\n"
        "\n--- CUERPO ---\n"
        f"{body}\n"
    )

    doc = DocumentoCompra(compra=compra, etapa="solicitud_factura", descripcion="Acuse envío solicitud factura")
    doc.archivo.save(filename, ContentFile(content.encode("utf-8")), save=True)


LEGAL_TOKENS = {
    "SA", "CV", "DE", "RL", "S", "A", "P", "I", "SC", "SPR", "SAPI", "SAB", "COOP", "AC", "THE", "DEL", "LA", "LOS", "LAS", "Y", "E",
}


def _get_compra_pdf_attachment(compra: Compra):
    doc = compra.documentos.filter(etapa="compra_original", archivo__iendswith=".pdf").order_by("-created_at").first()
    if not doc or not doc.archivo:
        return None
    try:
        data = doc.archivo.read()
        filename = (doc.archivo.name or "compra.pdf").split("/")[-1]
        return (filename, data, "application/pdf")
    except Exception:
        return None


def _norm_attachment_base(filename: str) -> str:
    base = (filename or "").strip().rsplit(".", 1)[0].lower()
    # Gmail/clients often append " (1)", " (2)" to duplicates.
    base = re.sub(r"\s*\(\d+\)$", "", base)
    return base


def _extract_xml_basic(xml_bytes: bytes):
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
        return {}
    ns = {"cfdi": "http://www.sat.gob.mx/cfd/4", "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital"}
    comp = root
    emisor = root.find("cfdi:Emisor", ns)
    tfd = root.find("cfdi:Complemento/tfd:TimbreFiscalDigital", ns)
    return {
        "rfc_emisor": (emisor.attrib.get("Rfc", "") if emisor is not None else "").strip().upper(),
        "nombre_emisor": (emisor.attrib.get("Nombre", "") if emisor is not None else "").strip(),
        "total": (comp.attrib.get("Total", "") if comp is not None else "").strip(),
        "uuid": (tfd.attrib.get("UUID", "") if tfd is not None else "").strip(),
    }


def _norm_name(value: str) -> str:
    txt = (value or "").upper().strip()
    txt = txt.replace("Á", "A").replace("É", "E").replace("Í", "I").replace("Ó", "O").replace("Ú", "U")
    txt = re.sub(r"[^A-Z0-9 ]+", " ", txt)
    tokens = [t for t in txt.split() if t and t not in LEGAL_TOKENS and len(t) > 1]
    return " ".join(tokens)


def _token_similarity(a: str, b: str) -> Decimal:
    sa = set((a or "").split())
    sb = set((b or "").split())
    if not sa or not sb:
        return Decimal("0")
    inter = len(sa & sb)
    union = len(sa | sb)
    return Decimal(str(inter / union)) if union else Decimal("0")


def _beneficiary_validation(compra: Compra):
    latest = compra.invoice_validations.first()
    raw = (getattr(latest, "raw_result", {}) or {}) if latest else {}
    emisor_nombre = (raw.get("nombre_emisor") or compra.factura or "").strip()
    emisor_rfc = (raw.get("rfc_emisor") or getattr(latest, "rfc_emisor", "") or "").strip().upper()
    account_holder = ""
    if (compra.cuenta_productor or "").strip():
        acc = None
        if compra.facturador_id:
            acc = FacturadorCuentaBancaria.objects.filter(facturador=compra.facturador, cuenta=compra.cuenta_productor).first()
        if not acc:
            acc = ProductorCuentaBancaria.objects.filter(productor=compra.productor, cuenta=compra.cuenta_productor).first()
        if acc:
            account_holder = (acc.titular or "").strip()

    emisor_norm = _norm_name(emisor_nombre)
    holder_norm = _norm_name(account_holder)

    yellow_threshold = Decimal(str(getattr(settings, "BENEFICIARY_MATCH_YELLOW_THRESHOLD", "0.45")))

    if not emisor_norm or not holder_norm:
        return {"status": "yellow", "reason": "Falta nombre de emisor o titular de cuenta", "emisor": emisor_nombre, "holder": account_holder, "score": Decimal("0")}

    if emisor_norm == holder_norm:
        return {"status": "green", "reason": "Titular coincide con emisor", "emisor": emisor_nombre, "holder": account_holder, "score": Decimal("1")}

    score = _token_similarity(emisor_norm, holder_norm)

    has_exception = BeneficiaryValidationException.objects.filter(
        active=True,
        productor=compra.productor,
        account_holder__iexact=account_holder,
    ).filter(Q(emisor_rfc="") | Q(emisor_rfc=emisor_rfc)).exists()

    if has_exception:
        return {"status": "yellow", "reason": "Excepción autorizada encontrada (requiere justificación)", "emisor": emisor_nombre, "holder": account_holder, "score": score}
    if score >= Decimal("0.80"):
        return {"status": "green", "reason": "Coincidencia alta de nombre", "emisor": emisor_nombre, "holder": account_holder, "score": score}
    if score >= yellow_threshold:
        return {"status": "yellow", "reason": "Coincidencia parcial de nombre (requiere justificación)", "emisor": emisor_nombre, "holder": account_holder, "score": score}
    return {"status": "red", "reason": "Titular de cuenta no coincide con emisor XML", "emisor": emisor_nombre, "holder": account_holder, "score": score}


class HomeView(LoginRequiredMixin, ListView):
    template_name = "pagos/home.html"
    model = Compra
    context_object_name = "compras_recientes"
    paginate_by = 10

    def get_queryset(self):
        return Compra.objects.select_related("productor").filter(cancelada=False).order_by("-fecha_liq", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        anticipos_stats = Anticipo.objects.aggregate(total=Sum("monto_anticipo"), conteo=Count("id"))
        compras_stats = Compra.objects.filter(cancelada=False).aggregate(total=Sum("compra_en_libras"), conteo=Count("id"))
        context["productores_activos"] = Productor.objects.filter(activo=True).count()
        context["anticipos_total"] = anticipos_stats["total"] or 0
        context["anticipos_count"] = anticipos_stats["conteo"] or 0
        context["compras_total_libras"] = compras_stats["total"] or 0
        context["compras_count"] = compras_stats["conteo"] or 0
        context["tc_ultimo"] = TipoCambio.objects.order_by("-fecha").first()

        today = timezone.localdate()
        pending_qs = Compra.objects.filter(cancelada=False).exclude(workflow_state=WorkflowStateChoices.PAID).only("id", "fecha_liq")
        aging = {"0_7": 0, "8_15": 0, "16_30": 0, "31_plus": 0}
        for c in pending_qs:
            days = (today - c.fecha_liq).days if c.fecha_liq else 0
            if days <= 7:
                aging["0_7"] += 1
            elif days <= 15:
                aging["8_15"] += 1
            elif days <= 30:
                aging["16_30"] += 1
            else:
                aging["31_plus"] += 1
        context["aging"] = aging
        context["sla_over_15"] = aging["16_30"] + aging["31_plus"]
        return context


@login_required
def registro_view(request):
    forms_config = [
        ("productor", ProductorForm, "Productor guardado."),
        ("tipo_cambio", TipoCambioForm, "Tipo de cambio guardado."),
        ("anticipo", AnticipoForm, "Anticipo guardado."),
        ("compra", CompraForm, "Compra guardada."),
        ("aplicacion", AplicacionAnticipoForm, "Aplicacion guardada."),
    ]
    active = request.GET.get("form", "productor")
    form_instances = {name: form_cls(prefix=name) for name, form_cls, _ in forms_config}

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect(request.path)
        active = request.POST.get("form_type", "productor")
        for name, form_cls, success_msg in forms_config:
            if name == active:
                form_instances[name] = form_cls(request.POST, prefix=name)
                if form_instances[name].is_valid():
                    form_instances[name].save()
                    messages.success(request, success_msg)
                    return redirect(f"{request.path}?form={active}")
                messages.error(request, "Revisa los errores del formulario.")

    return render(
        request,
        "pagos/registro.html",
        {"form_instances": form_instances, "active_form": active},
    )


@login_required
def compras_operativas_view(request):
    qs = Compra.objects.select_related("productor", "tipo_cambio").filter(cancelada=False).order_by("-fecha_liq", "-id")
    filtro = CompraFiltroForm(request.GET or None)
    numeric_search_mode = False
    if filtro.is_valid():
        data = filtro.cleaned_data
        if data.get("q"):
            term = data["q"].strip()
            if term.isdigit():
                numeric_search_mode = True
                num = int(term)
                qs = qs.filter(
                    Q(numero_compra=num)
                    | Q(parent_compra__numero_compra=num)
                )
            else:
                qs = qs.filter(
                    Q(numero_compra__icontains=term)
                    | Q(productor__nombre__icontains=term)
                    | Q(uuid_factura__icontains=term)
                    | Q(factura__icontains=term)
                )
        if data.get("productor"):
            qs = qs.filter(productor=data["productor"])
        if data.get("fecha_desde"):
            qs = qs.filter(fecha_liq__gte=data["fecha_desde"])
        if data.get("fecha_hasta"):
            qs = qs.filter(fecha_liq__lte=data["fecha_hasta"])
        if data.get("estatus_de_pago"):
            qs = qs.filter(estatus_de_pago=data["estatus_de_pago"])
        if data.get("workflow_state"):
            qs = qs.filter(workflow_state=data["workflow_state"])

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    params = request.GET.copy()
    if "page" in params:
        params.pop("page")
    querystring = params.urlencode()

    return render(
        request,
        "pagos/compras_operativas.html",
        {
            "filtro_form": filtro,
            "page_obj": page_obj,
            "querystring": querystring,
            "numeric_search_mode": numeric_search_mode,
        },
    )


def _queue_blockers_for_compra(c: Compra):
    b = []
    has_compra_pdf = c.documentos.filter(etapa="compra_original", archivo__iendswith=".pdf").exists()
    if not has_compra_pdf:
        b.append("Falta compra original PDF")
    if not (c.productor.rfc or "").strip():
        b.append("Falta RFC productor")

    has_xml = c.documentos.filter(etapa="factura", archivo__iendswith=".xml").exists()
    has_pdf = c.documentos.filter(etapa="factura", archivo__iendswith=".pdf").exists()
    if c.workflow_state in {WorkflowStateChoices.WAITING_INVOICE, WorkflowStateChoices.INVOICE_BLOCKED}:
        if not has_xml:
            b.append("Falta XML factura")
        if not has_pdf:
            b.append("Falta PDF factura")

    if c.workflow_state in {WorkflowStateChoices.WAITING_BANK_CONFIRMATION, WorkflowStateChoices.READY_TO_PAY} and not c.bank_account_confirmed:
        b.append("Falta confirmación bancaria")

    if c.workflow_state == WorkflowStateChoices.READY_TO_PAY:
        ben = _beneficiary_validation(c)
        if ben.get("status") == "red":
            b.append("Beneficiario no coincide")

    return b


@login_required
def readiness_queue_view(request):
    qs = Compra.objects.select_related("productor").filter(cancelada=False).order_by("fecha_liq", "id")
    state = (request.GET.get("state") or "").strip()
    if state:
        qs = qs.filter(workflow_state=state)
    else:
        qs = qs.filter(
            workflow_state__in=[
                WorkflowStateChoices.WAITING_INVOICE,
                WorkflowStateChoices.INVOICE_BLOCKED,
                WorkflowStateChoices.WAITING_BANK_CONFIRMATION,
                WorkflowStateChoices.READY_TO_PAY,
                WorkflowStateChoices.PAID,
            ]
        )

    base_q = Compra.objects.filter(cancelada=False)
    counts = {
        "WAITING_INVOICE": base_q.filter(workflow_state=WorkflowStateChoices.WAITING_INVOICE).count(),
        "INVOICE_BLOCKED": base_q.filter(workflow_state=WorkflowStateChoices.INVOICE_BLOCKED).count(),
        "WAITING_BANK_CONFIRMATION": base_q.filter(workflow_state=WorkflowStateChoices.WAITING_BANK_CONFIRMATION).count(),
        "READY_TO_PAY": base_q.filter(workflow_state=WorkflowStateChoices.READY_TO_PAY).count(),
        "PAID": base_q.filter(workflow_state=WorkflowStateChoices.PAID).count(),
    }

    queue_items = list(qs[:300])

    priority_scores = {}
    for c in queue_items:
        score = Decimal("0")
        # Base by state urgency
        if c.workflow_state == WorkflowStateChoices.READY_TO_PAY:
            score += Decimal("100")
        elif c.workflow_state == WorkflowStateChoices.WAITING_BANK_CONFIRMATION:
            score += Decimal("70")
        elif c.workflow_state == WorkflowStateChoices.INVOICE_BLOCKED:
            score += Decimal("40")
        elif c.workflow_state == WorkflowStateChoices.WAITING_INVOICE:
            score += Decimal("20")

        # Older liquidation date => higher priority
        if c.fecha_liq:
            days = (timezone.localdate() - c.fecha_liq).days
            if days > 0:
                score += Decimal(str(min(days, 30)))

        # Higher payable saldo => slightly higher priority
        try:
            saldo = Decimal(str(c.saldo_por_pagar or "0"))
            if saldo > 0:
                score += min(saldo / Decimal("10000"), Decimal("20"))
        except Exception:
            pass

        priority_scores[c.id] = score.quantize(Decimal("0.01"))

    blocked = {
        c.id: (c.invoice_validations.first().blocked_reason if c.invoice_validations.first() else "")
        for c in queue_items
    }

    queue_blockers = {c.id: _queue_blockers_for_compra(c) for c in queue_items}

    queue_items.sort(key=lambda x: (priority_scores.get(x.id, Decimal("0")), x.fecha_liq or timezone.localdate()), reverse=True)

    return render(
        request,
        "pagos/readiness_queue.html",
        {
            "compras": queue_items,
            "counts": counts,
            "active_state": state,
            "blocked_reasons": blocked,
            "queue_blockers": queue_blockers,
            "priority_scores": priority_scores,
        },
    )


@login_required
def import_anticipos_view(request):
    form = ImportAnticiposExcelForm()
    result = None
    preview_rows = []
    import_run = None

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos para importar.")
            return redirect("anticipos")
        form = ImportAnticiposExcelForm(request.POST, request.FILES)
        action = request.POST.get("action", "preview")
        if form.is_valid():
            f = form.cleaned_data["archivo"]
            tmp_path = f"/tmp/{f.name}"
            with open(tmp_path, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)

            preview_rows = preview_anticipos_excel(tmp_path, limit=20)
            if action == "import":
                stats = import_anticipos_excel(tmp_path, dry_run=False)
                result = stats
                import_run = ImportRun.objects.order_by("-created_at").first()
                messages.success(request, "Importación de anticipos completada.")
            else:
                messages.info(request, "Vista previa de anticipos generada.")

    return render(
        request,
        "pagos/import_anticipos.html",
        {"form": form, "result": result, "preview_rows": preview_rows, "import_run": import_run},
    )


@login_required
def compras_archivadas_view(request):
    qs = Compra.objects.select_related("productor").filter(cancelada=True).order_by("-updated_at", "-id")
    return render(request, "pagos/compras_archivadas.html", {"compras": qs[:500]})


@login_required
def import_compras_view(request):
    form = ImportComprasExcelForm()
    result = None
    preview_rows = []
    conflict_rows = []
    import_run = None
    conflict_policy = "ask"

    # Solo Admin (grupo) o superuser pueden importar compras.
    user = getattr(request, "user", None)
    is_admin_import = bool(user and user.is_authenticated and (user.is_superuser or user.groups.filter(name="Admin").exists()))
    if not is_admin_import:
        messages.error(request, "Solo Admin puede importar compras.")
        return redirect("compras_operativas")

    if request.method == "POST":
        form = ImportComprasExcelForm(request.POST, request.FILES)
        action = request.POST.get("action", "preview")
        if form.is_valid():
            f = form.cleaned_data["archivo"]
            conflict_policy = form.cleaned_data.get("conflict_policy", "ask")
            tmp_path = f"/tmp/{f.name}"
            with open(tmp_path, "wb") as out:
                for chunk in f.chunks():
                    out.write(chunk)

            preview_rows = preview_compras_excel(tmp_path, limit=20)
            conflict_rows = detect_compras_conflicts(tmp_path)

            if action == "import":
                resolutions = {}
                if conflict_policy == "ask":
                    for c in conflict_rows:
                        rn = str(c["row_number"])
                        resolutions[rn] = request.POST.get(f"conflict_row_{rn}", conflict_policy)

                stats = import_compras_excel(
                    tmp_path,
                    dry_run=False,
                    conflict_policy=conflict_policy,
                    conflict_resolutions=resolutions,
                )
                result = stats
                import_run = ImportRun.objects.order_by("-created_at").first()
                messages.success(request, "Importacion de compras completada.")
            else:
                if conflict_rows and conflict_policy == "ask":
                    messages.info(request, "Vista previa generada. Revisa conflictos y luego confirma importación.")
                elif conflict_rows:
                    messages.info(request, "Vista previa generada. Los conflictos se resolverán automáticamente según la política seleccionada.")
                else:
                    messages.info(request, "Vista previa generada.")

    return render(
        request,
        "pagos/import_compras.html",
        {
            "form": form,
            "result": result,
            "preview_rows": preview_rows,
            "conflict_rows": conflict_rows,
            "import_run": import_run,
            "conflict_policy": conflict_policy,
        },
    )


@login_required
def readiness_queue_action_view(request, compra_id):
    if request.method != "POST":
        return redirect("readiness_queue")

    compra = get_object_or_404(Compra, pk=compra_id)
    action = (request.POST.get("action") or "").strip()
    actor = str(getattr(request.user, "username", "operador") or "operador")

    if action == "bank_confirm":
        if not (compra.cuenta_productor or "").strip():
            messages.warning(request, "No se puede confirmar banco desde queue: falta cuenta bancaria seleccionada en la compra.")
            return redirect("readiness_queue")
        compra.bank_account_confirmed = True
        if not compra.bank_confirmed_at:
            compra.bank_confirmed_at = timezone.now()
        if not compra.bank_confirmation_source:
            compra.bank_confirmation_source = "queue_quick_action"
        compra.save(update_fields=["bank_account_confirmed", "bank_confirmed_at", "bank_confirmation_source", "updated_at"])
        messages.success(request, "Cuenta bancaria confirmada.")
    elif action == "mark_ready":
        blockers = _queue_blockers_for_compra(compra)
        if blockers:
            messages.error(request, f"No se puede marcar READY_TO_PAY. Bloqueadores: {', '.join(blockers)}")
            return redirect("readiness_queue")
        try:
            transition_compra(compra, WorkflowStateChoices.READY_TO_PAY, actor=actor, reason="Acción rápida desde queue")
            messages.success(request, "Compra marcada como READY_TO_PAY.")
        except ValueError as e:
            messages.warning(request, f"No se pudo mover a READY_TO_PAY: {e}")
    elif action == "reopen_invoice":
        try:
            transition_compra(compra, WorkflowStateChoices.INVOICE_RECEIVED, actor=actor, reason="Reapertura de factura bloqueada")
            messages.success(request, "Factura reabierta para corrección.")
        except ValueError:
            messages.warning(request, "No se pudo reabrir factura desde el estado actual.")

    return redirect("readiness_queue")


@login_required
def compra_mapear_microsip_view(request, compra_id):
    compra = get_object_or_404(Compra.objects.select_related("productor"), pk=compra_id)
    if not _can_write(request.user):
        messages.error(request, "No tienes permisos de edición.")
        return redirect(f"/compras/{compra.id}/flujo/?step=deudas")

    if not (compra.productor.rfc or "").strip():
        messages.error(request, "Antes de mapear Microsip, completa el RFC del productor.")
        return redirect(f"/productores/{compra.productor.id}/editar/?next=/compras/{compra.id}/flujo/%3Fstep%3Ddeudas")

    if request.method == "POST":
        selected = (request.POST.get("cliente_microsip") or "").strip()
        if selected:
            parts = selected.split("||")
            selected_id = (parts[0] if len(parts) > 0 else "").strip()
            selected_name = (parts[1] if len(parts) > 1 else "").strip()
            selected_rfc = (parts[2] if len(parts) > 2 else "").strip().upper()

            prod_rfc = (compra.productor.rfc or "").strip().upper()
            if prod_rfc and selected_rfc and prod_rfc != selected_rfc:
                messages.error(
                    request,
                    f"No se puede vincular: RFC productor ({prod_rfc}) no coincide con RFC Microsip ({selected_rfc}).",
                )
                return redirect(f"/compras/{compra.id}/mapear-microsip/")

            compra.productor.microsip_cliente_nombre = selected_name
            compra.productor.microsip_cliente_id = selected_id
            compra.productor.save(update_fields=["microsip_cliente_nombre", "microsip_cliente_id", "updated_at"])
            messages.success(request, "Vinculación Microsip guardada. Ahora sincroniza deudas.")
            return redirect(f"/compras/{compra.id}/flujo/?step=deudas")

    candidates = find_microsip_candidates_for_productor(compra.productor.nombre, limit=20)
    search = (request.GET.get("search") or "").strip()
    manual_candidates = list_all_microsip_debt_clients(search=search, limit=80) if (search or not candidates) else []
    rfc_candidates = list_microsip_clients_by_rfc(compra.productor.rfc, limit=30) if (compra.productor.rfc or "").strip() else []

    # Unificar candidatos: primero coincidencias por RFC, luego deuda activa; sin duplicados por cliente_id.
    unified_candidates = []
    seen_ids = set()
    for c in (rfc_candidates + candidates):
        cid = str(c.get("cliente_id") or "")
        if cid and cid in seen_ids:
            continue
        if cid:
            seen_ids.add(cid)
        c2 = dict(c)
        c2["source"] = "RFC" if c in rfc_candidates else "DEUDA"
        unified_candidates.append(c2)

    return render(
        request,
        "pagos/mapear_microsip.html",
        {
            "compra": compra,
            "candidates": unified_candidates,
            "manual_candidates": manual_candidates,
            "search": search,
        },
    )


@login_required
def compra_create_view(request):
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("compras_operativas")
        form = CompraFlujo1Form(request.POST)
        if form.is_valid():
            compra = form.save()
            transition_compra(
                compra,
                WorkflowStateChoices.DEBT_CALCULATED,
                actor=str(getattr(request.user, "username", "operador") or "operador"),
                reason="Compra capturada en flujo 1",
            )
            messages.success(request, "Flujo 1 guardado. Continua con los siguientes pasos.")
            return redirect("compra_flujo", compra_id=compra.id)
        messages.error(request, "Revisa los datos de la compra.")
    else:
        form = CompraFlujo1Form()
    return render(request, "pagos/compra_form.html", {"form": form, "form_title": "Nueva Compra - Flujo 1"})


@login_required
def compra_edit_view(request, compra_id):
    compra = get_object_or_404(Compra, pk=compra_id)
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("compras_operativas")
        form = CompraOperativaForm(request.POST, instance=compra)
        if form.is_valid():
            form.save()
            messages.success(request, "Compra actualizada correctamente.")
            next_url = request.POST.get("next") or request.GET.get("next")
            if next_url:
                return redirect(next_url)
            return redirect("compras_operativas")
        messages.error(request, "Revisa los datos de la compra.")
    else:
        form = CompraOperativaForm(instance=compra)
    return render(
        request,
        "pagos/compra_form.html",
        {
            "form": form,
            "form_title": f"Editar Compra {compra.numero_compra}",
            "next_url": request.GET.get("next", ""),
        },
    )


@login_required
def division_delete_view(request, compra_id):
    if request.method != "POST":
        return redirect("compras_operativas")
    division = get_object_or_404(Compra, pk=compra_id)
    if not division.es_division:
        messages.error(request, "Solo se pueden eliminar compras de tipo division.")
        return redirect("compras_operativas")

    parent_id = division.parent_compra_id
    try:
        division.delete()
        messages.success(request, "Division eliminada correctamente.")
    except ProtectedError:
        messages.error(
            request,
            "No se puede eliminar la division porque tiene movimientos relacionados (anticipos/pagos).",
        )
    return redirect(f"/compras/{parent_id}/flujo/?step=dividir")


@login_required
def pago_delete_view(request, pago_id):
    if request.method != "POST":
        return redirect("compras_operativas")
    pago = get_object_or_404(PagoCompra, pk=pago_id)
    compra_id = pago.compra_id
    pago.delete()
    messages.success(request, "Pago eliminado correctamente.")
    return redirect(f"/compras/{compra_id}/flujo/?step=pago")


@login_required
def compra_delete_view(request, compra_id):
    if request.method != "POST":
        return redirect("compras_operativas")

    compra = get_object_or_404(Compra, pk=compra_id)
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_superuser:
        messages.error(request, "Solo un administrador puede eliminar definitivamente una compra.")
        return redirect(f"/compras/{compra_id}/flujo/?step={compra.flujo_step_default}")

    try:
        compra.delete()
        messages.success(request, "Compra eliminada definitivamente.")
        return redirect("compras_operativas")
    except ProtectedError:
        messages.error(
            request,
            "No se puede eliminar: la compra tiene relaciones protegidas (pagos/anticipos/divisiones).",
        )
        return redirect(f"/compras/{compra_id}/flujo/?step={compra.flujo_step_default}")


@login_required
def compra_flujo_view(request, compra_id):
    compra = get_object_or_404(Compra, pk=compra_id)
    ui_pending_step = compra.flujo_step_default

    # Auto-align workflow state with advanced UI pending step, to avoid queue invisibility drift.
    if ui_pending_step in {"solicitar_factura", "revisar_factura", "pago"} and compra.workflow_state in {
        WorkflowStateChoices.IMPORTED,
        WorkflowStateChoices.DEBT_CALCULATED,
    }:
        actor = str(getattr(request.user, "username", "operador") or "operador")
        try:
            if compra.workflow_state == WorkflowStateChoices.IMPORTED:
                transition_compra(
                    compra,
                    WorkflowStateChoices.DEBT_CALCULATED,
                    actor=actor,
                    reason="Auto-align por paso pendiente avanzado",
                )
            transition_compra(
                compra,
                WorkflowStateChoices.WAITING_INVOICE,
                actor=actor,
                reason="Auto-align por paso pendiente avanzado",
            )
            compra.refresh_from_db()
            ui_pending_step = compra.flujo_step_default
        except ValueError:
            pass

    # If purchase is effectively paid, align state to PAID so readiness queue/reporting is consistent.
    if ui_pending_step == "pago" and compra.workflow_state != WorkflowStateChoices.PAID:
        try:
            saldo = Decimal(str(compra.saldo_por_pagar or "0"))
        except Exception:
            saldo = Decimal("999999")
        paid_like = compra.total_pagado_vigente > Decimal("0") and abs(saldo) <= Decimal("3")
        if paid_like:
            actor = str(getattr(request.user, "username", "operador") or "operador")
            try:
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
                current = compra.workflow_state
                if current in chain:
                    for target in chain[chain.index(current) + 1 :]:
                        transition_compra(compra, target, actor=actor, reason="Auto-align pago efectivo")
                compra.refresh_from_db()
            except Exception:
                pass

    form_map = {
        "captura": (CompraFlujo1Form, "Captura actualizada."),
        "anticipos": (CompraFlujoAnticiposForm, "Anticipos revisados."),
        "deudas": (CompraFlujo3Form, "Deudas actualizadas."),
        "solicitar_factura": (CompraSolicitarFacturaForm, "Solicitud de factura actualizada."),
        "revisar_factura": (CompraRegistrarFacturaForm, "Revisión de factura actualizada."),
        "tc": (CompraFlujo2Form, "Tipo de cambio actualizado."),
    }
    forms = {k: cls(instance=compra, prefix=k) for k, (cls, _) in form_map.items()}
    documento_form = DocumentoCompraForm(prefix="doc")
    division_form = CompraDivisionCreateForm(compra=compra, prefix="div")
    pago_form = PagoCompraForm(prefix="pagoitem")
    bank_form = CompraBankConfirmationForm(instance=compra, prefix="bank")
    deduccion_form = DeduccionForm(prefix="ded")
    cancelar_form = CancelarCompraForm(prefix="cancel")
    facturador_form = PersonaFacturaQuickForm(prefix="pf")

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")
        form_name = request.POST.get("flow_form")
        if form_name == "cancelar_compra":
            cancelar_form = CancelarCompraForm(request.POST, prefix="cancel")
            user = getattr(request, "user", None)
            if not user or not user.is_authenticated or not user.is_superuser:
                messages.error(request, "Solo un administrador autenticado puede cancelar compras.")
                return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")

            if cancelar_form.is_valid():
                motivo = cancelar_form.cleaned_data["motivo_cancelacion"]
                pwd = cancelar_form.cleaned_data["admin_password"]
                auth_user = authenticate(request, username=user.username, password=pwd)
                if not auth_user:
                    messages.error(request, "Autenticación inválida: contraseña de admin incorrecta.")
                    return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")

                compra.cancelada = True
                compra.motivo_cancelacion = motivo
                compra.workflow_state = WorkflowStateChoices.ARCHIVED
                compra.save(update_fields=["cancelada", "motivo_cancelacion", "workflow_state", "updated_at"])
                messages.success(request, "Compra cancelada.")
                return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")
            messages.error(request, "No se pudo cancelar la compra.")
        elif form_name == "reactivar_compra":
            compra.cancelada = False
            compra.save(update_fields=["cancelada", "updated_at"])
            messages.success(request, "Compra reactivada.")
            return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")
        elif form_name == "documento_delete":
            is_admin_docs = bool(request.user.is_authenticated and (request.user.is_superuser or request.user.groups.filter(name="Admin").exists()))
            if not is_admin_docs:
                messages.error(request, "Solo Admin puede eliminar documentos del expediente.")
                return redirect(f"/compras/{compra.id}/flujo/?step={request.GET.get('step') or compra.flujo_step_default}")
            doc_id = (request.POST.get("documento_id") or "").strip()
            doc = compra.documentos.filter(pk=int(doc_id)).first() if doc_id.isdigit() else None
            if not doc:
                messages.error(request, "Documento no encontrado.")
                return redirect(f"/compras/{compra.id}/flujo/?step={request.GET.get('step') or compra.flujo_step_default}")
            doc.delete()
            messages.success(request, "Documento eliminado.")
            return redirect(f"/compras/{compra.id}/flujo/?step={request.GET.get('step') or compra.flujo_step_default}")
        elif form_name == "documento_update":
            is_admin_docs = bool(request.user.is_authenticated and (request.user.is_superuser or request.user.groups.filter(name="Admin").exists()))
            if not is_admin_docs:
                messages.error(request, "Solo Admin puede actualizar documentos del expediente.")
                return redirect(f"/compras/{compra.id}/flujo/?step={request.GET.get('step') or compra.flujo_step_default}")
            doc_id = (request.POST.get("documento_id") or "").strip()
            doc = compra.documentos.filter(pk=int(doc_id)).first() if doc_id.isdigit() else None
            if not doc:
                messages.error(request, "Documento no encontrado.")
                return redirect(f"/compras/{compra.id}/flujo/?step={request.GET.get('step') or compra.flujo_step_default}")
            new_desc = (request.POST.get("descripcion") or "").strip()
            new_file = request.FILES.get("archivo")
            if new_desc:
                doc.descripcion = new_desc
            if new_file:
                doc.archivo = new_file
            doc.save()
            messages.success(request, "Documento actualizado.")
            return redirect(f"/compras/{compra.id}/flujo/?step={request.GET.get('step') or compra.flujo_step_default}")
        elif form_name == "documento":
            documento_form = DocumentoCompraForm(request.POST, request.FILES, prefix="doc")
            if documento_form.is_valid():
                doc = documento_form.save(commit=False)
                file_name = (getattr(doc.archivo, "name", "") or "").lower()

                current_step = (request.GET.get("step") or "").strip()
                if current_step == "revisar_factura" and doc.etapa != "factura":
                    messages.error(request, "En Revisar factura solo se deben subir documentos de etapa Factura.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")

                if doc.etapa == "factura" and not (file_name.endswith(".xml") or file_name.endswith(".pdf")):
                    messages.error(request, "Para etapa Factura solo se permiten archivos XML o PDF.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")

                desc = (doc.descripcion or "").upper()
                if "XML" in desc and not file_name.endswith(".xml"):
                    messages.error(request, "La descripción indica XML, pero el archivo no es .xml")
                    return redirect(f"/compras/{compra.id}/flujo/?step={current_step or compra.flujo_step_default}")
                if ("PDF" in desc or "SAT" in desc) and not file_name.endswith(".pdf"):
                    messages.error(request, "La descripción indica PDF/SAT, pero el archivo no es .pdf")
                    return redirect(f"/compras/{compra.id}/flujo/?step={current_step or compra.flujo_step_default}")

                doc.compra = compra
                doc.save()

                # Parse payment receipt PDF and keep preview pending confirmation.
                is_payment_pdf = doc.etapa == "pago" and file_name.endswith(".pdf")
                if is_payment_pdf:
                    try:
                        pdf_bytes = doc.archivo.read()
                    except Exception:
                        pdf_bytes = b""

                    parsed = parse_payment_receipt_text(extract_pdf_text(pdf_bytes)) if pdf_bytes else {}
                    amount = parsed.get("amount")
                    fecha_pago = parsed.get("apply_date")
                    moneda_raw = (parsed.get("currency") or "").upper()
                    moneda = "PESOS" if moneda_raw in {"MXP", "MXN"} else "DOLARES"
                    referencia = (parsed.get("reference") or parsed.get("tracking") or "").strip()

                    if amount and fecha_pago:
                        request.session[f"pago_pdf_preview_{compra.id}"] = {
                            "fecha_pago": str(fecha_pago),
                            "monto": str(amount),
                            "moneda": moneda,
                            "cuenta_de_pago": (parsed.get("from_account") or compra.cuenta_productor or ""),
                            "metodo_de_pago": "TRANSFERENCIA",
                            "referencia": referencia[:100],
                            "notas": (
                                f"Autocargado desde comprobante PDF. "
                                f"Beneficiario: {parsed.get('beneficiary') or '-'}; "
                                f"Cuenta destino: {parsed.get('to_account') or '-'}; "
                                f"Concepto: {parsed.get('concept') or '-'}"
                            )[:1200],
                        }
                        request.session.modified = True
                        messages.success(request, "Comprobante detectado. Revisa y confirma el pago sugerido en la sección de pago.")
                    else:
                        messages.warning(request, "No se pudo extraer monto/fecha del comprobante PDF para autocompletar pago.")

                # Invoice XML validation scaffold (spec phase 3)
                is_invoice_xml = doc.etapa == "factura" and file_name.endswith(".xml")
                if is_invoice_xml:
                    try:
                        xml_bytes = doc.archivo.read()
                    except Exception:
                        xml_bytes = b""

                    expected_moneda = (compra.expected_moneda or "").strip().upper()
                    facturador = getattr(compra, "facturador", None)
                    regimen_txt = " ".join(
                        [
                            (facturador.regimen_fiscal if facturador else "") or "",
                            (compra.regimen_fiscal or ""),
                            (compra.productor.regimen_fiscal or ""),
                            (facturador.regimen_fiscal_codigo if facturador else "") or "",
                            (compra.productor.regimen_fiscal_codigo or ""),
                        ]
                    ).upper()
                    requires_resico = ("RESICO" in regimen_txt) or ("626" in regimen_txt)
                    resico_policy = (facturador.resico_policy if facturador else "AUTO") or "AUTO"
                    actor = str(getattr(request.user, "username", "operador") or "operador")

                    cfg = XmlValidationConfig.get_default()
                    global_rfc = (cfg.global_rfc_receptor or settings.CFDI_RFC_RECEPTOR_GLOBAL or "").strip().upper()
                    validation = create_invoice_validation_for_compra(
                        compra,
                        xml_bytes,
                        expected_rfc_receptor=((compra.expected_rfc_receptor or "").strip().upper() or global_rfc),
                        expected_regimen_fiscal_receptor=(cfg.global_regimen_fiscal_receptor or ""),
                        expected_codigo_fiscal_receptor=(cfg.global_codigo_fiscal_receptor or ""),
                        expected_nombre_receptor=(cfg.global_nombre_receptor or ""),
                        expected_efecto_comprobante=(cfg.global_efecto_comprobante or ""),
                        expected_impuesto_trasladado=(cfg.global_impuesto_trasladado or ""),
                        expected_moneda=expected_moneda,
                        expected_uso_cfdi=(compra.expected_uso_cfdi or ""),
                        expected_metodo_pago=(compra.expected_metodo_pago or ""),
                        expected_forma_pago=(compra.expected_forma_pago or ""),
                        expected_total_comprobante=str(compra.compra_en_libras or ""),
                        total_tolerance_usd="3",
                        requires_resico_retention=requires_resico,
                        resico_policy=resico_policy,
                    )

                    if validation.valid:
                        updates = []
                        if validation.uuid and compra.uuid_factura != validation.uuid:
                            compra.uuid_factura = validation.uuid
                            updates.append("uuid_factura")
                        if updates:
                            updates.append("updated_at")
                            compra.save(update_fields=updates)
                        messages.success(request, "XML de factura validado correctamente. UUID actualizado automáticamente.")
                        try:
                            transition_compra(
                                compra,
                                WorkflowStateChoices.INVOICE_RECEIVED,
                                actor=actor,
                                reason="XML de factura recibido en expediente",
                            )
                        except ValueError:
                            pass
                        try:
                            transition_compra(
                                compra,
                                WorkflowStateChoices.INVOICE_VALID,
                                actor=actor,
                                reason="XML de factura validado",
                            )
                        except ValueError:
                            pass
                    else:
                        messages.warning(request, f"Factura bloqueada: {validation.blocked_reason}")
                        try:
                            transition_compra(
                                compra,
                                WorkflowStateChoices.INVOICE_BLOCKED,
                                actor=actor,
                                reason=validation.blocked_reason or "Validacion XML fallida",
                            )
                        except ValueError:
                            pass
                else:
                    messages.success(request, "Documento cargado al expediente.")

                compra.refresh_from_db()
                return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")
            messages.error(request, "Error al cargar documento.")
        elif form_name == "facturador_create":
            facturador_form = PersonaFacturaQuickForm(request.POST, prefix="pf")
            if facturador_form.is_valid():
                pf = facturador_form.save()
                compra.facturador = pf
                compra.factura = pf.nombre
                compra.save(update_fields=["facturador", "factura", "updated_at"])
                messages.success(request, "Facturador creado y asignado a la compra.")
                return redirect(f"/compras/{compra.id}/flujo/?step=solicitar_factura")
            messages.error(request, "No se pudo crear el facturador. Revisa los datos.")
        elif form_name == "enviar_solicitud_email":
            messages.warning(request, "Envío real a contador deshabilitado temporalmente. Usa el botón de envío TEST.")
            return redirect(f"/compras/{compra.id}/flujo/?step=solicitar_factura")
        elif form_name == "leer_inbox_factura":
            try:
                if not gmail_inbox_ready():
                    messages.error(request, "Gmail no tiene permisos de lectura (gmail.readonly). Reautoriza OAuth.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")

                expected_rfc = ((compra.facturador.rfc if compra.facturador_id else "") or compra.productor.rfc or "").strip().upper()
                expected_total = Decimal(str(compra.compra_en_libras or "0"))
                total_tolerance = max(Decimal("50"), expected_total * Decimal("0.10"))
                items = fetch_gmail_attachments_for_compra(compra.numero_compra, max_messages=40)
                by_msg = {}
                for it in items:
                    by_msg.setdefault(it.get("message_id"), []).append(it)

                preview = []
                for msg_id, atts in by_msg.items():
                    xmls = [a for a in atts if str(a.get("filename", "")).lower().endswith(".xml")]
                    pdfs = [a for a in atts if str(a.get("filename", "")).lower().endswith(".pdf")]
                    for x in xmls:
                        meta = _extract_xml_basic(x.get("bytes") or b"")
                        rfc = (meta.get("rfc_emisor") or "").strip().upper()
                        rfc_ok = bool(expected_rfc and rfc == expected_rfc)
                        if not rfc_ok:
                            continue

                        total_raw = (meta.get("total") or "").strip()
                        try:
                            total_xml = Decimal(total_raw)
                        except Exception:
                            total_xml = None
                        amount_ok = bool(total_xml is not None and abs(total_xml - expected_total) <= total_tolerance)
                        if not amount_ok:
                            continue
                        xml_name = x.get("filename")
                        base = _norm_attachment_base(xml_name)
                        pdf_match = next((p for p in pdfs if _norm_attachment_base(p.get("filename", "")) == base), None)
                        preview.append({
                            "key": f"{msg_id}||{xml_name}",
                            "message_id": msg_id,
                            "xml_filename": xml_name,
                            "pdf_filename": (pdf_match.get("filename") if pdf_match else ""),
                            "rfc_emisor": rfc,
                            "uuid": meta.get("uuid", ""),
                            "total": meta.get("total", ""),
                        })

                request.session[f"inbox_factura_preview_{compra.id}"] = preview
                request.session.modified = True
                if preview:
                    messages.success(request, f"Inbox leído: {len(preview)} XML candidatos (RFC y monto dentro de tolerancia). Selecciona cuál importar.")
                else:
                    messages.info(request, "Inbox leído: no se encontraron XML con RFC+monto válidos para esta compra.")
            except Exception as e:
                messages.error(request, f"Error al leer inbox: {e}")
            return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")
        elif form_name == "importar_inbox_factura":
            try:
                selected = (request.POST.get("inbox_pick") or "").strip()
                preview = request.session.get(f"inbox_factura_preview_{compra.id}") or []
                pick = next((p for p in preview if p.get("key") == selected), None)
                if not pick:
                    messages.error(request, "Selecciona un XML del preview para importar.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")

                items = fetch_gmail_attachments_for_compra(compra.numero_compra, max_messages=40)
                msg_items = [i for i in items if i.get("message_id") == pick.get("message_id")]
                xml_item = next((i for i in msg_items if i.get("filename") == pick.get("xml_filename")), None)
                pdf_item = next((i for i in msg_items if i.get("filename") == pick.get("pdf_filename")), None)
                if xml_item and not pdf_item:
                    xml_base = _norm_attachment_base(xml_item.get("filename") or "")
                    pdf_item = next(
                        (
                            i
                            for i in msg_items
                            if str(i.get("filename", "")).lower().endswith(".pdf")
                            and _norm_attachment_base(i.get("filename") or "") == xml_base
                        ),
                        None,
                    )
                if not xml_item:
                    messages.error(request, "No se encontró el XML seleccionado en inbox (posible cambio de estado).")
                    return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")

                saved = 0
                for it in [xml_item, pdf_item]:
                    if not it:
                        continue
                    filename = (it.get("filename") or "").strip()
                    data = it.get("bytes") or b""
                    if not filename or not data:
                        continue
                    exists = compra.documentos.filter(etapa="factura", archivo__iendswith=filename).exists()
                    if exists:
                        continue
                    doc = DocumentoCompra(compra=compra, etapa="factura", descripcion=f"Inbox Gmail #{it.get('message_id')}")
                    doc.archivo.save(filename, ContentFile(data), save=True)
                    saved += 1

                cfg = XmlValidationConfig.get_default()
                global_rfc = ((cfg.global_rfc_receptor or "").strip().upper() or "UAM140522Q51")
                expected_moneda = (compra.expected_moneda or "").strip().upper() or "USD"
                resico_policy = ((compra.facturador.resico_policy if compra.facturador_id else "") or "AUTO").strip().upper()
                requires_resico = resico_policy in {"RETENCION_125", "EXENCION_LEYENDA"}
                v = create_invoice_validation_for_compra(
                    compra,
                    xml_item.get("bytes") or b"",
                    expected_rfc_receptor=((compra.expected_rfc_receptor or "").strip().upper() or global_rfc),
                    expected_regimen_fiscal_receptor=(cfg.global_regimen_fiscal_receptor or ""),
                    expected_codigo_fiscal_receptor=(cfg.global_codigo_fiscal_receptor or ""),
                    expected_nombre_receptor=(cfg.global_nombre_receptor or ""),
                    expected_efecto_comprobante=(cfg.global_efecto_comprobante or ""),
                    expected_impuesto_trasladado=(cfg.global_impuesto_trasladado or ""),
                    expected_moneda=expected_moneda,
                    expected_uso_cfdi=(compra.expected_uso_cfdi or ""),
                    expected_metodo_pago=(compra.expected_metodo_pago or ""),
                    expected_forma_pago=(compra.expected_forma_pago or ""),
                    expected_total_comprobante=str(compra.compra_en_libras or ""),
                    total_tolerance_usd="3",
                    requires_resico_retention=requires_resico,
                    resico_policy=resico_policy,
                )
                if v.valid and not compra.uuid_factura:
                    compra.uuid_factura = v.uuid or compra.uuid_factura
                    compra.save(update_fields=["uuid_factura", "updated_at"])

                request.session.pop(f"inbox_factura_preview_{compra.id}", None)
                request.session.modified = True
                messages.success(request, f"Inbox importado: {saved} adjuntos guardados y XML validado.")
            except Exception as e:
                messages.error(request, f"Error al importar inbox seleccionado: {e}")
            return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")
        elif form_name == "descartar_inbox_factura":
            request.session.pop(f"inbox_factura_preview_{compra.id}", None)
            request.session.modified = True
            messages.info(request, "Preview de inbox descartado.")
            return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")
        elif form_name == "enviar_solicitud_email_test":
            to_email = (settings.SOLICITUD_FACTURA_TEST_TO or "").strip()
            if not to_email:
                messages.error(request, "No hay correo TEST configurado.")
                return redirect(f"/compras/{compra.id}/flujo/?step=solicitar_factura")
            payload = build_invoice_request_email(compra)
            subject = f"[TEST] {payload['subject']}"
            body = payload["body"]
            try:
                provider = "gmail_oauth" if gmail_ready() else "smtp"
                provider_msg_id = ""
                html_body = render_invoice_email_html(body)
                compra_pdf_att = _get_compra_pdf_attachment(compra)
                attachments = [compra_pdf_att] if compra_pdf_att else []
                if provider == "gmail_oauth":
                    provider_msg_id = send_gmail(to_email, subject, body, html_body=html_body, attachments=attachments)
                else:
                    msg = EmailMultiAlternatives(subject, body, settings.DEFAULT_FROM_EMAIL, [to_email])
                    if html_body:
                        msg.attach_alternative(html_body, "text/html")
                    for att in attachments:
                        if att:
                            msg.attach(att[0], att[1], att[2])
                    msg.send(fail_silently=False)
                EmailOutboxLog.objects.create(
                    compra=compra,
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    template_code=payload.get("template_code", ""),
                    provider=provider,
                    status="SENT",
                    error=(provider_msg_id or ""),
                )

                _attach_email_proof_to_expediente(
                    compra,
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    provider=provider,
                    provider_msg_id=provider_msg_id,
                    template_code=payload.get("template_code", ""),
                )

                compra.solicitud_factura_enviada = True
                compra.fecha_solicitud_factura = timezone.localdate()
                compra.save(update_fields=["solicitud_factura_enviada", "fecha_solicitud_factura", "updated_at"])
                actor = str(getattr(request.user, "username", "operador") or "operador")
                try:
                    transition_compra(
                        compra,
                        WorkflowStateChoices.WAITING_INVOICE,
                        actor=actor,
                        reason="Solicitud de factura enviada por correo TEST",
                    )
                except ValueError:
                    pass

                messages.success(request, f"Solicitud TEST enviada a {to_email} ({provider}).")
            except Exception as e:
                EmailOutboxLog.objects.create(
                    compra=compra,
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    template_code=payload.get("template_code", ""),
                    provider="gmail_oauth" if gmail_ready() else "smtp",
                    status="ERROR",
                    error=str(e),
                )
                messages.error(request, f"No se pudo enviar correo TEST: {e}")
            return redirect(f"/compras/{compra.id}/flujo/?step=solicitar_factura")
        elif form_name == "microsip_sync_debt":
            try:
                if not (compra.productor.rfc or "").strip():
                    messages.error(request, "Completa RFC del productor antes de sincronizar deudas Microsip.")
                    return redirect(f"/productores/{compra.productor.id}/editar/?next=/compras/{compra.id}/flujo/%3Fstep%3Ddeudas")

                if not (compra.productor.microsip_cliente_nombre or "").strip():
                    cands = find_microsip_candidates_for_productor(compra.productor.nombre)
                    if len(cands) != 1:
                        messages.info(request, "Selecciona el cliente Microsip para vincular este productor.")
                        return redirect(f"/compras/{compra.id}/mapear-microsip/")
                    compra.productor.microsip_cliente_nombre = cands[0]["cliente"]
                    compra.productor.microsip_cliente_id = str(cands[0].get("cliente_id") or "")
                    compra.productor.save(update_fields=["microsip_cliente_nombre", "microsip_cliente_id", "updated_at"])

                snap = sync_microsip_debt_for_compra(compra)
                messages.success(
                    request,
                    f"Deuda Microsip sincronizada. USD: {snap.total_usd:,.2f} | MXN: {snap.total_mxn:,.2f}",
                )
                compra.refresh_from_db()
                if (compra.retencion_deudas_mxn or 0) > 0 and not (compra.tipo_cambio_valor or 0):
                    messages.warning(request, "⚠️ Hay deuda en MXN pero no hay tipo de cambio cargado; total en DLS puede ser parcial.")
                if (compra.total_deuda_en_dls or 0) > (compra.compra_en_libras or 0):
                    messages.warning(
                        request,
                        "⚠️ La deuda total es mayor al monto de la compra. Revisa mapeo de cliente y retenciones antes de continuar.",
                    )
            except Exception as e:
                messages.error(request, f"Error al consultar Microsip: {e}")
            return redirect(f"/compras/{compra.id}/flujo/?step=deudas")
        elif form_name == "deduccion_add":
            deduccion_form = DeduccionForm(request.POST, prefix="ded")
            if deduccion_form.is_valid():
                d = deduccion_form.save(commit=False)
                d.compra = compra
                d.fuente = "manual"
                d.save()
                messages.success(request, "Deducción manual agregada.")
                return redirect(f"/compras/{compra.id}/flujo/?step=pago")
            messages.error(request, "Error al agregar deducción.")
        elif form_name == "bank_confirm":
            selected_account_id = (request.POST.get("bank_account_id") or "").strip()
            selected = None
            if selected_account_id.startswith("f:") and selected_account_id[2:].isdigit() and compra.facturador_id:
                selected = FacturadorCuentaBancaria.objects.filter(
                    pk=int(selected_account_id[2:]), facturador=compra.facturador, activa=True
                ).first()
            elif selected_account_id.startswith("p:") and selected_account_id[2:].isdigit():
                selected = ProductorCuentaBancaria.objects.filter(
                    pk=int(selected_account_id[2:]), productor=compra.productor, activa=True
                ).first()

            if not selected:
                messages.error(request, "Selecciona una cuenta del catálogo para confirmar.")
                return redirect(f"/compras/{compra.id}/flujo/?step=pago")

            compra.cuenta_productor = selected.cuenta
            compra.bank_account_confirmed = True
            if not compra.bank_confirmed_at:
                compra.bank_confirmed_at = timezone.now()
            compra.bank_confirmation_source = "catalogo_cuentas"
            extra_note = (request.POST.get("bank_confirmation_note") or "").strip()
            compra.bank_confirmation_notes = (
                f"Cuenta confirmada desde catálogo ({selected.banco} {selected.cuenta})"
                + (f" · {extra_note}" if extra_note else "")
            ).strip()

            # Si la cuenta tiene carátula, anexarla automáticamente al expediente de pago.
            if selected.caratula_archivo:
                pago_docs = list(compra.documentos.filter(etapa="pago").values("archivo", "descripcion"))
                has_caratula = any(
                    ("caratula" in ((d.get("descripcion") or "").lower().replace("á", "a")))
                    or ("caratula" in str(d.get("archivo") or "").lower().replace("á", "a"))
                    for d in pago_docs
                )
                if not has_caratula:
                    DocumentoCompra.objects.create(
                        compra=compra,
                        etapa="pago",
                        descripcion="Carátula bancaria (catálogo cuenta seleccionada)",
                        archivo=selected.caratula_archivo,
                    )

            compra.save(update_fields=[
                "cuenta_productor",
                "bank_account_confirmed",
                "bank_confirmed_at",
                "bank_confirmation_source",
                "bank_confirmation_notes",
                "updated_at",
            ])

            actor = str(getattr(request.user, "username", "operador") or "operador")
            try:
                transition_compra(
                    compra,
                    WorkflowStateChoices.READY_TO_PAY,
                    actor=actor,
                    reason="Cuenta bancaria confirmada",
                )
            except ValueError:
                pass
            banco_txt = (selected.banco or "-").strip() if selected else "-"
            cuenta_txt = (selected.cuenta or "-").strip() if selected else "-"
            clabe_txt = (selected.clabe or "-").strip() if selected else "-"
            messages.success(
                request,
                f"Cuenta confirmada para pago. Beneficiario → Banco: {banco_txt} · Cuenta: {cuenta_txt} · CLABE: {clabe_txt}",
            )
            return redirect(f"/compras/{compra.id}/flujo/?step=pago")
        elif form_name == "pago_pdf_discard":
            request.session.pop(f"pago_pdf_preview_{compra.id}", None)
            request.session.modified = True
            messages.info(request, "Sugerencia de pago descartada.")
            return redirect(f"/compras/{compra.id}/flujo/?step=pago")
        elif form_name == "pago_pdf_confirm":
            data = request.session.get(f"pago_pdf_preview_{compra.id}") or {}
            if not data:
                messages.error(request, "No hay sugerencia de pago pendiente por confirmar.")
                return redirect(f"/compras/{compra.id}/flujo/?step=pago")
            try:
                fecha_pago = datetime.strptime(str(data.get("fecha_pago")), "%Y-%m-%d").date()
                exists = PagoCompra.objects.filter(
                    compra=compra,
                    fecha_pago=fecha_pago,
                    monto=Decimal(str(data.get("monto") or "0")),
                    referencia=(data.get("referencia") or ""),
                ).exists()
                if exists:
                    messages.info(request, "El pago sugerido ya existe, no se duplicó.")
                else:
                    PagoCompra.objects.create(
                        compra=compra,
                        fecha_pago=fecha_pago,
                        monto=Decimal(str(data.get("monto") or "0")),
                        moneda=(data.get("moneda") or "DOLARES"),
                        cuenta_de_pago=(data.get("cuenta_de_pago") or ""),
                        metodo_de_pago=(data.get("metodo_de_pago") or "TRANSFERENCIA"),
                        referencia=(data.get("referencia") or "")[:100],
                        notas=(data.get("notas") or "")[:1200],
                    )
                    messages.success(request, "Pago registrado desde comprobante PDF.")
                request.session.pop(f"pago_pdf_preview_{compra.id}", None)
                request.session.modified = True
                return redirect(f"/compras/{compra.id}/flujo/?step=pago")
            except Exception as e:
                messages.error(request, f"No se pudo registrar pago desde comprobante: {e}")
                return redirect(f"/compras/{compra.id}/flujo/?step=pago")
        elif form_name == "pago_registrar":
            pago_form = PagoCompraForm(request.POST, prefix="pagoitem")
            if pago_form.is_valid():
                if compra.cancelada:
                    messages.error(request, "No se puede registrar pago: la compra está cancelada.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=pago")
                latest_validation = compra.invoice_validations.first()
                if not compra.bank_account_confirmed:
                    messages.error(request, "No se puede registrar pago: falta confirmación bancaria.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=pago")
                if not latest_validation or not latest_validation.valid:
                    messages.error(request, "No se puede registrar pago: la factura no está validada.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=pago")

                beneficiary = _beneficiary_validation(compra)
                if beneficiary["status"] == "red":
                    messages.error(request, f"No se puede registrar pago: {beneficiary['reason']}")
                    return redirect(f"/compras/{compra.id}/flujo/?step=pago")
                if beneficiary["status"] == "yellow":
                    justif = (request.POST.get("beneficiary_justification") or "").strip()
                    if not justif:
                        messages.error(request, "Se requiere justificación para excepción de beneficiario (coincidencia parcial).")
                        return redirect(f"/compras/{compra.id}/flujo/?step=pago")
                factura_files = list(compra.documentos.filter(etapa="factura").values_list("archivo", flat=True))
                has_pdf = any(str(x).lower().endswith(".pdf") for x in factura_files)
                if not has_pdf:
                    messages.error(request, "No se puede registrar pago: falta PDF de factura en expediente.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=pago")

                pago_docs = list(compra.documentos.filter(etapa="pago").values("archivo", "descripcion"))
                has_caratula_bancaria = any(
                    ("caratula" in ((d.get("descripcion") or "").lower().replace("á", "a")))
                    or ("caratula" in str(d.get("archivo") or "").lower().replace("á", "a"))
                    for d in pago_docs
                )
                if not has_caratula_bancaria:
                    messages.error(request, "No se puede registrar pago: falta carátula bancaria en expediente (etapa Pago).")
                    return redirect(f"/compras/{compra.id}/flujo/?step=pago")

                pago = pago_form.save(commit=False)
                pago.compra = compra
                if beneficiary["status"] == "yellow":
                    justif = (request.POST.get("beneficiary_justification") or "").strip()
                    extra = f"[EXCEPCIÓN BENEFICIARIO] {justif}"
                    pago.notas = f"{(pago.notas or '').strip()}\n{extra}".strip()
                pago.save()
                actor = str(getattr(request.user, "username", "operador") or "operador")
                try:
                    transition_compra(
                        compra,
                        WorkflowStateChoices.READY_TO_PAY,
                        actor=actor,
                        reason="Pago listo para ejecutar",
                    )
                except ValueError:
                    pass
                try:
                    transition_compra(
                        compra,
                        WorkflowStateChoices.PAID,
                        actor=actor,
                        reason="Pago registrado en sistema",
                    )
                except ValueError:
                    pass
                messages.success(request, "Pago registrado.")
                return redirect(f"/compras/{compra.id}/flujo/?step=pago")
            messages.error(request, "Error al registrar pago.")
        elif form_name == "dividir_crear":
            division_form = CompraDivisionCreateForm(request.POST, compra=compra, prefix="div")
            if division_form.is_valid():
                pct = division_form.cleaned_data["porcentaje_division"]
                child = Compra(
                    numero_compra=compra.numero_compra,
                    fecha_liq=compra.fecha_liq,
                    productor=compra.productor,
                    regimen_fiscal=compra.regimen_fiscal,
                    parent_compra=compra,
                    porcentaje_division=pct,
                    pacas=(compra.pacas or 0) * pct / 100,
                    compra_en_libras=(compra.compra_en_libras or 0) * pct / 100,
                    tipo_cambio=compra.tipo_cambio,
                    tipo_cambio_valor=compra.tipo_cambio_valor,
                    moneda=compra.moneda,
                    factura=division_form.cleaned_data.get("factura", ""),
                    uuid_factura=division_form.cleaned_data.get("uuid_factura", ""),
                    anticipos_revisados=False,
                    deudas_revisadas=False,
                    division_revisada=False,
                )
                child.save()
                messages.success(request, "Division creada correctamente.")
                return redirect(f"/compras/{compra.id}/flujo/?step=dividir")
            messages.error(request, "Error al crear division.")
        elif form_name == "anticipo_aplicar":
            anticipo_id = (request.POST.get("anticipo_id") or "").strip()
            monto_raw = (request.POST.get("monto_aplicar") or "").strip()
            try:
                monto = Decimal(monto_raw or "0")
            except Exception:
                monto = Decimal("0")
            anticipo = Anticipo.objects.filter(id=anticipo_id, productor=compra.productor).first() if anticipo_id.isdigit() else None
            if not anticipo:
                messages.error(request, "Anticipo no encontrado para este productor.")
                return redirect(f"/compras/{compra.id}/flujo/?step=anticipos")
            if monto <= 0:
                monto = min(anticipo.saldo_disponible, compra.saldo_por_pagar)
            try:
                AplicacionAnticipo.objects.create(
                    anticipo=anticipo,
                    compra=compra,
                    fecha=timezone.localdate(),
                    monto_aplicado=monto,
                )
                messages.success(request, f"Anticipo #{anticipo.numero_anticipo} aplicado por {monto}.")
            except Exception as e:
                messages.error(request, f"No se pudo aplicar anticipo: {e}")
            return redirect(f"/compras/{compra.id}/flujo/?step=anticipos")
        elif form_name == "anticipo_quitar":
            app_id = (request.POST.get("app_id") or "").strip()
            app = compra.aplicaciones_anticipo.filter(id=app_id).first() if app_id.isdigit() else None
            if not app:
                messages.error(request, "Aplicación de anticipo no encontrada.")
                return redirect(f"/compras/{compra.id}/flujo/?step=anticipos")
            app.delete()
            messages.success(request, "Aplicación de anticipo eliminada.")
            return redirect(f"/compras/{compra.id}/flujo/?step=anticipos")
        elif form_name in form_map:
            if form_name == "deudas" and not (compra.productor.rfc or "").strip():
                messages.error(request, "Completa RFC del productor para continuar en Revisar deudas.")
                return redirect(f"/productores/{compra.productor.id}/editar/?next=/compras/{compra.id}/flujo/%3Fstep%3Ddeudas")

            form_cls, msg = form_map[form_name]
            forms[form_name] = form_cls(request.POST, instance=compra, prefix=form_name)
            if forms[form_name].is_valid():
                forms[form_name].save()
                compra.refresh_from_db()
                if form_name == "solicitar_factura":
                    source = (forms[form_name].cleaned_data.get("factura_source") or "productor").strip()
                    if source == "facturador" and compra.facturador:
                        compra.factura = compra.facturador.nombre
                        compra.expected_rfc_receptor = (compra.facturador.rfc or "").strip().upper()
                        linked_contador = compra.facturador.contador
                        if linked_contador:
                            compra.contador = linked_contador.nombre
                            compra.correo = linked_contador.email
                            compra.save(update_fields=["factura", "expected_rfc_receptor", "contador", "correo", "updated_at"])
                        else:
                            compra.save(update_fields=["factura", "expected_rfc_receptor", "updated_at"])
                            messages.warning(request, "La entidad facturadora no tiene contador ligado. Vincúlalo en el catálogo de Entidades que facturan.")
                    else:
                        compra.facturador = None
                        compra.factura = compra.productor.nombre
                        compra.expected_rfc_receptor = (compra.productor.rfc or "").strip().upper()
                        if compra.productor.contador:
                            compra.contador = compra.productor.contador.nombre
                            compra.correo = compra.productor.contador.email
                            compra.save(update_fields=["facturador", "factura", "expected_rfc_receptor", "contador", "correo", "updated_at"])
                        else:
                            compra.save(update_fields=["facturador", "factura", "expected_rfc_receptor", "updated_at"])

                if form_name == "revisar_factura":
                    factura_files = list(compra.documentos.filter(etapa="factura").values_list("archivo", flat=True))
                    has_xml_review = any(str(x).lower().endswith(".xml") for x in factura_files)
                    has_pdf_review = any(str(x).lower().endswith(".pdf") for x in factura_files)
                    if not (has_xml_review and has_pdf_review and compra.uuid_factura):
                        messages.error(request, "Para continuar en revisión de factura necesitas XML, PDF y UUID capturado.")
                        return redirect(f"/compras/{compra.id}/flujo/?step=revisar_factura")

                actor = str(getattr(request.user, "username", "operador") or "operador")
                try:
                    if form_name == "deudas":
                        transition_compra(
                            compra,
                            WorkflowStateChoices.WAITING_INVOICE,
                            actor=actor,
                            reason="Deudas revisadas",
                        )
                    elif form_name == "solicitar_factura":
                        if compra.solicitud_factura_enviada:
                            transition_compra(
                                compra,
                                WorkflowStateChoices.WAITING_INVOICE,
                                actor=actor,
                                reason="Solicitud de factura enviada",
                            )
                    elif form_name == "revisar_factura":
                        if compra.uuid_factura:
                            transition_compra(
                                compra,
                                WorkflowStateChoices.INVOICE_RECEIVED,
                                actor=actor,
                                reason="Factura recibida y UUID capturado",
                            )
                            transition_compra(
                                compra,
                                WorkflowStateChoices.INVOICE_VALID,
                                actor=actor,
                                reason="Validacion inicial interna aprobada",
                            )
                            transition_compra(
                                compra,
                                WorkflowStateChoices.WAITING_BANK_CONFIRMATION,
                                actor=actor,
                                reason="Esperando confirmacion bancaria",
                            )
                except ValueError:
                    pass

                messages.success(request, msg)
                compra.refresh_from_db()
                return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")
            messages.error(request, "Hay errores en el formulario.")

    current_step = request.GET.get("step") or ui_pending_step
    edit_bank = bool((request.GET.get("edit_bank") or "").strip())
    edit_solicitud_factura = bool((request.GET.get("edit_solicitud_factura") or "").strip())
    step_items = [
        ("captura", "Registrar compra"),
        ("anticipos", "Revisar anticipos"),
        ("deudas", "Revisar deudas"),
        ("solicitar_factura", "Solicitar factura"),
        ("revisar_factura", "Revisar factura"),
        ("pago", "Pagar factura"),
    ]
    extra_items = [("tc", "TC"), ("expediente", "Expediente")]
    if request.user.is_authenticated and request.user.is_superuser:
        extra_items.append(("cancelacion", "Control cancelación"))
    if not compra.es_division and compra.captura_completa:
        extra_items.append(("dividir", "Dividir compra"))
    main_order = [code for code, _ in step_items]
    pending_idx = main_order.index(ui_pending_step) if ui_pending_step in main_order else len(main_order) - 1
    unlocked_main = set(main_order[: pending_idx + 1])
    unlocked_extra = {code for code, _ in extra_items} if compra.captura_completa else set()
    if "cancelacion" in [c for c, _ in extra_items]:
        unlocked_extra.add("cancelacion")
    unlocked_steps = unlocked_main | unlocked_extra
    if compra.flujo_codigo == "completo":
        unlocked_steps = set(main_order) | set(code for code, _ in extra_items)

    if request.GET.get("step") and current_step not in unlocked_steps:
        messages.warning(request, "Ese paso aun no esta disponible. Se muestra el paso pendiente.")
        return redirect(f"/compras/{compra.id}/flujo/?step={ui_pending_step}")
    if current_step not in unlocked_steps:
        current_step = ui_pending_step

    active_form = forms.get(current_step)
    step_items_ui = [{"code": code, "label": label, "unlocked": code in unlocked_steps} for code, label in step_items]
    extra_items_ui = [{"code": code, "label": label, "unlocked": code in unlocked_steps} for code, label in extra_items]

    existing_etapas = set(compra.documentos.values_list("etapa", flat=True))
    cuentas_productor = list(compra.productor.cuentas_bancarias.filter(activa=True).order_by("-predeterminada", "banco", "cuenta")[:20])
    cuentas_facturador = list(compra.facturador.cuentas_bancarias.filter(activa=True).order_by("-predeterminada", "banco", "cuenta")[:20]) if compra.facturador_id else []

    confirmed_account = None
    if (compra.cuenta_productor or "").strip():
        if compra.facturador_id:
            confirmed_account = compra.facturador.cuentas_bancarias.filter(cuenta=compra.cuenta_productor).first()
        if not confirmed_account:
            confirmed_account = compra.productor.cuentas_bancarias.filter(cuenta=compra.cuenta_productor).first()

    beneficiary_validation = _beneficiary_validation(compra)
    solicitud_configurada = bool(
        (compra.expected_moneda or "").strip()
        and (compra.expected_forma_pago or "").strip()
        and (compra.expected_metodo_pago or "").strip()
        and (compra.expected_uso_cfdi or "").strip()
        and (compra.contador or "").strip()
        and (compra.correo or "").strip()
    )
    pago_pdf_preview = request.session.get(f"pago_pdf_preview_{compra.id}")
    last_microsip_snapshot = compra.debt_snapshots.filter(fuente="microsip").first()
    factura_docs = list(compra.documentos.filter(etapa="factura").values_list("archivo", flat=True))
    pago_docs_qs = compra.documentos.filter(etapa="pago").order_by("-created_at")
    pago_doc_latest = pago_docs_qs.first()
    has_pago_comprobante = bool(pago_doc_latest)
    solicitud_logs = list(compra.email_logs.order_by("-created_at")[:10])

    inbox_factura_preview = request.session.get(f"inbox_factura_preview_{compra.id}") or []

    solicitud_resumen = {
        "contador": (compra.contador or (compra.productor.contador.nombre if compra.productor.contador else "") or "").strip(),
        "correo": (compra.correo or (compra.productor.contador.email if compra.productor.contador else "") or "").strip(),
        "expected_moneda": (compra.expected_moneda or ("USD" if compra.moneda == "DOLARES" else "MXN")).strip(),
        "expected_forma_pago": (compra.expected_forma_pago or "03").strip(),
        "expected_metodo_pago": (compra.expected_metodo_pago or "PUE").strip(),
        "expected_uso_cfdi": (compra.expected_uso_cfdi or "G01").strip(),
        "expected_rfc_receptor": (
            compra.expected_rfc_receptor
            or (compra.facturador.rfc if compra.facturador_id else compra.productor.rfc)
            or ""
        ).strip().upper(),
    }
    has_xml = any(str(x).lower().endswith(".xml") for x in factura_docs)
    has_pdf = any(str(x).lower().endswith(".pdf") for x in factura_docs)
    revisar_factura_ready = bool(has_xml and has_pdf and compra.uuid_factura)
    facturador_sin_contador = bool(compra.facturador_id and not getattr(compra.facturador, "contador", None))
    productor_missing_fields = []
    if not (compra.productor.rfc or "").strip():
        productor_missing_fields.append("RFC")
    if not (compra.productor.regimen_fiscal_codigo or "").strip():
        productor_missing_fields.append("Régimen fiscal")
    if not compra.productor.contador:
        productor_missing_fields.append("Contador ligado")
    elif not (compra.productor.contador.email or "").strip():
        productor_missing_fields.append("Correo de contador")

    docs_compra_original = list(compra.documentos.filter(etapa="compra_original").order_by("-created_at")[:5])
    has_compra_original_pdf = compra.documentos.filter(etapa="compra_original", archivo__iendswith=".pdf").exists()
    docs_solicitud = list(compra.documentos.filter(etapa="solicitud_factura").order_by("-created_at")[:5])
    docs_factura = list(compra.documentos.filter(etapa="factura").order_by("-created_at")[:8])
    docs_pago = list(compra.documentos.filter(etapa="pago").order_by("-created_at")[:8])

    expediente_status = [
        ("Compra original", bool(docs_compra_original), docs_compra_original),
        ("Solicitud factura", bool(docs_solicitud), docs_solicitud),
        ("Factura XML/PDF", bool(docs_factura), docs_factura),
        ("Comprobante pago", bool(docs_pago), docs_pago),
    ]

    payable = payable_breakdown(compra)
    deducs = list(compra.deducciones.all())
    tc_val = Decimal(str(compra.tipo_cambio_valor or "0"))

    def _ded_to_usd(d):
        m = Decimal(str(d.monto or "0"))
        if (d.moneda or "").upper() == "PESOS" and tc_val > 0:
            return (m / tc_val)
        return m

    coberturas_usd = Decimal("0")
    otros_pendientes_usd = Decimal("0")
    for d in deducs:
        usd = _ded_to_usd(d)
        concepto = (d.concepto or "").upper()
        if "COBERT" in concepto:
            coberturas_usd += usd
        else:
            otros_pendientes_usd += usd

    total_descontar_usd = (
        Decimal(str(payable.get("anticipos") or 0))
        + Decimal(str(payable.get("debt_usd") or 0))
        + Decimal(str(payable.get("debt_mxn_in_usd") or 0))
        + Decimal(str(payable.get("resico") or 0))
        + Decimal(str(payable.get("manual_usd") or 0))
    )

    return render(
        request,
        "pagos/compra_flujo.html",
        {
            "compra": compra,
            "documento_form": documento_form,
            "current_step": current_step,
            "active_form": active_form,
            "step_items": step_items_ui,
            "extra_items": extra_items_ui,
            "documentos": compra.documentos.all()[:30],
            "division_form": division_form,
            "divisiones": compra.divisiones.select_related("productor").order_by("id"),
            "pago_form": pago_form,
            "bank_form": bank_form,
            "cuentas_productor": cuentas_productor,
            "cuentas_facturador": cuentas_facturador,
            "confirmed_account": confirmed_account,
            "beneficiary_validation": beneficiary_validation,
            "solicitud_configurada": solicitud_configurada,
            "pago_pdf_preview": pago_pdf_preview,
            "edit_bank": edit_bank,
            "edit_solicitud_factura": edit_solicitud_factura,
            "deduccion_form": deduccion_form,
            "cancelar_form": cancelar_form,
            "facturador_form": facturador_form,
            "deducciones": compra.deducciones.all(),
            "pagos_registrados": compra.pagos_registrados.all(),
            "total_pagado_registrado": compra.total_pagado_registrado,
            "monto_objetivo_pago": compra.compra_en_libras,
            "payable": payable,
            "total_descontar_usd": total_descontar_usd,
            "total_pagar_usd": (payable.get("saldo_a_pagar") or Decimal("0")),
            "pending_microsip_usd": (compra.retencion_deudas_usd or Decimal("0")),
            "pending_microsip_mxn": (compra.retencion_deudas_mxn or Decimal("0")),
            "pending_coberturas_usd": coberturas_usd,
            "pending_otros_usd": otros_pendientes_usd,
            "anticipos_pendientes": compra.productor.anticipos.filter(
                pendiente_aplicar="PENDIENTE"
            ).order_by("-fecha_pago")[:20],
            "anticipos_aplicados": compra.aplicaciones_anticipo.select_related("anticipo").order_by("-fecha", "-id")[:20],
            "ultima_validacion_factura": compra.invoice_validations.first(),
            "expediente_status": expediente_status,
            "last_microsip_snapshot": last_microsip_snapshot,
            "factura_has_xml": has_xml,
            "factura_has_pdf": has_pdf,
            "revisar_factura_ready": revisar_factura_ready,
            "facturador_sin_contador": facturador_sin_contador,
            "solicitud_factura_texto": build_invoice_request_message(compra),
            "solicitud_logs": solicitud_logs,
            "solicitud_resumen": solicitud_resumen,
            "inbox_factura_preview": inbox_factura_preview,
            "productor_missing_fields": productor_missing_fields,
            "has_pago_comprobante": has_pago_comprobante,
            "has_compra_original_pdf": has_compra_original_pdf,
            "pago_doc_latest": pago_doc_latest,
            "edit_comprobante": bool((request.GET.get("edit_comprobante") or "").strip()),
            "can_manage_docs": bool(request.user.is_authenticated and (request.user.is_superuser or request.user.groups.filter(name="Admin").exists())),
        },
    )


@login_required
@login_required
def compra_validacion_factura_view(request, compra_id):
    compra = get_object_or_404(Compra, pk=compra_id)

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect(f"/compras/{compra.id}/validacion-factura/")
        xml_doc = compra.documentos.filter(etapa="factura", archivo__iendswith=".xml").order_by("-created_at").first()
        if not xml_doc:
            messages.error(request, "No hay XML de factura para revalidar.")
            return redirect(f"/compras/{compra.id}/validacion-factura/")
        xml_bytes = xml_doc.archivo.read()
        facturador = getattr(compra, "facturador", None)
        regimen_txt = " ".join([
            (facturador.regimen_fiscal if facturador else "") or "",
            (compra.regimen_fiscal or ""),
            (compra.productor.regimen_fiscal or ""),
            (facturador.regimen_fiscal_codigo if facturador else "") or "",
            (compra.productor.regimen_fiscal_codigo or ""),
        ]).upper()
        requires_resico = ("RESICO" in regimen_txt) or ("626" in regimen_txt)
        resico_policy = (facturador.resico_policy if facturador else "AUTO") or "AUTO"
        cfg = XmlValidationConfig.get_default()
        global_rfc = (cfg.global_rfc_receptor or settings.CFDI_RFC_RECEPTOR_GLOBAL or "").strip().upper()
        expected_moneda = (compra.expected_moneda or "").strip().upper()

        v = create_invoice_validation_for_compra(
            compra,
            xml_bytes,
            expected_rfc_receptor=((compra.expected_rfc_receptor or "").strip().upper() or global_rfc),
            expected_regimen_fiscal_receptor=(cfg.global_regimen_fiscal_receptor or ""),
            expected_codigo_fiscal_receptor=(cfg.global_codigo_fiscal_receptor or ""),
            expected_nombre_receptor=(cfg.global_nombre_receptor or ""),
            expected_efecto_comprobante=(cfg.global_efecto_comprobante or ""),
            expected_impuesto_trasladado=(cfg.global_impuesto_trasladado or ""),
            expected_moneda=expected_moneda,
            expected_uso_cfdi=(compra.expected_uso_cfdi or ""),
            expected_metodo_pago=(compra.expected_metodo_pago or ""),
            expected_forma_pago=(compra.expected_forma_pago or ""),
            expected_total_comprobante=str(compra.compra_en_libras or ""),
            total_tolerance_usd="3",
            requires_resico_retention=requires_resico,
            resico_policy=resico_policy,
        )
        if v.valid and v.uuid and compra.uuid_factura != v.uuid:
            compra.uuid_factura = v.uuid
            compra.save(update_fields=["uuid_factura", "updated_at"])
        messages.success(request, "XML revalidado con reglas actuales.")
        return redirect(f"/compras/{compra.id}/validacion-factura/")

    validaciones = list(compra.invoice_validations.all().order_by("-created_at")[:30])
    ultima = validaciones[0] if validaciones else None
    anterior = validaciones[1] if len(validaciones) > 1 else None

    monto_diff = None
    monto_within_tolerance = None
    if ultima:
        try:
            expected = Decimal(str(compra.compra_en_libras or "0"))
            actual_raw = (getattr(ultima, "raw_result", {}) or {}).get("total_comprobante", "")
            if str(actual_raw).strip():
                actual = Decimal(str(actual_raw))
                monto_diff = abs(expected - actual)
                monto_within_tolerance = monto_diff <= Decimal("3")
        except (InvalidOperation, TypeError, ValueError):
            monto_diff = None
            monto_within_tolerance = None

    return render(
        request,
        "pagos/compra_validacion_factura.html",
        {
            "compra": compra,
            "validaciones": validaciones,
            "ultima": ultima,
            "anterior": anterior,
            "monto_diff": monto_diff,
            "monto_within_tolerance": monto_within_tolerance,
        },
    )


@login_required
def api_facturador_contacto_view(request, facturador_id):
    facturador = get_object_or_404(PersonaFactura.objects.select_related("contador"), pk=facturador_id)
    contador = facturador.contador
    return JsonResponse(
        {
            "ok": True,
            "contador": (contador.nombre if contador else ""),
            "correo": (contador.email if contador else ""),
        }
    )


@login_required
def productores_catalogo_view(request):
    productores = Productor.objects.order_by("nombre")
    q = request.GET.get("q", "").strip()
    if q:
        productores = productores.filter(
            Q(nombre__icontains=q) | Q(codigo__icontains=q) | Q(cuenta_productor__icontains=q)
        )
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("productores_catalogo")
        form = ProductorForm(request.POST, prefix="prod")
        if form.is_valid():
            form.save()
            messages.success(request, "Productor creado correctamente.")
            return redirect("productores_catalogo")
        messages.error(request, "Revisa los datos del productor.")
    else:
        form = ProductorForm(prefix="prod")
    return render(request, "pagos/productores_catalogo.html", {"productores": productores[:300], "form": form, "q": q})


@login_required
def productor_edit_view(request, productor_id):
    productor = get_object_or_404(Productor, pk=productor_id)
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("productores_catalogo")
        form = ProductorForm(request.POST, instance=productor, prefix="prod")
        if form.is_valid():
            form.save()
            messages.success(request, "Productor actualizado correctamente.")
            return redirect("productores_catalogo")
        messages.error(request, "Revisa los datos del productor.")
    else:
        form = ProductorForm(instance=productor, prefix="prod")
    return render(request, "pagos/productor_form.html", {"form": form, "productor": productor})


@login_required
def productor_cuentas_view(request, productor_id):
    productor = get_object_or_404(Productor, pk=productor_id)
    cuentas = productor.cuentas_bancarias.all()

    edit_id = (request.GET.get("edit") or "").strip()
    edit_obj = None
    if edit_id.isdigit():
        edit_obj = productor.cuentas_bancarias.filter(pk=int(edit_id)).first()

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect(f"/productores/{productor.id}/cuentas/")

        flow_form = (request.POST.get("flow_form") or "save").strip()
        if flow_form == "delete":
            account_id = (request.POST.get("account_id") or "").strip()
            obj = productor.cuentas_bancarias.filter(pk=int(account_id)).first() if account_id.isdigit() else None
            if not obj:
                messages.error(request, "Cuenta bancaria no encontrada.")
                return redirect(f"/productores/{productor.id}/cuentas/")
            was_default = obj.predeterminada
            obj.delete()
            if was_default:
                next_default = productor.cuentas_bancarias.filter(activa=True).order_by("id").first()
                if next_default:
                    next_default.predeterminada = True
                    next_default.save()
                    productor.cuenta_productor = next_default.cuenta
                    productor.save(update_fields=["cuenta_productor", "updated_at"])
            messages.success(request, "Cuenta bancaria eliminada.")
            return redirect(f"/productores/{productor.id}/cuentas/")

        account_id = (request.POST.get("account_id") or "").strip()
        instance = productor.cuentas_bancarias.filter(pk=int(account_id)).first() if account_id.isdigit() else None
        form = ProductorCuentaBancariaForm(request.POST, request.FILES, instance=instance, prefix="cta")
        if form.is_valid():
            obj = form.save(commit=False)
            obj.productor = productor
            obj.save()
            if obj.predeterminada or not (productor.cuenta_productor or "").strip():
                productor.cuenta_productor = obj.cuenta
                productor.save(update_fields=["cuenta_productor", "updated_at"])
            messages.success(request, "Cuenta bancaria guardada.")
            return redirect(f"/productores/{productor.id}/cuentas/")
        messages.error(request, "Revisa los datos de la cuenta bancaria.")
    else:
        form = ProductorCuentaBancariaForm(instance=edit_obj, prefix="cta")

    return render(
        request,
        "pagos/productor_cuentas.html",
        {"productor": productor, "cuentas": cuentas, "form": form, "edit_obj": edit_obj},
    )


@login_required
def facturador_cuentas_view(request, facturador_id):
    facturador = get_object_or_404(PersonaFactura, pk=facturador_id)
    cuentas = facturador.cuentas_bancarias.all()

    edit_id = (request.GET.get("edit") or "").strip()
    edit_obj = facturador.cuentas_bancarias.filter(pk=int(edit_id)).first() if edit_id.isdigit() else None

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect(f"/facturadores/{facturador.id}/cuentas/")

        flow_form = (request.POST.get("flow_form") or "save").strip()
        if flow_form == "delete":
            account_id = (request.POST.get("account_id") or "").strip()
            obj = facturador.cuentas_bancarias.filter(pk=int(account_id)).first() if account_id.isdigit() else None
            if not obj:
                messages.error(request, "Cuenta bancaria no encontrada.")
                return redirect(f"/facturadores/{facturador.id}/cuentas/")
            obj.delete()
            messages.success(request, "Cuenta bancaria eliminada.")
            return redirect(f"/facturadores/{facturador.id}/cuentas/")

        account_id = (request.POST.get("account_id") or "").strip()
        instance = facturador.cuentas_bancarias.filter(pk=int(account_id)).first() if account_id.isdigit() else None
        form = FacturadorCuentaBancariaForm(request.POST, request.FILES, instance=instance, prefix="cta")
        if form.is_valid():
            obj = form.save(commit=False)
            obj.facturador = facturador
            obj.save()
            messages.success(request, "Cuenta bancaria guardada.")
            return redirect(f"/facturadores/{facturador.id}/cuentas/")
        messages.error(request, "Revisa los datos de la cuenta bancaria.")
    else:
        form = FacturadorCuentaBancariaForm(instance=edit_obj, prefix="cta")

    return render(
        request,
        "pagos/facturador_cuentas.html",
        {"facturador": facturador, "cuentas": cuentas, "form": form, "edit_obj": edit_obj},
    )


@login_required
def beneficiary_exceptions_view(request):
    qs = BeneficiaryValidationException.objects.select_related("productor", "facturador").order_by("-created_at")
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("beneficiary_exceptions")
        form = BeneficiaryValidationExceptionForm(request.POST, prefix="bex")
        if form.is_valid():
            form.save()
            messages.success(request, "Excepción guardada.")
            return redirect("beneficiary_exceptions")
        messages.error(request, "Revisa los datos de la excepción.")
    else:
        form = BeneficiaryValidationExceptionForm(prefix="bex")

    return render(request, "pagos/beneficiary_exceptions.html", {"items": qs[:200], "form": form})


@login_required
def contadores_catalogo_view(request):
    contadores = Contador.objects.order_by("nombre")
    q = request.GET.get("q", "").strip()
    if q:
        contadores = contadores.filter(
            Q(nombre__icontains=q) | Q(email__icontains=q) | Q(telefono__icontains=q)
        )

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("contadores_catalogo")
        form = ContadorForm(request.POST, prefix="cont")
        if form.is_valid():
            form.save()
            messages.success(request, "Contador guardado correctamente.")
            return redirect("contadores_catalogo")
        messages.error(request, "Revisa los datos del contador.")
    else:
        form = ContadorForm(prefix="cont")

    return render(request, "pagos/contadores_catalogo.html", {"contadores": contadores[:300], "form": form, "q": q})


@login_required
def contador_edit_view(request, contador_id):
    contador = get_object_or_404(Contador, pk=contador_id)
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("contadores_catalogo")
        form = ContadorForm(request.POST, instance=contador, prefix="cont")
        if form.is_valid():
            form.save()
            messages.success(request, "Contador actualizado correctamente.")
            return redirect("contadores_catalogo")
        messages.error(request, "Revisa los datos del contador.")
    else:
        form = ContadorForm(instance=contador, prefix="cont")
    return render(request, "pagos/contador_form.html", {"form": form, "contador": contador})


@login_required
def plantillas_email_view(request):
    templates = EmailTemplate.objects.order_by("scenario", "nombre")
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("plantillas_email")
        form = EmailTemplateForm(request.POST, prefix="tpl")
        if form.is_valid():
            tpl = form.save()
            if tpl.is_default:
                EmailTemplate.objects.exclude(pk=tpl.pk).filter(scenario=tpl.scenario).update(is_default=False)
            messages.success(request, "Plantilla guardada.")
            return redirect("plantillas_email")
        messages.error(request, "Revisa los datos de la plantilla.")
    else:
        form = EmailTemplateForm(prefix="tpl")
    return render(request, "pagos/plantillas_email.html", {"templates": templates, "form": form})


@login_required
def plantilla_email_delete_view(request, template_id):
    tpl = get_object_or_404(EmailTemplate, pk=template_id)
    if request.method != "POST":
        return redirect("plantillas_email")
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_superuser:
        messages.error(request, "Solo admin puede eliminar plantillas.")
        return redirect("plantillas_email")
    tpl.delete()
    messages.success(request, "Plantilla eliminada.")
    return redirect("plantillas_email")


@login_required
def plantilla_email_edit_view(request, template_id):
    tpl = get_object_or_404(EmailTemplate, pk=template_id)
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("plantillas_email")
        form = EmailTemplateForm(request.POST, instance=tpl, prefix="tpl")
        if form.is_valid():
            tpl = form.save()
            if tpl.is_default:
                EmailTemplate.objects.exclude(pk=tpl.pk).filter(scenario=tpl.scenario).update(is_default=False)
            messages.success(request, "Plantilla actualizada.")
            return redirect("plantillas_email")
        messages.error(request, "Revisa los datos de la plantilla.")
    else:
        form = EmailTemplateForm(instance=tpl, prefix="tpl")
    return render(request, "pagos/plantilla_email_form.html", {"form": form, "tpl": tpl})


@login_required
def configuracion_validacion_xml_view(request):
    cfg = XmlValidationConfig.get_default()
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("config_validacion_xml")
        form = XmlValidationConfigForm(request.POST, instance=cfg, prefix="cfg")
        if form.is_valid():
            form.save()
            messages.success(request, "Configuración de validación XML actualizada.")
            return redirect("config_validacion_xml")
        messages.error(request, "Revisa la configuración.")
    else:
        form = XmlValidationConfigForm(instance=cfg, prefix="cfg")

    return render(request, "pagos/config_validacion_xml.html", {"form": form})


@login_required
def facturadores_catalogo_view(request):
    facturadores = PersonaFactura.objects.order_by("nombre")
    q = request.GET.get("q", "").strip()
    if q:
        facturadores = facturadores.filter(
            Q(nombre__icontains=q) | Q(rfc__icontains=q) | Q(regimen_fiscal__icontains=q)
        )

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("facturadores_catalogo")
        form = PersonaFacturaQuickForm(request.POST, prefix="pfcat")
        if form.is_valid():
            form.save()
            messages.success(request, "Entidad facturadora guardada correctamente.")
            return redirect("facturadores_catalogo")
        messages.error(request, "Revisa los datos de la entidad facturadora.")
    else:
        form = PersonaFacturaQuickForm(prefix="pfcat")

    return render(
        request,
        "pagos/facturadores_catalogo.html",
        {"facturadores": facturadores[:300], "form": form, "q": q},
    )


@login_required
def facturador_edit_view(request, facturador_id):
    facturador = get_object_or_404(PersonaFactura, pk=facturador_id)
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("facturadores_catalogo")
        form = PersonaFacturaQuickForm(request.POST, instance=facturador, prefix="pfcat")
        if form.is_valid():
            form.save()
            messages.success(request, "Entidad facturadora actualizada correctamente.")
            return redirect("facturadores_catalogo")
        messages.error(request, "Revisa los datos de la entidad facturadora.")
    else:
        form = PersonaFacturaQuickForm(instance=facturador, prefix="pfcat")
    return render(request, "pagos/facturador_form.html", {"form": form, "facturador": facturador})


@login_required
def anticipos_view(request):
    anticipos = Anticipo.objects.select_related("productor").order_by("-fecha_pago", "-numero_anticipo")
    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos de edición.")
            return redirect("anticipos")
        form = AnticipoForm(request.POST, prefix="ant")
        if form.is_valid():
            form.save()
            messages.success(request, "Anticipo guardado.")
            return redirect("anticipos")
        messages.error(request, "Revisa los datos del anticipo.")
    else:
        form = AnticipoForm(prefix="ant")

    return render(
        request,
        "pagos/anticipos.html",
        {"anticipos": anticipos[:300], "form": form},
    )

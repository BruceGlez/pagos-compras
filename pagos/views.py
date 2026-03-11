from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q, Sum
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
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
    DocumentoCompraForm,
    ImportAnticiposExcelForm,
    ImportComprasExcelForm,
    PagoCompraForm,
    PersonaFacturaQuickForm,
    ProductorForm,
    TipoCambioForm,
    XmlValidationConfigForm,
)
from .models import Anticipo, Compra, Contador, Deduccion, ImportRun, PagoCompra, PersonaFactura, Productor, TipoCambio, WorkflowStateChoices, XmlValidationConfig
from .services import (
    build_invoice_request_message,
    create_invoice_validation_for_compra,
    detect_compras_conflicts,
    import_anticipos_excel,
    import_compras_excel,
    payable_breakdown,
    find_microsip_candidates_for_productor,
    list_all_microsip_debt_clients,
    preview_anticipos_excel,
    preview_compras_excel,
    sync_microsip_debt_for_compra,
    transition_compra,
)


def _can_write(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__in=["Admin", "Operador"]).exists()


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

    blocked = {
        c.id: (c.invoice_validations.first().blocked_reason if c.invoice_validations.first() else "")
        for c in qs[:300]
    }

    return render(
        request,
        "pagos/readiness_queue.html",
        {
            "compras": qs[:300],
            "counts": counts,
            "active_state": state,
            "blocked_reasons": blocked,
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

    if request.method == "POST":
        if not _can_write(request.user):
            messages.error(request, "No tienes permisos para importar.")
            return redirect("compras_operativas")
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
                messages.info(request, "Vista previa generada. Revisa conflictos y luego confirma importación.")

    return render(
        request,
        "pagos/import_compras.html",
        {
            "form": form,
            "result": result,
            "preview_rows": preview_rows,
            "conflict_rows": conflict_rows,
            "import_run": import_run,
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
        from django.utils import timezone

        compra.bank_account_confirmed = True
        if not compra.bank_confirmed_at:
            compra.bank_confirmed_at = timezone.now()
        if not compra.bank_confirmation_source:
            compra.bank_confirmation_source = "queue_quick_action"
        compra.save(update_fields=["bank_account_confirmed", "bank_confirmed_at", "bank_confirmation_source", "updated_at"])
        messages.success(request, "Cuenta bancaria confirmada.")
    elif action == "mark_ready":
        try:
            transition_compra(compra, WorkflowStateChoices.READY_TO_PAY, actor=actor, reason="Acción rápida desde queue")
            messages.success(request, "Compra marcada como READY_TO_PAY.")
        except ValueError:
            messages.warning(request, "No se pudo mover a READY_TO_PAY desde el estado actual.")
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

    if request.method == "POST":
        selected = (request.POST.get("cliente_microsip") or "").strip()
        if selected:
            if "||" in selected:
                selected_id, selected_name = selected.split("||", 1)
            else:
                selected_id, selected_name = "", selected
            compra.productor.microsip_cliente_nombre = selected_name
            compra.productor.microsip_cliente_id = selected_id
            compra.productor.save(update_fields=["microsip_cliente_nombre", "microsip_cliente_id", "updated_at"])
            messages.success(request, "Vinculación Microsip guardada. Ahora sincroniza deudas.")
            return redirect(f"/compras/{compra.id}/flujo/?step=deudas")

    candidates = find_microsip_candidates_for_productor(compra.productor.nombre, limit=20)
    search = (request.GET.get("search") or "").strip()
    manual_candidates = list_all_microsip_debt_clients(search=search, limit=80) if (search or not candidates) else []

    return render(
        request,
        "pagos/mapear_microsip.html",
        {
            "compra": compra,
            "candidates": candidates,
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
                        requires_resico_retention=requires_resico,
                        resico_policy=resico_policy,
                    )

                    if validation.valid:
                        messages.success(request, "XML de factura validado correctamente.")
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
        elif form_name == "marcar_solicitud_factura":
            compra.solicitud_factura_enviada = True
            compra.save(update_fields=["solicitud_factura_enviada", "updated_at"])
            actor = str(getattr(request.user, "username", "operador") or "operador")
            try:
                transition_compra(
                    compra,
                    WorkflowStateChoices.WAITING_INVOICE,
                    actor=actor,
                    reason="Solicitud de factura enviada",
                )
            except ValueError:
                pass
            messages.success(request, "Solicitud de factura marcada como enviada.")
            return redirect(f"/compras/{compra.id}/flujo/?step=solicitar_factura")
        elif form_name == "microsip_sync_debt":
            try:
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
            bank_form = CompraBankConfirmationForm(request.POST, instance=compra, prefix="bank")
            if bank_form.is_valid():
                obj = bank_form.save(commit=False)
                if obj.bank_account_confirmed and not obj.bank_confirmed_at:
                    from django.utils import timezone

                    obj.bank_confirmed_at = timezone.now()
                obj.save()
                actor = str(getattr(request.user, "username", "operador") or "operador")
                if obj.bank_account_confirmed:
                    try:
                        transition_compra(
                            compra,
                            WorkflowStateChoices.READY_TO_PAY,
                            actor=actor,
                            reason="Cuenta bancaria confirmada",
                        )
                    except ValueError:
                        pass
                messages.success(request, "Confirmación bancaria guardada.")
                return redirect(f"/compras/{compra.id}/flujo/?step=pago")
            messages.error(request, "Error al guardar confirmación bancaria.")
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
                factura_files = list(compra.documentos.filter(etapa="factura").values_list("archivo", flat=True))
                has_pdf = any(str(x).lower().endswith(".pdf") for x in factura_files)
                if not has_pdf:
                    messages.error(request, "No se puede registrar pago: falta PDF de factura en expediente.")
                    return redirect(f"/compras/{compra.id}/flujo/?step=pago")

                pago = pago_form.save(commit=False)
                pago.compra = compra
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
        elif form_name in form_map:
            form_cls, msg = form_map[form_name]
            forms[form_name] = form_cls(request.POST, instance=compra, prefix=form_name)
            if forms[form_name].is_valid():
                forms[form_name].save()
                compra.refresh_from_db()
                if form_name == "solicitar_factura" and compra.facturador:
                    compra.factura = compra.facturador.nombre
                    linked_contador = compra.facturador.contador
                    if linked_contador:
                        compra.contador = linked_contador.nombre
                        compra.correo = linked_contador.email
                        compra.save(update_fields=["factura", "contador", "correo", "updated_at"])
                    else:
                        compra.save(update_fields=["factura", "updated_at"])
                        messages.warning(request, "La entidad facturadora no tiene contador ligado. Vincúlalo en el catálogo de Entidades que facturan.")

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
    step_items = [
        ("captura", "Registrar compra"),
        ("anticipos", "Revisar anticipos"),
        ("deudas", "Revisar deudas"),
        ("solicitar_factura", "Solicitar factura"),
        ("revisar_factura", "Revisar factura"),
        ("pago", "Pagar factura"),
    ]
    extra_items = [("tc", "TC")]
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
    last_microsip_snapshot = compra.debt_snapshots.filter(fuente="microsip").first()
    factura_docs = list(compra.documentos.filter(etapa="factura").values_list("archivo", flat=True))
    has_xml = any(str(x).lower().endswith(".xml") for x in factura_docs)
    has_pdf = any(str(x).lower().endswith(".pdf") for x in factura_docs)
    revisar_factura_ready = bool(has_xml and has_pdf and compra.uuid_factura)
    facturador_sin_contador = bool(compra.facturador_id and not getattr(compra.facturador, "contador", None))
    expediente_status = [
        ("Solicitud factura", "solicitud_factura" in existing_etapas),
        ("Factura XML/PDF", "factura" in existing_etapas),
        ("Comprobante pago", "pago" in existing_etapas),
    ]

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
            "deduccion_form": deduccion_form,
            "cancelar_form": cancelar_form,
            "facturador_form": facturador_form,
            "deducciones": compra.deducciones.all(),
            "pagos_registrados": compra.pagos_registrados.all(),
            "total_pagado_registrado": compra.total_pagado_registrado,
            "monto_objetivo_pago": compra.compra_en_libras,
            "payable": payable_breakdown(compra),
            "anticipos_pendientes": compra.productor.anticipos.filter(
                pendiente_aplicar="PENDIENTE"
            ).order_by("-fecha_pago")[:20],
            "ultima_validacion_factura": compra.invoice_validations.first(),
            "expediente_status": expediente_status,
            "last_microsip_snapshot": last_microsip_snapshot,
            "factura_has_xml": has_xml,
            "factura_has_pdf": has_pdf,
            "revisar_factura_ready": revisar_factura_ready,
            "facturador_sin_contador": facturador_sin_contador,
            "solicitud_factura_texto": build_invoice_request_message(compra),
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

        create_invoice_validation_for_compra(
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
            requires_resico_retention=requires_resico,
            resico_policy=resico_policy,
        )
        messages.success(request, "XML revalidado con reglas actuales.")
        return redirect(f"/compras/{compra.id}/validacion-factura/")

    validaciones = list(compra.invoice_validations.all().order_by("-created_at")[:30])
    ultima = validaciones[0] if validaciones else None
    anterior = validaciones[1] if len(validaciones) > 1 else None
    return render(
        request,
        "pagos/compra_validacion_factura.html",
        {"compra": compra, "validaciones": validaciones, "ultima": ultima, "anterior": anterior},
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

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models.deletion import ProtectedError
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView

from .forms import (
    AnticipoForm,
    AplicacionAnticipoForm,
    CompraDivisionCreateForm,
    CompraFacturasForm,
    CompraFiltroForm,
    CompraFlujo1Form,
    CompraFlujo2Form,
    CompraFlujo3Form,
    CompraFlujoAnticiposForm,
    CompraOperativaForm,
    CompraForm,
    DocumentoCompraForm,
    PagoCompraForm,
    ProductorForm,
    TipoCambioForm,
)
from .models import Anticipo, Compra, PagoCompra, Productor, TipoCambio


class HomeView(ListView):
    template_name = "pagos/home.html"
    model = Compra
    context_object_name = "compras_recientes"
    paginate_by = 10

    def get_queryset(self):
        return Compra.objects.select_related("productor").order_by("-fecha_liq", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        anticipos_stats = Anticipo.objects.aggregate(total=Sum("monto_anticipo"), conteo=Count("id"))
        compras_stats = Compra.objects.aggregate(total=Sum("compra_en_libras"), conteo=Count("id"))
        context["productores_activos"] = Productor.objects.filter(activo=True).count()
        context["anticipos_total"] = anticipos_stats["total"] or 0
        context["anticipos_count"] = anticipos_stats["conteo"] or 0
        context["compras_total_libras"] = compras_stats["total"] or 0
        context["compras_count"] = compras_stats["conteo"] or 0
        context["tc_ultimo"] = TipoCambio.objects.order_by("-fecha").first()
        return context


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


def compras_operativas_view(request):
    qs = Compra.objects.select_related("productor", "tipo_cambio").order_by("-fecha_liq", "-id")
    filtro = CompraFiltroForm(request.GET or None)
    if filtro.is_valid():
        data = filtro.cleaned_data
        if data.get("q"):
            term = data["q"].strip()
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

    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get("page"))
    return render(request, "pagos/compras_operativas.html", {"filtro_form": filtro, "page_obj": page_obj})


def compra_create_view(request):
    if request.method == "POST":
        form = CompraFlujo1Form(request.POST)
        if form.is_valid():
            compra = form.save()
            messages.success(request, "Flujo 1 guardado. Continua con los siguientes pasos.")
            return redirect("compra_flujo", compra_id=compra.id)
        messages.error(request, "Revisa los datos de la compra.")
    else:
        form = CompraFlujo1Form()
    return render(request, "pagos/compra_form.html", {"form": form, "form_title": "Nueva Compra - Flujo 1"})


def compra_edit_view(request, compra_id):
    compra = get_object_or_404(Compra, pk=compra_id)
    if request.method == "POST":
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


def pago_delete_view(request, pago_id):
    if request.method != "POST":
        return redirect("compras_operativas")
    pago = get_object_or_404(PagoCompra, pk=pago_id)
    compra_id = pago.compra_id
    pago.delete()
    messages.success(request, "Pago eliminado correctamente.")
    return redirect(f"/compras/{compra_id}/flujo/?step=pago")


def compra_flujo_view(request, compra_id):
    compra = get_object_or_404(Compra, pk=compra_id)
    ui_pending_step = compra.flujo_step_default
    form_map = {
        "captura": (CompraFlujo1Form, "Captura actualizada."),
        "anticipos": (CompraFlujoAnticiposForm, "Anticipos revisados."),
        "deudas": (CompraFlujo3Form, "Deudas actualizadas."),
        "facturas": (CompraFacturasForm, "Facturas actualizadas."),
        "tc": (CompraFlujo2Form, "Tipo de cambio actualizado."),
    }
    forms = {k: cls(instance=compra, prefix=k) for k, (cls, _) in form_map.items()}
    documento_form = DocumentoCompraForm(prefix="doc")
    division_form = CompraDivisionCreateForm(compra=compra, prefix="div")
    pago_form = PagoCompraForm(prefix="pagoitem")

    if request.method == "POST":
        form_name = request.POST.get("flow_form")
        if form_name == "documento":
            documento_form = DocumentoCompraForm(request.POST, request.FILES, prefix="doc")
            if documento_form.is_valid():
                doc = documento_form.save(commit=False)
                doc.compra = compra
                doc.save()
                messages.success(request, "Documento cargado al expediente.")
                compra.refresh_from_db()
                return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")
            messages.error(request, "Error al cargar documento.")
        elif form_name == "pago_registrar":
            pago_form = PagoCompraForm(request.POST, prefix="pagoitem")
            if pago_form.is_valid():
                pago = pago_form.save(commit=False)
                pago.compra = compra
                pago.save()
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
                messages.success(request, msg)
                compra.refresh_from_db()
                return redirect(f"/compras/{compra.id}/flujo/?step={compra.flujo_step_default}")
            messages.error(request, "Hay errores en el formulario.")

    current_step = request.GET.get("step") or ui_pending_step
    step_items = [
        ("captura", "Registrar compra"),
        ("anticipos", "Revisar anticipos"),
        ("deudas", "Revisar deudas"),
        ("facturas", "Solicitar facturas"),
        ("pago", "Pagar factura"),
    ]
    extra_items = [("tc", "TC")]
    if not compra.es_division and compra.captura_completa:
        extra_items.append(("dividir", "Dividir compra"))
    main_order = [code for code, _ in step_items]
    pending_idx = main_order.index(ui_pending_step) if ui_pending_step in main_order else len(main_order) - 1
    unlocked_main = set(main_order[: pending_idx + 1])
    unlocked_extra = {code for code, _ in extra_items} if compra.captura_completa else set()
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
            "pagos_registrados": compra.pagos_registrados.all(),
            "total_pagado_registrado": compra.total_pagado_registrado,
            "monto_objetivo_pago": compra.compra_en_libras,
            "anticipos_pendientes": compra.productor.anticipos.filter(
                pendiente_aplicar="PENDIENTE"
            ).order_by("-fecha_pago")[:20],
        },
    )


def productores_catalogo_view(request):
    productores = Productor.objects.order_by("nombre")
    q = request.GET.get("q", "").strip()
    if q:
        productores = productores.filter(
            Q(nombre__icontains=q) | Q(codigo__icontains=q) | Q(cuenta_productor__icontains=q)
        )
    if request.method == "POST":
        form = ProductorForm(request.POST, prefix="prod")
        if form.is_valid():
            form.save()
            messages.success(request, "Productor creado correctamente.")
            return redirect("productores_catalogo")
        messages.error(request, "Revisa los datos del productor.")
    else:
        form = ProductorForm(prefix="prod")
    return render(request, "pagos/productores_catalogo.html", {"productores": productores[:300], "form": form, "q": q})


def productor_edit_view(request, productor_id):
    productor = get_object_or_404(Productor, pk=productor_id)
    if request.method == "POST":
        form = ProductorForm(request.POST, instance=productor, prefix="prod")
        if form.is_valid():
            form.save()
            messages.success(request, "Productor actualizado correctamente.")
            return redirect("productores_catalogo")
        messages.error(request, "Revisa los datos del productor.")
    else:
        form = ProductorForm(instance=productor, prefix="prod")
    return render(request, "pagos/productor_form.html", {"form": form, "productor": productor})


def anticipos_view(request):
    anticipos = Anticipo.objects.select_related("productor").order_by("-fecha_pago", "-numero_anticipo")
    if request.method == "POST":
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

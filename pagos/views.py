from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import ListView

from .forms import (
    AnticipoForm,
    AplicacionAnticipoForm,
    CompraFiltroForm,
    CompraExpedienteForm,
    CompraFlujo1Form,
    CompraFlujo2Form,
    CompraFlujo3Form,
    CompraFlujo5Form,
    CompraRegistrarFacturaForm,
    CompraSolicitarFacturaForm,
    CompraOperativaForm,
    CompraForm,
    DocumentoCompraForm,
    ProductorForm,
    TipoCambioForm,
)
from .models import Anticipo, Compra, Productor, TipoCambio


class HomeView(ListView):
    template_name = "pagos/home.html"
    model = Compra
    context_object_name = "compras_recientes"
    paginate_by = 10

    def get_queryset(self):
        return Compra.objects.select_related("productor").order_by("-fecha_liq", "-id")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        anticipos_stats = Anticipo.objects.aggregate(
            total=Sum("monto_anticipo"),
            conteo=Count("id"),
        )
        compras_stats = Compra.objects.aggregate(
            total=Sum("compra_en_libras"),
            conteo=Count("id"),
        )
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

    context = {
        "form_instances": form_instances,
        "active_form": active,
    }
    return render(request, "pagos/registro.html", context)


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
    return render(
        request,
        "pagos/compras_operativas.html",
        {
            "filtro_form": filtro,
            "page_obj": page_obj,
        },
    )


def compra_create_view(request):
    if request.method == "POST":
        form = CompraFlujo1Form(request.POST)
        if form.is_valid():
            compra = form.save()
            messages.success(request, "Flujo 1 guardado. Continúa con los siguientes pasos.")
            return redirect("compra_flujo", compra_id=compra.id)
        messages.error(request, "Revisa los datos de la compra.")
    else:
        form = CompraFlujo1Form()
    return render(
        request,
        "pagos/compra_form.html",
        {"form": form, "form_title": "Nueva Compra - Flujo 1"},
    )


def compra_edit_view(request, compra_id):
    compra = get_object_or_404(Compra, pk=compra_id)
    if request.method == "POST":
        form = CompraOperativaForm(request.POST, instance=compra)
        if form.is_valid():
            form.save()
            messages.success(request, "Compra actualizada correctamente.")
            return redirect("compras_operativas")
        messages.error(request, "Revisa los datos de la compra.")
    else:
        form = CompraOperativaForm(instance=compra)
    return render(
        request,
        "pagos/compra_form.html",
        {"form": form, "form_title": f"Editar Compra {compra.numero_compra}"},
    )


def compra_flujo_view(request, compra_id):
    compra = get_object_or_404(Compra, pk=compra_id)
    form_map = {
        "flujo1": (CompraFlujo1Form, "Flujo 1 actualizado."),
        "solicitar_factura": (CompraSolicitarFacturaForm, "Solicitud de factura actualizada."),
        "registrar_factura": (CompraRegistrarFacturaForm, "Factura registrada."),
        "flujo3": (CompraFlujo3Form, "Flujo 3 actualizado."),
        "pago": (CompraFlujo5Form, "Pago actualizado."),
        "expediente": (CompraExpedienteForm, "Expediente actualizado."),
        "flujo2": (CompraFlujo2Form, "Tipo de cambio actualizado."),
    }

    forms = {k: cls(instance=compra, prefix=k) for k, (cls, _) in form_map.items()}
    documento_form = DocumentoCompraForm(prefix="doc")
    if request.method == "POST":
        form_name = request.POST.get("flow_form")
        if form_name == "documento":
            documento_form = DocumentoCompraForm(
                request.POST, request.FILES, prefix="doc"
            )
            if documento_form.is_valid():
                doc = documento_form.save(commit=False)
                doc.compra = compra
                doc.save()
                messages.success(request, "Documento cargado al expediente.")
                return redirect("compra_flujo", compra_id=compra.id)
            messages.error(request, "Error al cargar documento.")
        elif form_name in form_map:
            form_cls, msg = form_map[form_name]
            forms[form_name] = form_cls(request.POST, instance=compra, prefix=form_name)
            if forms[form_name].is_valid():
                forms[form_name].save()
                messages.success(request, msg)
                return redirect("compra_flujo", compra_id=compra.id)
            messages.error(request, "Hay errores en el formulario.")

    current_step = request.GET.get("step") or compra.flujo_codigo
    return render(
        request,
        "pagos/compra_flujo.html",
        {
            "compra": compra,
            "forms": forms,
            "documento_form": documento_form,
            "current_step": current_step,
            "documentos": compra.documentos.all()[:30],
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
    return render(
        request,
        "pagos/productores_catalogo.html",
        {"productores": productores[:300], "form": form, "q": q},
    )


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
    return render(
        request,
        "pagos/productor_form.html",
        {"form": form, "productor": productor},
    )

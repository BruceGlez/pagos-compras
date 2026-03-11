from django.contrib import admin
from django.db.models import Sum

from .models import (
    Anticipo,
    AplicacionAnticipo,
    Compra,
    Contador,
    DebtSnapshot,
    Deduccion,
    DocumentoCompra,
    ImportRowLog,
    ImportRun,
    InvoiceValidationResult,
    PagoCompra,
    PersonaFactura,
    Productor,
    TipoCambio,
    WorkflowEvent,
)


@admin.register(Productor)
class ProductorAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "telefono", "activo")
    list_filter = ("activo",)
    search_fields = ("codigo", "nombre")


@admin.register(Contador)
class ContadorAdmin(admin.ModelAdmin):
    list_display = ("nombre", "email", "telefono", "activo")
    list_filter = ("activo",)
    search_fields = ("nombre", "email")


@admin.register(PersonaFactura)
class PersonaFacturaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "rfc", "regimen_fiscal_codigo", "activo", "created_at")
    list_filter = ("activo", "regimen_fiscal_codigo")
    search_fields = ("nombre", "rfc")


@admin.register(TipoCambio)
class TipoCambioAdmin(admin.ModelAdmin):
    list_display = ("fecha", "tc", "fuente")
    search_fields = ("fuente",)
    date_hierarchy = "fecha"


class AplicacionAnticipoInline(admin.TabularInline):
    model = AplicacionAnticipo
    extra = 0


@admin.register(Anticipo)
class AnticipoAdmin(admin.ModelAdmin):
    list_display = (
        "numero_anticipo",
        "productor",
        "fecha_pago",
        "monto_anticipo",
        "monto_aplicado",
        "pendiente_aplicar",
    )
    list_filter = ("estado", "pendiente_aplicar", "fecha_pago")
    search_fields = ("productor__codigo", "productor__nombre", "factura")
    date_hierarchy = "fecha_pago"
    inlines = [AplicacionAnticipoInline]

    @admin.display(description="Aplicado")
    def monto_aplicado(self, obj):
        total = obj.aplicaciones.aggregate(total=Sum("monto_aplicado"))["total"]
        return total or 0


@admin.register(Compra)
class CompraAdmin(admin.ModelAdmin):
    list_display = (
        "numero_compra",
        "productor",
        "fecha_liq",
        "pacas",
        "pago",
        "cancelada",
        "flujo_label",
        "saldo_por_pagar_display",
    )
    list_filter = ("fecha_liq", "estatus_de_pago")
    search_fields = ("numero_compra", "productor__codigo", "productor__nombre", "uuid_factura")
    date_hierarchy = "fecha_liq"
    inlines = [AplicacionAnticipoInline]

    @admin.display(description="Saldo")
    def saldo_por_pagar_display(self, obj):
        return obj.saldo_por_pagar


@admin.register(AplicacionAnticipo)
class AplicacionAnticipoAdmin(admin.ModelAdmin):
    list_display = ("id", "anticipo", "compra", "fecha", "monto_aplicado")
    list_filter = ("fecha",)
    search_fields = ("anticipo__productor__codigo", "compra__numero_compra")
    date_hierarchy = "fecha"


@admin.register(DocumentoCompra)
class DocumentoCompraAdmin(admin.ModelAdmin):
    list_display = ("id", "compra", "etapa", "descripcion", "created_at")
    list_filter = ("etapa", "created_at")
    search_fields = ("compra__numero_compra", "descripcion")


@admin.register(PagoCompra)
class PagoCompraAdmin(admin.ModelAdmin):
    list_display = ("id", "compra", "fecha_pago", "monto", "moneda", "referencia")
    list_filter = ("fecha_pago", "moneda")
    search_fields = ("compra__numero_compra", "referencia", "compra__productor__nombre")


@admin.register(DebtSnapshot)
class DebtSnapshotAdmin(admin.ModelAdmin):
    list_display = ("id", "compra", "fuente", "total_usd", "total_mxn", "created_at")
    list_filter = ("fuente", "created_at")
    search_fields = ("compra__numero_compra", "compra__productor__nombre")


@admin.register(Deduccion)
class DeduccionAdmin(admin.ModelAdmin):
    list_display = ("id", "compra", "concepto", "monto", "moneda", "fuente", "created_at")
    list_filter = ("moneda", "fuente", "created_at")
    search_fields = ("compra__numero_compra", "concepto", "compra__productor__nombre")


@admin.register(WorkflowEvent)
class WorkflowEventAdmin(admin.ModelAdmin):
    list_display = ("id", "compra", "from_state", "to_state", "actor", "created_at")
    list_filter = ("to_state", "actor", "created_at")
    search_fields = ("compra__numero_compra", "compra__productor__nombre", "reason")


@admin.register(InvoiceValidationResult)
class InvoiceValidationResultAdmin(admin.ModelAdmin):
    list_display = ("id", "compra", "uuid", "valid", "uso_cfdi", "moneda", "created_at")
    list_filter = ("valid", "uso_cfdi", "moneda", "created_at")
    search_fields = ("compra__numero_compra", "uuid", "rfc_emisor", "rfc_receptor", "blocked_reason")


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = ("id", "source_name", "dry_run", "created_count", "duplicate_count", "division_count", "error_count", "created_at")
    list_filter = ("dry_run", "created_at")
    search_fields = ("source_name",)


@admin.register(ImportRowLog)
class ImportRowLogAdmin(admin.ModelAdmin):
    list_display = ("id", "run", "row_number", "status", "compra_numero", "productor_nombre")
    list_filter = ("status", "created_at")
    search_fields = ("message", "productor_nombre")

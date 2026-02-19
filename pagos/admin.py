from django.contrib import admin
from django.db.models import Sum

from .models import (
    Anticipo,
    AplicacionAnticipo,
    Compra,
    DocumentoCompra,
    Productor,
    TipoCambio,
)


@admin.register(Productor)
class ProductorAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "telefono", "activo")
    list_filter = ("activo",)
    search_fields = ("codigo", "nombre")


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

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class SiNoChoices(models.TextChoices):
    SI = "SI", _("Si")
    NO = "NO", _("No")


class MonedaChoices(models.TextChoices):
    PESOS = "PESOS", _("Pesos")
    DOLARES = "DOLARES", _("Dolares")
    PESOS_DOLARES = "PESOS/DOLARES", _("Pesos/Dolares")


class PendienteAplicarChoices(models.TextChoices):
    PENDIENTE = "PENDIENTE", _("Pendiente")
    APLICADO = "APLICADO", _("Aplicado")


class EstadoFacturaChoices(models.TextChoices):
    FACTURADO = "FACTURADO", _("Facturado")
    PENDIENTE = "PENDIENTE", _("Pendiente")


class EstadoPagoChoices(models.TextChoices):
    PAGADO = "PAGADO", _("Pagado")
    PENDIENTE = "PENDIENTE", _("Pendiente")
    PARCIAL = "PARCIAL", _("Parcial")


class DocumentoEtapaChoices(models.TextChoices):
    SOLICITUD_FACTURA = "solicitud_factura", _("Solicitud Factura")
    FACTURA = "factura", _("Factura")
    PAGO = "pago", _("Pago")
    OTRO = "otro", _("Otro")


class Productor(TimestampedModel):
    codigo = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=200)
    regimen_fiscal = models.CharField(max_length=120, blank=True)
    cuenta_productor = models.CharField(max_length=80, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    correo_facturas = models.EmailField(blank=True)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"


class TipoCambio(TimestampedModel):
    fecha = models.DateField(unique=True)
    tc = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    fuente = models.CharField(
        max_length=120, default="Diario Oficial de la Federacion"
    )

    class Meta:
        ordering = ["-fecha"]

    def __str__(self):
        return f"{self.fecha} - {self.tc}"


class Anticipo(TimestampedModel):
    numero_anticipo = models.PositiveIntegerField(unique=True, null=True, blank=True)
    fecha_pago = models.DateField(default=timezone.localdate)
    productor = models.ForeignKey(
        Productor, on_delete=models.PROTECT, related_name="anticipos"
    )
    persona_que_factura = models.CharField(max_length=200, blank=True)
    factura = models.CharField(max_length=80, blank=True, help_text="UUID factura")
    monto_anticipo = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    moneda = models.CharField(
        max_length=20, choices=MonedaChoices.choices, default=MonedaChoices.DOLARES
    )
    pendiente_aplicar = models.CharField(
        max_length=20,
        choices=PendienteAplicarChoices.choices,
        default=PendienteAplicarChoices.PENDIENTE,
    )
    estado = models.CharField(
        max_length=20,
        choices=EstadoFacturaChoices.choices,
        default=EstadoFacturaChoices.FACTURADO,
    )
    uuid_nota_credito = models.CharField(max_length=80, blank=True)
    total_en_pesos = models.DecimalField(
        max_digits=16, decimal_places=4, null=True, blank=True
    )
    cuenta_de_pago = models.CharField(max_length=80, blank=True)
    cuenta = models.CharField(max_length=80, blank=True)
    contador = models.CharField(max_length=160, blank=True)
    correo_para_facturas = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    observaciones = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha_pago", "-numero_anticipo"]

    def __str__(self):
        return f"Anticipo {self.numero_anticipo} - {self.productor.codigo}"

    @property
    def monto_aplicado(self):
        if not self.pk:
            return Decimal("0")
        value = self.aplicaciones.aggregate(total=Sum("monto_aplicado"))["total"]
        return value or Decimal("0")

    @property
    def saldo_disponible(self):
        return self.monto_anticipo - self.monto_aplicado

    def actualizar_estado(self):
        self.pendiente_aplicar = (
            PendienteAplicarChoices.APLICADO
            if self.saldo_disponible <= Decimal("0")
            else PendienteAplicarChoices.PENDIENTE
        )

    def save(self, *args, **kwargs):
        self.actualizar_estado()
        return super().save(*args, **kwargs)


class Compra(TimestampedModel):
    numero_compra = models.PositiveIntegerField(default=0)
    intereses = models.CharField(
        max_length=2, choices=SiNoChoices.choices, default=SiNoChoices.NO
    )
    fecha_de_pago = models.DateField(default=timezone.localdate)
    fecha_liq = models.DateField(default=timezone.localdate)
    regimen_fiscal = models.CharField(max_length=120, blank=True)
    productor = models.ForeignKey(
        Productor, on_delete=models.PROTECT, related_name="compras"
    )
    parent_compra = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        related_name="divisiones",
        null=True,
        blank=True,
    )
    porcentaje_division = models.DecimalField(
        max_digits=6, decimal_places=2, null=True, blank=True
    )
    uuid_factura = models.CharField(max_length=80, blank=True)
    factura = models.CharField(max_length=200, blank=True)
    pacas = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    compra_en_libras = models.DecimalField(
        max_digits=16, decimal_places=4, null=True, blank=True
    )
    anticipo = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    pago = models.DecimalField(max_digits=16, decimal_places=4, null=True, blank=True)
    dias_transcurridos = models.IntegerField(default=0)
    tipo_cambio = models.ForeignKey(
        TipoCambio,
        on_delete=models.PROTECT,
        related_name="compras",
        null=True,
        blank=True,
    )
    tipo_cambio_valor = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True
    )
    retencion_deudas_usd = models.DecimalField(
        max_digits=16, decimal_places=4, default=0
    )
    retencion_deudas_mxn = models.DecimalField(
        max_digits=16, decimal_places=4, default=0
    )
    total_deuda_en_dls = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    retencion_resico = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    saldo_pendiente = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    estatus_factura = models.CharField(
        max_length=20,
        choices=EstadoFacturaChoices.choices,
        default=EstadoFacturaChoices.FACTURADO,
    )
    vencimiento = models.DateField(null=True, blank=True)
    cuenta_de_pago = models.CharField(max_length=120, blank=True)
    metodo_de_pago = models.CharField(max_length=60, blank=True)
    moneda = models.CharField(
        max_length=20, choices=MonedaChoices.choices, default=MonedaChoices.DOLARES
    )
    total_en_pesos = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    cuenta_productor = models.CharField(max_length=80, blank=True)
    estatus_de_pago = models.CharField(
        max_length=20, choices=EstadoPagoChoices.choices, default=EstadoPagoChoices.PENDIENTE
    )
    contador = models.CharField(max_length=160, blank=True)
    correo = models.EmailField(blank=True)
    estatus_rep = models.CharField(max_length=30, blank=True)
    uuid_ppd = models.CharField(max_length=80, blank=True)
    solicitud_factura_enviada = models.BooleanField(default=False)
    fecha_solicitud_factura = models.DateField(null=True, blank=True)
    anticipos_revisados = models.BooleanField(default=False)
    deudas_revisadas = models.BooleanField(default=False)
    division_revisada = models.BooleanField(default=False)
    expediente_completo = models.BooleanField(default=False)

    class Meta:
        ordering = ["-fecha_liq", "-id"]

    def __str__(self):
        return f"Compra {self.numero_compra} - {self.productor.codigo}"

    @property
    def base_pago(self):
        if self.pago is not None:
            return self.pago
        return self.total_en_pesos

    @property
    def total_aplicado_anticipos(self):
        if not self.pk:
            return Decimal("0")
        value = self.aplicaciones_anticipo.aggregate(total=Sum("monto_aplicado"))["total"]
        return value or Decimal("0")

    @property
    def saldo_por_pagar(self):
        return self.base_pago - self.total_aplicado_anticipos

    def save(self, *args, **kwargs):
        if self.productor_id and not self.regimen_fiscal:
            self.regimen_fiscal = self.productor.regimen_fiscal
        if self.tipo_cambio_id:
            self.tipo_cambio_valor = self.tipo_cambio.tc
        elif self.fecha_liq:
            tc = TipoCambio.objects.filter(fecha=self.fecha_liq).first()
            if tc:
                self.tipo_cambio = tc
                self.tipo_cambio_valor = tc.tc
        if self.fecha_de_pago and self.fecha_liq:
            self.dias_transcurridos = (self.fecha_de_pago - self.fecha_liq).days
        tc_val = self.tipo_cambio_valor or Decimal("0")
        if tc_val > 0:
            self.total_deuda_en_dls = self.retencion_deudas_usd + (
                self.retencion_deudas_mxn / tc_val
            )
        else:
            self.total_deuda_en_dls = self.retencion_deudas_usd

        if self.pago is not None:
            if self.moneda == MonedaChoices.DOLARES and tc_val > 0:
                self.total_en_pesos = self.pago * tc_val
            elif self.moneda == MonedaChoices.PESOS:
                self.total_en_pesos = self.pago
        return super().save(*args, **kwargs)

    def clean(self):
        super().clean()
        if self.parent_compra_id:
            if self.parent_compra.parent_compra_id:
                raise ValidationError("No se permite dividir una compra ya dividida.")
            if self.parent_compra_id == self.id:
                raise ValidationError("La compra no puede ser su propio padre.")
            if self.porcentaje_division is None or self.porcentaje_division <= 0:
                raise ValidationError("La division debe tener un porcentaje mayor a 0.")
            siblings_total = (
                self.parent_compra.divisiones.exclude(pk=self.pk).aggregate(
                    total=Sum("porcentaje_division")
                )["total"]
                or Decimal("0")
            )
            if siblings_total + self.porcentaje_division > Decimal("100"):
                raise ValidationError("La suma de divisiones no puede exceder 100%.")
        elif self.porcentaje_division:
            raise ValidationError(
                "Solo las compras divididas pueden tener porcentaje de division."
            )

    @property
    def captura_completa(self):
        return bool(
            self.numero_compra
            and self.fecha_liq
            and self.productor_id
            and self.pacas is not None
            and self.compra_en_libras is not None
        )

    @property
    def es_division(self):
        return self.parent_compra_id is not None

    @property
    def total_porcentaje_dividido(self):
        if self.es_division:
            return Decimal("0")
        value = self.divisiones.aggregate(total=Sum("porcentaje_division"))["total"]
        return value or Decimal("0")

    @property
    def porcentaje_disponible_division(self):
        return Decimal("100") - self.total_porcentaje_dividido

    @property
    def total_monto_dividido(self):
        if self.es_division:
            return Decimal("0")
        base = self.compra_en_libras or Decimal("0")
        return (base * self.total_porcentaje_dividido) / Decimal("100")

    @property
    def monto_disponible_division(self):
        base = self.compra_en_libras or Decimal("0")
        return base - self.total_monto_dividido

    @property
    def factura_registrada(self):
        return bool(self.factura and self.uuid_factura)

    @property
    def pago_registrado(self):
        return bool(
            self.fecha_de_pago
            and self.pago is not None
            and self.estatus_de_pago == EstadoPagoChoices.PAGADO
        )

    @property
    def flujo_codigo(self):
        if self.es_division:
            if not self.factura_registrada:
                return "facturas"
            if not self.pago_registrado:
                return "pago"
            return "completo"

        if not self.captura_completa:
            return "captura"
        if not self.anticipos_revisados:
            return "anticipos"
        if not self.deudas_revisadas:
            return "deudas"
        if not self.factura_registrada:
            return "facturas"
        if not self.pago_registrado:
            return "pago"
        return "completo"

    @property
    def flujo_label(self):
        labels = {
            "captura": "Completar captura",
            "anticipos": "Revisar anticipos",
            "deudas": "Revisar deudas",
            "facturas": "Solicitar/registrar facturas",
            "pago": "Registrar pago",
            "completo": "Completado",
        }
        return labels[self.flujo_codigo]

    @property
    def flujo_progress(self):
        steps = {
            "captura": 20,
            "anticipos": 40,
            "deudas": 60,
            "facturas": 80,
            "pago": 95,
            "completo": 100,
        }
        return steps[self.flujo_codigo]


class AplicacionAnticipo(TimestampedModel):
    anticipo = models.ForeignKey(
        Anticipo, on_delete=models.PROTECT, related_name="aplicaciones"
    )
    compra = models.ForeignKey(
        Compra, on_delete=models.PROTECT, related_name="aplicaciones_anticipo"
    )
    fecha = models.DateField()
    monto_aplicado = models.DecimalField(max_digits=16, decimal_places=4, default=0)

    class Meta:
        ordering = ["-fecha", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["anticipo", "compra", "fecha"],
                name="unique_aplicacion_anticipo_compra_fecha",
            )
        ]

    def __str__(self):
        return f"Aplicacion {self.id}: Anticipo {self.anticipo_id} -> Compra {self.compra_id}"

    def clean(self):
        super().clean()
        if self.anticipo.productor_id != self.compra.productor_id:
            raise ValidationError(
                "El anticipo y la compra deben pertenecer al mismo productor."
            )
        if self.monto_aplicado <= 0:
            raise ValidationError("El monto aplicado debe ser mayor a cero.")
        prev_value = (
            AplicacionAnticipo.objects.get(pk=self.pk).monto_aplicado if self.pk else 0
        )
        disponible = self.anticipo.saldo_disponible + prev_value
        if self.monto_aplicado > disponible:
            raise ValidationError("El monto aplicado excede el saldo del anticipo.")
        saldo_compra = self.compra.saldo_por_pagar + prev_value
        if self.monto_aplicado > saldo_compra:
            raise ValidationError("El monto aplicado excede el saldo de la compra.")

    def save(self, *args, **kwargs):
        self.full_clean()
        result = super().save(*args, **kwargs)
        self.anticipo.save(update_fields=["pendiente_aplicar", "updated_at"])
        return result


class DocumentoCompra(TimestampedModel):
    compra = models.ForeignKey(
        Compra, on_delete=models.CASCADE, related_name="documentos"
    )
    etapa = models.CharField(
        max_length=30,
        choices=DocumentoEtapaChoices.choices,
        default=DocumentoEtapaChoices.OTRO,
    )
    descripcion = models.CharField(max_length=200, blank=True)
    archivo = models.FileField(upload_to="compras_documentos/%Y/%m/")

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Documento compra {self.compra_id} ({self.etapa})"

from decimal import Decimal, ROUND_HALF_UP
from django.db import IntegrityError

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


class WorkflowStateChoices(models.TextChoices):
    IMPORTED = "IMPORTED", _("Imported")
    DEBT_CALCULATED = "DEBT_CALCULATED", _("Debt Calculated")
    WAITING_INVOICE = "WAITING_INVOICE", _("Waiting Invoice")
    INVOICE_RECEIVED = "INVOICE_RECEIVED", _("Invoice Received")
    INVOICE_VALID = "INVOICE_VALID", _("Invoice Valid")
    INVOICE_BLOCKED = "INVOICE_BLOCKED", _("Invoice Blocked")
    WAITING_BANK_CONFIRMATION = "WAITING_BANK_CONFIRMATION", _("Waiting Bank Confirmation")
    READY_TO_PAY = "READY_TO_PAY", _("Ready To Pay")
    PAID = "PAID", _("Paid")
    ARCHIVED = "ARCHIVED", _("Archived")


class Productor(TimestampedModel):
    codigo = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=200)
    regimen_fiscal = models.CharField(max_length=120, blank=True)
    regimen_fiscal_codigo = models.CharField(max_length=3, blank=True, db_index=True)
    microsip_cliente_nombre = models.CharField(max_length=240, blank=True)
    microsip_cliente_id = models.CharField(max_length=40, blank=True)
    contador = models.ForeignKey(
        "Contador", on_delete=models.SET_NULL, null=True, blank=True, related_name="productores"
    )
    cuenta_productor = models.CharField(max_length=80, blank=True)
    telefono = models.CharField(max_length=30, blank=True)
    correo_facturas = models.EmailField(blank=True)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.codigo} - {self.nombre}"

    @staticmethod
    def _next_codigo():
        last = Productor.objects.order_by("-id").first()
        next_num = (last.id + 1) if last else 1
        return f"PRD-{next_num:05d}"

    def save(self, *args, **kwargs):
        if not self.codigo:
            for _ in range(5):
                self.codigo = self._next_codigo()
                try:
                    return super().save(*args, **kwargs)
                except IntegrityError:
                    self.codigo = ""
            raise RuntimeError("No se pudo generar codigo automatico para productor.")
        return super().save(*args, **kwargs)


class ProductorCuentaBancaria(TimestampedModel):
    productor = models.ForeignKey("Productor", on_delete=models.CASCADE, related_name="cuentas_bancarias")
    banco = models.CharField(max_length=120, blank=True)
    titular = models.CharField(max_length=200, blank=True)
    cuenta = models.CharField(max_length=80)
    clabe = models.CharField(max_length=30, blank=True)
    caratula_archivo = models.FileField(upload_to="compras_documentos/%Y/%m/", blank=True)
    activa = models.BooleanField(default=True)
    predeterminada = models.BooleanField(default=False)

    class Meta:
        ordering = ["-predeterminada", "banco", "cuenta"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.predeterminada:
            ProductorCuentaBancaria.objects.filter(productor=self.productor).exclude(pk=self.pk).update(predeterminada=False)

    def __str__(self):
        bank = f"{self.banco} " if self.banco else ""
        return f"{bank}{self.cuenta}".strip()


class FacturadorCuentaBancaria(TimestampedModel):
    facturador = models.ForeignKey("PersonaFactura", on_delete=models.CASCADE, related_name="cuentas_bancarias")
    banco = models.CharField(max_length=120, blank=True)
    titular = models.CharField(max_length=200, blank=True)
    cuenta = models.CharField(max_length=80)
    clabe = models.CharField(max_length=30, blank=True)
    caratula_archivo = models.FileField(upload_to="compras_documentos/%Y/%m/", blank=True)
    activa = models.BooleanField(default=True)
    predeterminada = models.BooleanField(default=False)

    class Meta:
        ordering = ["-predeterminada", "banco", "cuenta"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.predeterminada:
            FacturadorCuentaBancaria.objects.filter(facturador=self.facturador).exclude(pk=self.pk).update(predeterminada=False)

    def __str__(self):
        bank = f"{self.banco} " if self.banco else ""
        return f"{bank}{self.cuenta}".strip()


class BeneficiaryValidationException(TimestampedModel):
    productor = models.ForeignKey("Productor", on_delete=models.CASCADE, related_name="beneficiary_exceptions")
    facturador = models.ForeignKey("PersonaFactura", on_delete=models.SET_NULL, null=True, blank=True, related_name="beneficiary_exceptions")
    emisor_rfc = models.CharField(max_length=20, blank=True, default="")
    emisor_nombre = models.CharField(max_length=200, blank=True, default="")
    account_holder = models.CharField(max_length=200)
    reason = models.CharField(max_length=240, blank=True)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.productor.nombre} · {self.account_holder}"


class Contador(TimestampedModel):
    nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=40, blank=True)
    email = models.EmailField(blank=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


class PersonaFactura(TimestampedModel):
    class ResicoPolicyChoices(models.TextChoices):
        AUTO = "AUTO", _("Auto (retención 1.25% o leyenda)")
        RETENCION_125 = "RETENCION_125", _("Requiere retención ISR 1.25%")
        EXENCION_LEYENDA = "EXENCION_LEYENDA", _("Requiere leyenda de exención")

    nombre = models.CharField(max_length=200, unique=True)
    regimen_fiscal = models.CharField(max_length=120, blank=True)
    regimen_fiscal_codigo = models.CharField(max_length=3, blank=True, db_index=True)
    rfc = models.CharField(max_length=20, blank=True)
    contador = models.ForeignKey(
        "Contador", on_delete=models.SET_NULL, null=True, blank=True, related_name="entidades_facturadoras"
    )
    resico_policy = models.CharField(
        max_length=30,
        choices=ResicoPolicyChoices.choices,
        default=ResicoPolicyChoices.AUTO,
    )
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre


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
    persona_facturadora = models.ForeignKey(
        PersonaFactura,
        on_delete=models.PROTECT,
        related_name="anticipos",
        null=True,
        blank=True,
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
        if self.persona_facturadora and not self.persona_que_factura:
            self.persona_que_factura = self.persona_facturadora.nombre

        if not self.numero_anticipo:
            for _ in range(5):
                last = Anticipo.objects.order_by("-numero_anticipo").first()
                self.numero_anticipo = (last.numero_anticipo + 1) if last and last.numero_anticipo else 1
                try:
                    self.actualizar_estado()
                    return super().save(*args, **kwargs)
                except IntegrityError:
                    self.numero_anticipo = None
            raise RuntimeError("No se pudo generar numero automatico de anticipo.")
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
    facturador = models.ForeignKey(
        PersonaFactura, on_delete=models.SET_NULL, null=True, blank=True, related_name="compras_facturadas"
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
    expected_moneda = models.CharField(max_length=10, blank=True, default="")
    expected_metodo_pago = models.CharField(max_length=10, blank=True, default="")
    expected_forma_pago = models.CharField(max_length=10, blank=True, default="")
    expected_uso_cfdi = models.CharField(max_length=10, blank=True, default="")
    expected_rfc_receptor = models.CharField(max_length=20, blank=True, default="")
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
    cancelada = models.BooleanField(default=False)
    motivo_cancelacion = models.TextField(blank=True)
    bank_account_confirmed = models.BooleanField(default=False)
    bank_confirmed_at = models.DateTimeField(null=True, blank=True)
    bank_confirmation_source = models.CharField(max_length=40, blank=True)
    bank_confirmation_notes = models.TextField(blank=True)
    workflow_state = models.CharField(
        max_length=40,
        choices=WorkflowStateChoices.choices,
        default=WorkflowStateChoices.IMPORTED,
        db_index=True,
    )

    class Meta:
        ordering = ["-fecha_liq", "-id"]

    def __str__(self):
        return f"Compra {self.numero_compra} - {self.productor.codigo}"

    @property
    def base_pago(self):
        return self.total_pagado_vigente

    @property
    def total_aplicado_anticipos(self):
        if not self.pk:
            return Decimal("0")
        value = self.aplicaciones_anticipo.aggregate(total=Sum("monto_aplicado"))["total"]
        return value or Decimal("0")

    @property
    def saldo_por_pagar(self):
        objetivo = self.compra_en_libras or Decimal("0")
        deuda = self.total_deuda_en_dls or Decimal("0")
        resico = self.retencion_resico or Decimal("0")
        return objetivo - self.total_pagado_vigente - self.total_aplicado_anticipos - deuda - resico

    @property
    def total_pagado_registrado(self):
        total = Decimal("0")
        for pago in self.pagos_registrados.all():
            total += pago.monto_en_dolares
        return total

    @property
    def total_pagado_vigente(self):
        # Prioridad al nuevo esquema de pagos independientes.
        if self.pagos_registrados.exists():
            return self.total_pagado_registrado
        # Compatibilidad legacy: solo considerar pago manual si estatus no es pendiente.
        if self.estatus_de_pago in (EstadoPagoChoices.PAGADO, EstadoPagoChoices.PARCIAL):
            return self.pago or Decimal("0")
        return Decimal("0")

    def actualizar_estatus_pago_desde_registros(self):
        if not self.pagos_registrados.exists():
            self.estatus_de_pago = EstadoPagoChoices.PENDIENTE
            self.pago = Decimal("0")
            return
        total_pagado = self.total_pagado_registrado
        self.pago = total_pagado
        objetivo = self.compra_en_libras or Decimal("0")
        if total_pagado <= 0:
            self.estatus_de_pago = EstadoPagoChoices.PENDIENTE
        elif objetivo > 0 and total_pagado < objetivo:
            self.estatus_de_pago = EstadoPagoChoices.PARCIAL
        else:
            self.estatus_de_pago = EstadoPagoChoices.PAGADO

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
        tc_val = Decimal(str(self.tipo_cambio_valor or "0"))
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
        if self.pagos_registrados.exists():
            return self.estatus_de_pago == EstadoPagoChoices.PAGADO
        return bool(
            self.fecha_de_pago
            and self.pago is not None
            and self.estatus_de_pago == EstadoPagoChoices.PAGADO
        )

    @property
    def flujo_codigo(self):
        if not self.captura_completa:
            return "captura"
        if not self.anticipos_revisados:
            return "anticipos"
        if not self.deudas_revisadas:
            return "deudas"
        if not self.solicitud_factura_enviada:
            return "solicitar_factura"
        if not self.uuid_factura:
            return "revisar_factura"
        # Si ya no hay saldo por pagar (por pagos/deducciones), marcar flujo completo.
        if (self.saldo_por_pagar or Decimal("0")) <= Decimal("0"):
            return "completo"
        if not self.pago_registrado:
            return "pago"
        return "completo"

    @property
    def flujo_label(self):
        labels = {
            "captura": "Completar captura",
            "anticipos": "Revisar anticipos",
            "deudas": "Revisar deudas",
            "solicitar_factura": "Solicitar factura",
            "revisar_factura": "Revisar factura",
            "pago": "Registrar pago",
            "completo": "Completado",
        }
        return labels[self.flujo_codigo]

    @property
    def flujo_progress(self):
        steps = {
            "captura": 15,
            "anticipos": 30,
            "deudas": 50,
            "solicitar_factura": 70,
            "revisar_factura": 85,
            "pago": 95,
            "completo": 100,
        }
        return steps[self.flujo_codigo]

    @property
    def flujo_step_default(self):
        # "completo" no tiene formulario propio; para ajustes abrimos Pago.
        return "pago" if self.flujo_codigo == "completo" else self.flujo_codigo

    @property
    def uuid_factura_faltante(self):
        # Recordatorio no bloqueante: se solicito factura pero aun no llega UUID.
        return bool(self.solicitud_factura_enviada and not self.uuid_factura)

    def set_workflow_state(self, new_state: str, reason: str = "", actor: str = "system"):
        previous = self.workflow_state
        if previous == new_state:
            return
        self.workflow_state = new_state
        self.save(update_fields=["workflow_state", "updated_at"])
        WorkflowEvent.objects.create(
            compra=self,
            from_state=previous,
            to_state=new_state,
            actor=actor,
            reason=reason,
        )


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


class PagoCompra(TimestampedModel):
    compra = models.ForeignKey(
        Compra, on_delete=models.CASCADE, related_name="pagos_registrados"
    )
    fecha_pago = models.DateField(default=timezone.localdate)
    monto = models.DecimalField(max_digits=16, decimal_places=4)
    moneda = models.CharField(
        max_length=20, choices=MonedaChoices.choices, default=MonedaChoices.DOLARES
    )
    cuenta_de_pago = models.CharField(max_length=120, blank=True)
    metodo_de_pago = models.CharField(max_length=60, blank=True)
    referencia = models.CharField(max_length=100, blank=True)
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ["-fecha_pago", "-id"]

    def __str__(self):
        return f"Pago {self.id} compra {self.compra_id}"

    @property
    def monto_en_dolares(self):
        if self.moneda == MonedaChoices.DOLARES:
            return self.monto
        if self.moneda == MonedaChoices.PESOS:
            tc_pactado = Decimal(str(self.compra.tipo_cambio_valor or "0"))
            if tc_pactado > 0:
                return (self.monto / tc_pactado).quantize(
                    Decimal("0.0001"), rounding=ROUND_HALF_UP
                )
            return Decimal("0")
        return self.monto

    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        self.compra.actualizar_estatus_pago_desde_registros()
        self.compra.save(update_fields=["pago", "estatus_de_pago", "updated_at"])
        return result

    def delete(self, *args, **kwargs):
        compra = self.compra
        result = super().delete(*args, **kwargs)
        compra.actualizar_estatus_pago_desde_registros()
        compra.save(update_fields=["pago", "estatus_de_pago", "updated_at"])
        return result


class DebtSnapshot(TimestampedModel):
    compra = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name="debt_snapshots")
    fuente = models.CharField(max_length=60, default="microsip")
    total_usd = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    total_mxn = models.DecimalField(max_digits=16, decimal_places=4, default=0)
    detalle_json = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class Deduccion(TimestampedModel):
    compra = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name="deducciones")
    concepto = models.CharField(max_length=120)
    monto = models.DecimalField(max_digits=16, decimal_places=4)
    moneda = models.CharField(max_length=20, choices=MonedaChoices.choices, default=MonedaChoices.DOLARES)
    fuente = models.CharField(max_length=30, default="manual")
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class XmlValidationConfig(TimestampedModel):
    class IvaPolicyChoices(models.TextChoices):
        ANY = "ANY", _("Sin validar")
        IVA_0 = "0", _("0%")
        IVA_16 = "16", _("16%")

    key = models.CharField(max_length=32, unique=True, default="default")
    global_rfc_receptor = models.CharField(max_length=20, blank=True, default="")
    global_regimen_fiscal_receptor = models.CharField(max_length=10, blank=True, default="")
    global_codigo_fiscal_receptor = models.CharField(max_length=10, blank=True, default="")
    global_nombre_receptor = models.CharField(max_length=200, blank=True, default="")
    global_efecto_comprobante = models.CharField(max_length=4, blank=True, default="")
    global_impuesto_trasladado = models.CharField(
        max_length=8,
        choices=IvaPolicyChoices.choices,
        default=IvaPolicyChoices.ANY,
    )

    class Meta:
        verbose_name = "Configuración validación XML"
        verbose_name_plural = "Configuración validación XML"

    @classmethod
    def get_default(cls):
        obj, _ = cls.objects.get_or_create(key="default")
        return obj


class EmailTemplate(TimestampedModel):
    code = models.CharField(max_length=40, unique=True)
    nombre = models.CharField(max_length=120)
    scenario = models.CharField(max_length=40, blank=True, default="GENERAL")
    subject_template = models.CharField(max_length=240)
    body_template = models.TextField()
    is_default = models.BooleanField(default=False)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]


class EmailOutboxLog(TimestampedModel):
    compra = models.ForeignKey("Compra", on_delete=models.CASCADE, related_name="email_logs")
    to_email = models.EmailField()
    subject = models.CharField(max_length=240)
    body = models.TextField(blank=True)
    template_code = models.CharField(max_length=40, blank=True, default="")
    provider = models.CharField(max_length=40, default="smtp")
    status = models.CharField(max_length=20, default="SENT")
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class WorkflowEvent(TimestampedModel):
    compra = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name="workflow_events")
    from_state = models.CharField(max_length=40, blank=True)
    to_state = models.CharField(max_length=40)
    actor = models.CharField(max_length=120, default="system")
    reason = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class InvoiceValidationResult(TimestampedModel):
    compra = models.ForeignKey(Compra, on_delete=models.CASCADE, related_name="invoice_validations")
    uuid = models.CharField(max_length=80, blank=True)
    rfc_emisor = models.CharField(max_length=20, blank=True)
    rfc_receptor = models.CharField(max_length=20, blank=True)
    uso_cfdi = models.CharField(max_length=20, blank=True)
    metodo_pago = models.CharField(max_length=20, blank=True)
    moneda = models.CharField(max_length=20, blank=True)
    iva_tasa = models.CharField(max_length=20, blank=True)
    isr_retencion = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    valid = models.BooleanField(default=False)
    blocked_reason = models.TextField(blank=True)
    raw_result = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class ImportRun(TimestampedModel):
    source_name = models.CharField(max_length=255)
    dry_run = models.BooleanField(default=False)
    created_count = models.IntegerField(default=0)
    duplicate_count = models.IntegerField(default=0)
    division_count = models.IntegerField(default=0)
    error_count = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]


class ImportRowLog(TimestampedModel):
    run = models.ForeignKey(ImportRun, on_delete=models.CASCADE, related_name="rows")
    row_number = models.IntegerField()
    status = models.CharField(max_length=20, default="ok")
    message = models.TextField(blank=True)
    compra_numero = models.IntegerField(null=True, blank=True)
    productor_nombre = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["row_number", "id"]

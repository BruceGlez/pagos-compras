from django import forms

from .models import (
    Anticipo,
    AplicacionAnticipo,
    Compra,
    DocumentoCompra,
    PagoCompra,
    Productor,
    TipoCambio,
)


class DateInput(forms.DateInput):
    input_type = "date"


class BootstrapFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = "form-check-input" if isinstance(field.widget, forms.CheckboxInput) else "form-control"
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} {css}".strip()


class ProductorForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Productor
        fields = [
            "nombre",
            "regimen_fiscal",
            "cuenta_productor",
            "telefono",
            "correo_facturas",
            "activo",
            "notas",
        ]


class TipoCambioForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = TipoCambio
        fields = ["fecha", "tc", "fuente"]
        widgets = {"fecha": DateInput()}


class AnticipoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Anticipo
        fields = [
            "fecha_pago",
            "productor",
            "persona_que_factura",
            "factura",
            "monto_anticipo",
            "moneda",
            "estado",
            "uuid_nota_credito",
            "total_en_pesos",
            "cuenta_de_pago",
            "cuenta",
            "contador",
            "correo_para_facturas",
            "telefono",
            "observaciones",
        ]
        widgets = {"fecha_pago": DateInput()}


class CompraForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = [
            "numero_compra",
            "intereses",
            "fecha_de_pago",
            "fecha_liq",
            "regimen_fiscal",
            "productor",
            "uuid_factura",
            "factura",
            "pacas",
            "compra_en_libras",
            "anticipo",
            "pago",
            "dias_transcurridos",
            "tipo_cambio",
            "retencion_deudas_usd",
            "retencion_deudas_mxn",
            "total_deuda_en_dls",
            "retencion_resico",
            "saldo_pendiente",
            "estatus_factura",
            "vencimiento",
            "cuenta_de_pago",
            "metodo_de_pago",
            "moneda",
            "total_en_pesos",
            "cuenta_productor",
            "estatus_de_pago",
            "contador",
            "correo",
            "estatus_rep",
            "uuid_ppd",
        ]
        widgets = {"fecha_de_pago": DateInput(), "fecha_liq": DateInput(), "vencimiento": DateInput()}
        labels = {
            "productor": "PRODUCTOR (base)",
            "uuid_factura": "UUID FACTURA",
            "factura": "FACTURA (quien factura)",
            "compra_en_libras": "Total en DLS",
        }


class AplicacionAnticipoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = AplicacionAnticipo
        fields = ["anticipo", "compra", "fecha", "monto_aplicado"]
        widgets = {"fecha": DateInput()}


class CompraOperativaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = [
            "numero_compra",
            "fecha_de_pago",
            "fecha_liq",
            "productor",
            "uuid_factura",
            "factura",
            "pacas",
            "compra_en_libras",
            "anticipo",
            "pago",
            "dias_transcurridos",
            "tipo_cambio",
            "retencion_deudas_usd",
            "retencion_deudas_mxn",
            "total_deuda_en_dls",
            "retencion_resico",
            "saldo_pendiente",
            "vencimiento",
            "cuenta_de_pago",
            "metodo_de_pago",
            "moneda",
            "total_en_pesos",
            "cuenta_productor",
            "estatus_factura",
            "estatus_de_pago",
            "estatus_rep",
            "uuid_ppd",
        ]
        widgets = {
            "fecha_de_pago": DateInput(),
            "fecha_liq": DateInput(),
            "vencimiento": DateInput(),
        }
        labels = {
            "productor": "PRODUCTOR (base)",
            "uuid_factura": "UUID FACTURA",
            "factura": "FACTURA (quien factura)",
            "compra_en_libras": "Total en DLS",
        }


class CompraFiltroForm(BootstrapFormMixin, forms.Form):
    q = forms.CharField(required=False, label="Buscar")
    productor = forms.ModelChoiceField(
        queryset=Productor.objects.filter(activo=True).order_by("nombre"),
        required=False,
        label="Productor base",
    )
    fecha_desde = forms.DateField(required=False, widget=DateInput())
    fecha_hasta = forms.DateField(required=False, widget=DateInput())
    estatus_de_pago = forms.ChoiceField(
        required=False,
        choices=[("", "Todos")] + list(Compra._meta.get_field("estatus_de_pago").choices),
    )


class CompraFlujo1Form(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = [
            "numero_compra",
            "fecha_liq",
            "productor",
            "pacas",
            "compra_en_libras",
        ]
        widgets = {"fecha_liq": DateInput()}
        labels = {
            "compra_en_libras": "Total en DLS",
            "productor": "Productor (con regimen fiscal en catalogo)",
        }


class CompraFlujo2Form(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["tipo_cambio", "tipo_cambio_valor"]
        labels = {
            "tipo_cambio": "TC diario",
            "tipo_cambio_valor": "TC aplicado/pactado",
        }


class CompraFlujoAnticiposForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["anticipo", "anticipos_revisados"]
        labels = {
            "anticipo": "Anticipo aplicado en compra",
            "anticipos_revisados": "Anticipos revisados",
        }


class CompraFlujo3Form(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = [
            "retencion_deudas_usd",
            "retencion_deudas_mxn",
            "total_deuda_en_dls",
            "deudas_revisadas",
        ]
        labels = {
            "retencion_deudas_usd": "Retencion de USD",
            "retencion_deudas_mxn": "Retencion de MXN",
            "total_deuda_en_dls": "Total de deudas en DLS (calculado)",
            "deudas_revisadas": "Deudas revisadas",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["total_deuda_en_dls"].disabled = True


class CompraFacturasForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = [
            "solicitud_factura_enviada",
            "fecha_solicitud_factura",
            "factura",
            "uuid_factura",
            "contador",
            "correo",
            "estatus_factura",
        ]
        widgets = {"fecha_solicitud_factura": DateInput()}
        labels = {
            "solicitud_factura_enviada": "Solicitud enviada",
            "fecha_solicitud_factura": "Fecha solicitud",
            "factura": "Nombre de quien factura",
            "uuid_factura": "UUID de Factura",
            "correo": "Correo del contador",
        }


class CompraFlujo5Form(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = [
            "fecha_de_pago",
            "pago",
            "cuenta_de_pago",
            "metodo_de_pago",
            "moneda",
            "total_en_pesos",
            "cuenta_productor",
            "estatus_de_pago",
        ]
        widgets = {"fecha_de_pago": DateInput()}
        labels = {
            "cuenta_de_pago": "Cuenta de la que se pago",
            "cuenta_productor": "Cuenta a la que se pago",
            "total_en_pesos": "Total en MXN (calculado con TC)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["total_en_pesos"].disabled = True


class CompraSolicitarFacturaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["solicitud_factura_enviada", "fecha_solicitud_factura", "contador", "correo"]
        widgets = {"fecha_solicitud_factura": DateInput()}
        labels = {
            "solicitud_factura_enviada": "Solicitud enviada",
            "fecha_solicitud_factura": "Fecha solicitud",
            "correo": "Correo contador",
        }


class CompraRegistrarFacturaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["factura", "uuid_factura", "estatus_factura", "contador", "correo"]
        labels = {
            "factura": "Nombre de quien factura",
            "uuid_factura": "UUID de factura",
            "correo": "Correo contador",
        }


class CompraExpedienteForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["expediente_completo"]
        labels = {"expediente_completo": "Expediente completo"}


class DocumentoCompraForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = DocumentoCompra
        fields = ["etapa", "descripcion", "archivo"]


class PagoCompraForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PagoCompra
        fields = [
            "fecha_pago",
            "monto",
            "moneda",
            "cuenta_de_pago",
            "metodo_de_pago",
            "referencia",
            "notas",
        ]
        widgets = {"fecha_pago": DateInput()}
        labels = {
            "monto": "Monto del pago",
            "cuenta_de_pago": "Cuenta de la que se pago",
            "metodo_de_pago": "Metodo de pago",
        }


class CompraDivisionEstadoForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["division_revisada"]
        labels = {"division_revisada": "Division revisada/completa"}


class CompraDivisionCreateForm(BootstrapFormMixin, forms.Form):
    porcentaje_division = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=0.01, required=False
    )
    monto_division = forms.DecimalField(
        max_digits=16, decimal_places=4, min_value=0.0001, required=False
    )
    factura = forms.CharField(required=False, max_length=200)
    uuid_factura = forms.CharField(required=False, max_length=80)

    def __init__(self, *args, **kwargs):
        self.compra = kwargs.pop("compra")
        super().__init__(*args, **kwargs)
        self.fields["porcentaje_division"].label = "Porcentaje a dividir (opcional)"
        self.fields["monto_division"].label = "Monto a dividir (opcional)"
        self.fields["factura"].label = "Quien factura (opcional)"
        self.fields["uuid_factura"].label = "UUID factura (opcional)"
        self.fields["porcentaje_division"].widget.attrs["max"] = str(
            self.compra.porcentaje_disponible_division
        )
        self.fields["monto_division"].widget.attrs["max"] = str(
            self.compra.monto_disponible_division
        )

    def clean(self):
        cleaned = super().clean()
        porcentaje = cleaned.get("porcentaje_division")
        monto = cleaned.get("monto_division")
        base = self.compra.compra_en_libras or 0

        if not porcentaje and not monto:
            raise forms.ValidationError("Captura porcentaje o monto para dividir.")
        if monto:
            if base <= 0:
                raise forms.ValidationError("La compra base no tiene monto para dividir.")
            if monto > self.compra.monto_disponible_division:
                raise forms.ValidationError("El monto excede el disponible de la compra.")
            porcentaje = (monto * 100) / base
        elif porcentaje:
            if porcentaje > self.compra.porcentaje_disponible_division:
                raise forms.ValidationError("La division excede el disponible de la compra.")
            monto = (base * porcentaje) / 100
        cleaned["porcentaje_division"] = porcentaje
        cleaned["monto_division"] = monto
        return cleaned

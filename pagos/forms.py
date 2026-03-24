from string import Formatter

from django import forms
from django.core.validators import validate_email

from .catalogs import (
    SAT_FORMAS_PAGO,
    SAT_METODOS_PAGO,
    SAT_REGIMENES_FISCALES,
    SAT_REGIMENES_MAP,
    SAT_USOS_CFDI,
)
from .models import (
    Anticipo,
    AplicacionAnticipo,
    Compra,
    Contador,
    Deduccion,
    DocumentoCompra,
    DocumentoTipoChoices,
    DocumentoEtapaChoices,
    PagoCompra,
    PersonaFactura,
    Productor,
    ProductorCuentaBancaria,
    FacturadorCuentaBancaria,
    BeneficiaryValidationException,
    EmailTemplate,
    XmlValidationConfig,
    TipoCambio,
    WorkflowStateChoices,
)


def _sync_facturador_cuentas_from_productor(productor: Productor, facturador: PersonaFactura):
    if not productor or not facturador:
        return 0
    created = 0
    for acc in ProductorCuentaBancaria.objects.filter(productor=productor):
        exists = FacturadorCuentaBancaria.objects.filter(
            facturador=facturador,
            cuenta=acc.cuenta,
            clabe=acc.clabe,
        ).exists()
        if exists:
            continue
        FacturadorCuentaBancaria.objects.create(
            facturador=facturador,
            banco=acc.banco,
            titular=acc.titular,
            cuenta=acc.cuenta,
            clabe=acc.clabe,
            caratula_archivo=acc.caratula_archivo,
            activa=acc.activa,
            predeterminada=acc.predeterminada,
        )
        created += 1
    return created


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
    regimen_fiscal = forms.ChoiceField(
        required=False,
        choices=[("", "Selecciona régimen fiscal SAT...")] + SAT_REGIMENES_FISCALES,
        label="Régimen fiscal (SAT)",
    )

    class Meta:
        model = Productor
        fields = [
            "nombre",
            "rfc",
            "regimen_fiscal",
            "microsip_cliente_nombre",
            "microsip_cliente_id",
            "contador",
            "cuenta_productor",
            "telefono",
            "correo_facturas",
            "activo",
            "notas",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            code = self.instance.regimen_fiscal_codigo or ""
            if not code and self.instance.regimen_fiscal:
                maybe = self.instance.regimen_fiscal.strip().split(" ")[0]
                if maybe.isdigit() and len(maybe) == 3:
                    code = maybe
            if code:
                self.initial["regimen_fiscal"] = code

    def save(self, commit=True):
        obj = super().save(commit=False)
        code = self.cleaned_data.get("regimen_fiscal", "") or ""
        obj.regimen_fiscal_codigo = code
        obj.regimen_fiscal = SAT_REGIMENES_MAP.get(code, code)
        obj.rfc = (obj.rfc or "").strip().upper()
        if commit:
            obj.save()
        return obj


class ProductorCuentaBancariaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ProductorCuentaBancaria
        fields = ["banco", "titular", "cuenta", "clabe", "caratula_archivo", "activa", "predeterminada"]
        labels = {
            "predeterminada": "Usar como cuenta predeterminada",
            "caratula_archivo": "Carátula bancaria (PDF/imagen)",
        }


class FacturadorCuentaBancariaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = FacturadorCuentaBancaria
        fields = ["banco", "titular", "cuenta", "clabe", "caratula_archivo", "activa", "predeterminada"]
        labels = {
            "predeterminada": "Usar como cuenta predeterminada",
            "caratula_archivo": "Carátula bancaria (PDF/imagen)",
        }


class BeneficiaryValidationExceptionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = BeneficiaryValidationException
        fields = ["productor", "facturador", "emisor_rfc", "emisor_nombre", "account_holder", "reason", "active"]


class ContadorForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Contador
        fields = ["nombre", "telefono", "email", "emails_adicionales", "activo"]
        labels = {
            "email": "Correo principal",
            "emails_adicionales": "Correos adicionales (separados por coma o salto de línea)",
        }

    def clean_email(self):
        email = (self.cleaned_data.get("email") or "").strip()
        if not email:
            raise forms.ValidationError("El correo principal es obligatorio para registrar un contador.")
        validate_email(email)
        return email

    def clean_emails_adicionales(self):
        raw = (self.cleaned_data.get("emails_adicionales") or "").strip()
        if not raw:
            return ""

        chunks = []
        for part in raw.replace(";", ",").replace("\n", ",").split(","):
            mail = part.strip()
            if not mail:
                continue
            validate_email(mail)
            chunks.append(mail)

        # Deduplicar preservando orden
        out = []
        seen = set()
        for m in chunks:
            k = m.lower()
            if k in seen:
                continue
            seen.add(k)
            out.append(m)
        return ", ".join(out)


class EmailTemplateForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = EmailTemplate
        fields = ["code", "nombre", "scenario", "subject_template", "body_template", "is_default", "activo"]

    def clean(self):
        cleaned = super().clean()
        subject_tpl = cleaned.get("subject_template") or ""
        body_tpl = cleaned.get("body_template") or ""

        allowed_fields = {
            "productor_nombre",
            "facturador_nombre",
            "productor_rfc",
            "receptor_rfc",
            "compra_numero",
            "monto_compra",
            "subtotal_compra",
            "retencion_125",
            "total_con_retencion",
            "moneda_detalle",
            "moneda",
            "monto_compra_usd",
            "monto_compra_mxn",
            "regimen_fiscal",
            "forma_pago",
            "metodo_pago",
            "uso_cfdi",
            "forma_pago_detalle",
            "metodo_pago_detalle",
            "uso_cfdi_detalle",
        }

        found = set()
        fmt = Formatter()
        for tpl in (subject_tpl, body_tpl):
            for _, field_name, _, _ in fmt.parse(tpl):
                if field_name:
                    found.add(field_name)

        invalid = sorted([f for f in found if f not in allowed_fields])
        if invalid:
            raise forms.ValidationError(
                "Placeholder(s) inválido(s): "
                + ", ".join(invalid)
                + ". Usa solo variables permitidas para solicitud de factura."
            )

        return cleaned


class XmlValidationConfigForm(BootstrapFormMixin, forms.ModelForm):
    global_efecto_comprobante = forms.ChoiceField(
        required=False,
        choices=[("", "(Sin validar)")] + [("I", "I Ingreso"), ("E", "E Egreso"), ("T", "T Traslado"), ("N", "N Nómina"), ("P", "P Pago")],
        label="Efecto comprobante",
    )

    class Meta:
        model = XmlValidationConfig
        fields = [
            "global_rfc_receptor",
            "global_regimen_fiscal_receptor",
            "global_codigo_fiscal_receptor",
            "global_nombre_receptor",
            "global_efecto_comprobante",
            "global_impuesto_trasladado",
        ]
        labels = {
            "global_rfc_receptor": "RFC receptor",
            "global_regimen_fiscal_receptor": "Régimen fiscal receptor",
            "global_codigo_fiscal_receptor": "Código fiscal receptor",
            "global_nombre_receptor": "Nombre receptor",
            "global_efecto_comprobante": "Efecto comprobante",
            "global_impuesto_trasladado": "Impuesto trasladado",
        }

    def clean(self):
        cleaned = super().clean()
        for k in [
            "global_rfc_receptor",
            "global_regimen_fiscal_receptor",
            "global_codigo_fiscal_receptor",
            "global_nombre_receptor",
            "global_efecto_comprobante",
        ]:
            cleaned[k] = (cleaned.get(k) or "").strip().upper()
        return cleaned


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
            "persona_facturadora",
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
    workflow_state = forms.ChoiceField(
        required=False,
        label="Estado workflow",
        choices=[("", "Todos")] + list(WorkflowStateChoices.choices),
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

    def clean(self):
        cleaned = super().clean()
        tc_val = cleaned.get("tipo_cambio_valor")
        # Si se captura TC pactado manual, respetarlo y evitar overwrite desde TC diario.
        if tc_val not in (None, ""):
            cleaned["tipo_cambio"] = None
        return cleaned


class CompraFlujoAnticiposForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["anticipos_revisados"]
        labels = {
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
            "facturador",
            "uuid_factura",
            "contador",
            "correo",
            "estatus_factura",
        ]
        widgets = {"fecha_solicitud_factura": DateInput()}
        labels = {
            "solicitud_factura_enviada": "Solicitud enviada",
            "fecha_solicitud_factura": "Fecha solicitud",
            "facturador": "Entidad que emitirá la factura (RFC)",
            "uuid_factura": "UUID de Factura",
            "correo": "Correo del contador",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["facturador"].queryset = PersonaFactura.objects.filter(activo=True).order_by("nombre")
        self.fields["facturador"].widget.attrs.update({"class": "form-select js-filterable-select"})

        linked_contador = None
        if self.instance and self.instance.pk and self.instance.facturador_id:
            linked_contador = getattr(self.instance.facturador, "contador", None)

        if linked_contador:
            if not (self.initial.get("contador") or self.instance.contador):
                self.initial["contador"] = linked_contador.nombre
            if not (self.initial.get("correo") or self.instance.correo):
                self.initial["correo"] = linked_contador.email

    def clean(self):
        cleaned = super().clean()
        facturador = cleaned.get("facturador")
        if facturador and facturador.contador:
            cleaned["contador"] = facturador.contador.nombre
            cleaned["correo"] = facturador.contador.email
        return cleaned


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
    factura_source = forms.ChoiceField(
        required=False,
        choices=[("productor", "Mismo productor"), ("facturador", "Diferente entidad")],
        label="¿Quién emitirá la factura?",
        widget=forms.RadioSelect,
        initial="productor",
    )
    productor_facturador = forms.ModelChoiceField(
        required=False,
        queryset=Productor.objects.none(),
        label="O seleccionar desde catálogo de productores",
        help_text="Úsalo cuando la entidad que factura ya existe como productor.",
    )

    expected_moneda = forms.ChoiceField(
        required=False,
        choices=[("", "(Sin validar moneda)")] + [("USD", "USD Dólar americano"), ("MXN", "MXN Peso mexicano")],
        label="Moneda esperada",
    )
    expected_forma_pago = forms.ChoiceField(
        required=False,
        choices=[("", "(Sin validar forma)")] + SAT_FORMAS_PAGO,
        label="Forma de pago esperada",
    )
    expected_metodo_pago = forms.ChoiceField(
        required=False,
        choices=[("", "(Sin validar método)")] + SAT_METODOS_PAGO,
        label="Método de pago esperado",
    )
    expected_uso_cfdi = forms.ChoiceField(
        required=False,
        choices=[("", "(Sin validar uso CFDI)")] + SAT_USOS_CFDI,
        label="Uso CFDI esperado",
    )

    class Meta:
        model = Compra
        fields = [
            "facturador",
            "expected_moneda",
            "expected_forma_pago",
            "expected_metodo_pago",
            "expected_uso_cfdi",
            "contador",
            "correo",
        ]
        labels = {
            "facturador": "Entidad que emitirá la factura (RFC)",
            "expected_moneda": "Moneda esperada",
            "expected_forma_pago": "Forma de pago esperada",
            "expected_metodo_pago": "Método de pago esperado",
            "expected_uso_cfdi": "Uso CFDI esperado",
            "correo": "Correo contador",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["facturador"].queryset = PersonaFactura.objects.filter(activo=True).order_by("nombre")
        self.fields["facturador"].widget.attrs.update({"class": "form-select js-filterable-select"})
        self.fields["productor_facturador"].queryset = Productor.objects.filter(activo=True).order_by("nombre")
        self.fields["productor_facturador"].widget.attrs.update({"class": "form-select js-filterable-select"})
        if self.instance and self.instance.pk:
            self.initial["factura_source"] = "facturador" if self.instance.facturador_id else "productor"

        if not self.initial.get("expected_moneda"):
            if (self.instance.moneda or "") == "DOLARES":
                self.initial["expected_moneda"] = "USD"
            elif (self.instance.moneda or "") == "PESOS":
                self.initial["expected_moneda"] = "MXN"
        if not self.initial.get("expected_metodo_pago"):
            self.initial["expected_metodo_pago"] = "PUE"
        if not self.initial.get("expected_forma_pago"):
            self.initial["expected_forma_pago"] = "03"
        if not self.initial.get("expected_uso_cfdi"):
            self.initial["expected_uso_cfdi"] = "G01"

        linked_contador = None
        if self.instance and self.instance.pk:
            source = self.initial.get("factura_source") or ("facturador" if self.instance.facturador_id else "productor")
            if source == "facturador" and self.instance.facturador_id:
                linked_contador = getattr(self.instance.facturador, "contador", None)
            elif source == "productor" and self.instance.productor_id:
                linked_contador = getattr(self.instance.productor, "contador", None)

        if linked_contador:
            self.initial["contador"] = linked_contador.nombre or ""
            self.initial["correo"] = linked_contador.email or ""

    def clean(self):
        cleaned = super().clean()
        source = (cleaned.get("factura_source") or "productor").strip()
        facturador = cleaned.get("facturador")
        productor_facturador = cleaned.get("productor_facturador")

        if source == "facturador":
            if not facturador and not productor_facturador:
                self.add_error("facturador", "Selecciona la entidad que factura.")

            if not facturador and productor_facturador:
                mapped, created = PersonaFactura.objects.get_or_create(
                    rfc=(productor_facturador.rfc or "").strip().upper(),
                    defaults={
                        "nombre": productor_facturador.nombre,
                        "regimen_fiscal": productor_facturador.regimen_fiscal,
                        "regimen_fiscal_codigo": productor_facturador.regimen_fiscal_codigo,
                        "contador": productor_facturador.contador,
                        "activo": True,
                    },
                )
                # Keep single registry by RFC updated from productor catalog when reusing existing row.
                if not created:
                    changed = False
                    if mapped.nombre != productor_facturador.nombre:
                        mapped.nombre = productor_facturador.nombre
                        changed = True
                    if (mapped.regimen_fiscal_codigo or "") != (productor_facturador.regimen_fiscal_codigo or ""):
                        mapped.regimen_fiscal_codigo = productor_facturador.regimen_fiscal_codigo
                        mapped.regimen_fiscal = productor_facturador.regimen_fiscal
                        changed = True
                    if not mapped.contador and productor_facturador.contador_id:
                        mapped.contador = productor_facturador.contador
                        changed = True
                    if changed:
                        mapped.save()

                _sync_facturador_cuentas_from_productor(productor_facturador, mapped)
                facturador = mapped
                cleaned["facturador"] = mapped

            # If both selectors point to same RFC/entity, ensure payment accounts are unified.
            if facturador and productor_facturador:
                if ((facturador.rfc or "").strip().upper() == (productor_facturador.rfc or "").strip().upper()):
                    _sync_facturador_cuentas_from_productor(productor_facturador, facturador)

            if facturador and facturador.contador:
                cleaned["contador"] = facturador.contador.nombre
                cleaned["correo"] = facturador.contador.email
        else:
            cleaned["facturador"] = None
            productor = getattr(self.instance, "productor", None)
            missing = []
            if not productor:
                missing.append("Productor asignado")
            else:
                if not (productor.nombre or "").strip():
                    missing.append("Nombre de productor")
                if not (productor.rfc or "").strip():
                    missing.append("RFC del productor")
                if not (productor.regimen_fiscal_codigo or "").strip():
                    missing.append("Régimen fiscal del productor")
                if not productor.contador:
                    missing.append("Contador ligado al productor")
                else:
                    if not (productor.contador.email or "").strip():
                        missing.append("Correo del contador")

            if missing:
                raise forms.ValidationError(
                    "Para usar 'Mismo productor', completa primero: " + ", ".join(missing)
                )

            # Autocompletar contacto desde catálogo de productor/contador.
            cleaned["contador"] = (productor.contador.nombre if productor and productor.contador else cleaned.get("contador", ""))
            cleaned["correo"] = (productor.contador.email if productor and productor.contador else cleaned.get("correo", ""))

        cleaned["expected_moneda"] = (cleaned.get("expected_moneda") or "").strip().upper()
        cleaned["expected_metodo_pago"] = (cleaned.get("expected_metodo_pago") or "").strip().upper()
        cleaned["expected_forma_pago"] = (cleaned.get("expected_forma_pago") or "").strip().upper()
        cleaned["expected_uso_cfdi"] = (cleaned.get("expected_uso_cfdi") or "").strip().upper()
        return cleaned


class PersonaFacturaQuickForm(BootstrapFormMixin, forms.ModelForm):
    regimen_fiscal_codigo = forms.ChoiceField(
        required=False,
        choices=[("", "Selecciona régimen fiscal SAT...")] + SAT_REGIMENES_FISCALES,
        label="Código régimen SAT",
    )

    class Meta:
        model = PersonaFactura
        fields = ["nombre", "rfc", "regimen_fiscal_codigo", "contador", "resico_policy", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["contador"].queryset = Contador.objects.filter(activo=True).order_by("nombre")
        if self.instance and self.instance.pk and self.instance.regimen_fiscal_codigo:
            self.initial["regimen_fiscal_codigo"] = self.instance.regimen_fiscal_codigo

    def save(self, commit=True):
        obj = super().save(commit=False)
        code = self.cleaned_data.get("regimen_fiscal_codigo", "") or ""
        obj.regimen_fiscal_codigo = code
        obj.regimen_fiscal = SAT_REGIMENES_MAP.get(code, code)

        # Guardrail: avoid duplicate PersonaFactura rows for same RFC.
        rfc = (obj.rfc or "").strip().upper()
        if not self.instance.pk and rfc:
            existing = PersonaFactura.objects.filter(rfc=rfc).first()
            if existing:
                existing.nombre = obj.nombre
                existing.regimen_fiscal_codigo = obj.regimen_fiscal_codigo
                existing.regimen_fiscal = obj.regimen_fiscal
                existing.contador = obj.contador
                existing.resico_policy = obj.resico_policy
                existing.activo = obj.activo
                if commit:
                    existing.save()
                return existing

        if commit:
            obj.save()
        return obj


class CompraRegistrarFacturaForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["uuid_factura"]
        labels = {
            "uuid_factura": "UUID de factura",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Si ya se capturó UUID (normalmente desde XML), evitar sobreescritura accidental.
        if self.instance and self.instance.pk and (self.instance.uuid_factura or "").strip():
            self.fields["uuid_factura"].disabled = True
            self.fields["uuid_factura"].help_text = "UUID autocompletado por validación XML."


class CompraExpedienteForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["expediente_completo"]
        labels = {"expediente_completo": "Expediente completo"}


class DocumentoCompraForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = DocumentoCompra
        fields = ["tipo_documento", "etapa", "descripcion", "archivo"]
        labels = {"tipo_documento": "Tipo de documento"}
        widgets = {"etapa": forms.HiddenInput()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["etapa"].required = False

    def clean(self):
        cleaned = super().clean()
        t = (cleaned.get("tipo_documento") or DocumentoTipoChoices.OTRO).strip().upper()
        file_obj = cleaned.get("archivo")
        file_name = (getattr(file_obj, "name", "") or "").lower()

        etapa = DocumentoEtapaChoices.OTRO
        es_compra_mxn = False
        if t == DocumentoTipoChoices.COMPRA_USD:
            etapa = DocumentoEtapaChoices.COMPRA_ORIGINAL
        elif t == DocumentoTipoChoices.COMPRA_MXN:
            etapa = DocumentoEtapaChoices.COMPRA_ORIGINAL
            es_compra_mxn = True
        elif t in {DocumentoTipoChoices.FACTURA_XML, DocumentoTipoChoices.FACTURA_PDF, DocumentoTipoChoices.SAT_PDF}:
            etapa = DocumentoEtapaChoices.FACTURA
        elif t in {DocumentoTipoChoices.CARATULA_BANCARIA, DocumentoTipoChoices.COMPROBANTE_PAGO}:
            etapa = DocumentoEtapaChoices.PAGO
        elif t == DocumentoTipoChoices.ACUSE_SOLICITUD:
            etapa = DocumentoEtapaChoices.SOLICITUD_FACTURA

        if file_name:
            if t == DocumentoTipoChoices.FACTURA_XML and not file_name.endswith(".xml"):
                self.add_error("archivo", "Para 'Factura XML' el archivo debe ser .xml")
            if t in {
                DocumentoTipoChoices.COMPRA_USD,
                DocumentoTipoChoices.COMPRA_MXN,
                DocumentoTipoChoices.FACTURA_PDF,
                DocumentoTipoChoices.SAT_PDF,
                DocumentoTipoChoices.CARATULA_BANCARIA,
                DocumentoTipoChoices.COMPROBANTE_PAGO,
            } and not file_name.endswith(".pdf"):
                self.add_error("archivo", "Para este tipo de documento el archivo debe ser .pdf")

        cleaned["etapa"] = etapa
        cleaned["es_compra_mxn"] = es_compra_mxn
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.es_compra_mxn = bool(self.cleaned_data.get("es_compra_mxn"))
        if commit:
            obj.save()
        return obj


class DeduccionForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Deduccion
        fields = ["concepto", "monto", "moneda", "notas"]


class CancelarCompraForm(BootstrapFormMixin, forms.Form):
    motivo_cancelacion = forms.CharField(
        required=True,
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Motivo de cancelación",
    )
    admin_password = forms.CharField(
        required=True,
        widget=forms.PasswordInput(render_value=False),
        label="Confirmar contraseña de admin",
    )


class CompraBankConfirmationForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Compra
        fields = ["bank_account_confirmed", "bank_confirmation_source", "bank_confirmation_notes"]
        labels = {
            "bank_account_confirmed": "Cuenta bancaria confirmada",
            "bank_confirmation_source": "Fuente de confirmación (WhatsApp/llamada)",
            "bank_confirmation_notes": "Notas de confirmación",
        }


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


class ImportComprasExcelForm(BootstrapFormMixin, forms.Form):
    archivo = forms.FileField(label="Archivo Excel de algodon.net")
    conflict_policy = forms.ChoiceField(
        label="Si hay conflicto de compra existente",
        choices=[
            ("ask", "Mostrar conflicto y no sobrescribir"),
            ("keep_existing", "Conservar compra existente"),
            ("overwrite", "Sobrescribir con nuevo registro"),
        ],
        initial="ask",
        required=True,
    )


class ImportAnticiposExcelForm(BootstrapFormMixin, forms.Form):
    archivo = forms.FileField(label="Archivo Excel de anticipos")


class CompraDivisionCreateForm(BootstrapFormMixin, forms.Form):
    porcentaje_division = forms.DecimalField(
        max_digits=8, decimal_places=4, min_value=0.0001, required=False
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
            self.compra.porcentaje_disponible_division_manual
        )
        self.fields["porcentaje_division"].widget.attrs["step"] = "0.0001"
        base = self.compra.compra_en_libras or 0
        self.fields["monto_division"].widget.attrs["max"] = str(
            (base * self.compra.porcentaje_disponible_division_manual / 100) if base else 0
        )

    def clean(self):
        cleaned = super().clean()
        porcentaje = cleaned.get("porcentaje_division")
        monto = cleaned.get("monto_division")
        base = self.compra.compra_en_libras or 0

        if not porcentaje and not monto:
            raise forms.ValidationError("Captura porcentaje o monto para dividir.")

        monto_disponible_manual = (base * self.compra.porcentaje_disponible_division_manual) / 100

        # Si ambos vienen capturados, usar el que sea válido (prioridad práctica: monto).
        if porcentaje and monto:
            pct_ok = porcentaje <= self.compra.porcentaje_disponible_division_manual
            monto_ok = monto <= monto_disponible_manual
            if monto_ok:
                porcentaje = (monto * 100) / base
            elif pct_ok:
                monto = (base * porcentaje) / 100
            else:
                raise forms.ValidationError("La division excede el disponible de la compra.")
        elif porcentaje:
            if porcentaje > self.compra.porcentaje_disponible_division_manual:
                raise forms.ValidationError("La division excede el disponible de la compra.")
            monto = (base * porcentaje) / 100
        elif monto:
            if base <= 0:
                raise forms.ValidationError("La compra base no tiene monto para dividir.")
            if monto > monto_disponible_manual:
                raise forms.ValidationError("El monto excede el disponible de la compra.")
            porcentaje = (monto * 100) / base
        cleaned["porcentaje_division"] = porcentaje
        cleaned["monto_division"] = monto
        return cleaned

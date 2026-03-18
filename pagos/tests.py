from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from unittest.mock import patch
from datetime import timedelta

from .models import (
    Anticipo,
    AplicacionAnticipo,
    Compra,
    Contador,
    DocumentoCompra,
    EmailTemplate,
    InvoiceValidationResult,
    MonedaChoices,
    PagoCompra,
    Productor,
    TipoCambio,
    WorkflowStateChoices,
)
from .forms import ContadorForm
from .services import build_invoice_request_email, parse_and_validate_cfdi_xml


class PagosFlowTests(TestCase):
    def setUp(self):
        self.productor = Productor.objects.create(codigo="P001", nombre="Juan Perez")
        self.tc = TipoCambio.objects.create(fecha=timezone.now().date(), tc=17.2500)
        self.anticipo = Anticipo.objects.create(
            numero_anticipo=1,
            productor=self.productor,
            fecha_pago=timezone.now().date(),
            monto_anticipo=10000,
        )
        self.compra = Compra.objects.create(
            numero_compra=1,
            productor=self.productor,
            fecha_de_pago=timezone.now().date(),
            fecha_liq=timezone.now().date(),
            compra_en_libras=15000,
            pago=15000,
            tipo_cambio=self.tc,
        )

    def test_compra_total_and_saldo(self):
        self.assertEqual(self.compra.base_pago, 0)
        self.assertEqual(self.compra.saldo_por_pagar, 15000)

    def test_aplicacion_de_anticipo_recalcula_saldo(self):
        AplicacionAnticipo.objects.create(
            anticipo=self.anticipo,
            compra=self.compra,
            fecha=timezone.now().date(),
            monto_aplicado=4000,
        )
        self.anticipo.refresh_from_db()
        self.compra.refresh_from_db()
        self.assertEqual(self.anticipo.saldo_disponible, 6000)
        self.assertEqual(self.compra.saldo_por_pagar, 11000)

    def test_divisiones_no_exceden_100_por_ciento(self):
        Compra.objects.create(
            numero_compra=1,
            productor=self.productor,
            fecha_de_pago=timezone.now().date(),
            fecha_liq=timezone.now().date(),
            compra_en_libras=300,
            parent_compra=self.compra,
            porcentaje_division=60,
            anticipos_revisados=True,
            deudas_revisadas=True,
            division_revisada=True,
        )
        over = Compra(
            numero_compra=1,
            productor=self.productor,
            fecha_de_pago=timezone.now().date(),
            fecha_liq=timezone.now().date(),
            compra_en_libras=300,
            parent_compra=self.compra,
            porcentaje_division=50,
            anticipos_revisados=True,
            deudas_revisadas=True,
            division_revisada=True,
        )
        with self.assertRaises(ValidationError):
            over.full_clean()

    def test_codigo_productor_se_genera_automaticamente(self):
        p = Productor.objects.create(nombre="Maria")
        self.assertTrue(p.codigo.startswith("PRD-"))

    def test_numero_anticipo_se_genera_automaticamente(self):
        a = Anticipo.objects.create(
            productor=self.productor,
            fecha_pago=timezone.now().date(),
            monto_anticipo=5000,
        )
        self.assertIsNotNone(a.numero_anticipo)

    def test_division_inicia_independiente_en_captura_y_queda_vinculada(self):
        division = Compra.objects.create(
            numero_compra=1,
            productor=self.productor,
            fecha_liq=timezone.now().date(),
            fecha_de_pago=timezone.now().date(),
            parent_compra=self.compra,
            porcentaje_division=25,
            pacas=10,
            compra_en_libras=125,
            tipo_cambio=self.tc,
        )
        self.assertTrue(division.es_division)
        self.assertEqual(division.parent_compra_id, self.compra.id)
        self.assertEqual(division.flujo_codigo, "captura")

    def test_base_parcialmente_dividida_usa_monto_remanente(self):
        self.compra.compra_en_libras = 1000
        self.compra.save(update_fields=["compra_en_libras", "updated_at"])

        Compra.objects.create(
            numero_compra=1,
            productor=self.productor,
            fecha_liq=timezone.now().date(),
            fecha_de_pago=timezone.now().date(),
            parent_compra=self.compra,
            porcentaje_division=40,
            compra_en_libras=400,
            tipo_cambio=self.tc,
            pacas=10,
        )

        self.assertEqual(self.compra.monto_objetivo_operativo, 600)

    def test_base_100_dividida_se_vuelve_referencia_solo(self):
        self.compra.compra_en_libras = 1000
        self.compra.save(update_fields=["compra_en_libras", "updated_at"])

        Compra.objects.create(
            numero_compra=1,
            productor=self.productor,
            fecha_liq=timezone.now().date(),
            fecha_de_pago=timezone.now().date(),
            parent_compra=self.compra,
            porcentaje_division=100,
            compra_en_libras=1000,
            tipo_cambio=self.tc,
            pacas=10,
        )
        self.compra.refresh_from_db()
        self.assertTrue(self.compra.es_base_referencia_solo)
        self.assertEqual(self.compra.flujo_codigo, "completo")

    def test_pago_en_pesos_se_convierte_a_usd_con_tc_pactado(self):
        fecha_sin_tc = timezone.now().date() + timedelta(days=1)
        self.compra.fecha_liq = fecha_sin_tc
        self.compra.tipo_cambio = None
        self.compra.tipo_cambio_valor = 20
        self.compra.compra_en_libras = 1000
        self.compra.save(
            update_fields=["fecha_liq", "tipo_cambio", "tipo_cambio_valor", "compra_en_libras", "updated_at"]
        )

        PagoCompra.objects.create(
            compra=self.compra,
            fecha_pago=timezone.now().date(),
            monto=2000,
            moneda=MonedaChoices.PESOS,
        )
        self.compra.refresh_from_db()

        self.assertEqual(self.compra.total_pagado_registrado, 100)
        self.assertEqual(self.compra.saldo_por_pagar, 900)
        self.assertEqual(self.compra.estatus_de_pago, "PARCIAL")

    def test_pago_en_pesos_con_tc_no_disponible_no_aplica_descuento(self):
        fecha_sin_tc = timezone.now().date() + timedelta(days=1)
        self.compra.fecha_liq = fecha_sin_tc
        self.compra.tipo_cambio = None
        self.compra.tipo_cambio_valor = None
        self.compra.compra_en_libras = 1000
        self.compra.save(
            update_fields=["fecha_liq", "tipo_cambio", "tipo_cambio_valor", "compra_en_libras", "updated_at"]
        )

        PagoCompra.objects.create(
            compra=self.compra,
            fecha_pago=timezone.now().date(),
            monto=2000,
            moneda=MonedaChoices.PESOS,
        )
        self.compra.refresh_from_db()

        self.assertEqual(self.compra.total_pagado_registrado, 0)
        self.assertEqual(self.compra.saldo_por_pagar, 1000)
        self.assertEqual(self.compra.estatus_de_pago, "PENDIENTE")

    def test_parse_cfdi_xml_basico(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Moneda="USD" MetodoPago="PUE">
  <cfdi:Emisor Rfc="AAA010101AAA" />
  <cfdi:Receptor Rfc="BBB010101BBB" UsoCFDI="G01" />
  <cfdi:Impuestos>
    <cfdi:Traslados>
      <cfdi:Traslado Impuesto="002" TasaOCuota="0.000000" />
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital UUID="123e4567-e89b-12d3-a456-426614174000" />
  </cfdi:Complemento>
</cfdi:Comprobante>'''
        result = parse_and_validate_cfdi_xml(xml, expected_rfc_receptor="BBB010101BBB", expected_moneda="USD")
        self.assertTrue(result["valid"])
        self.assertEqual(result["uuid"], "123e4567-e89b-12d3-a456-426614174000")


class ContadorEmailsTests(TestCase):
    def test_contador_form_admite_emails_adicionales_y_deduplica(self):
        form = ContadorForm(data={
            "nombre": "Conta 1",
            "telefono": "",
            "email": "main@example.com",
            "emails_adicionales": "a@example.com; b@example.com\na@example.com",
            "activo": True,
        })
        self.assertTrue(form.is_valid(), form.errors)
        obj = form.save()
        self.assertEqual(obj.emails_adicionales, "a@example.com, b@example.com")


class QueueAndInboxGuardsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(username="operador", email="op@example.com", password="secret123")
        self.client.force_login(self.user)
        self.productor = Productor.objects.create(codigo="P002", nombre="Proveedor Uno", rfc="AAA010101AAA")
        self.tc = TipoCambio.objects.create(fecha=timezone.now().date(), tc=17.2500)

    def _make_compra(self, **kwargs):
        data = {
            "numero_compra": 200,
            "productor": self.productor,
            "fecha_de_pago": timezone.now().date(),
            "fecha_liq": timezone.now().date(),
            "compra_en_libras": 1000,
            "tipo_cambio": self.tc,
        }
        data.update(kwargs)
        return Compra.objects.create(**data)

    def test_mark_ready_bloqueado_si_hay_blockers(self):
        compra = self._make_compra(workflow_state=WorkflowStateChoices.WAITING_BANK_CONFIRMATION)
        url = reverse("readiness_queue_action", args=[compra.id])
        self.client.post(url, {"action": "mark_ready"}, follow=True)
        compra.refresh_from_db()
        self.assertEqual(compra.workflow_state, WorkflowStateChoices.WAITING_BANK_CONFIRMATION)

    def test_mark_ready_funciona_cuando_cumple_precondiciones(self):
        compra = self._make_compra(workflow_state=WorkflowStateChoices.WAITING_BANK_CONFIRMATION)
        compra.bank_account_confirmed = True
        compra.save(update_fields=["bank_account_confirmed", "updated_at"])

        doc = DocumentoCompra(compra=compra, etapa="compra_original", tipo_documento="COMPRA_ORIGINAL")
        doc.archivo.save("compra.pdf", ContentFile(b"%PDF-1.4 base"), save=True)

        InvoiceValidationResult.objects.create(compra=compra, valid=True, uuid="uuid-ok")

        url = reverse("readiness_queue_action", args=[compra.id])
        self.client.post(url, {"action": "mark_ready"}, follow=True)
        compra.refresh_from_db()
        self.assertEqual(compra.workflow_state, WorkflowStateChoices.READY_TO_PAY)

    @patch("pagos.views.mark_gmail_message_processed")
    @patch("pagos.views.create_invoice_validation_for_compra")
    @patch("pagos.views.fetch_gmail_attachments_for_compra")
    def test_inbox_import_dedup_por_message_id(self, mock_fetch, mock_validate, mock_mark):
        compra = self._make_compra(numero_compra=201)
        existing = DocumentoCompra(compra=compra, etapa="factura", tipo_documento="FACTURA_XML", descripcion="Inbox Gmail #m1")
        existing.archivo.save("f1.xml", ContentFile(b"<xml/>"), save=True)

        session = self.client.session
        session[f"inbox_factura_preview_{compra.id}"] = [{
            "key": "m1||f1.xml",
            "message_id": "m1",
            "xml_filename": "f1.xml",
            "pdf_filename": "",
        }]
        session.save()

        mock_fetch.return_value = [{"message_id": "m1", "filename": "f1.xml", "bytes": b"<xml/>"}]

        url = reverse("compra_flujo", args=[compra.id]) + "?step=revisar_factura"
        self.client.post(url, {"flow_form": "importar_inbox_factura", "inbox_pick": "m1||f1.xml"}, follow=True)

        self.assertEqual(compra.documentos.filter(etapa="factura").count(), 1)
        mock_validate.assert_not_called()
        mock_mark.assert_not_called()

    @patch("pagos.views.mark_gmail_message_processed")
    @patch("pagos.views.create_invoice_validation_for_compra")
    @patch("pagos.views.fetch_gmail_attachments_for_compra")
    def test_inbox_import_dedup_por_hash_contenido(self, mock_fetch, mock_validate, mock_mark):
        compra = self._make_compra(numero_compra=202)
        existing = DocumentoCompra(compra=compra, etapa="factura", tipo_documento="FACTURA_XML", descripcion="Carga manual")
        existing.archivo.save("previo.xml", ContentFile(b"same-content"), save=True)

        session = self.client.session
        session[f"inbox_factura_preview_{compra.id}"] = [{
            "key": "m2||nuevo.xml",
            "message_id": "m2",
            "xml_filename": "nuevo.xml",
            "pdf_filename": "",
        }]
        session.save()

        mock_fetch.return_value = [{"message_id": "m2", "filename": "nuevo.xml", "bytes": b"same-content"}]

        class _V:
            valid = True
            uuid = "uuid-1"

        mock_validate.return_value = _V()

        url = reverse("compra_flujo", args=[compra.id]) + "?step=revisar_factura"
        self.client.post(url, {"flow_form": "importar_inbox_factura", "inbox_pick": "m2||nuevo.xml"}, follow=True)

        self.assertEqual(compra.documentos.filter(etapa="factura").count(), 1)
        self.assertLessEqual(mock_validate.call_count, 1)
        self.assertLessEqual(mock_mark.call_count, 1)

    @patch("pagos.views.mark_gmail_message_processed")
    @patch("pagos.views.fetch_gmail_attachments_for_compra")
    def test_inbox_import_valida_mueve_a_waiting_bank_confirmation(self, mock_fetch, mock_mark):
        compra = self._make_compra(numero_compra=203, workflow_state=WorkflowStateChoices.WAITING_INVOICE)

        doc = DocumentoCompra(compra=compra, etapa="compra_original", tipo_documento="COMPRA_ORIGINAL")
        doc.archivo.save("compra.pdf", ContentFile(b"%PDF-1.4 base"), save=True)

        session = self.client.session
        session[f"inbox_factura_preview_{compra.id}"] = [{
            "key": "m3||f.xml",
            "message_id": "m3",
            "xml_filename": "f.xml",
            "pdf_filename": "f.pdf",
        }]
        session.save()

        xml_ok = b'''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Moneda="USD" MetodoPago="PUE" Total="1000">
  <cfdi:Emisor Rfc="AAA010101AAA" />
  <cfdi:Receptor Rfc="UAM140522Q51" UsoCFDI="G01" />
  <cfdi:Impuestos>
    <cfdi:Traslados><cfdi:Traslado Impuesto="002" TasaOCuota="0.000000" /></cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="uuid-ok" /></cfdi:Complemento>
</cfdi:Comprobante>'''
        mock_fetch.return_value = [
            {"message_id": "m3", "filename": "f.xml", "bytes": xml_ok},
            {"message_id": "m3", "filename": "f.pdf", "bytes": b"%PDF-1.4"},
        ]

        url = reverse("compra_flujo", args=[compra.id]) + "?step=revisar_factura"
        self.client.post(url, {"flow_form": "importar_inbox_factura", "inbox_pick": "m3||f.xml"}, follow=True)

        compra.refresh_from_db()
        self.assertEqual(compra.workflow_state, WorkflowStateChoices.WAITING_BANK_CONFIRMATION)
        self.assertEqual(compra.uuid_factura, "uuid-ok")
        mock_mark.assert_called_once_with("m3", label_name="pagos-processed")


class InvoiceTemplateSelectionTests(TestCase):
    def test_uses_productor_regimen_for_resico_when_facturador_absent(self):
        productor = Productor.objects.create(
            codigo="P003",
            nombre="Proveedor Resico",
            regimen_fiscal="626 Régimen Simplificado de Confianza",
            regimen_fiscal_codigo="626",
        )
        tc = TipoCambio.objects.create(fecha=timezone.now().date(), tc=17.2500)
        compra = Compra.objects.create(
            numero_compra=300,
            productor=productor,
            fecha_de_pago=timezone.now().date(),
            fecha_liq=timezone.now().date(),
            compra_en_libras=1000,
            tipo_cambio=tc,
            # stale value at compra level (historical drift)
            regimen_fiscal="612 Personas Físicas con Actividades Empresariales y Profesionales",
        )

        EmailTemplate.objects.create(
            code="GENERAL_STD",
            nombre="General",
            scenario="GENERAL",
            subject_template="GENERAL {compra_numero}",
            body_template="GENERAL",
            is_default=True,
            activo=True,
        )
        EmailTemplate.objects.create(
            code="AE_STD",
            nombre="AE",
            scenario="AE",
            subject_template="AE {compra_numero}",
            body_template="AE",
            is_default=True,
            activo=True,
        )
        EmailTemplate.objects.create(
            code="RESICO_STD",
            nombre="RESICO",
            scenario="RESICO",
            subject_template="RESICO {compra_numero}",
            body_template="RESICO",
            is_default=True,
            activo=True,
        )

        payload = build_invoice_request_email(compra)
        self.assertEqual(payload["scenario"], "RESICO")
        self.assertEqual(payload["template_code"], "RESICO_STD")


class ResicoPolicyValidationTests(TestCase):
    def test_resico_retencion_125_requerida_falla_sin_retencion(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Moneda="USD" MetodoPago="PUE">
  <cfdi:Emisor Rfc="AAA010101AAA" />
  <cfdi:Receptor Rfc="BBB010101BBB" UsoCFDI="G01" />
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="u1" /></cfdi:Complemento>
</cfdi:Comprobante>'''
        result = parse_and_validate_cfdi_xml(xml, requires_resico_retention=True, resico_policy="RETENCION_125")
        self.assertFalse(result["valid"])
        self.assertTrue(any("1.25" in e for e in result["errors"]))

    def test_resico_leyenda_requerida_pasa_con_leyenda(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Moneda="USD" MetodoPago="PUE">
  <cfdi:Emisor Rfc="AAA010101AAA" />
  <cfdi:Receptor Rfc="BBB010101BBB" UsoCFDI="G01" />
  <cfdi:Conceptos><cfdi:Concepto Descripcion="Supuesto de exencion art 113 e Ley de ISR" /></cfdi:Conceptos>
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="u2" /></cfdi:Complemento>
</cfdi:Comprobante>'''
        result = parse_and_validate_cfdi_xml(xml, requires_resico_retention=True, resico_policy="EXENCION_LEYENDA")
        self.assertTrue(result["valid"])

    def test_resico_auto_pasa_con_retencion_125(self):
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4" xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital" Moneda="USD" MetodoPago="PUE">
  <cfdi:Emisor Rfc="AAA010101AAA" />
  <cfdi:Receptor Rfc="BBB010101BBB" UsoCFDI="G01" />
  <cfdi:Impuestos><cfdi:Retenciones><cfdi:Retencion Impuesto="001" Importe="10.00" TasaOCuota="0.012500" /></cfdi:Retenciones></cfdi:Impuestos>
  <cfdi:Complemento><tfd:TimbreFiscalDigital UUID="u3" /></cfdi:Complemento>
</cfdi:Comprobante>'''
        result = parse_and_validate_cfdi_xml(xml, requires_resico_retention=True, resico_policy="AUTO")
        self.assertTrue(result["valid"])

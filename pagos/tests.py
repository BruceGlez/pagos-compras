from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError
from datetime import timedelta

from .models import (
    Anticipo,
    AplicacionAnticipo,
    Compra,
    MonedaChoices,
    PagoCompra,
    Productor,
    TipoCambio,
)


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

    def test_division_sigue_mismo_flujo_que_compra_base(self):
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
        self.assertEqual(division.flujo_codigo, "anticipos")

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

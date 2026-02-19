from django.test import TestCase
from django.utils import timezone

from .models import Anticipo, AplicacionAnticipo, Compra, Productor, TipoCambio


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
            compra_en_libras=500,
            pago=15000,
            tipo_cambio=self.tc,
        )

    def test_compra_total_and_saldo(self):
        self.assertEqual(self.compra.base_pago, 15000)
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

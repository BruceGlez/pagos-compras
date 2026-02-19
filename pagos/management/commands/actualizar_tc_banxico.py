from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from pagos.models import TipoCambio
from pagos.services.banxico import fetch_tipo_cambio


class Command(BaseCommand):
    help = "Actualiza tabla TC desde API Banxico (FIX)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=5,
            help="Numero de dias hacia atras para sincronizar.",
        )

    def handle(self, *args, **options):
        lock_path = Path(settings.BASE_DIR) / ".tc_sync.lock"
        try:
            lock_path.touch(exist_ok=False)
        except FileExistsError:
            self.stderr.write(
                self.style.WARNING(
                    "Sincronizacion TC omitida: ya hay otra ejecucion en curso."
                )
            )
            return

        days = max(options["days"], 1)
        target_end_date = timezone.localdate()
        start_date = target_end_date - timedelta(days=days)
        token = settings.BANXICO_TOKEN
        serie_id = settings.BANXICO_SERIE_ID
        objetivo = settings.BANXICO_TC_OBJETIVO
        fetch_end_date = (
            target_end_date + timedelta(days=3)
            if objetivo == "publicacion_dof"
            else target_end_date
        )

        try:
            try:
                rows = fetch_tipo_cambio(token, serie_id, start_date, fetch_end_date)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Error consultando Banxico: {exc}"))
                return

            # SF60653 devuelve "fecha de liquidacion / para pagos".
            # Para "publicacion_dof" se desplaza un dia hacia atras.
            if objetivo == "publicacion_dof":
                rows = [(fecha - timedelta(days=1), valor) for fecha, valor in rows]
                rows = [(fecha, valor) for fecha, valor in rows if fecha <= target_end_date]

            created = 0
            updated = 0
            for fecha, valor in rows:
                _, was_created = TipoCambio.objects.update_or_create(
                    fecha=fecha,
                    defaults={"tc": valor, "fuente": f"Banxico {serie_id}"},
                )
                if was_created:
                    created += 1
                else:
                    updated += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"TC sincronizado. creados={created}, actualizados={updated}, total={len(rows)}"
                )
            )
        finally:
            lock_path.unlink(missing_ok=True)

from __future__ import annotations

import re
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError

from pagos.models import Compra, DocumentoCompra


class Command(BaseCommand):
    help = "Backfill de archivos históricos al expediente de compras (dry-run por defecto)."

    def add_arguments(self, parser):
        parser.add_argument("--root", required=True, help="Ruta raíz donde viven las carpetas por compra")
        parser.add_argument("--apply", action="store_true", help="Aplicar cambios (sin esto solo simula)")
        parser.add_argument("--ext", default="pdf", help="Extensión a importar (default: pdf)")

    def handle(self, *args, **options):
        root = Path(options["root"]).expanduser().resolve()
        do_apply = bool(options["apply"])
        ext = str(options["ext"] or "pdf").lower().lstrip(".")

        if not root.exists() or not root.is_dir():
            raise CommandError(f"Root inválido: {root}")

        total_files = 0
        matched = 0
        imported = 0
        skipped_existing = 0
        unresolved = 0

        self.stdout.write(self.style.WARNING(f"Modo: {'APPLY' if do_apply else 'DRY-RUN'} | root={root} | ext=.{ext}"))

        for folder in sorted([p for p in root.iterdir() if p.is_dir()]):
            folder_name = folder.name
            m = re.search(r"(\d+)", folder_name)
            if not m:
                continue
            numero_compra = int(m.group(1))

            compras = Compra.objects.filter(numero_compra=numero_compra, parent_compra__isnull=True).order_by("-id")
            if not compras.exists():
                unresolved += 1
                self.stdout.write(f"[NO_MATCH] carpeta={folder_name} compra={numero_compra}")
                continue
            if compras.count() > 1:
                self.stdout.write(self.style.WARNING(f"[MULTI_MATCH] carpeta={folder_name} compra={numero_compra} -> usando id={compras.first().id}"))

            compra = compras.first()
            files = list(folder.rglob(f"*.{ext}"))
            if not files:
                continue

            for fpath in files:
                total_files += 1
                rel = str(fpath.relative_to(root))
                fname = fpath.name

                exists = compra.documentos.filter(descripcion__icontains=f"SRC:{rel}").exists()
                if exists:
                    skipped_existing += 1
                    continue

                matched += 1
                desc = f"PDF compra original (backfill) · SRC:{rel}"
                if do_apply:
                    doc = DocumentoCompra(compra=compra, etapa="otro", descripcion=desc)
                    with fpath.open("rb") as fh:
                        doc.archivo.save(fname, File(fh), save=True)
                    imported += 1
                self.stdout.write(f"[{'IMPORT' if do_apply else 'PLAN'}] compra_id={compra.id} nro={compra.numero_compra} file={rel}")

        self.stdout.write("-" * 60)
        self.stdout.write(
            f"files={total_files} matched={matched} imported={imported} skipped_existing={skipped_existing} unresolved={unresolved}"
        )
        if not do_apply:
            self.stdout.write(self.style.SUCCESS("Dry-run completado. Usa --apply para importar."))
        else:
            self.stdout.write(self.style.SUCCESS("Backfill aplicado."))

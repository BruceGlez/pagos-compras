from django.db import migrations, models


def backfill_tipo_documento(apps, schema_editor):
    DocumentoCompra = apps.get_model("pagos", "DocumentoCompra")
    for d in DocumentoCompra.objects.all().iterator():
        etapa = (d.etapa or "").strip()
        name = (getattr(d, "archivo", "") or "")
        name_l = str(name).lower()
        desc_l = (d.descripcion or "").lower()

        tipo = "OTRO"
        if etapa == "compra_original":
            is_mxn = bool(getattr(d, "es_compra_mxn", False)) or ("mxn" in name_l) or ("peso" in name_l) or ("mxn" in desc_l) or ("peso" in desc_l)
            tipo = "COMPRA_MXN" if is_mxn else "COMPRA_USD"
        elif etapa == "factura":
            if name_l.endswith(".xml"):
                tipo = "FACTURA_XML"
            else:
                tipo = "FACTURA_PDF"
        elif etapa == "pago":
            if "caratula" in desc_l or "carátula" in desc_l:
                tipo = "CARATULA_BANCARIA"
            else:
                tipo = "COMPROBANTE_PAGO"
        elif etapa == "solicitud_factura":
            tipo = "ACUSE_SOLICITUD"

        d.tipo_documento = tipo
        d.save(update_fields=["tipo_documento", "updated_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("pagos", "0030_documentocompra_es_compra_mxn"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentocompra",
            name="tipo_documento",
            field=models.CharField(
                choices=[
                    ("COMPRA_USD", "Compra en dólares (PDF)"),
                    ("COMPRA_MXN", "Compra en pesos (PDF)"),
                    ("FACTURA_XML", "Factura XML"),
                    ("FACTURA_PDF", "Factura PDF"),
                    ("SAT_PDF", "Validación SAT (PDF)"),
                    ("CARATULA_BANCARIA", "Carátula bancaria"),
                    ("COMPROBANTE_PAGO", "Comprobante de pago"),
                    ("ACUSE_SOLICITUD", "Acuse solicitud factura"),
                    ("OTRO", "Otro"),
                ],
                default="OTRO",
                max_length=30,
            ),
        ),
        migrations.RunPython(backfill_tipo_documento, migrations.RunPython.noop),
    ]

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pagos", "0029_productor_rfc_alter_documentocompra_etapa"),
    ]

    operations = [
        migrations.AddField(
            model_name="documentocompra",
            name="es_compra_mxn",
            field=models.BooleanField(default=False),
        ),
    ]

from django.db import migrations


def seed_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for name in ["Admin", "Operador", "Consulta"]:
        Group.objects.get_or_create(name=name)


def unseed_groups(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name__in=["Admin", "Operador", "Consulta"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("pagos", "0012_compra_cancelada_compra_motivo_cancelacion"),
    ]

    operations = [
        migrations.RunPython(seed_groups, unseed_groups),
    ]

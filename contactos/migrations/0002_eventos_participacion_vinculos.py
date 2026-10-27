"""Merge del pseudocódigo: Evento genérico, Participacion ampliada, Vinculo y PosibleDuplicado.

Los eventos existentes quedan como tipo "anual", estado "cerrado" y código anual-<año>.
"""
import django.db.models.deletion
from django.db import migrations, models


def rellenar_eventos(apps, schema_editor):
    Evento = apps.get_model("contactos", "Evento")
    for e in Evento.objects.all():
        e.codigo = f"anual-{e.anio}"
        e.tipo = "anual"
        e.estado = "cerrado"
        e.save(update_fields=["codigo", "tipo", "estado"])


class Migration(migrations.Migration):

    dependencies = [
        ("contactos", "0001_inicial"),
    ]

    operations = [
        # --- Evento -------------------------------------------------------
        migrations.AlterField(
            model_name="evento",
            name="anio",
            field=models.PositiveIntegerField(blank=True, help_text="Si se deja vacío, se toma de la fecha.", verbose_name="año"),
        ),
        migrations.AddField(
            model_name="evento",
            name="codigo",
            field=models.SlugField(blank=True, default="", verbose_name="código"),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="evento",
            name="tipo",
            field=models.CharField(choices=[("anual", "Evento anual"), ("mentoria", "Mentoría"), ("clinica", "Clínica"), ("reunion", "Reunión"), ("otro", "Otro")], default="anual", max_length=20),
        ),
        migrations.AddField(
            model_name="evento",
            name="estado",
            field=models.CharField(choices=[("borrador", "Borrador"), ("convocando", "Convocando"), ("inscripcion_abierta", "Inscripción abierta"), ("en_curso", "En curso"), ("cerrado", "Cerrado")], default="borrador", max_length=20),
        ),
        migrations.AddField(
            model_name="evento",
            name="lugar",
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name="evento",
            name="cupo_maximo",
            field=models.PositiveIntegerField(blank=True, null=True, verbose_name="cupo máximo"),
        ),
        migrations.AddField(
            model_name="evento",
            name="checkin_abierto",
            field=models.BooleanField(default=False, help_text="Se abre y se cierra a mano desde el panel, sin depender del estado.", verbose_name="registro de asistencia abierto"),
        ),
        migrations.RunPython(rellenar_eventos, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="evento",
            name="codigo",
            field=models.SlugField(blank=True, help_text="Va en la URL del QR (por ejemplo anual-2026). Si se deja vacío: tipo-año.", unique=True, verbose_name="código"),
        ),
        migrations.AlterModelOptions(
            name="evento",
            options={"ordering": ["-anio", "-fecha"]},
        ),
        # --- Participacion ------------------------------------------------
        migrations.AddField(
            model_name="participacion",
            name="no_puede",
            field=models.BooleanField(default=False, verbose_name="no puede asistir"),
        ),
        migrations.AddField(
            model_name="participacion",
            name="fecha_invitacion",
            field=models.DateTimeField(blank=True, null=True, verbose_name="fecha de invitación"),
        ),
        migrations.AddField(
            model_name="participacion",
            name="hora_llegada",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="participacion",
            name="origen_inscripcion",
            field=models.CharField(blank=True, choices=[("importacion", "Importación"), ("formulario", "Formulario"), ("manual", "Manual"), ("qr", "QR")], max_length=20),
        ),
        migrations.AddField(
            model_name="participacion",
            name="origen_llegada",
            field=models.CharField(blank=True, choices=[("qr", "QR"), ("manual", "Manual (panel)")], max_length=20),
        ),
        migrations.AddField(
            model_name="participacion",
            name="logistica",
            field=models.JSONField(blank=True, default=dict, help_text="Transporte, km, hospedaje... (la restricción alimentaria tiene su propio campo).", verbose_name="logística"),
        ),
        # --- Vínculos -----------------------------------------------------
        migrations.CreateModel(
            name="Vinculo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("nombre", models.CharField(max_length=60, unique=True)),
                ("activo", models.BooleanField(default=True)),
            ],
            options={"verbose_name": "vínculo", "verbose_name_plural": "vínculos", "ordering": ["nombre"]},
        ),
        migrations.CreateModel(
            name="PersonaVinculo",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("desde", models.DateField()),
                ("hasta", models.DateField(blank=True, null=True)),
                ("asignado_por", models.CharField(blank=True, max_length=100)),
                ("persona", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="asignaciones", to="contactos.persona")),
                ("vinculo", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="asignaciones", to="contactos.vinculo")),
            ],
            options={"verbose_name": "vínculo de persona", "verbose_name_plural": "vínculos de personas", "ordering": ["-desde"]},
        ),
        migrations.AddField(
            model_name="persona",
            name="vinculos",
            field=models.ManyToManyField(blank=True, related_name="personas", through="contactos.PersonaVinculo", to="contactos.vinculo"),
        ),
        # --- Posibles duplicados -----------------------------------------
        migrations.CreateModel(
            name="PosibleDuplicado",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("detectado", models.DateTimeField(auto_now_add=True)),
                ("resuelto", models.BooleanField(default=False)),
                ("persona_existente", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="contactos.persona")),
                ("persona_nueva", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="+", to="contactos.persona")),
            ],
            options={
                "verbose_name": "posible duplicado", "verbose_name_plural": "posibles duplicados",
                "ordering": ["resuelto", "-detectado"],
                "unique_together": {("persona_nueva", "persona_existente")},
            },
        ),
    ]

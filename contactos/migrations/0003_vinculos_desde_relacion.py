"""Llena los vínculos desde el texto `relacion` de cada persona.

Lo que no mapea queda sin vínculo y se informa al migrar (no se inventa).
`grupo` no se toca: scoring.py lo sigue usando hasta diciembre.
"""
import datetime as dt
from collections import Counter

from django.db import migrations

from contactos.vinculos import DESCONOCIDO, vinculo_desde_texto


def llenar_vinculos(apps, schema_editor):
    Persona = apps.get_model("contactos", "Persona")
    Vinculo = apps.get_model("contactos", "Vinculo")
    PersonaVinculo = apps.get_model("contactos", "PersonaVinculo")
    hoy = dt.date.today()
    sin_mapear = Counter()
    asignados = 0
    for p in Persona.objects.exclude(relacion=""):
        nombre, terminado = vinculo_desde_texto(p.relacion)
        if nombre == DESCONOCIDO:
            sin_mapear[p.relacion.strip()] += 1
            continue
        if nombre is None:
            continue
        vinculo, _ = Vinculo.objects.get_or_create(nombre=nombre)
        PersonaVinculo.objects.create(
            persona=p, vinculo=vinculo, desde=hoy, hasta=hoy if terminado else None,
            asignado_por="migración desde relación" + (" (ex)" if terminado else ""),
        )
        asignados += 1
    if asignados or sin_mapear:
        print(f"\n  Vínculos asignados desde 'relación': {asignados}")
        for texto, n in sin_mapear.most_common():
            print(f"  Sin mapear (revisar con Alvaro): {texto!r} × {n}")


def borrar_vinculos(apps, schema_editor):
    apps.get_model("contactos", "PersonaVinculo").objects.filter(
        asignado_por__startswith="migración desde relación"
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("contactos", "0002_eventos_participacion_vinculos"),
    ]

    operations = [
        migrations.RunPython(llenar_vinculos, borrar_vinculos),
    ]

"""
Importa el Excel limpio (ISF_Base_Contactos_*.xlsx), hojas Personas y Participaciones.

Uso:
    python manage.py importar_excel ruta/al/archivo.xlsx

Se puede correr varias veces: actualiza por correo, no duplica.
"""
import datetime as dt

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from openpyxl import load_workbook

from contactos.models import Estado, Evento, Grupo, Participacion, Persona
from contactos.scoring import recalcular_todo

EVENTOS = {
    2024: ("Evento Anual 2024", dt.date(2024, 12, 10)),
    2025: ("Evento Anual 2025", dt.date(2025, 12, 17)),
}
ESTADO_POR_ETIQUETA = {label: value for value, label in Estado.choices}
GRUPO_POR_ETIQUETA = {label: value for value, label in Grupo.choices}


def filas(hoja):
    """Devuelve cada fila como dict {encabezado: valor}."""
    it = hoja.iter_rows(values_only=True)
    encabezados = [str(h).strip() if h else "" for h in next(it)]
    for fila in it:
        if any(v not in (None, "") for v in fila):
            yield dict(zip(encabezados, fila))


def texto(v):
    return "" if v is None else str(v).strip()


def booleano(v):
    if isinstance(v, bool):
        return v
    return texto(v).lower() in ("true", "verdadero", "sí", "si", "y", "1")


class Command(BaseCommand):
    help = "Importa Personas y Participaciones desde el Excel limpio."

    def add_arguments(self, parser):
        parser.add_argument("archivo")

    @transaction.atomic
    def handle(self, archivo, **opts):
        try:
            libro = load_workbook(archivo, read_only=True, data_only=True)
        except FileNotFoundError:
            raise CommandError(f"No encuentro el archivo: {archivo}")
        for hoja in ("Personas", "Participaciones"):
            if hoja not in libro.sheetnames:
                raise CommandError(f"Al archivo le falta la hoja '{hoja}'.")

        eventos = {
            anio: Evento.objects.update_or_create(anio=anio, defaults={"nombre": n, "fecha": f})[0]
            for anio, (n, f) in EVENTOS.items()
        }

        # 1) Personas
        personas = {}
        for f in filas(libro["Personas"]):
            correo = texto(f["correo"]).lower()
            if not correo:
                continue
            estado = ESTADO_POR_ETIQUETA.get(texto(f["estado_contacto"]))
            if estado is None:
                raise CommandError(f"Estado desconocido '{f['estado_contacto']}' para {correo}")
            persona, _ = Persona.objects.update_or_create(
                correo=correo,
                defaults=dict(
                    persona_id=texto(f["persona_id"]),
                    correos_alternativos=texto(f["correos_alternativos"]),
                    nombre=texto(f["nombre_completo"]),
                    organizacion=texto(f["organizacion"]),
                    relacion=texto(f["relacion"]),
                    grupo=GRUPO_POR_ETIQUETA.get(texto(f["grupo_relacion"]), Grupo.SIN_CLASIFICAR),
                    telefono=texto(f["telefono"]),
                    estado=estado,
                    comentarios=texto(f["comentarios_originales"]),
                    posible_socio_actual=booleano(f["posible_socio_actual"]),
                ),
            )
            personas[persona.persona_id] = (persona, f)

        # 2) Participaciones (inscripciones 2024 y 2025, asistencia 2025)
        n_part = 0
        for f in filas(libro["Participaciones"]):
            persona = personas.get(texto(f["persona_id"]), (None,))[0]
            if persona is None:
                continue
            anio = int(texto(f["evento"])[-4:])
            fecha = f["fecha_inscripcion"]
            if isinstance(fecha, dt.datetime) and fecha.tzinfo is None:
                fecha = fecha.replace(tzinfo=dt.timezone(dt.timedelta(hours=-3)))
            Participacion.objects.update_or_create(
                persona=persona, evento=eventos[anio],
                defaults=dict(
                    inscrito=booleano(f["inscrito"]),
                    fecha_inscripcion=fecha if isinstance(fecha, dt.datetime) else None,
                    asistio=None if f["asistio"] is None else booleano(f["asistio"]),
                    categoria_formulario=texto(f["categoria_en_formulario"]),
                    restriccion_alimentaria=texto(f["restriccion_alimentaria"]),
                ),
            )
            n_part += 1

        # 3) Datos de 2025 que viven en la hoja Personas: invitado y respondió
        ev = eventos[2025]
        for persona, f in personas.values():
            invitado = "Lista invitados 2025" in texto(f["fuentes"])
            respondio = booleano(f["respondio_2025"])
            if invitado or respondio:
                part, _ = Participacion.objects.get_or_create(persona=persona, evento=ev)
                part.invitado, part.respondio = invitado, respondio
                part.save(update_fields=["invitado", "respondio"])

        resumen = recalcular_todo()
        self.stdout.write(self.style.SUCCESS(
            f"Importadas {len(personas)} personas y {n_part} participaciones."
        ))
        for k, v in resumen.items():
            self.stdout.write(f"  {k}: {v}")

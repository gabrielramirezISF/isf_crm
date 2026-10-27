"""
Importa las respuestas del Google Form de inscripción (CSV) a un evento.

Uso:
    python manage.py importar_inscripciones --evento anual-2026 "ruta/Inscripciones.csv"

Se puede correr varias veces con el CSV actualizado: no duplica personas ni
inscripciones, no cambia la fecha de inscripción y completa la logística nueva.

Regla: cada columna conocida se MAPEA a un campo propio; el resto se ignora
(las columnas de trabajo de Alvaro: Calza Mail, Repetido, conteos sueltos...).
"""
import csv
import datetime as dt
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from contactos.models import Estado, Evento, OrigenInscripcion, PosibleDuplicado
from contactos.scoring import recalcular_todo
from contactos.servicios import buscar_o_crear, inscribir
from contactos.vinculos import DESCONOCIDO, vinculo_desde_texto


def leer_filas(ruta):
    with Path(ruta).open(encoding="utf-8-sig", newline="") as f:
        for fila in csv.DictReader(f):
            yield {(k or "").strip(): (v or "").strip() for k, v in fila.items() if k and k.strip()}


def columna(fila, *prefijos):
    """Busca una columna por el inicio de su nombre (los títulos del formulario son larguísimos)."""
    for clave, valor in fila.items():
        if any(clave.lower().startswith(p.lower()) for p in prefijos):
            return valor
    return ""


def parsear_fecha_formulario(texto):
    """Google Forms exporta '10/21/2026 15:49:53' (mes primero). Hora de Chile."""
    for formato in ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return timezone.make_aware(dt.datetime.strptime(texto, formato))
        except (ValueError, TypeError):
            pass
    return None


def parsear_km(texto):
    """'6 km', '2,2', 'Aprox 58 km', '1/4' -> número o None. Nunca inventa."""
    t = (texto or "").lower().replace("aprox", "").replace("km", "").replace(",", ".").strip()
    try:
        return float(t)
    except ValueError:
        return None


class Command(BaseCommand):
    help = "Importa las inscripciones del formulario (CSV) a un evento. Idempotente."

    def add_arguments(self, parser):
        parser.add_argument("archivo")
        parser.add_argument("--evento", required=True, help="Código del evento, por ejemplo anual-2026")

    @transaction.atomic
    def handle(self, archivo, evento, **opts):
        try:
            ev = Evento.objects.get(codigo=evento)
        except Evento.DoesNotExist:
            raise CommandError(f"No existe el evento '{evento}'. Créalo primero en /admin/ (Eventos).")
        if not Path(archivo).exists():
            raise CommandError(f"No encuentro el archivo: {archivo}")

        r = Counter()
        sin_mapear = Counter()
        duplicados_antes = PosibleDuplicado.objects.count()
        for fila in leer_filas(archivo):
            r["filas"] += 1
            correo = columna(fila, "Correo electrónico", "Dirección de correo")
            if not correo:
                r["sin correo"] += 1
                continue
            persona, creada = buscar_o_crear(
                correo,
                columna(fila, "Nombre y apellidos"),
                columna(fila, "Organización"),
                columna(fila, "Número de contacto"),
            )
            r["personas nuevas" if creada else "ya estaban en la base"] += 1

            categoria = columna(fila, "Categoría")
            nombre_vinculo, terminado = vinculo_desde_texto(categoria)
            if nombre_vinculo == DESCONOCIDO:
                sin_mapear[categoria] += 1
            elif nombre_vinculo:
                persona.agregar_vinculo(nombre_vinculo, quien=f"inscripción {ev.codigo}", terminado=terminado)

            # Se inscribió alguien marcado "fuera de Chile": su inscripción lo desmiente.
            if persona.estado == Estado.FUERA_CHILE:
                persona.cambiar_estado(Estado.CONTACTABLE, motivo=f"Se inscribió al {ev}")

            logistica = {
                "restriccion_alimentaria": columna(fila, "¿Tienes alguna restricción"),
                "transporte_ida": columna(fila, "¿Qué medio de transporte utilizaras para llegar"),
                "transporte_vuelta": columna(fila, "¿Qué medio de transporte utilizaras para retornar"),
                "km_ida": parsear_km(columna(fila, "¿Cuántos kilómetros de distancia recorrerás en promedio en la IDA")),
                "km_vuelta": parsear_km(columna(fila, "¿Cuántos kilómetros de distancia recorrerás en promedio en la VUELTA")),
                "comparte_auto": columna(fila, "Si viajas en vehículo particular"),
                "hospedaje": columna(fila, "¿Tu asistencia al evento anual requerirá hospedaje"),
            }
            _, cambio = inscribir(
                ev, persona, OrigenInscripcion.FORMULARIO,
                cuando=parsear_fecha_formulario(fila.get("Timestamp") or fila.get("Marca temporal", "")),
                categoria=categoria, logistica=logistica,
            )
            r["inscripciones nuevas" if cambio else "inscripciones que ya estaban"] += 1

        recalcular_todo()
        nuevos_duplicados = PosibleDuplicado.objects.count() - duplicados_antes
        self.stdout.write(self.style.SUCCESS(f"Importado a {ev} ({ev.codigo})"))
        for k in ["filas", "sin correo", "ya estaban en la base", "personas nuevas",
                  "inscripciones nuevas", "inscripciones que ya estaban"]:
            self.stdout.write(f"  {k}: {r[k]}")
        self.stdout.write(f"  posibles duplicados nuevos (revisar en /admin/): {nuevos_duplicados}")
        for texto, n in sin_mapear.most_common():
            self.stdout.write(self.style.WARNING(f"  categoría sin mapear: {texto!r} × {n}"))

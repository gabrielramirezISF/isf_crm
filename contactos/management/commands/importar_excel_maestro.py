"""
Importa el Excel maestro 2026 completo (varias hojas) a un evento. FEAT-002.

Uso:
    python manage.py importar_excel_maestro --evento anual-2026 "Excel Maestro - Evento Anual 2026.xlsx"

Se puede correr varias veces con el Excel actualizado: no duplica personas, roles ni
participaciones (el estado solo avanza). El comando solo lee hojas y llama a los
servicios existentes (buscar_o_crear, invitar, inscribir, marcar_no_puede, asignar_rol);
las reglas viven ahí, no aquí.

Hojas que importa: Inscripciones, Lista invitados, Form No pueden, lista nominadores,
Autoridades a Invitar, Staff, para_mail (apodo).
Hojas fuera de alcance (CORE §7): Check list, Cronograma, Presupuesto, Lluvia de ideas.
Hoja "Maestros" NO se importa todavía: tiene dos tablas sin alinear en la misma hoja
(ver docs/features/FEAT-002), pendiente de confirmar con el aprendiz.
"""
import datetime as dt
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from openpyxl import load_workbook

from contactos.models import Estado, Evento, Rol
from contactos.scoring import recalcular_todo
from contactos.servicios import asignar_rol, buscar_o_crear, buscar_por_correo, inscribir, invitar, marcar_no_puede
from contactos.vinculos import DESCONOCIDO, vinculo_desde_texto


def filas(hoja):
    """Devuelve cada fila como dict {encabezado: valor}, saltando filas vacías."""
    it = hoja.iter_rows(values_only=True)
    encabezados = [str(h).strip() if h else "" for h in next(it, [])]
    for fila in it:
        if any(v not in (None, "") for v in fila):
            yield dict(zip(encabezados, fila))


def columna(fila, *prefijos):
    """Busca una columna por el inicio de su nombre (los títulos varían entre hojas)."""
    for clave, valor in fila.items():
        if clave and any(clave.lower().startswith(p.lower()) for p in prefijos):
            return valor
    return ""


def texto(v):
    return "" if v is None else str(v).strip()


def parsear_fecha(v):
    if isinstance(v, dt.datetime):
        return timezone.make_aware(v) if timezone.is_naive(v) else v
    cadena = texto(v)
    if not cadena:
        return None
    for formato in ("%m/%d/%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return timezone.make_aware(dt.datetime.strptime(cadena, formato))
        except ValueError:
            pass
    return None


def parsear_km(v):
    t = texto(v).lower().replace("aprox", "").replace("km", "").replace(",", ".").strip()
    try:
        return float(t)
    except ValueError:
        return None


def nombre_completo(fila):
    directo = columna(fila, "Nombre Completo")
    if directo:
        return directo
    return " ".join(filter(None, [texto(columna(fila, "Nombre")), texto(columna(fila, "Apellido"))]))


class Command(BaseCommand):
    help = "Importa el Excel maestro 2026 (varias hojas) a un evento. Idempotente."

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
        libro = load_workbook(archivo, read_only=True, data_only=True)

        r = Counter()
        self._inscripciones(libro, ev, r)
        self._lista_invitados(libro, ev, r)
        self._form_no_pueden(libro, ev, r)
        self._roles(libro, ev, "lista nominadores", Rol.NOMINADOR, r)
        self._roles(libro, ev, "Autoridades a Invitar", Rol.AUTORIDAD, r)
        self._roles(libro, ev, "Staff", Rol.STAFF, r)
        self._apodos_para_mail(libro, ev, r)

        recalcular_todo()
        self.stdout.write(self.style.SUCCESS(f"Importado a {ev} ({ev.codigo})"))
        for k, v in sorted(r.items()):
            self.stdout.write(f"  {k}: {v}")

    def _hoja(self, libro, nombre):
        return libro[nombre] if nombre in libro.sheetnames else None

    # ---------------------------------------------------------- Inscripciones

    def _inscripciones(self, libro, ev, r):
        hoja = self._hoja(libro, "Inscripciones")
        if hoja is None:
            return
        for f in filas(hoja):
            correo = columna(f, "Correo electrónico", "Dirección de correo")
            if not correo:
                r["inscripciones: sin correo"] += 1
                continue
            persona, creada = buscar_o_crear(
                correo, columna(f, "Nombre y apellidos"),
                columna(f, "Organización"), columna(f, "Número de contacto"),
            )
            r["personas nuevas" if creada else "personas ya en la base"] += 1
            categoria = columna(f, "Categoría")
            nombre_vinculo, terminado = vinculo_desde_texto(categoria)
            if nombre_vinculo and nombre_vinculo != DESCONOCIDO:
                persona.agregar_vinculo(nombre_vinculo, quien=f"excel maestro {ev.codigo}", terminado=terminado)
            if persona.estado == Estado.FUERA_CHILE:
                persona.cambiar_estado(Estado.CONTACTABLE, motivo=f"Se inscribió al {ev} (Excel maestro)")
            logistica = {
                "restriccion_alimentaria": columna(f, "¿Tienes alguna restricción"),
                "transporte_ida": columna(f, "¿Qué medio de transporte utilizaras para llegar"),
                "transporte_vuelta": columna(f, "¿Qué medio de transporte utilizaras para retornar"),
                "km_ida": parsear_km(columna(f, "¿Cuántos kilómetros de distancia recorrerás en promedio en la IDA")),
                "km_vuelta": parsear_km(columna(f, "¿Cuántos kilómetros de distancia recorrerás en promedio en la VUELTA")),
                "hospedaje": columna(f, "¿Tu asistencia al evento anual requerirá hospedaje"),
            }
            logistica = {k: v for k, v in logistica.items() if v not in (None, "")}
            _, cambio = inscribir(
                ev, persona, "importacion",
                cuando=parsear_fecha(columna(f, "Timestamp")), categoria=categoria, logistica=logistica,
            )
            r["inscripciones nuevas" if cambio else "inscripciones que ya estaban"] += 1

    # -------------------------------------------------------- Lista invitados

    def _lista_invitados(self, libro, ev, r):
        hoja = self._hoja(libro, "Lista invitados")
        if hoja is None:
            return
        for f in filas(hoja):
            correo = columna(f, "Correo")
            if not correo:
                r["lista invitados: sin correo"] += 1
                continue
            persona, creada = buscar_o_crear(correo, nombre_completo(f), columna(f, "ORGANIZACIÓN", "Organización"))
            r["personas nuevas" if creada else "personas ya en la base"] += 1

            alterno = texto(columna(f, "Correo Alternativo")).lower()
            if alterno and alterno not in persona.correos_alternativos.lower():
                persona.correos_alternativos = ", ".join(filter(None, [persona.correos_alternativos, alterno]))
                persona.save(update_fields=["correos_alternativos"])

            asignar_rol(ev, persona, Rol.INVITADO)
            invitar(ev, persona)
            r["roles invitado"] += 1

            revisar = texto(columna(f, "POR REVISAR")).lower()
            if revisar and persona.estado in (Estado.CONTACTABLE, Estado.FUERA_CHILE):
                if "elimina" in revisar:
                    persona.cambiar_estado(Estado.BAJA, motivo=f"Pide ser eliminado: {revisar!r} (Excel maestro)")
                elif "rebot" in revisar or "bloque" in revisar:
                    persona.cambiar_estado(Estado.CORREO_PROBLEMA, motivo=f"{revisar!r} (Excel maestro)")

    # -------------------------------------------------------- Form No pueden

    def _form_no_pueden(self, libro, ev, r):
        hoja = self._hoja(libro, "Form No pueden")
        if hoja is None:
            return
        for f in filas(hoja):
            correo = columna(f, "Correo electrónico", "Dirección de correo")
            if not correo:
                continue
            persona, creada = buscar_o_crear(correo)
            r["personas nuevas" if creada else "personas ya en la base"] += 1
            _, cambio = marcar_no_puede(ev, persona)
            r["no puede: nuevas" if cambio else "no puede: ya estaban"] += 1

    # ----------------------------------------------- roles (nominador/autoridad/staff)

    def _roles(self, libro, ev, nombre_hoja, rol, r):
        hoja = self._hoja(libro, nombre_hoja)
        if hoja is None:
            return
        for f in filas(hoja):
            correo = columna(f, "Correo")
            if not correo:
                r[f"{nombre_hoja}: sin correo"] += 1
                continue
            persona, creada = buscar_o_crear(correo, nombre_completo(f), columna(f, "ORGANIZACIÓN", "Organización"))
            r["personas nuevas" if creada else "personas ya en la base"] += 1
            asignar_rol(ev, persona, rol)
            invitar(ev, persona)
            r[f"roles {rol}"] += 1

    # ------------------------------------------------------------- para_mail

    def _apodos_para_mail(self, libro, ev, r):
        hoja = self._hoja(libro, "para_mail")
        if hoja is None:
            return
        for f in filas(hoja):
            correo = columna(f, "mail")
            if not correo:
                continue
            # Solo actualiza el apodo de alguien que ya existe por otra hoja; este
            # registro de envíos no crea personas nuevas.
            persona = buscar_por_correo(correo)
            if persona is None:
                continue
            apodo = texto(columna(f, "Nombre 2"))
            if apodo and apodo != persona.apodo:
                persona.apodo = apodo
                persona.save(update_fields=["apodo"])
                r["apodos actualizados"] += 1

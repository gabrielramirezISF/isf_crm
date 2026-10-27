"""
Reglas del dominio (migradas desde pseudocodigo/). Las vistas y los comandos llaman
a estas funciones; no repiten la lógica.

    buscar_por_correo / buscar_o_crear   el único lugar donde nace una Persona
    invitar / inscribir / marcar_no_puede / registrar_llegada   (devuelven (participacion, cambio))
    registrar_por_qr                     lo que hace la pantalla pública del check-in
    lista_para_enviar / filas_csv_envio / resumen_embudo / lista_respaldo   salidas

Reglas que no se rompen:
1. Una persona, una ficha: el correo se normaliza y se busca también en los alternativos,
   con comparación EXACTA (ana@x.cl no es juana@x.cl).
2. Quien no se puede contactar no recibe nada (Persona.se_puede_contactar).
3. El estado solo avanza y todo es idempotente; la hora de llegada no se sobrescribe.
4. Datos sensibles (teléfono, restricción alimentaria) nunca salen en un envío ni en el QR.
"""
import unicodedata
from collections import Counter
from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import (
    Grupo, OrigenInscripcion, OrigenLlegada, Participacion, Persona, PosibleDuplicado, RolEvento,
)


def normalizar_correo(correo):
    return (correo or "").strip().lower()


# ------------------------------------------------------------------ personas


def buscar_por_correo(correo, excluir_pk=None):
    """Busca por correo principal o alternativo. Devuelve la Persona o None."""
    correo = normalizar_correo(correo)
    if not correo:
        return None
    personas = Persona.objects.exclude(pk=excluir_pk) if excluir_pk else Persona.objects.all()
    principal = personas.filter(correo=correo).first()
    if principal:
        return principal
    # icontains solo reduce candidatos; la comparación real es exacta.
    for p in personas.filter(correos_alternativos__icontains=correo):
        if correo in p.todos_los_correos():
            return p
    return None


def _tokens(nombre):
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFD", (nombre or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return [t for t in sin_tildes.replace("'", "").split() if t]


def clave_nombre(nombre):
    """Nombre + primer apellido, sin tildes: 'Ignacio López Salazar' -> 'ignacio lopez'."""
    t = _tokens(nombre)
    return " ".join(t[:2]) if len(t) >= 2 else ""


def buscar_por_nombre(nombre, excluir_pk=None):
    clave = clave_nombre(nombre)
    if not clave:
        return []
    # Se compara en Python porque la base no sabe quitar tildes. Son ~1.200 nombres.
    pks = [pk for pk, n in Persona.objects.exclude(pk=excluir_pk).values_list("pk", "nombre")
           if clave_nombre(n) == clave]
    return list(Persona.objects.filter(pk__in=pks))


def buscar_o_crear(correo, nombre="", organizacion="", telefono="", revisar_duplicados=True):
    """Devuelve (persona, creada).

    Si el correo no existe pero el nombre coincide con alguien, crea la persona igual
    (no adivina) y deja un PosibleDuplicado para que Alvaro decida.
    """
    correo = normalizar_correo(correo)
    if not correo:
        raise ValueError("Sin correo no hay identidad")
    existente = buscar_por_correo(correo)
    if existente:
        if telefono and not existente.telefono:
            existente.telefono = telefono.strip()
            existente.save(update_fields=["telefono", "actualizado"])
        return existente, False

    nueva = Persona.objects.create(
        correo=correo, nombre=(nombre or "").strip(), organizacion=(organizacion or "").strip(),
        telefono=(telefono or "").strip(), grupo=Grupo.SIN_CLASIFICAR,
    )
    if revisar_duplicados:
        for parecida in buscar_por_nombre(nombre, excluir_pk=nueva.pk):
            PosibleDuplicado.objects.get_or_create(persona_nueva=nueva, persona_existente=parecida)
    return nueva, True


# ------------------------------------------------------------- participación


def obtener_o_crear(evento, persona):
    return Participacion.objects.get_or_create(evento=evento, persona=persona)[0]


def invitar(evento, persona, cuando=None):
    """Fase 1. Nunca invita a quien no se puede contactar."""
    if not persona.se_puede_contactar:
        return None, False
    p = obtener_o_crear(evento, persona)
    if p.invitado:
        return p, False
    p.invitado = True
    p.fecha_invitacion = cuando or timezone.now()
    p.save(update_fields=["invitado", "fecha_invitacion"])
    return p, True


def inscribir(evento, persona, origen, cuando=None, categoria="", logistica=None):
    """Fase 2. Funciona aunque no estuviera invitado (se enteró por otro lado).

    Si ya estaba inscrito no cambia la fecha, pero sí completa la logística nueva
    (el CSV del formulario se vuelve a importar actualizado).
    """
    if origen not in OrigenInscripcion.values:
        raise ValueError(f"Origen desconocido: {origen}")
    p = obtener_o_crear(evento, persona)
    logistica = {k: v for k, v in (logistica or {}).items() if v not in (None, "")}
    restriccion = logistica.pop("restriccion_alimentaria", "")
    if restriccion:
        p.restriccion_alimentaria = restriccion
    p.logistica.update(logistica)
    if categoria and not p.categoria_formulario:
        p.categoria_formulario = categoria
    cambio = not p.inscrito
    if cambio:
        p.inscrito = True
        p.no_puede = False  # se arrepintió de no poder: vale la inscripción
        p.fecha_inscripcion = cuando or timezone.now()
        p.origen_inscripcion = origen
    p.save()
    return p, cambio


def marcar_no_puede(evento, persona):
    p = obtener_o_crear(evento, persona)
    if p.asistio or p.no_puede:
        return p, False
    p.no_puede = True
    p.save(update_fields=["no_puede"])
    return p, True


def asignar_rol(evento, persona, rol):
    """Qué es la persona en este evento (ADR-007). Independiente de Participación:

    no respeta ni cambia se_puede_contactar; eso lo decide quien llame a invitar().
    """
    return RolEvento.objects.get_or_create(evento=evento, persona=persona, rol=rol)


def registrar_llegada(evento, persona, origen, cuando=None):
    """Fase 4. La usan el QR y el panel. La hora de llegada nunca se sobrescribe.

    El UPDATE condicional hace que dos registros simultáneos no se pisen:
    solo el primero cambia la fila.
    """
    if origen not in OrigenLlegada.values:
        raise ValueError(f"Origen desconocido: {origen}")
    try:
        with transaction.atomic():
            p = obtener_o_crear(evento, persona)
    except IntegrityError:  # otra petición la creó en el mismo instante
        p = Participacion.objects.get(evento=evento, persona=persona)
    cambiadas = (
        Participacion.objects.filter(pk=p.pk).exclude(asistio=True)
        .update(asistio=True, hora_llegada=cuando or timezone.now(), origen_llegada=origen)
    )
    p.refresh_from_db()
    return p, bool(cambiadas)


# ------------------------------------------------------------------ check-in


@dataclass
class Resultado:
    tipo: str  # cerrado | bienvenida | ya_registrado | pedir_datos | error
    mensaje: str


def registrar_por_qr(evento, correo, nombre="", organizacion=""):
    """La lógica de /checkin/<codigo>/. Solo muestra el primer nombre de quien se registra.

    Una persona en baja que llega SÍ se registra; su estado no cambia.
    """
    if not evento.checkin_abierto:
        return Resultado("cerrado", "El registro de asistencia aún no está abierto.")

    correo = normalizar_correo(correo)
    if "@" not in correo or " " in correo:
        return Resultado("error", "Revisa tu correo.")

    persona = buscar_por_correo(correo)
    nueva = persona is None
    if nueva:
        if not (nombre or "").strip():
            return Resultado("pedir_datos", "No te encontramos. Déjanos tu nombre para registrarte.")
        persona, _ = buscar_o_crear(correo, nombre, organizacion)

    _, cambio = registrar_llegada(evento, persona, OrigenLlegada.QR)
    if not cambio:
        return Resultado("ya_registrado", "Ya estabas registrado/a. ¡Disfruta el evento!")
    if nueva:
        return Resultado("bienvenida", f"¡Gracias por venir, {persona.primer_nombre}!")
    return Resultado("bienvenida", f"¡Bienvenido/a, {persona.primer_nombre}!")


# ------------------------------------------------------------------- salidas


def lista_para_enviar(evento, cupo=300, desde=0):
    """Invitados que aún no responden y se pueden contactar, de a `cupo` por semana."""
    pendientes = [
        p for p in evento.participaciones.select_related("persona")
        .filter(invitado=True, inscrito=False, no_puede=False)
        if p.persona.se_puede_contactar
    ]
    pendientes.sort(key=lambda p: p.persona.nombre.lower())
    return pendientes[desde:desde + cupo]


ENCABEZADO_ENVIO = ["persona_id", "nombre", "primer_nombre", "correo", "organizacion"]


def filas_csv_envio(personas_o_participaciones):
    """Filas para la herramienta de correo de Alvaro. Sin datos sensibles."""
    for x in personas_o_participaciones:
        p = getattr(x, "persona", x)
        yield [p.persona_id, p.nombre, p.primer_nombre, p.correo, p.organizacion]


def resumen_embudo(evento):
    ps = list(evento.participaciones.all())
    inscritos = sum(p.inscrito for p in ps)
    asistieron_inscritos = sum(bool(p.asistio) and p.inscrito for p in ps)
    return {
        "invitados": sum(p.invitado for p in ps),
        "inscritos": inscritos,
        "no_pueden": sum(p.no_puede for p in ps),
        "asistieron": sum(bool(p.asistio) for p in ps),
        "asistieron_inscritos": asistieron_inscritos,
        "llegaron_sin_inscripcion": sum(p.llego_sin_inscripcion for p in ps),
        "tasa_asistencia": round(asistieron_inscritos / inscritos, 3) if inscritos else None,
        "inscritos_por_dia": dict(sorted(Counter(
            timezone.localtime(p.fecha_inscripcion).date().isoformat() for p in ps if p.fecha_inscripcion
        ).items())),
    }


def ultimas_llegadas(evento, n=20):
    return (
        evento.participaciones.select_related("persona")
        .filter(asistio=True).order_by("-hora_llegada")[:n]
    )


def lista_respaldo(evento):
    """Para imprimir el día del evento, por si se cae internet. Uso interno en la puerta."""
    return evento.participaciones.select_related("persona").filter(inscrito=True).order_by("persona__nombre")

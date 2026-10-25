"""
Cálculo de puntaje, prioridad y orden sugerido.

Son reglas fijas y explicables (no IA): las mismas entradas dan siempre el mismo
resultado. Los pesos se editan en el admin (modelo Reglas).

"Último evento" y "evento anterior" se toman de los dos años más recientes
cargados en Evento, así que cuando agregues el Evento 2026 las reglas se
desplazan solas.
"""
import math

from .models import GRUPOS_ACTIVOS, Estado, Evento, Persona, Prioridad, Reglas

# Estados que sacan a la persona de la lista de invitación.
PRIORIDAD_POR_ESTADO = {
    Estado.BAJA: Prioridad.NO_INVITAR,
    Estado.NO_CONSIDERAR: Prioridad.NO_INVITAR,
    Estado.CORREO_PROBLEMA: Prioridad.REVISAR_CORREO,
    Estado.DESACTUALIZADO: Prioridad.REVISAR_CONTACTO,
    Estado.FUERA_CHILE: Prioridad.BAJA,
}
INVITABLES = (Prioridad.ALTA, Prioridad.MEDIA, Prioridad.BAJA)
NIVEL = {Prioridad.ALTA: 3, Prioridad.MEDIA: 2, Prioridad.BAJA: 1}


def calcular_puntaje(persona, ultimo, anterior, reglas):
    """`ultimo` y `anterior` son las Participacion de esa persona (o None)."""
    puntos = 0
    if ultimo:
        if ultimo.asistio:
            puntos += reglas.puntos_asistio
        elif ultimo.inscrito:
            puntos += reglas.puntos_inscrito
        if ultimo.respondio:
            puntos += reglas.puntos_respondio
    if anterior and anterior.inscrito:
        puntos += reglas.puntos_inscrito_anterior
    if persona.grupo in GRUPOS_ACTIVOS:
        puntos += reglas.puntos_grupo_activo
    return puntos


def calcular_prioridad(persona, puntaje, reglas):
    if persona.estado in PRIORIDAD_POR_ESTADO:
        return PRIORIDAD_POR_ESTADO[persona.estado]
    if puntaje >= reglas.umbral_alta:
        return Prioridad.ALTA
    if puntaje >= reglas.umbral_media:
        return Prioridad.MEDIA
    return Prioridad.BAJA


def perfil_potencial(persona, reglas):
    """Seis ejes (0-10) para el hexágono de la ficha. Cada eje vale el peso de su regla si la
    persona cumple la señal, y 0 si no. 'Contactable' no suma puntaje: indica si se le puede escribir."""
    anios = list(Evento.objects.order_by("-anio").values_list("anio", flat=True)[:2])
    por_anio = {part.evento.anio: part for part in persona.participaciones.select_related("evento")}
    ultimo = por_anio.get(anios[0]) if anios else None
    anterior = por_anio.get(anios[1]) if len(anios) > 1 else None

    ejes = [
        ("Asistió al último", reglas.puntos_asistio, bool(ultimo and ultimo.asistio)),
        ("Inscrito sin asistir", reglas.puntos_inscrito, bool(ultimo and ultimo.inscrito and not ultimo.asistio)),
        ("Inscrito el anterior", reglas.puntos_inscrito_anterior, bool(anterior and anterior.inscrito)),
        ("Respondió", reglas.puntos_respondio, bool(ultimo and ultimo.respondio)),
        ("Equipo / voluntariado", reglas.puntos_grupo_activo, persona.grupo in GRUPOS_ACTIVOS),
        ("Contactable", 10, persona.estado == Estado.CONTACTABLE),
    ]

    cx = cy = 150
    radio = 95

    def punto(i, valor, extra=0):
        ang = math.radians(-90 + i * 60)
        r = radio * min(max(valor, 0), 10) / 10 + extra
        return cx + r * math.cos(ang), cy + r * math.sin(ang)

    def poligono(valores):
        return " ".join("%.1f,%.1f" % punto(i, v) for i, v in enumerate(valores))

    return {
        "ejes": [
            {"nombre": n, "peso": peso, "cumple": ok,
             "valor": peso if ok else 0,
             "etiqueta": dict(zip(("x", "y"), ("%.1f" % c for c in punto(i, 10, 24)))),
             "extremo": dict(zip(("x", "y"), ("%.1f" % c for c in punto(i, 10))))}
            for i, (n, peso, ok) in enumerate(ejes)
        ],
        "anillos": [poligono([nivel] * 6) for nivel in (2.5, 5, 7.5, 10)],
        "potencial": poligono([peso for _, peso, _ in ejes]),
        "real": poligono([peso if ok else 0 for _, peso, ok in ejes]),
    }


def recalcular_todo():
    """Recalcula puntaje, prioridad y orden de todas las personas. Devuelve un resumen."""
    reglas = Reglas.actuales()
    anios = list(Evento.objects.order_by("-anio").values_list("anio", flat=True)[:2])
    anio_ultimo = anios[0] if anios else None
    anio_anterior = anios[1] if len(anios) > 1 else None

    personas = list(Persona.objects.prefetch_related("participaciones__evento"))
    for p in personas:
        por_anio = {part.evento.anio: part for part in p.participaciones.all()}
        p.puntaje = calcular_puntaje(p, por_anio.get(anio_ultimo), por_anio.get(anio_anterior), reglas)
        p.prioridad = calcular_prioridad(p, p.puntaje, reglas)

    # Orden: primero por prioridad (Alta > Media > Baja), luego puntaje, luego nombre.
    invitables = sorted(
        (p for p in personas if p.prioridad in INVITABLES),
        key=lambda p: (-NIVEL[p.prioridad], -p.puntaje, p.nombre.lower()),
    )
    for p in personas:
        p.orden_sugerido = None
    for i, p in enumerate(invitables, start=1):
        p.orden_sugerido = i

    Persona.objects.bulk_update(personas, ["puntaje", "prioridad", "orden_sugerido"], batch_size=500)
    resumen = {label: 0 for _, label in Prioridad.choices}
    for p in personas:
        resumen[Prioridad(p.prioridad).label] += 1
    resumen["Dentro del cupo"] = min(len(invitables), reglas.cupo_semanal)
    return resumen

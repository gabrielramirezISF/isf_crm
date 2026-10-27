"""
Traducción del texto libre de Alvaro (columna "relación" o "Categoría" del formulario)
a un vínculo del catálogo. Python puro: lo usan la migración de datos y los importadores.

Regla: lo desconocido se reporta, no se inventa.
"""

# Lo que hoy escribe Alvaro -> nombre del vínculo. None = no es un vínculo
# (Histórico / Interesados describen actividad, no relación: se calculan, no se asignan).
MAPA_VINCULOS = {
    "socio": "Socio", "mentor/a": "Mentor/a", "mentores/as": "Mentor/a",
    "alianza": "Alianza", "alianzas": "Alianza",
    "voluntad": "Voluntad", "equipo": "Equipo", "equipo nacional": "Equipo",
    "directorio": "Equipo", "universidad": "Universidad",
    "comunidad": "Comunidad", "comunidades": "Comunidad",
    "cercano": "Cercano", "proveedor": "Proveedor", "proveedores": "Proveedor",
    "contratistas": "Proveedor", "asesores": "Asesor/a", "directorio/asesores": "Equipo",
    "practicante": "Practicante",
    "historicos": None, "histórico": None, "interesados": None, "otro": None,
    "posible alianza": None,
    # Sin mapear a propósito (preguntar a Alvaro): "AQUA", "Fundador(a)".
}

DESCONOCIDO = "?"


def vinculo_desde_texto(texto):
    """Devuelve (nombre_vinculo, terminado), (None, False) o ("?", terminado).

    'ex voluntad BioBio' -> ('Voluntad', True). 'AQUA' -> ('?', False).
    """
    t = (texto or "").strip().lower()
    if not t:
        return None, False
    terminado = t.startswith(("ex ", "ex-"))
    if terminado:
        t = t[3:].strip()
    if t.startswith(("núcleo", "nucleo", "voluntad")):
        return "Voluntad", terminado
    if t in MAPA_VINCULOS:
        return MAPA_VINCULOS[t], terminado
    return DESCONOCIDO, terminado

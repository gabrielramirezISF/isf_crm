"""
Check-in con QR del día del evento (SPEC_checkin_qr.md). La lógica vive en servicios.py.

    /checkin/                         Eventos (con login)
    /checkin/<codigo>/                Pantalla pública: escribe tu correo (sin login)
    /checkin/<codigo>/panel/          Panel del equipo: contador, últimas llegadas, buscador
    /checkin/<codigo>/qr/             QR grande para imprimir (y qr.svg)
    /checkin/<codigo>/asistencia.csv  Exportar asistencia
    /checkin/<codigo>/respaldo/       Lista de inscritos para marcar a mano si se cae internet
"""
import csv

import segno
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import Evento, OrigenLlegada, Persona
from . import servicios


def _url_publica(request, evento):
    """La URL que va dentro del QR. Sin PUBLIC_BASE_URL se usa la de esta petición."""
    ruta = reverse("checkin_publico", args=[evento.codigo])
    if settings.PUBLIC_BASE_URL:
        return settings.PUBLIC_BASE_URL + ruta
    return request.build_absolute_uri(ruta)


def _demasiados_intentos(request):
    """Límite generoso por IP: en el wifi del lugar mucha gente comparte la misma IP."""
    clave = "checkin-intentos:" + request.META.get("REMOTE_ADDR", "")
    cache.add(clave, 0, timeout=60)
    try:
        intentos = cache.incr(clave)
    except ValueError:  # la clave expiró entre add e incr
        cache.set(clave, 1, timeout=60)
        intentos = 1
    return intentos > settings.CHECKIN_LIMITE_POR_MINUTO


# ------------------------------------------------------------------ pública


def publico(request, codigo):
    """GET solo muestra el formulario. POST registra. Solo se muestra el primer nombre."""
    evento = get_object_or_404(Evento, codigo=codigo)
    contexto = {"evento": evento}
    if not evento.checkin_abierto:
        return render(request, "contactos/checkin/publico.html", {**contexto, "cerrado": True})
    if request.method != "POST":
        return render(request, "contactos/checkin/publico.html", contexto)

    if _demasiados_intentos(request):
        contexto["error"] = "Demasiados intentos. Espera un minuto y vuelve a probar."
        return render(request, "contactos/checkin/publico.html", contexto, status=429)

    correo = request.POST.get("correo", "")
    resultado = servicios.registrar_por_qr(
        evento, correo, request.POST.get("nombre", "")[:200], request.POST.get("organizacion", "")[:200],
    )
    if resultado.tipo == "cerrado":
        contexto["cerrado"] = True
    elif resultado.tipo == "error":
        contexto["error"] = resultado.mensaje
    elif resultado.tipo == "pedir_datos":
        contexto.update(pedir_datos=True, correo=servicios.normalizar_correo(correo), aviso=resultado.mensaje)
    else:
        contexto["resultado"] = resultado
    return render(request, "contactos/checkin/publico.html", contexto)


# -------------------------------------------------------------- con login


@login_required
def eventos(request):
    return render(request, "contactos/checkin/eventos.html", {"eventos": Evento.objects.all()})


def _datos_panel(evento):
    embudo = servicios.resumen_embudo(evento)
    return {
        "evento": evento,
        "embudo": embudo,
        "llegadas": servicios.ultimas_llegadas(evento),
    }


@login_required
def panel(request, codigo):
    evento = get_object_or_404(Evento, codigo=codigo)
    q = request.GET.get("q", "").strip()
    encontrados = []
    if len(q) >= 2:
        personas = Persona.objects.filter(
            Q(nombre__icontains=q) | Q(correo__icontains=q) | Q(correos_alternativos__icontains=q)
        ).order_by("nombre")[:15]
        llegados = set(evento.participaciones.filter(asistio=True).values_list("persona_id", flat=True))
        encontrados = [(p, p.pk in llegados) for p in personas]
    return render(request, "contactos/checkin/panel.html", {
        **_datos_panel(evento),
        "q": q,
        "encontrados": encontrados,
        "url_publica": _url_publica(request, evento),
        "sin_url_publica": not settings.PUBLIC_BASE_URL,
        "avisos": evento.faltantes(),
    })


@login_required
def panel_estado(request, codigo):
    """Fragmento HTML que el panel pide cada 15 segundos (contador + últimas llegadas)."""
    evento = get_object_or_404(Evento, codigo=codigo)
    return render(request, "contactos/checkin/_estado.html", _datos_panel(evento))


@login_required
@require_POST
def abrir_cerrar(request, codigo):
    evento = get_object_or_404(Evento, codigo=codigo)
    evento.checkin_abierto = not evento.checkin_abierto
    evento.save(update_fields=["checkin_abierto"])
    messages.success(request, "Registro abierto." if evento.checkin_abierto else "Registro cerrado.")
    return redirect("checkin_panel", codigo=codigo)


@login_required
@require_POST
def marcar_llegada(request, codigo, persona_id):
    """Para quien no tiene batería o no tiene señal."""
    evento = get_object_or_404(Evento, codigo=codigo)
    persona = get_object_or_404(Persona, persona_id=persona_id)
    _, cambio = servicios.registrar_llegada(evento, persona, OrigenLlegada.MANUAL)
    if cambio:
        messages.success(request, f"Llegada registrada: {persona.nombre or persona.correo}.")
    else:
        messages.info(request, f"{persona.nombre or persona.correo} ya estaba registrado/a.")
    return redirect("checkin_panel", codigo=codigo)


@login_required
def qr_svg(request, codigo):
    evento = get_object_or_404(Evento, codigo=codigo)
    qr = segno.make(_url_publica(request, evento), error="m")
    return HttpResponse(qr.svg_inline(scale=10, border=2), content_type="image/svg+xml")


@login_required
def qr_imprimir(request, codigo):
    evento = get_object_or_404(Evento, codigo=codigo)
    return render(request, "contactos/checkin/qr.html", {
        "evento": evento, "url_publica": _url_publica(request, evento),
    })


@login_required
def asistencia_csv(request, codigo):
    """Inscritos y llegados. Sin restricción alimentaria ni teléfono."""
    evento = get_object_or_404(Evento, codigo=codigo)
    respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
    respuesta["Content-Disposition"] = f'attachment; filename="asistencia_{evento.codigo}.csv"'
    respuesta.write("﻿")  # BOM para que Excel lea bien los acentos
    w = csv.writer(respuesta)
    w.writerow(["persona_id", "nombre", "correo", "inscrito", "hora_llegada", "origen_llegada"])
    partes = (evento.participaciones.select_related("persona")
              .filter(Q(inscrito=True) | Q(asistio=True)).order_by("persona__nombre"))
    for p in partes:
        hora = timezone.localtime(p.hora_llegada).strftime("%Y-%m-%d %H:%M:%S") if p.hora_llegada else ""
        w.writerow([p.persona.persona_id, p.persona.nombre, p.persona.correo,
                    "sí" if p.inscrito else "no", hora, p.origen_llegada])
    return respuesta


@login_required
def respaldo(request, codigo):
    evento = get_object_or_404(Evento, codigo=codigo)
    return render(request, "contactos/checkin/respaldo.html", {
        "evento": evento, "inscritos": servicios.lista_respaldo(evento),
    })

"""
Pantallas de uso diario (con Bootstrap). El admin de Django sigue disponible
en /admin/ para todo lo demás.

    /                 Resumen
    /invitar/         Invitar esta semana (por bloques del cupo semanal)
    /personas/        Buscar y filtrar personas
    /personas/<id>/   Ficha con historial y botones de estado
    /nueva/           Agregar un contacto (avisa si el correo ya existe)
    /reglas/          Ajustar de 1 a 10 cuánto pesa cada señal de prioridad
"""
import csv
import math

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .admin import PersonaForm
from .models import CambioEstado, Estado, Evento, Grupo, Persona, Prioridad, Reglas
from .scoring import perfil_potencial, recalcular_todo


def _rango_semana(semana, cupo):
    desde = (semana - 1) * cupo + 1
    return desde, desde + cupo - 1


@login_required
def resumen(request):
    reglas = Reglas.actuales()
    por_prioridad = dict(Persona.objects.values_list("prioridad").annotate(n=Count("id")))
    por_grupo = dict(Persona.objects.values_list("grupo").annotate(n=Count("id")))
    tarjetas = [
        (label, por_prioridad.get(value, 0), Persona.COLOR_PRIORIDAD[value], value)
        for value, label in Prioridad.choices
    ]
    grupos = [(label, por_grupo.get(value, 0), value) for value, label in Grupo.choices]
    eventos = Evento.objects.annotate(
        invitados=Count("participaciones", filter=Q(participaciones__invitado=True)),
        inscritos=Count("participaciones", filter=Q(participaciones__inscrito=True)),
        asistieron=Count("participaciones", filter=Q(participaciones__asistio=True)),
    )
    invitables = Persona.objects.filter(orden_sugerido__isnull=False).count()
    return render(request, "contactos/resumen.html", {
        "total": Persona.objects.count(),
        "tarjetas": tarjetas,
        "grupos": grupos,
        "eventos": eventos,
        "cupo": reglas.cupo_semanal,
        "semanas": math.ceil(invitables / reglas.cupo_semanal) if reglas.cupo_semanal else 0,
        "invitables": invitables,
        "ultimos_cambios": CambioEstado.objects.select_related("persona", "usuario")[:6],
    })


@login_required
def invitar(request):
    cupo = Reglas.actuales().cupo_semanal
    invitables = Persona.objects.filter(orden_sugerido__isnull=False).count()
    n_semanas = max(1, math.ceil(invitables / cupo)) if cupo else 1
    try:
        semana = min(max(1, int(request.GET.get("semana", 1))), n_semanas)
    except ValueError:
        semana = 1
    desde, hasta = _rango_semana(semana, cupo)
    personas = Persona.objects.filter(orden_sugerido__gte=desde, orden_sugerido__lte=hasta).order_by("orden_sugerido")

    if request.GET.get("formato") == "csv":
        respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
        respuesta["Content-Disposition"] = f'attachment; filename="invitacion_semana_{semana}.csv"'
        respuesta.write("﻿")
        w = csv.writer(respuesta)
        w.writerow(["orden", "persona_id", "nombre", "correo", "organizacion", "prioridad"])
        for p in personas:
            w.writerow([p.orden_sugerido, p.persona_id, p.nombre, p.correo, p.organizacion, p.get_prioridad_display()])
        return respuesta

    q = request.GET.get("q", "").strip()
    if q:
        personas = personas.filter(Q(nombre__icontains=q) | Q(correo__icontains=q) | Q(organizacion__icontains=q))
    return render(request, "contactos/invitar.html", {
        "personas": personas, "semana": semana, "semanas": range(1, n_semanas + 1),
        "desde": desde, "hasta": min(hasta, invitables), "cupo": cupo, "q": q,
    })


@login_required
def personas(request):
    qs = Persona.objects.all()
    q = request.GET.get("q", "").strip()
    filtros = {k: request.GET.get(k, "") for k in ("prioridad", "estado", "grupo")}
    if q:
        qs = qs.filter(
            Q(nombre__icontains=q) | Q(correo__icontains=q) | Q(correos_alternativos__icontains=q)
            | Q(organizacion__icontains=q) | Q(persona_id__iexact=q)
        )
    for campo, valor in filtros.items():
        if valor:
            qs = qs.filter(**{campo: valor})
    pagina = Paginator(qs.order_by("orden_sugerido", "nombre"), 50).get_page(request.GET.get("page"))
    params = request.GET.copy(); params.pop("page", None)
    return render(request, "contactos/personas.html", {
        "pagina": pagina, "q": q, "filtros": filtros, "params": params.urlencode(),
        "total": qs.count(),
        "selects": [
            ("prioridad", "Prioridad", Prioridad.choices, filtros["prioridad"]),
            ("estado", "Estado", Estado.choices, filtros["estado"]),
            ("grupo", "Grupo", Grupo.choices, filtros["grupo"]),
        ],
    })


@login_required
def ficha(request, persona_id):
    p = get_object_or_404(Persona, persona_id=persona_id)
    reglas = Reglas.actuales()
    return render(request, "contactos/ficha.html", {
        "p": p,
        "radar": perfil_potencial(p, reglas),
        "participaciones": p.participaciones.select_related("evento").order_by("-evento__anio"),
        "cambios": p.cambios_estado.select_related("usuario"),
        "estados": Estado.choices,
        "en_cupo": bool(p.orden_sugerido and p.orden_sugerido <= reglas.cupo_semanal),
    })


CAMPOS_PESOS = ["puntos_asistio", "puntos_inscrito", "puntos_inscrito_anterior", "puntos_respondio", "puntos_grupo_activo"]


class ReglasForm(forms.ModelForm):
    class Meta:
        model = Reglas
        fields = CAMPOS_PESOS + ["umbral_alta", "umbral_media", "cupo_semanal"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre in CAMPOS_PESOS:
            self.fields[nombre] = forms.IntegerField(
                label=self.fields[nombre].label, min_value=1, max_value=10,
                widget=forms.NumberInput(attrs={"type": "range", "min": 1, "max": 10, "step": 1, "class": "form-range"}),
            )
        for nombre in ("umbral_alta", "umbral_media", "cupo_semanal"):
            self.fields[nombre] = forms.IntegerField(
                label=self.fields[nombre].label, min_value=1,
                widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            )

    def clean(self):
        datos = super().clean()
        alta, media = datos.get("umbral_alta"), datos.get("umbral_media")
        if alta is not None and media is not None and alta < media:
            self.add_error("umbral_alta", "El umbral de Alta no puede ser menor que el de Media.")
        return datos


@login_required
def reglas(request):
    form = ReglasForm(request.POST or None, instance=Reglas.actuales())
    if request.method == "POST" and form.is_valid():
        form.save()
        resumen = recalcular_todo()
        messages.success(
            request,
            "Reglas guardadas y prioridades recalculadas: "
            f"{resumen['Alta']} Alta, {resumen['Media']} Media, {resumen['Baja']} Baja.",
        )
        return redirect("reglas")
    return render(request, "contactos/reglas.html", {
        "form": form,
        "pesos": [form[n] for n in CAMPOS_PESOS],
    })


@login_required
@require_POST
def cambiar_estado(request, persona_id):
    p = get_object_or_404(Persona, persona_id=persona_id)
    nuevo = request.POST.get("estado")
    if nuevo not in Estado.values:
        messages.error(request, "Estado no válido.")
    else:
        motivo = request.POST.get("motivo", "").strip() or "Cambio desde la interfaz"
        if p.cambiar_estado(nuevo, motivo=motivo, usuario=request.user):
            recalcular_todo()
            messages.success(request, f"{p.nombre or p.correo}: ahora «{Estado(nuevo).label}».")
        else:
            messages.info(request, "Ya tenía ese estado.")
    siguiente = request.POST.get("next", "")
    if url_has_allowed_host_and_scheme(siguiente, allowed_hosts={request.get_host()}):
        return redirect(siguiente)
    return redirect("ficha", persona_id=p.persona_id)


class NuevaPersonaForm(PersonaForm):
    class Meta(PersonaForm.Meta):
        fields = ["nombre", "correo", "correos_alternativos", "telefono", "organizacion", "relacion", "grupo", "comentarios"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, campo in self.fields.items():
            campo.widget.attrs["class"] = "form-select" if nombre == "grupo" else "form-control"
        self.fields["correos_alternativos"].widget.attrs["rows"] = 2
        self.fields["comentarios"].widget.attrs["rows"] = 3


@login_required
def nueva(request):
    form = NuevaPersonaForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        p = form.save()
        recalcular_todo()
        messages.success(request, f"Contacto creado: {p}.")
        return redirect("ficha", persona_id=p.persona_id)
    return render(request, "contactos/nueva.html", {"form": form})

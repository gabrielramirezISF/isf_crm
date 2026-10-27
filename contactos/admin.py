"""
Panel de administración: es la "app" que usa Alvaro.

- Lista de personas con filtros (prioridad, estado, grupo, dentro del cupo).
- Ficha con su historial por evento y sus cambios de estado.
- Acciones masivas: marcar baja, exportar CSV, recalcular prioridades.
- Al agregar o editar, avisa si el correo ya existe en otra persona.
"""
import csv

from django import forms
from django.contrib import admin, messages
from django.db.models import Q
from django.http import HttpResponse

from .models import (
    CambioEstado, Estado, Evento, Participacion, Persona, PersonaVinculo, PosibleDuplicado, Reglas, RolEvento,
    Vinculo,
)
from .scoring import recalcular_todo
from .servicios import buscar_por_correo

admin.site.site_header = "ISF · Contactos Evento Anual"
admin.site.site_title = "ISF Contactos"
admin.site.index_title = "Administración"


class PersonaForm(forms.ModelForm):
    class Meta:
        model = Persona
        fields = "__all__"

    def _ya_existe(self, correo):
        return buscar_por_correo(correo, excluir_pk=self.instance.pk)

    def clean_correo(self):
        correo = self.cleaned_data["correo"].strip().lower()
        existente = self._ya_existe(correo)
        if existente:
            raise forms.ValidationError(f"Este correo ya pertenece a {existente}.")
        return correo

    def clean_correos_alternativos(self):
        texto = self.cleaned_data.get("correos_alternativos", "")
        correos = [c.strip().lower() for c in texto.split(",") if c.strip()]
        for c in correos:
            existente = self._ya_existe(c)
            if existente:
                raise forms.ValidationError(f"{c} ya pertenece a {existente}.")
        return ", ".join(correos)


class DentroDelCupoFilter(admin.SimpleListFilter):
    title = "dentro del cupo semanal"
    parameter_name = "cupo"

    def lookups(self, request, model_admin):
        return [("si", "Sí"), ("no", "No")]

    def queryset(self, request, queryset):
        cupo = Reglas.actuales().cupo_semanal
        if self.value() == "si":
            return queryset.filter(orden_sugerido__lte=cupo)
        if self.value() == "no":
            return queryset.filter(Q(orden_sugerido__gt=cupo) | Q(orden_sugerido__isnull=True))
        return queryset


class ParticipacionInline(admin.TabularInline):
    model = Participacion
    extra = 0
    fields = ["evento", "invitado", "inscrito", "fecha_inscripcion", "respondio", "no_puede", "asistio",
              "hora_llegada", "origen_llegada", "categoria_formulario"]


class PersonaVinculoInline(admin.TabularInline):
    model = PersonaVinculo
    extra = 0
    fields = ["vinculo", "desde", "hasta", "asignado_por"]


class RolEventoInline(admin.TabularInline):
    model = RolEvento
    extra = 0
    fields = ["evento", "rol", "creado"]
    readonly_fields = ["creado"]


class CambioEstadoInline(admin.TabularInline):
    model = CambioEstado
    extra = 0
    can_delete = False
    fields = ["fecha", "estado_anterior", "estado_nuevo", "motivo", "usuario"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False


def _cambiar_estado_masivo(request, queryset, estado, motivo):
    cambiadas = sum(p.cambiar_estado(estado, motivo=motivo, usuario=request.user) for p in queryset)
    recalcular_todo()
    return cambiadas


@admin.register(Persona)
class PersonaAdmin(admin.ModelAdmin):
    form = PersonaForm
    list_display = ["persona_id", "nombre", "correo", "organizacion", "grupo", "estado", "prioridad", "puntaje", "orden_sugerido"]
    list_display_links = ["persona_id", "nombre"]
    list_filter = [DentroDelCupoFilter, "prioridad", "estado", "grupo", "participaciones__evento", "posible_socio_actual"]
    search_fields = ["persona_id", "nombre", "correo", "correos_alternativos", "organizacion", "relacion"]
    readonly_fields = ["persona_id", "puntaje", "prioridad", "orden_sugerido", "creado", "actualizado"]
    fieldsets = [
        (None, {"fields": ["persona_id", "nombre", "apodo", "correo", "correos_alternativos", "telefono", "rut"]}),
        ("Relación con ISF", {"fields": ["organizacion", "relacion", "grupo", "posible_socio_actual"]}),
        ("Estado", {"fields": ["estado", "comentarios"]}),
        ("Prioridad (calculada)", {"fields": ["puntaje", "prioridad", "orden_sugerido"]}),
        ("Registro", {"fields": ["creado", "actualizado"], "classes": ["collapse"]}),
    ]
    inlines = [PersonaVinculoInline, ParticipacionInline, RolEventoInline, CambioEstadoInline]
    list_per_page = 50
    actions = ["marcar_baja", "marcar_correo_problema", "marcar_contactable", "exportar_csv", "recalcular"]

    def save_model(self, request, obj, form, change):
        if change and "estado" in form.changed_data:
            anterior = Persona.objects.get(pk=obj.pk).estado
            CambioEstado.objects.create(
                persona=obj, estado_anterior=anterior, estado_nuevo=obj.estado,
                motivo="Editado en la ficha", usuario=request.user,
            )
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        recalcular_todo()

    @admin.action(description="Marcar como BAJA SOLICITADA (no volver a contactar)")
    def marcar_baja(self, request, queryset):
        n = _cambiar_estado_masivo(request, queryset, Estado.BAJA, "Baja solicitada (acción masiva)")
        self.message_user(request, f"{n} persona(s) marcadas como baja.", messages.SUCCESS)

    @admin.action(description="Marcar correo con problema (rebotó)")
    def marcar_correo_problema(self, request, queryset):
        n = _cambiar_estado_masivo(request, queryset, Estado.CORREO_PROBLEMA, "Correo rebotó")
        self.message_user(request, f"{n} persona(s) marcadas con correo con problema.", messages.SUCCESS)

    @admin.action(description="Volver a Contactable")
    def marcar_contactable(self, request, queryset):
        n = _cambiar_estado_masivo(request, queryset, Estado.CONTACTABLE, "Reactivado")
        self.message_user(request, f"{n} persona(s) vuelven a contactable.", messages.SUCCESS)

    @admin.action(description="Exportar seleccionadas a CSV (para enviar correos)")
    def exportar_csv(self, request, queryset):
        respuesta = HttpResponse(content_type="text/csv; charset=utf-8")
        respuesta["Content-Disposition"] = 'attachment; filename="invitacion.csv"'
        respuesta.write("﻿")  # BOM para que Excel lea bien los acentos
        w = csv.writer(respuesta)
        w.writerow(["orden", "persona_id", "nombre", "correo", "organizacion", "prioridad"])
        # Nunca exportar a quien no se debe contactar, aunque esté seleccionado.
        for p in queryset.exclude(prioridad__in=["no_invitar", "revisar_correo", "revisar_contacto"]).order_by("orden_sugerido"):
            w.writerow([p.orden_sugerido, p.persona_id, p.nombre, p.correo, p.organizacion, p.get_prioridad_display()])
        return respuesta

    @admin.action(description="Recalcular prioridades de todos")
    def recalcular(self, request, queryset):
        resumen = recalcular_todo()
        self.message_user(request, "Recalculado: " + ", ".join(f"{k}: {v}" for k, v in resumen.items()))


@admin.register(Participacion)
class ParticipacionAdmin(admin.ModelAdmin):
    list_display = ["persona", "evento", "invitado", "inscrito", "respondio", "asistio", "fecha_inscripcion", "hora_llegada", "origen_llegada"]
    list_filter = ["evento", "inscrito", "asistio", "respondio", "invitado", "no_puede", "origen_inscripcion", "origen_llegada"]
    search_fields = ["persona__nombre", "persona__correo"]
    autocomplete_fields = ["persona"]


@admin.register(RolEvento)
class RolEventoAdmin(admin.ModelAdmin):
    list_display = ["persona", "evento", "rol", "creado"]
    list_filter = ["evento", "rol"]
    search_fields = ["persona__nombre", "persona__correo"]
    autocomplete_fields = ["persona"]


@admin.register(Evento)
class EventoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "codigo", "tipo", "anio", "fecha", "estado", "checkin_abierto"]
    list_filter = ["tipo", "estado"]
    fields = ["nombre", "codigo", "tipo", "fecha", "anio", "estado", "lugar", "cupo_maximo", "checkin_abierto"]


@admin.register(Vinculo)
class VinculoAdmin(admin.ModelAdmin):
    list_display = ["nombre", "activo"]

    def has_delete_permission(self, request, obj=None):
        return False  # se archiva, no se borra


@admin.register(PosibleDuplicado)
class PosibleDuplicadoAdmin(admin.ModelAdmin):
    list_display = ["persona_nueva", "persona_existente", "detectado", "resuelto"]
    list_filter = ["resuelto"]
    list_editable = ["resuelto"]
    readonly_fields = ["persona_nueva", "persona_existente", "detectado"]

    def has_add_permission(self, request):
        return False


@admin.register(CambioEstado)
class CambioEstadoAdmin(admin.ModelAdmin):
    list_display = ["fecha", "persona", "estado_anterior", "estado_nuevo", "motivo", "usuario"]
    list_filter = ["estado_nuevo", "usuario"]
    search_fields = ["persona__nombre", "persona__correo"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False  # el historial no se edita


@admin.register(Reglas)
class ReglasAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not Reglas.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        resumen = recalcular_todo()
        self.message_user(request, "Prioridades recalculadas: " + ", ".join(f"{k}: {v}" for k, v in resumen.items()))

"""
Modelos del CRM de contactos de ISF.

La idea central: una PERSONA existe una sola vez (una fila por correo), y todo
lo que pasa con ella en cada evento vive en PARTICIPACION. Así, el próximo año
no se rehace la lista: solo se agregan participaciones para el Evento 2026.

CambioEstado guarda quién cambió el estado de una persona y cuándo; es la
trazabilidad de bajas que pedía Alvaro.
"""
from django.conf import settings
from django.db import models


class Grupo(models.TextChoices):
    EQUIPO = "equipo", "Equipo ISF"
    VOLUNTARIADO = "voluntariado", "Voluntariado activo"
    EX = "ex", "Ex voluntariado / histórico"
    ALIADOS = "aliados", "Socios y aliados"
    CERCANOS = "cercanos", "Cercanos / otros"
    SIN_CLASIFICAR = "sin_clasificar", "Sin clasificar"


# Grupos que suman puntos por estar activos en la fundación.
GRUPOS_ACTIVOS = {Grupo.EQUIPO, Grupo.VOLUNTARIADO}


class Estado(models.TextChoices):
    CONTACTABLE = "contactable", "Contactable"
    BAJA = "baja", "Baja solicitada"
    NO_CONSIDERAR = "no_considerar", "No considerar (decisión interna)"
    CORREO_PROBLEMA = "correo_problema", "Correo con problema"
    DESACTUALIZADO = "desactualizado", "Contacto desactualizado"
    FUERA_CHILE = "fuera_chile", "Fuera de Chile"


class Prioridad(models.TextChoices):
    ALTA = "alta", "Alta"
    MEDIA = "media", "Media"
    BAJA = "baja", "Baja"
    REVISAR_CORREO = "revisar_correo", "Revisar correo"
    REVISAR_CONTACTO = "revisar_contacto", "Revisar contacto"
    NO_INVITAR = "no_invitar", "No invitar"


class Persona(models.Model):
    persona_id = models.CharField("ID", max_length=10, unique=True, editable=False)
    correo = models.EmailField(unique=True)
    correos_alternativos = models.TextField(
        blank=True, help_text="Otros correos de la misma persona, separados por coma."
    )
    nombre = models.CharField(max_length=200, blank=True)
    organizacion = models.CharField("organización", max_length=200, blank=True)
    relacion = models.CharField(
        "relación", max_length=100, blank=True,
        help_text="Categoría original de los Excel (Mentor/a, Núcleo RM, Socio...).",
    )
    grupo = models.CharField(max_length=20, choices=Grupo.choices, default=Grupo.SIN_CLASIFICAR)
    telefono = models.CharField("teléfono", max_length=30, blank=True)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.CONTACTABLE)
    comentarios = models.TextField(blank=True)
    posible_socio_actual = models.BooleanField(
        default=False, help_text="Cruce por nombre con la hoja 'Socios actuales'. Verificar."
    )

    # Campos calculados por contactos/scoring.py. No se editan a mano.
    puntaje = models.IntegerField(default=0, editable=False)
    prioridad = models.CharField(
        max_length=20, choices=Prioridad.choices, default=Prioridad.BAJA, editable=False
    )
    orden_sugerido = models.PositiveIntegerField(null=True, blank=True, editable=False)

    creado = models.DateTimeField(auto_now_add=True)
    actualizado = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["orden_sugerido", "nombre"]

    def __str__(self):
        return f"{self.nombre or self.correo} ({self.persona_id})"

    def save(self, *args, **kwargs):
        self.correo = self.correo.strip().lower()
        if not self.persona_id:
            self.persona_id = self._siguiente_id()
        super().save(*args, **kwargs)

    @staticmethod
    def _siguiente_id():
        ultimo = Persona.objects.order_by("-persona_id").values_list("persona_id", flat=True).first()
        numero = int(ultimo[1:]) + 1 if ultimo else 1
        return f"P{numero:04d}"

    # Colores de Bootstrap para las etiquetas de la interfaz (no se guardan en la base).
    COLOR_PRIORIDAD = {
        "alta": "success", "media": "primary", "baja": "secondary",
        "revisar_correo": "warning", "revisar_contacto": "warning", "no_invitar": "danger",
    }
    COLOR_ESTADO = {
        "contactable": "success", "baja": "danger", "no_considerar": "danger",
        "correo_problema": "warning", "desactualizado": "warning", "fuera_chile": "info",
    }

    @property
    def color_prioridad(self):
        return self.COLOR_PRIORIDAD.get(self.prioridad, "secondary")

    @property
    def color_estado(self):
        return self.COLOR_ESTADO.get(self.estado, "secondary")

    def todos_los_correos(self):
        alternativos = [c.strip().lower() for c in self.correos_alternativos.split(",") if c.strip()]
        return [self.correo, *alternativos]

    def cambiar_estado(self, nuevo, motivo="", usuario=None):
        """Cambia el estado y deja registro en CambioEstado. Usar siempre esto."""
        if nuevo == self.estado:
            return False
        CambioEstado.objects.create(
            persona=self, estado_anterior=self.estado, estado_nuevo=nuevo,
            motivo=motivo, usuario=usuario,
        )
        self.estado = nuevo
        self.save(update_fields=["estado", "actualizado"])
        return True


class Evento(models.Model):
    nombre = models.CharField(max_length=100)
    anio = models.PositiveIntegerField("año", unique=True)
    fecha = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-anio"]

    def __str__(self):
        return self.nombre


class Participacion(models.Model):
    """Qué hizo una persona en un evento: invitada, inscrita, respondió, asistió."""

    persona = models.ForeignKey(Persona, on_delete=models.CASCADE, related_name="participaciones")
    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name="participaciones")
    invitado = models.BooleanField(default=False)
    inscrito = models.BooleanField(default=False)
    fecha_inscripcion = models.DateTimeField(null=True, blank=True)
    respondio = models.BooleanField(
        "respondió", default=False,
        help_text="Respondió la invitación (aunque fuera para decir que no puede).",
    )
    asistio = models.BooleanField("asistió", null=True, blank=True, help_text="Vacío = sin dato.")
    categoria_formulario = models.CharField(max_length=100, blank=True)
    restriccion_alimentaria = models.CharField(
        "restricción alimentaria", max_length=200, blank=True,
        help_text="Dato sensible: usar solo para el catering del evento.",
    )

    class Meta:
        unique_together = [("persona", "evento")]
        verbose_name = "participación"
        verbose_name_plural = "participaciones"

    def __str__(self):
        return f"{self.persona} en {self.evento}"


class CambioEstado(models.Model):
    """Historial de cambios de estado: la trazabilidad de bajas y cambios."""

    persona = models.ForeignKey(Persona, on_delete=models.CASCADE, related_name="cambios_estado")
    estado_anterior = models.CharField(max_length=20, choices=Estado.choices)
    estado_nuevo = models.CharField(max_length=20, choices=Estado.choices)
    motivo = models.CharField(max_length=300, blank=True)
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    fecha = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-fecha"]
        verbose_name = "cambio de estado"
        verbose_name_plural = "cambios de estado"

    def __str__(self):
        return f"{self.persona}: {self.estado_anterior} → {self.estado_nuevo}"


class Reglas(models.Model):
    """Pesos de la prioridad. Existe una sola fila (se edita desde el admin)."""

    puntos_asistio = models.IntegerField("puntos: asistió al último evento", default=5)
    puntos_inscrito = models.IntegerField("puntos: se inscribió al último evento sin asistir", default=3)
    puntos_inscrito_anterior = models.IntegerField("puntos: se inscribió al evento anterior", default=2)
    puntos_respondio = models.IntegerField("puntos: respondió la invitación del último evento", default=1)
    puntos_grupo_activo = models.IntegerField("puntos: Equipo ISF o Voluntariado activo", default=1)
    umbral_alta = models.IntegerField("prioridad Alta si puntaje ≥", default=5)
    umbral_media = models.IntegerField("prioridad Media si puntaje ≥", default=2)
    cupo_semanal = models.PositiveIntegerField("cupo de correos por semana", default=300)

    class Meta:
        verbose_name = "reglas de prioridad"
        verbose_name_plural = "reglas de prioridad"

    def __str__(self):
        return "Reglas de prioridad"

    def save(self, *args, **kwargs):
        self.pk = 1  # siempre la misma fila
        super().save(*args, **kwargs)

    @classmethod
    def actuales(cls):
        return cls.objects.get_or_create(pk=1)[0]

"""
Modelos del CRM de contactos de ISF.

La idea central: una PERSONA existe una sola vez (una fila por correo), y todo
lo que pasa con ella en cada evento vive en PARTICIPACION. Así, el próximo año
no se rehace la lista: solo se agregan participaciones para el Evento 2026.

CambioEstado guarda quién cambió el estado de una persona y cuándo; es la
trazabilidad de bajas que pedía Alvaro.

Vinculo (qué es la persona para ISF, con fechas) convive con `grupo` hasta
diciembre: scoring.py todavía usa `grupo`.
"""
import datetime as dt

from django.conf import settings
from django.db import models
from django.utils.text import slugify


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


# La ÚNICA regla que decide si alguien puede recibir algo (invitación, correo, CSV).
# "Fuera de Chile" sigue invitable: la etiqueta no es confiable (en 2026 se inscribieron
# personas marcadas así). "Desactualizado" queda en revisión, como siempre.
ESTADOS_CONTACTABLES = {Estado.CONTACTABLE, Estado.FUERA_CHILE}


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
    apodo = models.CharField(
        max_length=100, blank=True, help_text="Nombre usado en el correo, si es distinto (Maestros)."
    )
    rut = models.CharField(
        "RUT", max_length=20, blank=True,
        help_text="Solo dato de contacto: no identifica a la persona ni se exige único.",
    )
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
    vinculos = models.ManyToManyField("Vinculo", through="PersonaVinculo", related_name="personas", blank=True)

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

    @property
    def primer_nombre(self):
        """Lo único que puede mostrar una pantalla pública."""
        partes = self.nombre.split()
        return partes[0].capitalize() if partes else ""

    @property
    def se_puede_contactar(self):
        return self.estado in ESTADOS_CONTACTABLES

    # --- vínculos: se cierran con fecha, no se borran ---------------------
    def tiene_vinculo(self, nombre):
        return self.asignaciones.filter(vinculo__nombre=nombre, hasta__isnull=True).exists()

    def agregar_vinculo(self, nombre, quien="", terminado=False):
        """Devuelve True si agregó. `terminado` = sabemos que fue ("ex ..."), no cuándo terminó."""
        if self.tiene_vinculo(nombre):
            return False
        vinculo, _ = Vinculo.objects.get_or_create(nombre=nombre)
        hoy = dt.date.today()
        PersonaVinculo.objects.create(
            persona=self, vinculo=vinculo, desde=hoy, hasta=hoy if terminado else None,
            asignado_por=quien + (" (ex)" if terminado else ""),
        )
        return True

    def terminar_vinculo(self, nombre, quien=""):
        return self.asignaciones.filter(vinculo__nombre=nombre, hasta__isnull=True).update(hasta=dt.date.today())

    @property
    def vinculos_vigentes(self):
        return list(self.asignaciones.filter(hasta__isnull=True).values_list("vinculo__nombre", flat=True))

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


class TipoEvento(models.TextChoices):
    ANUAL = "anual", "Evento anual"
    MENTORIA = "mentoria", "Mentoría"
    CLINICA = "clinica", "Clínica"
    REUNION = "reunion", "Reunión"
    OTRO = "otro", "Otro"


class EstadoEvento(models.TextChoices):
    """Ciclo de vida. Solo se avanza hacia abajo."""
    BORRADOR = "borrador", "Borrador"
    CONVOCANDO = "convocando", "Convocando"
    INSCRIPCION_ABIERTA = "inscripcion_abierta", "Inscripción abierta"
    EN_CURSO = "en_curso", "En curso"
    CERRADO = "cerrado", "Cerrado"


class Evento(models.Model):
    """Cualquier actividad de ISF. Crearlo es barato: nombre, tipo y fecha bastan."""

    codigo = models.SlugField(
        "código", unique=True, blank=True,
        help_text="Va en la URL del QR (por ejemplo anual-2026). Si se deja vacío: tipo-año.",
    )
    nombre = models.CharField(max_length=100)
    tipo = models.CharField(max_length=20, choices=TipoEvento.choices, default=TipoEvento.ANUAL)
    anio = models.PositiveIntegerField("año", blank=True, help_text="Si se deja vacío, se toma de la fecha.")
    fecha = models.DateField(null=True, blank=True)
    estado = models.CharField(max_length=20, choices=EstadoEvento.choices, default=EstadoEvento.BORRADOR)
    lugar = models.CharField(max_length=200, blank=True)
    cupo_maximo = models.PositiveIntegerField("cupo máximo", null=True, blank=True)
    checkin_abierto = models.BooleanField(
        "registro de asistencia abierto", default=False,
        help_text="Se abre y se cierra a mano desde el panel, sin depender del estado.",
    )

    class Meta:
        ordering = ["-anio", "-fecha"]

    def __str__(self):
        return self.nombre

    def save(self, *args, **kwargs):
        if not self.anio and self.fecha:
            self.anio = self.fecha.year
        if not self.codigo:
            base = slugify(f"{self.tipo}-{self.anio}")
            codigo, n = base, 2
            while Evento.objects.filter(codigo=codigo).exclude(pk=self.pk).exists():
                codigo, n = f"{base}-{n}", n + 1
            self.codigo = codigo
        super().save(*args, **kwargs)

    @property
    def fecha_orden(self):
        """Para ordenar por fecha cuando falta: se asume fin de año."""
        return self.fecha or dt.date(self.anio, 12, 31)

    # --- ciclo de vida: los requisitos son AVISOS, no bloqueos ------------
    def faltantes(self):
        falta = []
        if not self.lugar:
            falta.append("definir el lugar")
        if self.cupo_maximo is None:
            falta.append("definir el cupo máximo")
        if not self.participaciones.filter(invitado=True).exists():
            falta.append("cargar la lista de invitados")
        return falta

    def avanzar(self):
        """Pasa al siguiente estado y devuelve los avisos pendientes. No toca checkin_abierto."""
        valores = EstadoEvento.values
        i = valores.index(self.estado)
        avisos = self.faltantes()
        self.estado = valores[min(i + 1, len(valores) - 1)]
        self.save(update_fields=["estado"])
        return avisos

    @property
    def inscripciones_llenas(self):
        if self.cupo_maximo is None:
            return False
        return self.participaciones.filter(inscrito=True).count() >= self.cupo_maximo


class OrigenInscripcion(models.TextChoices):
    IMPORTACION = "importacion", "Importación"
    FORMULARIO = "formulario", "Formulario"
    MANUAL = "manual", "Manual"
    QR = "qr", "QR"


class OrigenLlegada(models.TextChoices):
    """Por dónde se registró la llegada. "Sin inscripción" no es un canal: se calcula."""
    QR = "qr", "QR"
    MANUAL = "manual", "Manual (panel)"


class Participacion(models.Model):
    """Qué hizo una persona en un evento: invitado → inscrito → asistió (o no puede).

    El estado solo avanza y todo es idempotente: repetir una operación no cambia fechas.
    """

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
    no_puede = models.BooleanField("no puede asistir", default=False)
    fecha_invitacion = models.DateTimeField("fecha de invitación", null=True, blank=True)
    hora_llegada = models.DateTimeField(null=True, blank=True)
    origen_inscripcion = models.CharField(max_length=20, choices=OrigenInscripcion.choices, blank=True)
    origen_llegada = models.CharField(max_length=20, choices=OrigenLlegada.choices, blank=True)
    logistica = models.JSONField(
        "logística", default=dict, blank=True,
        help_text="Transporte, km, hospedaje... (la restricción alimentaria tiene su propio campo).",
    )

    class Meta:
        unique_together = [("persona", "evento")]
        verbose_name = "participación"
        verbose_name_plural = "participaciones"

    def __str__(self):
        return f"{self.persona} en {self.evento}"

    @property
    def estado(self):
        if self.asistio:
            return "asistio"
        if self.no_puede:
            return "no_puede"
        if self.inscrito:
            return "inscrito"
        if self.invitado:
            return "invitado"
        return "sin_estado"

    @property
    def llego_sin_inscripcion(self):
        return bool(self.asistio) and not self.inscrito


class Rol(models.TextChoices):
    INVITADO = "invitado", "Invitado"
    AUTORIDAD = "autoridad", "Autoridad"
    STAFF = "staff", "Staff"
    NOMINADOR = "nominador", "Nominador"


class RolEvento(models.Model):
    """Qué es una persona en un evento concreto (ADR-007). Convive con Participación:

    no cambia su estado ni la reemplaza, son independientes (CORE invariante 7).
    """

    persona = models.ForeignKey(Persona, on_delete=models.CASCADE, related_name="roles_evento")
    evento = models.ForeignKey(Evento, on_delete=models.CASCADE, related_name="roles")
    rol = models.CharField(max_length=20, choices=Rol.choices)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("persona", "evento", "rol")]
        verbose_name = "rol en el evento"
        verbose_name_plural = "roles en el evento"

    def __str__(self):
        return f"{self.persona} · {self.get_rol_display()} en {self.evento}"


class Vinculo(models.Model):
    """Catálogo editable: Voluntad, Equipo, Mentor/a, Socio, Alianza... Se archiva, no se borra."""

    nombre = models.CharField(max_length=60, unique=True)
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["nombre"]
        verbose_name = "vínculo"
        verbose_name_plural = "vínculos"

    def __str__(self):
        return self.nombre


class PersonaVinculo(models.Model):
    """Una persona tiene un vínculo desde una fecha; `hasta` vacío = sigue vigente."""

    persona = models.ForeignKey(Persona, on_delete=models.CASCADE, related_name="asignaciones")
    vinculo = models.ForeignKey(Vinculo, on_delete=models.PROTECT, related_name="asignaciones")
    desde = models.DateField()
    hasta = models.DateField(null=True, blank=True)
    asignado_por = models.CharField(max_length=100, blank=True)

    class Meta:
        ordering = ["-desde"]
        verbose_name = "vínculo de persona"
        verbose_name_plural = "vínculos de personas"

    def __str__(self):
        return f"{self.persona} · {self.vinculo}"


class PosibleDuplicado(models.Model):
    """Correo distinto, nombre parecido. El sistema no adivina: Alvaro decide."""

    persona_nueva = models.ForeignKey(Persona, on_delete=models.CASCADE, related_name="+")
    persona_existente = models.ForeignKey(Persona, on_delete=models.CASCADE, related_name="+")
    detectado = models.DateTimeField(auto_now_add=True)
    resuelto = models.BooleanField(default=False)

    class Meta:
        unique_together = [("persona_nueva", "persona_existente")]
        ordering = ["resuelto", "-detectado"]
        verbose_name = "posible duplicado"
        verbose_name_plural = "posibles duplicados"

    def __str__(self):
        return f"¿{self.persona_nueva} = {self.persona_existente}?"


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

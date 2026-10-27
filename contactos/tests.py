"""Tests de las reglas del CRM. Correr con: python manage.py test"""
import csv
import datetime as dt
import tempfile
from pathlib import Path

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from openpyxl import Workbook

from .admin import PersonaForm
from .models import CambioEstado, Estado, Evento, Grupo, Participacion, Persona, Prioridad, Reglas
from .scoring import recalcular_todo


class Base(TestCase):
    def setUp(self):
        self.e24 = Evento.objects.create(nombre="Evento 2024", anio=2024)
        self.e25 = Evento.objects.create(nombre="Evento 2025", anio=2025)

    def persona(self, correo, nombre="X", grupo=Grupo.SIN_CLASIFICAR, estado=Estado.CONTACTABLE, **kw):
        return Persona.objects.create(correo=correo, nombre=nombre, grupo=grupo, estado=estado, **kw)

    def part(self, persona, evento, **kw):
        return Participacion.objects.create(persona=persona, evento=evento, **kw)


class PuntajeTests(Base):
    def test_asistir_vale_mas_que_inscribirse_y_no_se_suman(self):
        a = self.persona("a@x.cl")
        self.part(a, self.e25, inscrito=True, asistio=True)
        b = self.persona("b@x.cl")
        self.part(b, self.e25, inscrito=True, asistio=False)
        recalcular_todo()
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual(a.puntaje, 5)  # solo los 5 de asistir, no 5 + 3
        self.assertEqual(b.puntaje, 3)

    def test_suma_completa(self):
        p = self.persona("c@x.cl", grupo=Grupo.VOLUNTARIADO)
        self.part(p, self.e24, inscrito=True)
        self.part(p, self.e25, inscrito=True, asistio=True, respondio=True)
        recalcular_todo()
        p.refresh_from_db()
        self.assertEqual(p.puntaje, 5 + 2 + 1 + 1)
        self.assertEqual(p.prioridad, Prioridad.ALTA)

    def test_evento_nuevo_desplaza_las_reglas(self):
        p = self.persona("d@x.cl")
        self.part(p, self.e25, inscrito=True, asistio=True)
        Evento.objects.create(nombre="Evento 2026", anio=2026)
        recalcular_todo()
        p.refresh_from_db()
        # 2025 pasa a ser el "evento anterior": solo cuenta haberse inscrito.
        self.assertEqual(p.puntaje, 2)

    def test_reglas_editables(self):
        p = self.persona("e@x.cl")
        self.part(p, self.e25, inscrito=True, asistio=True)
        r = Reglas.actuales(); r.puntos_asistio = 10; r.save()
        recalcular_todo()
        p.refresh_from_db()
        self.assertEqual(p.puntaje, 10)


class PrioridadYOrdenTests(Base):
    def test_baja_nunca_tiene_orden_ni_entra_al_cupo(self):
        p = self.persona("baja@x.cl", estado=Estado.BAJA)
        self.part(p, self.e25, inscrito=True, asistio=True)
        recalcular_todo()
        p.refresh_from_db()
        self.assertEqual(p.prioridad, Prioridad.NO_INVITAR)
        self.assertIsNone(p.orden_sugerido)

    def test_fuera_de_chile_queda_despues_de_media(self):
        lejos = self.persona("lejos@x.cl", estado=Estado.FUERA_CHILE)
        self.part(lejos, self.e25, inscrito=True, asistio=True)  # puntaje alto
        media = self.persona("media@x.cl")
        self.part(media, self.e24, inscrito=True)  # puntaje 2 → Media
        recalcular_todo()
        lejos.refresh_from_db(); media.refresh_from_db()
        self.assertEqual(lejos.prioridad, Prioridad.BAJA)
        self.assertLess(media.orden_sugerido, lejos.orden_sugerido)

    def test_orden_es_correlativo(self):
        for i in range(5):
            self.persona(f"p{i}@x.cl", nombre=f"P{i}")
        recalcular_todo()
        ordenes = sorted(Persona.objects.values_list("orden_sugerido", flat=True))
        self.assertEqual(ordenes, [1, 2, 3, 4, 5])


class TrazabilidadTests(Base):
    def test_cambiar_estado_deja_registro(self):
        u = User.objects.create(username="alvaro")
        p = self.persona("f@x.cl")
        p.cambiar_estado(Estado.BAJA, motivo="Pidió salir por correo", usuario=u)
        c = CambioEstado.objects.get(persona=p)
        self.assertEqual((c.estado_anterior, c.estado_nuevo, c.usuario), (Estado.CONTACTABLE, Estado.BAJA, u))

    def test_mismo_estado_no_registra(self):
        p = self.persona("g@x.cl")
        self.assertFalse(p.cambiar_estado(Estado.CONTACTABLE))
        self.assertEqual(CambioEstado.objects.count(), 0)


class DuplicadosTests(Base):
    def test_correo_existente_se_rechaza_aunque_cambie_mayusculas(self):
        self.persona("ana@x.cl")
        form = PersonaForm(data={"correo": " ANA@x.cl ", "grupo": Grupo.SIN_CLASIFICAR, "estado": Estado.CONTACTABLE})
        self.assertFalse(form.is_valid())
        self.assertIn("correo", form.errors)

    def test_correo_alternativo_de_otra_persona_se_rechaza(self):
        self.persona("ana@x.cl", correos_alternativos="ana.trabajo@empresa.cl")
        form = PersonaForm(data={"correo": "ana.trabajo@empresa.cl", "grupo": Grupo.SIN_CLASIFICAR, "estado": Estado.CONTACTABLE})
        self.assertFalse(form.is_valid())

    def test_id_automatico(self):
        a = self.persona("h@x.cl"); b = self.persona("i@x.cl")
        self.assertEqual((a.persona_id, b.persona_id), ("P0001", "P0002"))


class AdminTests(Base):
    def setUp(self):
        super().setUp()
        self.admin = User.objects.create_superuser("admin", "a@a.cl", "clave-de-prueba-123")
        self.client.force_login(self.admin)

    def test_exportar_csv_excluye_bajas_aunque_esten_seleccionadas(self):
        ok = self.persona("ok@x.cl", nombre="Ok")
        baja = self.persona("no@x.cl", nombre="No", estado=Estado.BAJA)
        recalcular_todo()
        r = self.client.post(reverse("admin:contactos_persona_changelist"), {
            "action": "exportar_csv", "_selected_action": [ok.pk, baja.pk],
        })
        contenido = r.content.decode("utf-8-sig")
        self.assertIn("ok@x.cl", contenido)
        self.assertNotIn("no@x.cl", contenido)

    def test_accion_marcar_baja_registra_usuario(self):
        p = self.persona("j@x.cl")
        self.client.post(reverse("admin:contactos_persona_changelist"), {
            "action": "marcar_baja", "_selected_action": [p.pk],
        })
        p.refresh_from_db()
        self.assertEqual(p.estado, Estado.BAJA)
        self.assertEqual(CambioEstado.objects.get(persona=p).usuario, self.admin)

    def test_pantallas_cargan(self):
        for nombre in ["persona", "participacion", "evento", "cambioestado", "reglas"]:
            r = self.client.get(reverse(f"admin:contactos_{nombre}_changelist"))
            self.assertEqual(r.status_code, 200, nombre)


class ImportarExportarTests(TestCase):
    def _excel(self, ruta):
        wb = Workbook()
        ws = wb.active; ws.title = "Personas"
        ws.append(["persona_id", "correo", "correos_alternativos", "nombre_completo", "organizacion", "relacion",
                   "grupo_relacion", "telefono", "estado_contacto", "inscrito_2024", "inscrito_2025", "asistio_2025",
                   "respondio_2025", "comentarios_originales", "posible_socio_actual", "puntaje", "prioridad",
                   "orden_sugerido", "dentro_del_cupo", "fuentes"])
        ws.append(["P0001", "Uno@X.cl", "", "Uno", "ISF", "Mentor/a", "Voluntariado activo", "+56911111111",
                   "Contactable", False, True, True, True, "", False, None, None, None, None,
                   "Lista invitados 2025, Inscripciones 2025"])
        ws.append(["P0002", "dos@x.cl", "", "Dos", "", "", "Sin clasificar", "", "Baja solicitada",
                   False, False, False, False, "PIDE SER ELIMINADO", False, None, None, None, None, "Lista invitados 2025"])
        wp = wb.create_sheet("Participaciones")
        wp.append(["participacion_id", "persona_id", "correo", "evento", "inscrito", "fecha_inscripcion", "asistio",
                   "categoria_en_formulario", "restriccion_alimentaria"])
        wp.append(["E0001", "P0001", "uno@x.cl", "Evento Anual 2025", True, dt.datetime(2025, 11, 27, 17, 0), True, "Mentor/a", ""])
        wb.save(ruta)

    def test_importar_y_exportar(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "base.xlsx"; self._excel(ruta)
            call_command("importar_excel", str(ruta), stdout=open("/dev/null", "w"))
            call_command("importar_excel", str(ruta), stdout=open("/dev/null", "w"))  # idempotente
            self.assertEqual(Persona.objects.count(), 2)
            uno = Persona.objects.get(persona_id="P0001")
            self.assertEqual(uno.correo, "uno@x.cl")
            self.assertEqual(uno.puntaje, 5 + 1 + 1)  # asistió + respondió + grupo activo
            self.assertEqual(Persona.objects.get(persona_id="P0002").prioridad, Prioridad.NO_INVITAR)

            salida = Path(d) / "semana.csv"
            call_command("exportar_invitacion", "--salida", str(salida), stdout=open("/dev/null", "w"))
            filas = list(csv.reader(open(salida, encoding="utf-8-sig")))
            self.assertEqual([f[3] for f in filas[1:]], ["uno@x.cl"])


class VistasTests(Base):
    def setUp(self):
        super().setUp()
        self.u = User.objects.create_user("alvaro", password="clave-de-prueba-123")
        self.client.force_login(self.u)
        self.ok = self.persona("ok@x.cl", nombre="Ok")
        self.part(self.ok, self.e25, inscrito=True, asistio=True)
        self.baja = self.persona("baja@x.cl", nombre="Baja", estado=Estado.BAJA)
        recalcular_todo()

    def test_login_obligatorio(self):
        self.client.logout()
        for nombre in ["resumen", "invitar", "personas", "nueva"]:
            r = self.client.get(reverse(nombre))
            self.assertEqual(r.status_code, 302, nombre)
            self.assertIn(reverse("login"), r["Location"])

    def test_pantallas_cargan(self):
        for url in [reverse("resumen"), reverse("invitar"), reverse("personas"),
                    reverse("personas") + "?prioridad=alta&q=ok", reverse("nueva"),
                    reverse("ficha", args=[self.ok.persona_id])]:
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_invitar_no_muestra_bajas_y_csv_tampoco(self):
        r = self.client.get(reverse("invitar"))
        self.assertContains(r, "ok@x.cl")
        self.assertNotContains(r, "baja@x.cl")
        csv_ = self.client.get(reverse("invitar") + "?formato=csv").content.decode("utf-8-sig")
        self.assertIn("ok@x.cl", csv_)
        self.assertNotIn("baja@x.cl", csv_)

    def test_boton_baja_registra_y_vuelve(self):
        siguiente = reverse("invitar") + "?semana=1"
        r = self.client.post(reverse("cambiar_estado", args=[self.ok.persona_id]),
                             {"estado": "baja", "motivo": "Pidió salir", "next": siguiente})
        self.assertRedirects(r, siguiente, fetch_redirect_response=False)
        self.ok.refresh_from_db()
        self.assertEqual((self.ok.estado, self.ok.prioridad, self.ok.orden_sugerido), ("baja", "no_invitar", None))
        c = CambioEstado.objects.get(persona=self.ok)
        self.assertEqual((c.usuario, c.motivo), (self.u, "Pidió salir"))

    def test_next_externo_no_se_sigue(self):
        r = self.client.post(reverse("cambiar_estado", args=[self.ok.persona_id]),
                             {"estado": "fuera_chile", "next": "https://sitio-malo.com/"})
        self.assertEqual(r["Location"], reverse("ficha", args=[self.ok.persona_id]))

    def test_cambiar_estado_solo_por_post(self):
        r = self.client.get(reverse("cambiar_estado", args=[self.ok.persona_id]))
        self.assertEqual(r.status_code, 405)

    def test_nuevo_contacto_y_duplicado(self):
        r = self.client.post(reverse("nueva"), {"nombre": "Nueva", "correo": "nueva@x.cl", "grupo": "sin_clasificar"})
        nueva = Persona.objects.get(correo="nueva@x.cl")
        self.assertRedirects(r, reverse("ficha", args=[nueva.persona_id]), fetch_redirect_response=False)
        self.assertIsNotNone(nueva.orden_sugerido)  # quedó en el orden tras recalcular
        r = self.client.post(reverse("nueva"), {"nombre": "Otra", "correo": "OK@x.cl", "grupo": "sin_clasificar"})
        self.assertContains(r, "ya pertenece a")
        self.assertEqual(Persona.objects.filter(correo="ok@x.cl").count(), 1)


class ReglasUITests(Base):
    DATOS = {
        "puntos_asistio": 10, "puntos_inscrito": 6, "puntos_inscrito_anterior": 4,
        "puntos_respondio": 2, "puntos_grupo_activo": 1,
        "umbral_alta": 8, "umbral_media": 3, "cupo_semanal": 100,
    }

    def setUp(self):
        super().setUp()
        self.client.force_login(User.objects.create_user("u", password="x"))

    def test_guardar_reglas_recalcula_prioridades(self):
        a = self.persona("a@x.cl")
        self.part(a, self.e25, inscrito=True, asistio=True)
        recalcular_todo()
        a.refresh_from_db()
        self.assertEqual(a.prioridad, Prioridad.ALTA)  # 5 >= 5
        self.client.post(reverse("reglas"), self.DATOS)
        a.refresh_from_db()
        self.assertEqual((a.puntaje, a.prioridad), (10, Prioridad.ALTA))
        self.assertEqual(Reglas.actuales().cupo_semanal, 100)

    def test_pesos_fuera_de_1_a_10_se_rechazan(self):
        r = self.client.post(reverse("reglas"), {**self.DATOS, "puntos_asistio": 11})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Reglas.actuales().puntos_asistio, 5)

    def test_umbral_alta_menor_que_media_se_rechaza(self):
        self.client.post(reverse("reglas"), {**self.DATOS, "umbral_alta": 2, "umbral_media": 5})
        self.assertEqual(Reglas.actuales().umbral_alta, 5)

    def test_ficha_muestra_hexagono(self):
        a = self.persona("a@x.cl")
        self.part(a, self.e25, inscrito=True, asistio=True)
        r = self.client.get(reverse("ficha", args=[a.persona_id]))
        self.assertContains(r, "<svg")
        self.assertTrue(next(e for e in r.context["radar"]["ejes"] if e["nombre"] == "Asistió al último")["cumple"])


# ============================================================================
# Merge de pseudocodigo/: las 14 reglas de pruebas.py, ahora contra la base.
# ============================================================================
from django.core.cache import cache
from django.test import override_settings

from . import servicios
from .models import EstadoEvento, OrigenLlegada, PosibleDuplicado, Rol, RolEvento, TipoEvento, Vinculo
from .vinculos import vinculo_desde_texto


class Dominio(TestCase):
    def setUp(self):
        self.ev = Evento.objects.create(nombre="Evento de prueba", tipo=TipoEvento.ANUAL, fecha=dt.date(2026, 11, 18))

    def abrir(self):
        self.ev.checkin_abierto = True
        self.ev.save()


class PseudocodigoTests(Dominio):
    def test_crear_evento_es_barato(self):
        self.assertEqual((self.ev.estado, self.ev.anio, self.ev.codigo), ("borrador", 2026, "anual-2026"))
        self.assertIn("definir el lugar", self.ev.faltantes())

    def test_avanzar_sin_requisitos_avisa_pero_no_bloquea(self):
        # Decisión 2026-10-25: los requisitos son avisos (en 2026 no hay lista de invitados).
        avisos = self.ev.avanzar()
        self.assertEqual(self.ev.estado, EstadoEvento.CONVOCANDO)
        self.assertIn("cargar la lista de invitados", avisos)

    def test_correo_es_la_identidad(self):
        a, creada1 = servicios.buscar_o_crear("  Ana@ISF.cl ", "Ana Pérez")
        b, creada2 = servicios.buscar_o_crear("ana@isf.cl", "Ana P.")
        self.assertEqual(a, b)
        self.assertTrue(creada1)
        self.assertFalse(creada2)

    def test_correo_alternativo_encuentra_a_la_persona_y_es_exacto(self):
        a, _ = servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez")
        a.correos_alternativos = "ana.perez@gmail.com"; a.save()
        self.assertEqual(servicios.buscar_por_correo("ANA.PEREZ@gmail.com"), a)
        # Coincidencia parcial NO es la misma persona.
        self.assertIsNone(servicios.buscar_por_correo("na.perez@gmail.com"))

    def test_nombre_parecido_queda_para_revisar_sin_fusionar(self):
        servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez Soto")
        servicios.buscar_o_crear("ana@gmail.com", "Ana Perez")
        self.assertEqual(Persona.objects.count(), 2)
        self.assertEqual(PosibleDuplicado.objects.count(), 1)

    def test_estado_solo_avanza_e_idempotencia(self):
        p, _ = servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez")
        servicios.inscribir(self.ev, p, "formulario")
        part, _ = servicios.invitar(self.ev, p)
        self.assertEqual(part.estado, "inscrito")
        self.assertTrue(part.invitado)
        _, cambio = servicios.inscribir(self.ev, p, "manual")
        self.assertFalse(cambio)
        self.assertEqual(self.ev.participaciones.count(), 1)

    def test_baja_nunca_es_invitada_ni_sale_en_envios(self):
        p, _ = servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez")
        p.cambiar_estado(Estado.BAJA, motivo="lo pidió por correo")
        part, _ = servicios.invitar(self.ev, p)
        self.assertIsNone(part)
        self.assertEqual(servicios.lista_para_enviar(self.ev), [])

    def test_exclusion_dura_y_fuera_de_chile_invitable(self):
        for estado in (Estado.BAJA, Estado.NO_CONSIDERAR, Estado.CORREO_PROBLEMA, Estado.DESACTUALIZADO):
            p = Persona.objects.create(correo=f"{estado}@x.cl", estado=estado)
            self.assertIsNone(servicios.invitar(self.ev, p)[0], estado)
        lejos = Persona.objects.create(correo="lejos@x.cl", estado=Estado.FUERA_CHILE)
        self.assertTrue(servicios.invitar(self.ev, lejos)[1])

    def test_inscribir_sin_invitacion_y_no_puede(self):
        p, _ = servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez")
        part, _ = servicios.inscribir(self.ev, p, "manual")
        self.assertTrue(part.inscrito)
        self.assertFalse(part.invitado)
        part, _ = servicios.marcar_no_puede(self.ev, p)
        self.assertEqual(part.estado, "no_puede")

    def test_qr_cerrado_no_registra(self):
        servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez")
        self.assertEqual(servicios.registrar_por_qr(self.ev, "ana@isf.cl").tipo, "cerrado")
        self.assertFalse(self.ev.participaciones.exists())

    def test_qr_hora_de_llegada_no_se_sobrescribe(self):
        self.abrir()
        p, _ = servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez")
        servicios.inscribir(self.ev, p, "formulario")
        servicios.registrar_por_qr(self.ev, "ANA@isf.cl ")
        hora = self.ev.participaciones.get(persona=p).hora_llegada
        self.assertEqual(servicios.registrar_por_qr(self.ev, "ana@isf.cl").tipo, "ya_registrado")
        self.assertEqual(self.ev.participaciones.get(persona=p).hora_llegada, hora)

    def test_qr_desconocido_pide_datos_y_crea_una_sola_persona(self):
        self.abrir()
        self.assertEqual(servicios.registrar_por_qr(self.ev, "nuevo@x.cl").tipo, "pedir_datos")
        self.assertFalse(Persona.objects.exists())
        servicios.registrar_por_qr(self.ev, "nuevo@x.cl", "Juan Soto")
        servicios.registrar_por_qr(self.ev, "nuevo@x.cl", "Juan Soto")
        self.assertEqual(Persona.objects.count(), 1)
        self.assertEqual(self.ev.participaciones.count(), 1)
        self.assertTrue(self.ev.participaciones.get().llego_sin_inscripcion)

    def test_qr_solo_muestra_primer_nombre(self):
        self.abrir()
        servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez", "Empresa X", "+56911111111")
        msg = servicios.registrar_por_qr(self.ev, "ana@isf.cl").mensaje
        self.assertIn("Ana", msg)
        for dato in ("Pérez", "Empresa", "569"):
            self.assertNotIn(dato, msg)

    def test_csv_de_envio_sin_datos_sensibles(self):
        p, _ = servicios.buscar_o_crear("ana@isf.cl", "Ana Pérez", telefono="+56911111111")
        servicios.inscribir(self.ev, p, "formulario", logistica={"restriccion_alimentaria": "celiaca"})
        texto = str(list(servicios.filas_csv_envio([p])))
        self.assertNotIn("569", texto)
        self.assertNotIn("celiaca", texto)

    def test_avanzar_no_toca_el_checkin(self):
        # Decisión 2026-10-25: el registro se abre y cierra a mano desde el panel.
        for _ in range(3):
            self.ev.avanzar()
        self.assertEqual(self.ev.estado, EstadoEvento.EN_CURSO)
        self.assertFalse(self.ev.checkin_abierto)

    def test_baja_que_llega_se_registra_sin_cambiar_estado(self):
        self.abrir()
        p = Persona.objects.create(correo="baja@x.cl", nombre="Bea", estado=Estado.BAJA)
        self.assertEqual(servicios.registrar_por_qr(self.ev, "baja@x.cl").tipo, "bienvenida")
        p.refresh_from_db()
        self.assertEqual(p.estado, Estado.BAJA)


class RolEventoTests(Dominio):
    """ADR-007: RolEvento convive con Participación, no la reemplaza."""

    def test_asignar_rol_es_idempotente(self):
        p, _ = servicios.buscar_o_crear("auto@isf.cl", "Autoridad Uno")
        servicios.asignar_rol(self.ev, p, Rol.AUTORIDAD)
        servicios.asignar_rol(self.ev, p, Rol.AUTORIDAD)
        self.assertEqual(RolEvento.objects.filter(persona=p, evento=self.ev).count(), 1)

    def test_una_persona_puede_tener_varios_roles(self):
        p, _ = servicios.buscar_o_crear("doble@isf.cl", "Rol Doble")
        servicios.asignar_rol(self.ev, p, Rol.AUTORIDAD)
        servicios.asignar_rol(self.ev, p, Rol.NOMINADOR)
        self.assertEqual(RolEvento.objects.filter(persona=p, evento=self.ev).count(), 2)

    def test_asignar_rol_no_crea_ni_cambia_participacion(self):
        p, _ = servicios.buscar_o_crear("staff@isf.cl", "Staff Uno")
        servicios.asignar_rol(self.ev, p, Rol.STAFF)
        self.assertFalse(self.ev.participaciones.exists())


class ImportarExcelMaestroTests(Dominio):
    """FEAT-002: importar el Excel maestro 2026 completo (varias hojas)."""

    def _excel(self, ruta, con_staff=True):
        wb = Workbook()
        ws = wb.active; ws.title = "Inscripciones"
        ws.append(["Timestamp", "Correo electrónico", "Nombre y apellidos", "Organización",
                   "Número de contacto", "¿Tienes alguna restricción alimentaria?", "Categoría"])
        ws.append(["10/21/2026 15:49:53", "Ana@X.cl", "Ana Pérez", "ISF", "+56911111111", "vegana", "Mentor/a"])

        wi = wb.create_sheet("Lista invitados")
        wi.append(["id", "Nombre", "Apellido", "Correo", "Relación", "ORGANIZACIÓN",
                   "POR REVISAR/Comentarios", "Correo Alternativo"])
        wi.append(["P0001", "Beto", "Soto", "beto@x.cl", "Histórico", "", " ", ""])
        wi.append(["P0002", "Clara", "Reyes", "clara@x.cl", "Histórico", "", "Rebotó", ""])

        wn = wb.create_sheet("Form No pueden")
        wn.append(["Timestamp", "Correo electrónico"])
        wn.append([dt.datetime(2026, 10, 21, 16, 0, 0), "dario@x.cl"])

        wnom = wb.create_sheet("lista nominadores")
        wnom.append(["Nombre", "Apellido", "Correo", "Relación"])
        wnom.append(["Eva", "Nom", "eva@x.cl", "Equipo Nacional"])

        waut = wb.create_sheet("Autoridades a Invitar")
        waut.append(["Nombre", "Apellido", "Correo", "Relación", "ORGANIZACIÓN"])
        waut.append(["Fede", "Aut", "fede@x.cl", "Alianza", "Universidad X"])

        wst = wb.create_sheet("Staff")
        if con_staff:
            wst.append(["Nombre", "Apellido", "Correo"])

        wpm = wb.create_sheet("para_mail")
        wpm.append(["identificación", "Nombre", "Apellido", "mail", "Nombre 2", "estado envío", "token"])
        wpm.append(["P0001", "Beto", "Soto", "beto@x.cl", "Betito", "ENVIADO", "tok-1"])
        wpm.append(["PX", "Nadie", "Nadie", "nadie@x.cl", "Nadita", "ENVIADO", "tok-2"])

        wb.save(ruta)

    def test_idempotente_e_importa_todas_las_hojas(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "maestro.xlsx"; self._excel(ruta)
            nulo = open("/dev/null", "w")
            call_command("importar_excel_maestro", str(ruta), "--evento", self.ev.codigo, stdout=nulo)
            n_personas = Persona.objects.count()
            n_roles = RolEvento.objects.count()
            call_command("importar_excel_maestro", str(ruta), "--evento", self.ev.codigo, stdout=nulo)  # idempotente
            self.assertEqual(Persona.objects.count(), n_personas)
            self.assertEqual(RolEvento.objects.count(), n_roles)

        ana = Persona.objects.get(correo="ana@x.cl")
        self.assertTrue(self.ev.participaciones.get(persona=ana).inscrito)

        beto = Persona.objects.get(correo="beto@x.cl")
        self.assertEqual(RolEvento.objects.get(persona=beto, evento=self.ev).rol, Rol.INVITADO)
        self.assertTrue(self.ev.participaciones.get(persona=beto).invitado)
        self.assertEqual(beto.apodo, "Betito")  # viene de para_mail

        dario = Persona.objects.get(correo="dario@x.cl")
        self.assertEqual(self.ev.participaciones.get(persona=dario).estado, "no_puede")

        eva = Persona.objects.get(correo="eva@x.cl")
        self.assertEqual(RolEvento.objects.get(persona=eva, evento=self.ev).rol, Rol.NOMINADOR)
        self.assertTrue(self.ev.participaciones.get(persona=eva).invitado)  # también puede pasar por el check-in

        fede = Persona.objects.get(correo="fede@x.cl")
        self.assertEqual(RolEvento.objects.get(persona=fede, evento=self.ev).rol, Rol.AUTORIDAD)

        self.assertFalse(Persona.objects.filter(correo="nadie@x.cl").exists())  # para_mail no crea personas

    def test_rebote_no_pisa_una_baja_ya_registrada(self):
        clara = Persona.objects.create(correo="clara@x.cl", nombre="Clara Reyes", estado=Estado.BAJA)
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "maestro.xlsx"; self._excel(ruta)
            call_command("importar_excel_maestro", str(ruta), "--evento", self.ev.codigo, stdout=open("/dev/null", "w"))
        clara.refresh_from_db()
        self.assertEqual(clara.estado, Estado.BAJA)

    def test_hoja_staff_vacia_no_rompe(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "maestro.xlsx"; self._excel(ruta, con_staff=False)
            call_command("importar_excel_maestro", str(ruta), "--evento", self.ev.codigo, stdout=open("/dev/null", "w"))
        self.assertFalse(RolEvento.objects.filter(rol=Rol.STAFF).exists())

    def test_evento_inexistente_da_error_claro(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("importar_excel_maestro", "x.xlsx", "--evento", "no-existe")


class VinculosTests(TestCase):
    def test_traduccion_del_texto_de_alvaro(self):
        self.assertEqual(vinculo_desde_texto("Mentor/a"), ("Mentor/a", False))
        self.assertEqual(vinculo_desde_texto("ex voluntad BioBio"), ("Voluntad", True))
        self.assertEqual(vinculo_desde_texto("Histórico"), (None, False))
        self.assertEqual(vinculo_desde_texto("AQUA"), ("?", False))  # se reporta, no se inventa

    def test_vinculo_no_se_duplica_y_se_cierra_con_fecha(self):
        p = Persona.objects.create(correo="v@x.cl")
        self.assertTrue(p.agregar_vinculo("Socio"))
        self.assertFalse(p.agregar_vinculo("Socio"))
        p.terminar_vinculo("Socio")
        self.assertEqual(p.vinculos_vigentes, [])
        self.assertEqual(p.asignaciones.count(), 1)  # se cerró, no se borró
        self.assertEqual(Vinculo.objects.count(), 1)


class ScoringEventosTests(TestCase):
    def test_mentorias_no_cuentan_y_se_ordena_por_fecha(self):
        e25 = Evento.objects.create(nombre="Anual 2025", fecha=dt.date(2025, 12, 17))
        Evento.objects.create(nombre="Anual 2024", fecha=dt.date(2024, 12, 10))
        mentoria = Evento.objects.create(nombre="Mentoría", tipo=TipoEvento.MENTORIA, fecha=dt.date(2026, 3, 1))
        p = Persona.objects.create(correo="s@x.cl")
        Participacion.objects.create(persona=p, evento=e25, inscrito=True, asistio=True)
        Participacion.objects.create(persona=p, evento=mentoria, inscrito=True, asistio=True)
        recalcular_todo()
        p.refresh_from_db()
        self.assertEqual(p.puntaje, 5)  # 2025 sigue siendo el "último"; la mentoría no suma

    def test_dos_eventos_el_mismo_anio(self):
        Evento.objects.create(nombre="Anual 2026", fecha=dt.date(2026, 11, 18))
        otro = Evento.objects.create(nombre="Clínica 2026", tipo=TipoEvento.CLINICA, fecha=dt.date(2026, 5, 1))
        self.assertEqual(otro.codigo, "clinica-2026")


class ImportarInscripcionesTests(Dominio):
    ENCABEZADO = ["Timestamp", "Correo electrónico", "Nombre y apellidos", "Organización", "Número de contacto",
                  "Categoría", "¿Tienes alguna restricción alimentaria?", "Calza Mail"]

    def _csv(self, ruta, filas):
        with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(self.ENCABEZADO)
            w.writerows(filas)

    def test_idempotente_y_mapea_columnas(self):
        Persona.objects.create(correo="vieja@x.cl", nombre="Vieja Conocida", estado=Estado.FUERA_CHILE)
        filas = [
            ["10/21/2026 15:49:53", "Vieja@X.cl", "Vieja Conocida", "ISF", "", "Mentor/a", "", "SI"],
            ["10/22/2026 09:00:00", "nueva@x.cl", "Nueva Persona", "Org", "+56922222222", "AQUA", "vegana", "NO"],
        ]
        with tempfile.TemporaryDirectory() as d:
            ruta = Path(d) / "inscripciones.csv"
            self._csv(ruta, filas)
            nulo = open("/dev/null", "w")
            call_command("importar_inscripciones", str(ruta), "--evento", self.ev.codigo, stdout=nulo)
            primera = self.ev.participaciones.get(persona__correo="vieja@x.cl").fecha_inscripcion
            call_command("importar_inscripciones", str(ruta), "--evento", self.ev.codigo, stdout=nulo)
        self.assertEqual(Persona.objects.count(), 2)
        self.assertEqual(self.ev.participaciones.filter(inscrito=True).count(), 2)
        vieja = Persona.objects.get(correo="vieja@x.cl")
        self.assertEqual(vieja.estado, Estado.CONTACTABLE)  # su inscripción desmiente "fuera de Chile"
        self.assertEqual(vieja.vinculos_vigentes, ["Mentor/a"])
        part = self.ev.participaciones.get(persona=vieja)
        self.assertEqual(part.fecha_inscripcion, primera)  # no cambió al reimportar
        self.assertEqual(part.categoria_formulario, "Mentor/a")
        nueva = self.ev.participaciones.get(persona__correo="nueva@x.cl")
        self.assertEqual(nueva.restriccion_alimentaria, "vegana")
        self.assertNotIn("Calza Mail", str(nueva.logistica))

    def test_evento_inexistente_da_error_claro(self):
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("importar_inscripciones", "x.csv", "--evento", "no-existe")


# ============================================================================
# SPEC_checkin_qr.md §7: pruebas mínimas de la pantalla pública y el panel.
# ============================================================================
class CheckinVistasTests(Dominio):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.abrir()
        self.ana = Persona.objects.create(
            correo="ana@isf.cl", nombre="Ana Pérez", organizacion="Empresa X", telefono="+56911111111",
            correos_alternativos="ana.perez@gmail.com",
        )
        Participacion.objects.create(persona=self.ana, evento=self.ev, inscrito=True,
                                     restriccion_alimentaria="celiaca")
        self.url = reverse("checkin_publico", args=[self.ev.codigo])

    def test_correo_inscrito_queda_registrado_por_qr(self):
        r = self.client.post(self.url, {"correo": "ana@isf.cl"})
        self.assertContains(r, "¡Bienvenido/a, Ana!")
        p = self.ev.participaciones.get(persona=self.ana)
        self.assertTrue(p.asistio)
        self.assertIsNotNone(p.hora_llegada)
        self.assertEqual(p.origen_llegada, OrigenLlegada.QR)

    def test_mayusculas_espacios_y_alternativo(self):
        self.client.post(self.url, {"correo": "  ANA.PEREZ@Gmail.com "})
        self.assertTrue(self.ev.participaciones.get(persona=self.ana).asistio)

    def test_segundo_registro_no_cambia_la_hora(self):
        self.client.post(self.url, {"correo": "ana@isf.cl"})
        hora = self.ev.participaciones.get(persona=self.ana).hora_llegada
        r = self.client.post(self.url, {"correo": "ana@isf.cl"})
        self.assertContains(r, "Ya estabas registrado/a")
        self.assertEqual(self.ev.participaciones.get(persona=self.ana).hora_llegada, hora)

    def test_desconocido_crea_una_persona_y_una_participacion(self):
        r = self.client.post(self.url, {"correo": "nuevo@x.cl"})
        self.assertContains(r, 'name="nombre"')
        self.client.post(self.url, {"correo": "nuevo@x.cl", "nombre": "juan soto", "organizacion": "Org"})
        r = self.client.post(self.url, {"correo": "nuevo@x.cl", "nombre": "juan soto"})
        self.assertContains(r, "Ya estabas registrado/a")
        self.assertEqual(Persona.objects.filter(correo="nuevo@x.cl").count(), 1)
        nuevo = Persona.objects.get(correo="nuevo@x.cl")
        self.assertEqual((nuevo.grupo, nuevo.estado), (Grupo.SIN_CLASIFICAR, Estado.CONTACTABLE))
        self.assertEqual(self.ev.participaciones.filter(persona=nuevo).count(), 1)

    def test_cerrado_no_registra(self):
        self.ev.checkin_abierto = False; self.ev.save()
        r = self.client.post(self.url, {"correo": "ana@isf.cl"})
        self.assertContains(r, "aún no está abierto")
        self.assertFalse(self.ev.participaciones.get(persona=self.ana).asistio)

    def test_get_no_modifica_datos(self):
        r = self.client.get(self.url, {"correo": "ana@isf.cl"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'name="robots" content="noindex')
        self.assertFalse(self.ev.participaciones.get(persona=self.ana).asistio)

    def test_pagina_publica_no_muestra_datos_de_la_persona(self):
        r = self.client.post(self.url, {"correo": "ana@isf.cl"})
        html = r.content.decode()
        for dato in ("ana@isf.cl", "Empresa X", "celiaca", "Pérez", "+569"):
            self.assertNotIn(dato, html)

    def test_limite_de_intentos_por_ip(self):
        with override_settings(CHECKIN_LIMITE_POR_MINUTO=2):
            for _ in range(2):
                self.client.post(self.url, {"correo": "x@x.cl"})
            r = self.client.post(self.url, {"correo": "ana@isf.cl"})
        self.assertEqual(r.status_code, 429)
        self.assertFalse(self.ev.participaciones.get(persona=self.ana).asistio)

    def test_panel_qr_y_exportacion_exigen_login(self):
        c = self.ev.codigo
        urls = [reverse("checkin_eventos"), reverse("checkin_panel", args=[c]), reverse("checkin_estado", args=[c]),
                reverse("checkin_qr", args=[c]), reverse("checkin_qr_svg", args=[c]),
                reverse("checkin_csv", args=[c]), reverse("checkin_respaldo", args=[c])]
        for url in urls:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn(reverse("login"), r["Location"])
        r = self.client.post(reverse("checkin_marcar", args=[c, self.ana.persona_id]))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(self.ev.participaciones.get(persona=self.ana).asistio)

    def test_panel_marca_manual_csv_y_respaldo(self):
        self.client.force_login(User.objects.create_user("equipo", password="clave-de-prueba-123"))
        c = self.ev.codigo
        self.assertContains(self.client.get(reverse("checkin_panel", args=[c]), {"q": "ana"}), "Marcar llegada")
        self.client.post(reverse("checkin_marcar", args=[c, self.ana.persona_id]))
        p = self.ev.participaciones.get(persona=self.ana)
        self.assertEqual((p.asistio, p.origen_llegada), (True, OrigenLlegada.MANUAL))
        self.assertContains(self.client.get(reverse("checkin_estado", args=[c])), "Ana Pérez")
        svg = self.client.get(reverse("checkin_qr_svg", args=[c]))
        self.assertEqual(svg["Content-Type"], "image/svg+xml")
        self.assertContains(self.client.get(reverse("checkin_respaldo", args=[c])), "Ana Pérez")

        texto = self.client.get(reverse("checkin_csv", args=[c])).content.decode("utf-8-sig")
        encabezado = texto.splitlines()[0]
        self.assertEqual(encabezado, "persona_id,nombre,correo,inscrito,hora_llegada,origen_llegada")
        self.assertIn("ana@isf.cl", texto)
        self.assertNotIn("celiaca", texto)
        self.assertNotIn("569", texto)

    def test_abrir_y_cerrar_desde_el_panel(self):
        self.client.force_login(User.objects.create_user("equipo", password="clave-de-prueba-123"))
        self.client.post(reverse("checkin_abrir_cerrar", args=[self.ev.codigo]))
        self.ev.refresh_from_db()
        self.assertFalse(self.ev.checkin_abierto)
        self.assertEqual(self.client.get(reverse("checkin_abrir_cerrar", args=[self.ev.codigo])).status_code, 405)

    @override_settings(PUBLIC_BASE_URL="https://isf.ejemplo.cl")
    def test_qr_usa_la_url_publica(self):
        self.client.force_login(User.objects.create_user("equipo", password="clave-de-prueba-123"))
        r = self.client.get(reverse("checkin_qr", args=[self.ev.codigo]))
        self.assertContains(r, "https://isf.ejemplo.cl/checkin/anual-2026/")

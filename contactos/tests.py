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

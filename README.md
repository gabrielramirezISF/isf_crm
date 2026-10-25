# ISF · Contactos Evento Anual

CRM pequeño para el evento anual de ISF: una base de personas sin duplicados,
su participación en cada evento, la trazabilidad de bajas y una prioridad para
elegir a quién invitar primero (cupo de ~300 correos por semana).

Hecho con Django 5.2 + SQLite. La interfaz es el panel de administración de Django.

## Puesta en marcha (Linux)

```bash
cd isf_crm
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py createsuperuser           # tu usuario para entrar al panel
python manage.py importar_excel ../ISF_Base_Contactos_para_AppSheet.xlsx
python manage.py runserver
```

Abre http://127.0.0.1:8000/ y entra con tu usuario.

## Pantallas

| Dirección | Para qué |
|---|---|
| `/` | **Resumen**: totales por prioridad y grupo, participación por evento, últimos cambios |
| `/invitar/` | **Invitar esta semana**: la lista del cupo, por semanas, con CSV y botones "Rebotó" / "Baja" |
| `/personas/` | Buscar y filtrar a todos |
| `/personas/P0001/` | **Ficha**: datos, historial por evento, cambiar estado con motivo |
| `/nueva/` | Agregar un contacto (avisa si el correo ya existe) |
| `/admin/` | Panel completo de Django (editar todo, reglas de prioridad) |

Bootstrap 5.3.3 y Bootstrap Icons 1.11.3 vienen incluidos en `contactos/static/`
(con sus licencias MIT): la interfaz funciona sin internet.

## Uso diario (también desde /admin/)

| Quiero... | Cómo |
|---|---|
| Ver a quién invitar esta semana | Personas → filtro **Dentro del cupo semanal: Sí** |
| Marcar a alguien que pidió salir | Seleccionarlo → acción **Marcar como BAJA SOLICITADA** |
| Ver quién cambió un estado y cuándo | **Cambios de estado** (o al final de la ficha) |
| Agregar un contacto nuevo | Personas → **Añadir persona** (avisa si el correo ya existe) |
| Cambiar los pesos de la prioridad | **Reglas de prioridad** (al guardar, recalcula todo) |
| Sacar la lista en CSV | Seleccionar → **Exportar seleccionadas a CSV**, o por consola: |

```bash
python manage.py exportar_invitacion --salida semana1.csv            # posiciones 1–300
python manage.py exportar_invitacion --desde 301 --salida semana2.csv
```

La exportación nunca incluye a quien está en "No invitar" o "Revisar".

## Cómo está organizado (orden sugerido de lectura)

1. `contactos/models.py`: las tablas. Persona, Evento, Participacion, CambioEstado, Reglas.
2. `contactos/scoring.py`: el puntaje y la prioridad, en Python puro.
3. `contactos/admin.py`: el panel (filtros, acciones, chequeo de duplicados).
4. `contactos/views.py` + `contactos/templates/contactos/`: las pantallas con Bootstrap.
5. `contactos/management/commands/`: importar el Excel y exportar el CSV.
6. `contactos/tests.py`: las reglas expresadas como pruebas (`python manage.py test`).

## Reglas de prioridad (valores por defecto)

| Señal | Puntos |
|---|---|
| Asistió al último evento | 5 |
| Se inscribió al último evento pero no llegó | 3 |
| Se inscribió al evento anterior | 2 |
| Respondió la invitación del último evento | 1 |
| Es Equipo ISF o Voluntariado activo | 1 |

Alta ≥ 5, Media ≥ 2, Baja < 2. Bajas y "no considerar" → **No invitar**.
Correo rebotado → **Revisar correo**. Fuera de Chile → **Baja**.
"Último" y "anterior" son los dos eventos más recientes: al crear el Evento 2026
las reglas se desplazan solas.

## Datos personales

`db.sqlite3`, los `.xlsx` y los `.csv` están en `.gitignore`: tienen correos,
teléfonos y restricciones alimentarias (dato sensible). No los subas a GitHub.

Antes de publicarlo en un servidor: define `DJANGO_SECRET_KEY`, `DJANGO_DEBUG=0`
y `DJANGO_ALLOWED_HOSTS`.

## Fuera de alcance por ahora

- Envío masivo de correos (se exporta el CSV para la herramienta actual).
- Histórico 2023 y anteriores (probablemente en el Supabase de ISF).
- Asistencia 2024 por persona (no viene en los Excel).
- Dashboard y análisis (siguiente paso: notebook con pandas sobre `db.sqlite3`).

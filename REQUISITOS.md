# Requisitos · CRM Evento Anual ISF (borrador para validar con Alvaro)

Fuente: reunión con Alvaro, sus Excel del evento 2025 y el prototipo en AppSheet.
Estado: ✅ cubierto · 🟡 parcial · ❌ pendiente · ❓ por confirmar con Alvaro.

## Problema
Cada año la lista de invitados se rearma a mano en Excel. La herramienta de correo
permite ~300 envíos por semana, así que hay que elegir a quién invitar. Hace falta
saber quién sigue en la comunidad, quién se fue y quién pidió ser eliminado.

## Requisitos

| # | Requisito | Prototipo AppSheet | Django | Notas |
|---|---|---|---|---|
| R1 | Una fila por persona, sin duplicados | ✅ | ✅ | Clave: correo; correos alternativos unidos a la persona |
| R2 | Elegir 230–300 personas por historial | 🟡 | ✅ | Filtro "dentro del cupo" + exportar CSV |
| R3 | Estado de cada contacto (activo, baja, fuera de Chile...) | 🟡 texto libre | ✅ | Lista cerrada de estados |
| R4 | Bajas con trazabilidad (quién, cuándo, por qué) | ❌ | ✅ | Tabla CambioEstado, no editable |
| R5 | Nueva forma de añadir contactos sin duplicar | 🟡 | ✅ | Valida correo y correos alternativos |
| R6 | Historial por evento (inscrito, respondió, asistió) | ❌ | ✅ | Tabla Participacion |
| R7 | Reglas de prioridad ajustables | ❌ | ✅ | Modelo Reglas |
| R8 | Superar el tope de 300 correos | ❌ | 🟡 | Exporta por semanas; el envío sigue fuera |
| R9 | Histórico desde 2023 | ❌ | ❌ | ❓ ¿Exportable desde Supabase? |
| R10 | "Trackear tiempo" | ❌ | ❌ | ❓ ¿Tiempo de voluntariado o curva de inscripciones? |
| R11 | Dashboard de presencia y contribución | ❌ | 🟡 | Resumen con totales; faltan gráficos y análisis |
| R12 | Formulario de inscripción 2026 que alimente la base | ❌ | ❌ | ❓ ¿Google Forms → importar, o formulario propio? |

## Interfaz (v2)

| Pantalla | Qué permite | Estado |
|---|---|---|
| Resumen | Totales por prioridad y grupo, participación por evento, últimos cambios | ✅ |
| Invitar esta semana | Lista del cupo por semanas, CSV, botones "Rebotó" y "Baja" con confirmación | ✅ |
| Personas | Búsqueda y filtros por prioridad, estado y grupo | ✅ |
| Ficha | Datos, historial por evento, cambio de estado con motivo | ✅ |
| Nuevo contacto | Alta con aviso de duplicados | ✅ |
| Marcar "respondió / confirmó asistencia" desde la interfaz | Hoy solo desde el admin | ❌ |
| Gráficos (dashboard) | Pendiente: análisis con pandas o Streamlit de solo lectura | ❌ |

## Preguntas abiertas para Alvaro
1. ¿Los pesos de la prioridad le hacen sentido? (asistir 5, inscribirse 3, 2024 = 2...)
2. La lista tiene 108 personas con relación "Socio", pero la hoja de socios actuales tiene 35. ¿Los demás son ex socios?
3. ¿Existe la asistencia 2024 por persona?
4. ¿Quién más usaría el sistema, y con qué permisos?
5. ¿Dónde viviría: un computador de ISF, un servidor, o junto al Supabase de la web?

## Decisiones tomadas
- La prioridad se calcula con reglas fijas y explicables, no con IA.
- La restricción alimentaria vive solo en Participacion (es por evento y es dato sensible).
- Un comentario que contradice la actividad reciente no saca a nadie de la lista: va a revisión.

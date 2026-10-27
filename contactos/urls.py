from django.urls import path

from . import views, views_checkin as checkin

urlpatterns = [
    path("", views.resumen, name="resumen"),
    path("invitar/", views.invitar, name="invitar"),
    path("personas/", views.personas, name="personas"),
    path("personas/<str:persona_id>/", views.ficha, name="ficha"),
    path("personas/<str:persona_id>/estado/", views.cambiar_estado, name="cambiar_estado"),
    path("nueva/", views.nueva, name="nueva"),
    path("reglas/", views.reglas, name="reglas"),
    # Check-in con QR del día del evento
    path("checkin/", checkin.eventos, name="checkin_eventos"),
    path("checkin/<slug:codigo>/", checkin.publico, name="checkin_publico"),
    path("checkin/<slug:codigo>/panel/", checkin.panel, name="checkin_panel"),
    path("checkin/<slug:codigo>/panel/estado/", checkin.panel_estado, name="checkin_estado"),
    path("checkin/<slug:codigo>/panel/abrir-cerrar/", checkin.abrir_cerrar, name="checkin_abrir_cerrar"),
    path("checkin/<slug:codigo>/panel/llegada/<str:persona_id>/", checkin.marcar_llegada, name="checkin_marcar"),
    path("checkin/<slug:codigo>/qr/", checkin.qr_imprimir, name="checkin_qr"),
    path("checkin/<slug:codigo>/qr.svg", checkin.qr_svg, name="checkin_qr_svg"),
    path("checkin/<slug:codigo>/asistencia.csv", checkin.asistencia_csv, name="checkin_csv"),
    path("checkin/<slug:codigo>/respaldo/", checkin.respaldo, name="checkin_respaldo"),
]

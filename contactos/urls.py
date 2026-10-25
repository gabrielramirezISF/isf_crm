from django.urls import path

from . import views

urlpatterns = [
    path("", views.resumen, name="resumen"),
    path("invitar/", views.invitar, name="invitar"),
    path("personas/", views.personas, name="personas"),
    path("personas/<str:persona_id>/", views.ficha, name="ficha"),
    path("personas/<str:persona_id>/estado/", views.cambiar_estado, name="cambiar_estado"),
    path("nueva/", views.nueva, name="nueva"),
    path("reglas/", views.reglas, name="reglas"),
]

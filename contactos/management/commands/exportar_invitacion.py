"""
Exporta la lista de la semana: las primeras N personas según orden_sugerido.

Uso:
    python manage.py exportar_invitacion                  # usa el cupo de Reglas (300)
    python manage.py exportar_invitacion --cupo 250 --salida semana1.csv
    python manage.py exportar_invitacion --desde 301      # la segunda semana
"""
import csv

from django.core.management.base import BaseCommand

from contactos.models import Persona, Reglas


class Command(BaseCommand):
    help = "Exporta a CSV las personas a invitar esta semana."

    def add_arguments(self, parser):
        parser.add_argument("--cupo", type=int, default=None)
        parser.add_argument("--desde", type=int, default=1, help="Posición inicial (1 = la primera).")
        parser.add_argument("--salida", default="invitacion.csv")

    def handle(self, cupo, desde, salida, **opts):
        cupo = cupo or Reglas.actuales().cupo_semanal
        hasta = desde + cupo - 1
        qs = Persona.objects.filter(
            orden_sugerido__gte=desde, orden_sugerido__lte=hasta
        ).order_by("orden_sugerido")
        with open(salida, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["orden", "persona_id", "nombre", "correo", "organizacion", "prioridad"])
            for p in qs:
                w.writerow([p.orden_sugerido, p.persona_id, p.nombre, p.correo, p.organizacion, p.get_prioridad_display()])
        self.stdout.write(self.style.SUCCESS(f"{qs.count()} personas (posiciones {desde}–{hasta}) en {salida}"))

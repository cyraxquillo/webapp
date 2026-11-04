from django.core.management.base import BaseCommand

from api.breaks import auto_end_overdue_breaks


class Command(BaseCommand):
    help = "Automatically end overdue employee breaks and send notifications."

    def handle(self, *args, **options):
        ended_count = auto_end_overdue_breaks()
        self.stdout.write(
            self.style.SUCCESS(f"Automatically ended {ended_count} overdue break(s).")
        )

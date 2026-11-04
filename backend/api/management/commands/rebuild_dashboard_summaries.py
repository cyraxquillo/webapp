from django.core.management.base import BaseCommand, CommandError

from api.firestore_sync import rebuild_dashboard_summaries


class Command(BaseCommand):
    help = "Rebuild global and team dashboard summary documents from SQL."

    def handle(self, *args, **options):
        try:
            counts = rebuild_dashboard_summaries()
        except Exception as error:
            raise CommandError(f"Dashboard summary rebuild failed ({type(error).__name__})") from error
        self.stdout.write(
            self.style.SUCCESS(
                "Rebuilt dashboard summaries: written={written} "
                "deleted_stale_teams={deleted_stale_teams}.".format(**counts)
            )
        )
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from api.automation import create_monthly_evaluations


class Command(BaseCommand):
    help = "Create the current month's pending evaluations for active employees."

    def add_arguments(self, parser):
        parser.add_argument(
            "--month",
            help="Evaluation month in YYYY-MM format (defaults to the current month).",
        )

    def handle(self, *args, **options):
        month = None
        if options["month"]:
            try:
                month = datetime.strptime(options["month"], "%Y-%m").date()
            except ValueError as error:
                raise CommandError("--month must use YYYY-MM format.") from error
        try:
            created = create_monthly_evaluations(month)
        except ValueError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(
            self.style.SUCCESS(f"Created {len(created)} monthly evaluation(s).")
        )
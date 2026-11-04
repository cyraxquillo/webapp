from django.core.management.base import BaseCommand, CommandError

from api.firestore_sync import sync_pending_outbox


class Command(BaseCommand):
    help = "Retry pending SQL-to-Firestore sync outbox entries."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=100)
        parser.add_argument(
            "--include-retries",
            action="store_true",
            help="Retry entries before their backoff time.",
        )

    def handle(self, *args, **options):
        if options["batch_size"] < 1:
            raise CommandError("--batch-size must be a positive integer")
        counts = sync_pending_outbox(
            batch_size=options["batch_size"],
            include_retries=options["include_retries"],
        )
        style = self.style.SUCCESS if counts["failed"] == 0 else self.style.WARNING
        self.stdout.write(
            style(
                "Processed {processed}; synced {succeeded}; still pending {failed}.".format(
                    **counts
                )
            )
        )
        if counts["failed"]:
            raise CommandError("One or more outbox entries remain queued for retry")
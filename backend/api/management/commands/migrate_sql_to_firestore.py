from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from api.firebase_client import get_firestore_client
from api.firestore_sync import (
    employee_document,
    evaluation_document,
    rebuild_dashboard_summaries,
)
from api.models import Employee, Evaluation


class Command(BaseCommand):
    help = "Copy SQL employees and evaluations to Firestore using stable SQL-ID document IDs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compare SQL rows with Firestore and print planned counts without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        try:
            client = get_firestore_client()
        except Exception as error:
            raise CommandError(f"Could not initialize Firestore ({type(error).__name__})") from error

        counts = {}
        errors = Counter()
        sources = (
            (
                "employees",
                Employee.objects.select_related("department").iterator(chunk_size=500),
                employee_document,
            ),
            (
                "evaluations",
                Evaluation.objects.select_related("employee__department").iterator(chunk_size=500),
                evaluation_document,
            ),
        )
        for collection_name, records, serialize in sources:
            collection_counts = Counter()
            collection = client.collection(collection_name)
            for record in records:
                reference = collection.document(str(record.pk))
                try:
                    snapshot = reference.get(timeout=8)
                    payload = serialize(record)
                    if not snapshot.exists:
                        action = "create"
                    elif (snapshot.to_dict() or {}) != payload:
                        action = "update"
                    else:
                        action = "unchanged"
                    if not dry_run and action != "unchanged":
                        reference.set(payload, timeout=8)
                    collection_counts[action] += 1
                except Exception as error:
                    collection_counts["failed"] += 1
                    errors[type(error).__name__] += 1
            counts[collection_name] = collection_counts

        self.stdout.write("Mode: " + ("dry-run (no writes)" if dry_run else "write"))
        for collection_name in ("employees", "evaluations"):
            current = counts[collection_name]
            self.stdout.write(
                "{name}: scanned={scanned} create={create} update={update} "
                "unchanged={unchanged} failed={failed}".format(
                    name=collection_name,
                    scanned=sum(current.values()),
                    create=current["create"],
                    update=current["update"],
                    unchanged=current["unchanged"],
                    failed=current["failed"],
                )
            )
        if errors:
            self.stdout.write("Error types: " + ", ".join(
                f"{name}={count}" for name, count in sorted(errors.items())
            ))
            raise CommandError("Migration comparison/copy had failures; see counts above")
        if not dry_run:
            try:
                summary_counts = rebuild_dashboard_summaries(client)
            except Exception as error:
                raise CommandError(
                    f"Records copied, but dashboard summary rebuild failed ({type(error).__name__})"
                ) from error
            self.stdout.write(
                "dashboard_summaries: written={written} deleted_stale_teams={deleted_stale_teams}".format(
                    **summary_counts
                )
            )
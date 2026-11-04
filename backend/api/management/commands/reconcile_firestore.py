from collections import Counter

from django.core.management.base import BaseCommand, CommandError

from api.firebase_client import get_firestore_client
from api.firestore_sync import (
    employee_document,
    evaluation_document,
    rebuild_dashboard_summaries,
)
from api.models import Employee, Evaluation


def reconcile_collection(client, name, records, serializer, apply):
    collection = client.collection(name)
    expected = {str(record.pk): serializer(record) for record in records}
    actual = {snapshot.id: snapshot.to_dict() or {} for snapshot in collection.stream()}
    counts = Counter()
    for document_id, payload in expected.items():
        existing = actual.get(document_id)
        if existing is None:
            counts["missing"] += 1
            if apply:
                collection.document(document_id).set(payload, timeout=8)
        elif existing != payload:
            counts["different"] += 1
            if apply:
                collection.document(document_id).set(payload, timeout=8)
        else:
            counts["matching"] += 1
    orphan_ids = set(actual) - set(expected)
    counts["orphaned"] = len(orphan_ids)
    if apply:
        for document_id in orphan_ids:
            collection.document(document_id).delete(timeout=8)
    return counts


class Command(BaseCommand):
    help = "Compare Firestore employees/evaluations with SQL; --apply repairs Firestore from SQL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply SQL-authoritative repairs and delete Firestore-only documents.",
        )

    def handle(self, *args, **options):
        try:
            client = get_firestore_client()
            employees = Employee.objects.select_related("department").iterator(chunk_size=500)
            evaluations = Evaluation.objects.select_related(
                "employee__department"
            ).iterator(chunk_size=500)
            results = {
                "employees": reconcile_collection(
                    client, "employees", employees, employee_document, options["apply"]
                ),
                "evaluations": reconcile_collection(
                    client, "evaluations", evaluations, evaluation_document, options["apply"]
                ),
            }
            if options["apply"]:
                summaries = rebuild_dashboard_summaries(client)
            else:
                summaries = None
        except Exception as error:
            raise CommandError(f"Reconciliation failed ({type(error).__name__})") from error

        self.stdout.write("Mode: " + ("apply" if options["apply"] else "report-only"))
        for name, counts in results.items():
            self.stdout.write(
                "{name}: matching={matching} missing={missing} different={different} "
                "orphaned={orphaned}".format(name=name, **counts)
            )
        if summaries is not None:
            self.stdout.write(
                "dashboard_summaries: written={written} deleted_stale_teams={deleted_stale_teams}".format(
                    **summaries
                )
            )
import logging

from django.core.management.base import BaseCommand

from api.automation import classify_evaluation
from api.ml.predict import UnknownDepartmentError
from api.models import Employee, Evaluation


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Refresh each active employee's latest Random Forest classification."

    def handle(self, *args, **options):
        updated = 0
        skipped = 0
        employees = Employee.objects.filter(status="active").select_related("department")
        for employee in employees.iterator():
            evaluation = (
                Evaluation.objects.filter(
                    employee=employee,
                    status=Evaluation.Status.completed,
                )
                .exclude(productivity_score__isnull=True)
                .order_by("-date", "-id")
                .first()
            )
            if evaluation is None:
                skipped += 1
                continue
            try:
                prediction = classify_evaluation(evaluation)
            except UnknownDepartmentError as error:
                logger.warning(
                    "Skipping classification for employee %s (%s): %s",
                    employee.pk,
                    employee.name,
                    error,
                )
                skipped += 1
            else:
                if prediction:
                    updated += 1
                else:
                    skipped += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Updated {updated} classification(s); skipped {skipped} employee(s)."
            )
        )
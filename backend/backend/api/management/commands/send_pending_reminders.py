from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from api.automation import create_notification
from api.models import Evaluation, Notification


class Command(BaseCommand):
    help = "Notify assignees when a monthly evaluation has remained pending for five days."

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=5)
        reminded = 0
        evaluations = Evaluation.objects.filter(
            status=Evaluation.Status.pending,
            created_at__lte=cutoff,
            reminder_sent_at__isnull=True,
            assigned_to__is_active=True,
        ).select_related("employee", "assigned_to")
        for evaluation in evaluations.iterator():
            create_notification(
                evaluation.assigned_to,
                "Monthly evaluation reminder",
                f"The monthly evaluation for {evaluation.employee.name} is still pending. Please review it when you can.",
                Notification.Category.reminder,
                dedupe_key=f"evaluation-reminder-{evaluation.pk}",
            )
            evaluation.reminder_sent_at = timezone.now()
            evaluation.save(update_fields=("reminder_sent_at",))
            reminded += 1
        self.stdout.write(
            self.style.SUCCESS(f"Sent {reminded} pending evaluation reminder(s).")
        )
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from api.models import TrustedDevice


class Command(BaseCommand):
    help = "Approve one or all pending trusted devices for an account."

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument(
            "--all-pending",
            action="store_true",
            help="Approve every pending device for the account.",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        try:
            user = user_model.objects.get(email__iexact=options["email"].strip())
        except user_model.DoesNotExist as exc:
            raise CommandError(f"No account found for {options['email']!r}.") from exc

        with transaction.atomic():
            pending_devices = TrustedDevice.objects.filter(
                user=user,
                status=TrustedDevice.Status.pending,
            ).select_for_update()
            if not options["all_pending"]:
                pending_devices = pending_devices.order_by("-created_at", "-pk")[:1]
            devices = list(pending_devices)
            now = timezone.now()
            for device in devices:
                device.status = TrustedDevice.Status.approved
                device.approved_at = now
                device.approved_by = None
                device.save(
                    update_fields=("status", "approved_at", "approved_by"),
                )

        if not devices:
            self.stdout.write(self.style.WARNING(f"No pending devices for {user.email}."))
            return
        self.stdout.write(
            self.style.SUCCESS(
                f"Approved {len(devices)} pending device(s) for {user.email}.",
            )
        )

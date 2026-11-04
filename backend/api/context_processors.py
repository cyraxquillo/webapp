from django.conf import settings

from .access import can_manage_device
from .models import Notification, TrustedDevice, User


def notification_count(request):
    user = request.user
    count = (
        Notification.objects.filter(recipient=user, read_at__isnull=True).count()
        if user.is_authenticated
        else 0
    )
    pending_device_approvals_count = 0
    if user.is_authenticated and user.role in (
        User.Role.admin,
        User.Role.management,
        User.Role.team_leader,
    ):
        pending_devices = TrustedDevice.objects.filter(
            status=TrustedDevice.Status.pending,
        ).select_related("user")
        pending_device_approvals_count = sum(
            1
            for device in pending_devices
            if device.user_id != user.pk and can_manage_device(user, device)
        )
    return {
        "unread_notifications_count": count,
        "pending_device_approvals_count": pending_device_approvals_count,
        "login_demo_enabled": settings.DEBUG,
    }
from .models import Notification


def notification_count(request):
    user = request.user
    count = (
        Notification.objects.filter(recipient=user, read_at__isnull=True).count()
        if user.is_authenticated
        else 0
    )
    return {"unread_notifications_count": count}
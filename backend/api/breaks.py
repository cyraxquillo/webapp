from datetime import datetime
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from .access import MANAGEMENT_ROLES
from .automation import create_notification
from .models import AttendanceBreak, AttendanceRecord, AutomationSettings, Notification, User


MANILA = ZoneInfo("Asia/Manila")


def manila_localtime(value=None):
    return timezone.localtime(value or timezone.now(), MANILA)


def break_auto_end_at(work_date, end_time):
    return timezone.make_aware(datetime.combine(work_date, end_time), MANILA)


def break_seconds_for_attendance(record, now=None):
    now = now or timezone.now()
    total_seconds = 0
    for attendance_break in record.breaks.all():
        start = attendance_break.started_at
        if record.clock_in_at:
            start = max(start, record.clock_in_at)
        end = attendance_break.ended_at or now
        end = min(end, attendance_break.auto_end_at)
        if record.clock_out_at:
            end = min(end, record.clock_out_at)
        if end > start:
            total_seconds += (end - start).total_seconds()
    return total_seconds


def net_attendance_seconds(record, now=None):
    if not record.clock_in_at:
        return 0
    end = record.clock_out_at or now or timezone.now()
    gross_seconds = max(0, (end - record.clock_in_at).total_seconds())
    return max(0, gross_seconds - break_seconds_for_attendance(record, end))


def _break_end_label(attendance_break):
    return timezone.localtime(attendance_break.auto_end_at, MANILA).strftime("%I:%M %p").lstrip("0")


def _notify_break_auto_end(attendance_break):
    employee = attendance_break.attendance.employee
    recipients = {}
    if employee.user_id:
        recipients[employee.user_id] = employee.user
    if employee.team_leader_id and employee.team_leader.is_active:
        recipients[employee.team_leader_id] = employee.team_leader
    for user in User.objects.filter(
        role__in=MANAGEMENT_ROLES,
        is_active=True,
    ):
        recipients[user.pk] = user

    auto_end_label = _break_end_label(attendance_break)
    for recipient in recipients.values():
        if recipient.pk == employee.user_id:
            title = "Break not ended on time"
            message = (
                f"Your break was automatically ended at {auto_end_label}. "
                "Please remember to click End Break."
            )
        else:
            title = "Employee did not end break on time"
            message = (
                f"{employee.name} ({employee.employee_code}) did not end their break "
                f"on time. Their break was automatically ended at {auto_end_label}."
            )
        create_notification(
            recipient,
            title,
            message,
            Notification.Category.automatic,
            dedupe_key=f"break-auto-end-{attendance_break.pk}-{recipient.pk}",
        )


def auto_end_overdue_breaks(now=None, employee_user=None):
    now = now or timezone.now()
    overdue_ids = AttendanceBreak.objects.filter(
        ended_at__isnull=True,
        auto_end_at__lte=now,
    )
    if employee_user is not None:
        overdue_ids = overdue_ids.filter(attendance__employee__user=employee_user)

    ended_count = 0
    for break_id in overdue_ids.values_list("pk", flat=True):
        with transaction.atomic():
            attendance_break = (
                AttendanceBreak.objects.select_for_update()
                .select_related(
                    "attendance__employee__user",
                    "attendance__employee__team_leader",
                )
                .get(pk=break_id)
            )
            if attendance_break.ended_at is not None or attendance_break.auto_end_at > now:
                continue
            attendance_break.ended_at = attendance_break.auto_end_at
            attendance_break.auto_ended = True
            attendance_break.save(update_fields=("ended_at", "auto_ended"))
            _notify_break_auto_end(attendance_break)
            ended_count += 1
    return ended_count


def end_active_break_for_attendance(record, ended_at):
    attendance_break = (
        AttendanceBreak.objects.select_for_update()
        .filter(attendance=record, ended_at__isnull=True)
        .first()
    )
    if attendance_break is None:
        return None
    attendance_break.ended_at = min(ended_at, attendance_break.auto_end_at)
    attendance_break.save(update_fields=("ended_at",))
    return attendance_break

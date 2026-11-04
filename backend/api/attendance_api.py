from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .access import (
    ATTENDANCE_ROLES,
    STAFF_ROLES,
    attendance_visible_users,
)
from .attendance_locations import (
    OutsidePremisesBlocked,
    apply_attendance_location,
    create_outside_attendance_notifications,
)
from .breaks import (
    auto_end_overdue_breaks,
    end_active_break_for_attendance,
    manila_localtime,
    net_attendance_seconds,
)
from .hybrid import update_employee_attendance_rate
from .models import AttendanceRecord, AutomationSettings, Employee, OfficeLocation, User
from .serializers import OfficeLocationSerializer
from .views_hybrid import _record_status


def _attendance_record_data(record):
    user = record.account
    employee = record.employee
    team_leader = employee.team_leader if employee else None
    return {
        "id": record.pk,
        "user_id": user.pk if user else None,
        "user_name": record.display_name,
        "role": user.get_role_display() if user else "",
        "team": team_leader.name if team_leader else "",
        "work_date": record.work_date.isoformat(),
        "clock_in_at": record.clock_in_at.isoformat() if record.clock_in_at else None,
        "clock_out_at": record.clock_out_at.isoformat() if record.clock_out_at else None,
        "status": record.attendance_status,
        "latitude": record.latitude,
        "longitude": record.longitude,
        "accuracy": record.accuracy,
        "distance_from_office": record.distance_from_office,
        "is_outside_premises": record.is_outside_premises,
        "outside_premises_reason": record.outside_premises_reason,
    }


def _attendance_record_for_user(user, work_date, *, lock=False):
    queryset = AttendanceRecord.objects.filter(
        Q(user=user) | Q(employee__user=user),
        work_date=work_date,
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.select_related("employee", "employee__team_leader", "user").first()


def _employee_for_user(user):
    if user.role in STAFF_ROLES:
        return get_object_or_404(Employee.objects.select_related("team_leader"), user=user)
    return None


def _time_error(error):
    message = error.messages[0] if hasattr(error, "messages") else str(error)
    return Response({"detail": message}, status=status.HTTP_400_BAD_REQUEST)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def attendance_records(request):
    if request.user.role not in (*ATTENDANCE_ROLES, User.Role.admin):
        return Response({"detail": "You do not have permission"}, status=403)
    records = AttendanceRecord.objects.all()
    if request.user.role != User.Role.admin:
        visible_users = attendance_visible_users(request.user)
        records = records.filter(
            Q(user__in=visible_users) | Q(employee__user__in=visible_users)
        )
    records = (
        records.select_related("employee", "employee__user", "employee__team_leader", "user")
        .distinct()
        .order_by("-work_date", "-pk")[:200]
    )
    return Response([_attendance_record_data(record) for record in records])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attendance_time_in(request):
    user = request.user
    if user.role not in ATTENDANCE_ROLES:
        return Response({"detail": "You do not have permission"}, status=403)
    employee = _employee_for_user(user)
    now = timezone.now()
    local_now = manila_localtime(now)
    work_date = local_now.date()
    settings = AutomationSettings.objects.get_or_create_default()
    if local_now.time().replace(tzinfo=None) < settings.attendance_time_in_open_time:
        return Response(
            {"detail": "Time In is not open yet."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        with transaction.atomic():
            record = _attendance_record_for_user(user, work_date, lock=True)
            if record and record.clock_in_at:
                return Response(
                    {"detail": "You have already timed in today."},
                    status=status.HTTP_409_CONFLICT,
                )
            if record is None:
                record = AttendanceRecord(
                    employee=employee,
                    user=user,
                    work_date=work_date,
                )
            else:
                record.user = user
            outside = apply_attendance_location(
                record,
                user,
                request.data,
                require_reason=True,
            )
            record.clock_in_at = now
            record.status, record.minutes_late = _record_status(
                record.clock_in_at,
                settings,
            )
            record.time_in_status = (
                "late" if record.status == AttendanceRecord.Status.late else "on_time"
            )
            record.time_out_status = ""
            record.save()
            if employee is not None:
                update_employee_attendance_rate(
                    employee,
                    settings,
                    work_date.replace(day=1),
                )
            if outside:
                create_outside_attendance_notifications(user, record)
    except OutsidePremisesBlocked as error:
        create_outside_attendance_notifications(
            user,
            error.record,
            attempt_at=now,
            blocked=True,
        )
        return Response(
            {
                "detail": error.messages[0],
                "is_outside_premises": True,
                "distance_from_office": error.record.distance_from_office,
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except DjangoValidationError as error:
        return _time_error(error)
    return Response(_attendance_record_data(record), status=status.HTTP_201_CREATED)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attendance_time_out(request):
    user = request.user
    if user.role not in ATTENDANCE_ROLES:
        return Response({"detail": "You do not have permission"}, status=403)
    employee = _employee_for_user(user)
    now = timezone.now()
    auto_end_overdue_breaks(now=now, employee_user=user)
    work_date = manila_localtime(now).date()
    settings = AutomationSettings.objects.get_or_create_default()
    try:
        with transaction.atomic():
            record = _attendance_record_for_user(user, work_date, lock=True)
            if record is None or record.clock_in_at is None:
                return Response(
                    {"detail": "Time In before recording Time Out."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if record.clock_out_at is not None:
                return Response(
                    {"detail": "You have already timed out today."},
                    status=status.HTTP_409_CONFLICT,
                )
            apply_attendance_location(
                record,
                user,
                request.data,
                require_reason=False,
            )
            record.user = user
            end_active_break_for_attendance(record, now)
            record.clock_out_at = now
            if now > record.clock_in_at:
                record.hours = round(net_attendance_seconds(record, now) / 3600, 2)
            local_clock_out = manila_localtime(now)
            if local_clock_out.time().replace(tzinfo=None) < settings.attendance_expected_time_out:
                record.status = AttendanceRecord.Status.early_out
                record.time_out_status = AttendanceRecord.TimeOutStatus.early_out
            else:
                if record.status == AttendanceRecord.Status.early_out:
                    record.status = (
                        AttendanceRecord.Status.late
                        if record.time_in_status == "late"
                        else AttendanceRecord.Status.present
                    )
                record.time_out_status = AttendanceRecord.TimeOutStatus.on_time
            record.save()
            if employee is not None:
                update_employee_attendance_rate(
                    employee,
                    settings,
                    work_date.replace(day=1),
                )
    except DjangoValidationError as error:
        return _time_error(error)
    return Response(_attendance_record_data(record))


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def office_locations(request):
    if request.user.role != User.Role.admin:
        return Response({"detail": "Admin access is required"}, status=403)
    if request.method == "GET":
        rows = OfficeLocation.objects.all()
        return Response(OfficeLocationSerializer(rows, many=True).data)
    serializer = OfficeLocationSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    office = serializer.save()
    return Response(OfficeLocationSerializer(office).data, status=status.HTTP_201_CREATED)


@api_view(["PUT", "PATCH", "DELETE"])
@permission_classes([IsAuthenticated])
def office_location_detail(request, pk):
    if request.user.role != User.Role.admin:
        return Response({"detail": "Admin access is required"}, status=403)
    office = get_object_or_404(OfficeLocation, pk=pk)
    if request.method == "DELETE":
        office.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = OfficeLocationSerializer(
        office,
        data=request.data,
        partial=request.method == "PATCH",
    )
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    serializer.save()
    return Response(serializer.data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def notifications(request):
    queryset = request.user.notifications.all()
    return Response(
        {
            "unread_count": queryset.filter(read_at__isnull=True).count(),
            "results": [
                {
                    "id": item.pk,
                    "title": item.title,
                    "message": item.message,
                    "created_at": item.created_at.isoformat(),
                    "read_at": item.read_at.isoformat() if item.read_at else None,
                }
                for item in queryset[:100]
            ],
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def notification_mark_read(request, pk):
    notification = get_object_or_404(
        request.user.notifications.all(),
        pk=pk,
    )
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=("read_at",))
    return Response({"id": notification.pk, "read": True})

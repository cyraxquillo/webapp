from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import employee_queryset_for, evaluation_queryset_for, is_management, is_team_leader
from .forms_hybrid import HybridRatingForm
from .hybrid import (
    attendance_points_for_record,
    calculate_attendance_rate,
    calculate_hybrid_score,
    calculate_work_score,
    update_employee_attendance_rate,
    working_days_in_month,
)
from .models import AttendanceRecord, AutomationSettings, Employee, Evaluation, Submission, User


def _attendance_records(user):
    return AttendanceRecord.objects.filter(employee__in=employee_queryset_for(user)).order_by(
        "-work_date", "-id"
    )


def _settings():
    return AutomationSettings.objects.get_or_create_default()


def _local_now():
    return timezone.localdate(timezone.now())


def _local_datetime():
    return timezone.localtime(timezone.now())


def _can_manage_attendance(user):
    return user.role in (User.Role.admin, User.Role.management, User.Role.hr_manager)


def _attendance_scope(user):
    if _can_manage_attendance(user):
        return employee_queryset_for(user)
    if user.role == User.Role.team_leader:
        return employee_queryset_for(user)
    return employee_queryset_for(user)


@login_required
def attendance_list(request):
    if request.user.role not in (
        User.Role.staff,
        User.Role.employee,
        User.Role.team_leader,
        User.Role.management,
        User.Role.admin,
        User.Role.hr_manager,
    ):
        return redirect("dashboard")
    settings = _settings()
    local_now = _local_datetime()
    today = local_now.date()
    records = _attendance_records(request.user).filter(
        work_date__year=today.year,
        work_date__month=today.month,
    ).select_related("employee", "employee__user")
    if request.user.role in (User.Role.team_leader, User.Role.management, User.Role.admin, User.Role.hr_manager):
        records = AttendanceRecord.objects.filter(
            employee__in=_attendance_scope(request.user),
            work_date__year=today.year,
            work_date__month=today.month,
        ).select_related("employee", "employee__user").order_by("-work_date", "-id")
    current_record = None
    if request.user.role in (User.Role.staff, User.Role.employee):
        current_record = AttendanceRecord.objects.filter(
            employee__user=request.user,
            work_date=today,
        ).first()
    for record in records:
        record.points = attendance_points_for_record(record, settings)

    expected_time_out = timezone.make_aware(
        datetime.combine(today, settings.attendance_expected_time_out)
    )
    opens_at = timezone.make_aware(
        datetime.combine(today, settings.attendance_time_in_open_time)
    )
    can_time_in = local_now >= opens_at and not (
        current_record and current_record.clock_in_at
    )
    can_time_out = bool(
        current_record and current_record.clock_in_at and not current_record.clock_out_at
    )
    hours_so_far = None
    if can_time_out:
        hours_so_far = round((timezone.now() - current_record.clock_in_at).total_seconds() / 3600, 2)

    today_rows = []
    if request.user.role in (User.Role.team_leader, User.Role.management, User.Role.admin, User.Role.hr_manager):
        today_records = {
            record.employee_id: record
            for record in AttendanceRecord.objects.filter(
                employee__in=_attendance_scope(request.user), work_date=today
            ).select_related("employee")
        }
        for employee in employee_queryset_for(request.user).filter(
            status=Employee.Status.active,
            date_hired__lte=today,
        ).order_by("name"):
            record = today_records.get(employee.pk)
            if record:
                status_label = record.attendance_status
                time_in_label = "Late" if record.time_in_status == "late" or record.status == AttendanceRecord.Status.late else "On time"
                time_out_label = dict(AttendanceRecord.TimeOutStatus.choices).get(record.time_out_status, "—")
            elif local_now >= opens_at:
                status_label = AttendanceRecord.Status.absent.label
                time_in_label = "—"
                time_out_label = "—"
            else:
                status_label = "Not started"
                time_in_label = "—"
                time_out_label = "—"
            today_rows.append(
                {
                    "employee": employee,
                    "record": record,
                    "status": status_label,
                    "time_in_status": time_in_label,
                    "time_out_status": time_out_label,
                }
            )
    return render(
        request,
        "api/attendance_list.html",
        {
            "records": records,
            "employees": employee_queryset_for(request.user),
            "today": today,
            "local_now": local_now,
            "current_record": current_record,
            "can_correct": _can_manage_attendance(request.user) or request.user.role == User.Role.team_leader,
            "settings": settings,
            "can_time_in": can_time_in,
            "can_time_out": can_time_out,
            "hours_so_far": hours_so_far,
            "expected_time_out": expected_time_out,
            "time_in_opens_at": opens_at,
            "today_rows": today_rows,
            "is_employee": request.user.role in (User.Role.staff, User.Role.employee),
        },
    )


def _record_status(clock_in_at, settings):
    if clock_in_at is None:
        return AttendanceRecord.Status.absent, None
    local_clock_in = timezone.localtime(clock_in_at)
    cutoff = datetime.combine(local_clock_in.date(), settings.attendance_cutoff_time)
    cutoff = timezone.make_aware(cutoff)
    grace = settings.attendance_grace_minutes
    minutes_late = max(0, int((local_clock_in - cutoff).total_seconds() // 60) - grace)
    if local_clock_in.time().replace(tzinfo=None) <= settings.attendance_cutoff_time:
        return AttendanceRecord.Status.present, 0
    return AttendanceRecord.Status.late, minutes_late


@login_required
@require_POST
def attendance_time_in(request):
    if request.user.role not in (User.Role.staff, User.Role.employee):
        return redirect("attendance-list")
    employee = get_object_or_404(employee_queryset_for(request.user), user=request.user)
    server_now = timezone.now()
    local_now = timezone.localtime(server_now)
    today = local_now.date()
    settings = _settings()
    if local_now.time().replace(tzinfo=None) < settings.attendance_time_in_open_time:
        opening_time = settings.attendance_time_in_open_time.strftime("%I:%M %p").lstrip("0")
        messages.error(request, f"Time In opens at {opening_time}")
        return redirect("attendance-list")
    with transaction.atomic():
        existing = AttendanceRecord.objects.filter(employee=employee, work_date=today).first()
        if existing and existing.clock_in_at is not None:
            return HttpResponse(status=409)
        if existing is None:
            record = AttendanceRecord.objects.create(
                employee=employee,
                work_date=today,
                clock_in_at=server_now,
            )
        else:
            record = existing
            record.clock_in_at = server_now
        record.status, record.minutes_late = _record_status(record.clock_in_at, settings)
        record.time_in_status = (
            "late" if record.status == AttendanceRecord.Status.late else "on_time"
        )
        record.time_out_status = ""
        record.save(update_fields=("clock_in_at", "status", "time_in_status", "time_out_status", "minutes_late"))
        update_employee_attendance_rate(employee, settings, today.replace(day=1))
        messages.success(request, "Time In recorded from the server clock.")
    return redirect("attendance-list")


@login_required
@require_POST
def attendance_time_out(request):
    if request.user.role not in (User.Role.staff, User.Role.employee):
        return redirect("attendance-list")
    employee = get_object_or_404(employee_queryset_for(request.user), user=request.user)
    server_now = timezone.now()
    today = timezone.localdate(server_now)
    settings = _settings()
    with transaction.atomic():
        record = AttendanceRecord.objects.select_for_update().filter(
            employee=employee,
            work_date=today,
            clock_in_at__isnull=False,
        ).first()
        if record is None or record.clock_out_at is not None:
            return redirect("attendance-list")
        record.clock_out_at = server_now
        if server_now > record.clock_in_at:
            record.hours = round((server_now - record.clock_in_at).total_seconds() / 3600, 2)
        local_clock_out = timezone.localtime(server_now)
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
        record.save(update_fields=("clock_out_at", "hours", "status", "time_out_status"))
        update_employee_attendance_rate(employee, settings, today.replace(day=1))
    messages.success(request, "Time Out recorded from the server clock.")
    return redirect("attendance-list")


@login_required
def attendance_correct(request, pk):
    record = get_object_or_404(AttendanceRecord, pk=pk)
    if request.user.role != User.Role.team_leader and not _can_manage_attendance(request.user):
        raise PermissionDenied
    if request.user.role == User.Role.team_leader and record.employee.team_leader_id != request.user.pk:
        raise PermissionDenied
    if request.method != "POST":
        return render(
            request,
            "api/attendance_correct.html",
            {
                "record": record,
                "clock_in_local": timezone.localtime(record.clock_in_at).strftime("%Y-%m-%dT%H:%M") if record.clock_in_at else "",
                "clock_out_local": timezone.localtime(record.clock_out_at).strftime("%Y-%m-%dT%H:%M") if record.clock_out_at else "",
            },
        )
    new_status = request.POST.get("status")
    reason = request.POST.get("reason", "").strip()
    if new_status not in dict(AttendanceRecord.Status.choices):
        return HttpResponseBadRequest("Invalid attendance status.")
    if not reason:
        return HttpResponseBadRequest("A correction reason is required.")
    old_status = record.status
    old_clock_in_at = record.clock_in_at
    old_clock_out_at = record.clock_out_at
    old_time_out_status = record.time_out_status
    for field_name in ("clock_in_at", "clock_out_at"):
        if field_name not in request.POST:
            continue
        raw_value = request.POST.get(field_name, "").strip()
        if not raw_value:
            setattr(record, field_name, None)
            continue
        try:
            corrected_time = datetime.fromisoformat(raw_value)
        except ValueError:
            return HttpResponseBadRequest("Enter a valid corrected time.")
        if timezone.is_naive(corrected_time):
            corrected_time = timezone.make_aware(corrected_time)
        setattr(record, field_name, corrected_time)

    record.status = new_status
    if new_status in (AttendanceRecord.Status.present, AttendanceRecord.Status.late):
        record.time_in_status = "late" if new_status == AttendanceRecord.Status.late else "on_time"
    if new_status == AttendanceRecord.Status.early_out:
        record.time_out_status = AttendanceRecord.TimeOutStatus.early_out
    elif new_status == AttendanceRecord.Status.no_time_out:
        record.time_out_status = AttendanceRecord.TimeOutStatus.no_time_out
    elif new_status == AttendanceRecord.Status.absent:
        record.time_in_status = ""
        record.time_out_status = AttendanceRecord.TimeOutStatus.no_time_out
    if request.POST.get("excuse_time_out") == "on":
        if record.status != AttendanceRecord.Status.early_out:
            return HttpResponseBadRequest("Only an early Time Out can be excused here.")
        record.time_out_status = AttendanceRecord.TimeOutStatus.excused

    record.correction_reason = reason
    record.corrected_at = timezone.now()
    record.corrected_by = request.user
    record.save()
    from .models import AttendanceCorrection

    AttendanceCorrection.objects.create(
        record=record,
        changed_by=request.user,
        old_status=old_status,
        new_status=record.status,
        old_clock_in_at=old_clock_in_at,
        new_clock_in_at=record.clock_in_at,
        old_clock_out_at=old_clock_out_at,
        new_clock_out_at=record.clock_out_at,
        old_time_out_status=old_time_out_status,
        new_time_out_status=record.time_out_status,
        reason=reason,
    )
    update_employee_attendance_rate(record.employee, _settings(), record.work_date.replace(day=1))
    messages.success(request, "Attendance correction recorded.")
    return redirect("attendance-list")


def hybrid_review(request, pk):
    evaluation = get_object_or_404(evaluation_queryset_for(request.user), pk=pk)
    if not (is_team_leader(request.user) or is_management(request.user)):
        return redirect("dashboard")
    actor = request.user
    if actor.role == User.Role.team_leader:
        field_name = "team_leader_rating"
    else:
        field_name = "management_rating"
    form = HybridRatingForm(
        request.POST or None,
        instance=evaluation,
        actor=actor,
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        attendance_records = AttendanceRecord.objects.filter(
            employee=evaluation.employee,
            work_date__year=evaluation.cycle_month.year,
            work_date__month=evaluation.cycle_month.month,
        )
        settings = _settings()
        attendance = calculate_attendance_rate(
            list(attendance_records),
            settings,
            working_days_in_month(settings, evaluation.cycle_month, evaluation.employee.date_hired),
        )
        evaluation.employee.attendance_rate = attendance["attendance_rate"]
        evaluation.employee.save(update_fields=("attendance_rate",))
        submissions = list(
            Submission.objects.filter(
                employee=evaluation.employee,
                uploaded_at__year=evaluation.cycle_month.year,
                uploaded_at__month=evaluation.cycle_month.month,
            )
        )
        work = calculate_work_score(submissions)
        score = calculate_hybrid_score(
            attendance_rate=attendance["attendance_rate"],
            work_score=work["work_score"],
            team_leader_rating=evaluation.team_leader_rating,
            management_rating=evaluation.management_rating,
            weights={"attendance": 20, "work": 30, "team_leader": 30, "management": 20},
        )
        evaluation.hybrid_score = score["overall_score"]
        evaluation.phase = Evaluation.Phase.finalized if evaluation.management_rating is not None else Evaluation.Phase.management
        evaluation.save(update_fields=("team_leader_rating", "management_rating", "hybrid_score", "phase"))
        messages.success(request, "Hybrid evaluation score saved.")
        return redirect("evaluation-detail", pk=evaluation.pk)
    return render(
        request,
        "api/hybrid_review.html",
        {"form": form, "evaluation": evaluation, "rating_field": field_name},
    )

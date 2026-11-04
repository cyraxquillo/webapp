from datetime import date, datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from .access import (
    ATTENDANCE_ROLES,
    STAFF_ROLES,
    attendance_visible_users,
    employee_queryset_for,
    evaluation_queryset_for,
    get_managed_users,
    is_management,
    is_team_leader,
)
from .attendance_locations import (
    OutsidePremisesBlocked,
    apply_attendance_location,
    create_outside_attendance_notifications,
)
from .breaks import (
    MANILA,
    auto_end_overdue_breaks,
    break_auto_end_at,
    break_seconds_for_attendance,
    end_active_break_for_attendance,
    manila_localtime,
    net_attendance_seconds,
)
from .forms_hybrid import HybridRatingForm
from .hybrid import (
    attendance_points_for_record,
    calculate_attendance_rate,
    calculate_hybrid_score,
    calculate_work_score,
    update_employee_attendance_rate,
    working_days_in_month,
)
from .models import AttendanceBreak, AttendanceRecord, AutomationSettings, Employee, Evaluation, Submission, User


def _attendance_records(user):
    records = AttendanceRecord.objects.all()
    if user.role != User.Role.admin:
        visible_users = attendance_visible_users(user)
        records = records.filter(
            Q(user__in=visible_users) | Q(employee__user__in=visible_users)
        )
    return records.distinct().order_by("-work_date", "-id")


def _settings():
    return AutomationSettings.objects.get_or_create_default()


def _local_now():
    return timezone.localdate(timezone.now())


def _local_datetime():
    return timezone.localtime(timezone.now())


def _can_manage_attendance(user):
    return user.role in (User.Role.admin, User.Role.management, User.Role.hr_manager)


def _attendance_record_for_user(user, work_date, *, lock=False):
    queryset = AttendanceRecord.objects.filter(
        Q(user=user) | Q(employee__user=user),
        work_date=work_date,
    )
    if lock:
        queryset = queryset.select_for_update()
    return queryset.select_related("employee", "user").first()


def _employee_for_attendance(user):
    if user.role in STAFF_ROLES:
        return get_object_or_404(Employee.objects.select_related("team_leader"), user=user)
    return None


def _attendance_location_error(request, error):
    message = error.messages[0] if hasattr(error, "messages") else str(error)
    if request.content_type == "application/json":
        return HttpResponseBadRequest(message)
    messages.error(request, message)
    return redirect("attendance-list")


@login_required
def attendance_list(request):
    if request.user.role not in (*ATTENDANCE_ROLES, User.Role.admin):
        return redirect("dashboard")
    settings = _settings()
    now = timezone.now()
    if request.user.role in (User.Role.staff, User.Role.employee):
        auto_end_overdue_breaks(now=now, employee_user=request.user)
    local_now = manila_localtime(now)
    today = local_now.date()
    records = _attendance_records(request.user).filter(
        work_date__year=today.year,
        work_date__month=today.month,
    ).select_related("employee", "employee__user").prefetch_related("breaks")
    correction_records = records.filter(employee__isnull=False)
    current_record = None
    if request.user.role in ATTENDANCE_ROLES:
        current_record = _attendance_record_for_user(request.user, today)
        if current_record is not None:
            current_record = AttendanceRecord.objects.prefetch_related("breaks").get(
                pk=current_record.pk
            )
    for record in records:
        record.points = attendance_points_for_record(record, settings)
        record.break_minutes = int(break_seconds_for_attendance(record, now) // 60)
        record.active_break = next(
            (attendance_break for attendance_break in record.breaks.all() if attendance_break.ended_at is None),
            None,
        )
        record.live_hours = (
            round(net_attendance_seconds(record, now) / 3600, 2)
            if record.clock_in_at and not record.clock_out_at
            else None
        )

    expected_time_out = timezone.make_aware(datetime.combine(today, settings.attendance_expected_time_out), MANILA)
    opens_at = timezone.make_aware(datetime.combine(today, settings.attendance_time_in_open_time), MANILA)
    break_end_at = break_auto_end_at(today, settings.attendance_break_end_time)
    can_time_in = local_now >= opens_at and not (
        current_record and current_record.clock_in_at
    )
    can_time_out = bool(
        current_record and current_record.clock_in_at and not current_record.clock_out_at
    )
    active_break = (
        current_record.breaks.filter(ended_at__isnull=True).first()
        if current_record
        else None
    )
    current_time = local_now.time().replace(tzinfo=None)
    break_window_open = settings.attendance_break_start_time <= current_time < settings.attendance_break_end_time
    can_start_break = bool(
        break_window_open
        and can_time_out
        and active_break is None
    )
    can_end_break = bool(active_break and now < active_break.auto_end_at)
    break_window_message = (
        "Break is only available from "
        f"{settings.attendance_break_start_time.strftime('%I:%M %p').lstrip('0')} to "
        f"{settings.attendance_break_end_time.strftime('%I:%M %p').lstrip('0')}."
    )
    hours_so_far = None
    if can_time_out:
        hours_so_far = round(net_attendance_seconds(current_record, now) / 3600, 2)

    today_rows = []
    if request.user.role in (User.Role.admin, User.Role.management, User.Role.hr_manager, User.Role.team_leader):
        roster_users = (
            User.objects.filter(role__in=ATTENDANCE_ROLES)
            if request.user.role == User.Role.admin
            else get_managed_users(request.user)
        )
        today_records = {
            record.account.pk: record
            for record in _attendance_records(request.user)
            .filter(work_date=today)
            .select_related("employee", "employee__user", "user")
            if record.account is not None
        }
        for account in roster_users.order_by("name"):
            employee = Employee.objects.filter(user=account).first()
            if employee and (
                employee.status != Employee.Status.active
                or employee.date_hired > today
            ):
                continue
            record = today_records.get(account.pk)
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
                    "user": account,
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
            "correction_records": correction_records,
            "employees": employee_queryset_for(request.user),
            "today": today,
            "local_now": local_now,
            "current_record": current_record,
            "can_correct": _can_manage_attendance(request.user) or request.user.role == User.Role.team_leader,
            "settings": settings,
            "can_time_in": can_time_in,
            "can_time_out": can_time_out,
            "can_start_break": can_start_break,
            "can_end_break": can_end_break,
            "active_break": active_break,
            "break_window_open": break_window_open,
            "break_window_message": break_window_message,
            "break_end_at": break_end_at,
            "hours_so_far": hours_so_far,
            "expected_time_out": expected_time_out,
            "time_in_opens_at": opens_at,
            "today_rows": today_rows,
            "attendance_location_status": request.session.pop(
                "attendance_location_status",
                None,
            ),
            "is_employee": request.user.role in STAFF_ROLES,
            "can_use_attendance": request.user.role in ATTENDANCE_ROLES,
        },
    )


def _record_status(clock_in_at, settings):
    if clock_in_at is None:
        return AttendanceRecord.Status.absent, None
    local_clock_in = manila_localtime(clock_in_at)
    cutoff = datetime.combine(local_clock_in.date(), settings.attendance_cutoff_time)
    cutoff = timezone.make_aware(cutoff, MANILA)
    grace = settings.attendance_grace_minutes
    minutes_late = max(0, int((local_clock_in - cutoff).total_seconds() // 60) - grace)
    if local_clock_in.time().replace(tzinfo=None) <= settings.attendance_cutoff_time:
        return AttendanceRecord.Status.present, 0
    return AttendanceRecord.Status.late, minutes_late


@login_required
@require_POST
def attendance_time_in(request):
    if request.user.role not in ATTENDANCE_ROLES:
        return HttpResponse(status=403)
    employee = _employee_for_attendance(request.user)
    server_now = timezone.now()
    local_now = manila_localtime(server_now)
    today = local_now.date()
    settings = _settings()
    if local_now.time().replace(tzinfo=None) < settings.attendance_time_in_open_time:
        opening_time = settings.attendance_time_in_open_time.strftime("%I:%M %p").lstrip("0")
        messages.error(request, f"Time In opens at {opening_time}")
        return redirect("attendance-list")
    with transaction.atomic():
        existing = _attendance_record_for_user(request.user, today, lock=True)
        if existing and existing.clock_in_at is not None:
            return HttpResponse(status=409)
        if existing is None:
            record = AttendanceRecord(
                employee=employee,
                user=request.user,
                work_date=today,
            )
        else:
            record = existing
            record.user = request.user
        try:
            outside = apply_attendance_location(
                record,
                request.user,
                request.POST,
                require_reason=True,
            )
        except OutsidePremisesBlocked as error:
            create_outside_attendance_notifications(
                request.user,
                error.record,
                attempt_at=server_now,
                blocked=True,
            )
            request.session["attendance_location_status"] = {
                "is_outside_premises": True,
                "distance_from_office": error.record.distance_from_office,
                "reason": error.record.outside_premises_reason,
            }
            return _attendance_location_error(request, error)
        except ValidationError as error:
            return _attendance_location_error(request, error)
        record.clock_in_at = server_now
        record.status, record.minutes_late = _record_status(record.clock_in_at, settings)
        record.time_in_status = (
            "late" if record.status == AttendanceRecord.Status.late else "on_time"
        )
        record.time_out_status = ""
        record.save()
        if employee is not None:
            update_employee_attendance_rate(employee, settings, today.replace(day=1))
        if outside:
            create_outside_attendance_notifications(request.user, record)
        messages.success(request, "Time In recorded from the server clock.")
    return redirect("attendance-list")


@login_required
@require_POST
def attendance_break_start(request):
    if request.user.role not in (User.Role.staff, User.Role.employee):
        return redirect("attendance-list")
    employee = get_object_or_404(employee_queryset_for(request.user), user=request.user)
    server_now = timezone.now()
    auto_end_overdue_breaks(now=server_now, employee_user=request.user)
    local_now = manila_localtime(server_now)
    today = local_now.date()
    settings = _settings()
    current_time = local_now.time().replace(tzinfo=None)
    if not settings.attendance_break_start_time <= current_time < settings.attendance_break_end_time:
        messages.error(request, "Break is only available from 12:00 PM to 1:00 PM.")
        return redirect("attendance-list")

    with transaction.atomic():
        record = AttendanceRecord.objects.select_for_update().filter(
            employee=employee,
            work_date=today,
        ).first()
        if record is None or record.clock_in_at is None:
            messages.error(request, "Time In before starting a break.")
            return redirect("attendance-list")
        if record.clock_out_at is not None:
            messages.error(request, "A break cannot be started after Time Out.")
            return redirect("attendance-list")
        if AttendanceBreak.objects.filter(attendance=record, ended_at__isnull=True).exists():
            messages.error(request, "You already have an active break.")
            return redirect("attendance-list")
        AttendanceBreak.objects.create(
            attendance=record,
            started_at=server_now,
            auto_end_at=break_auto_end_at(today, settings.attendance_break_end_time),
        )
    messages.success(request, "Break started.")
    return redirect("attendance-list")


@login_required
@require_POST
def attendance_break_end(request):
    if request.user.role not in (User.Role.staff, User.Role.employee):
        return redirect("attendance-list")
    employee = get_object_or_404(employee_queryset_for(request.user), user=request.user)
    server_now = timezone.now()
    auto_end_overdue_breaks(now=server_now, employee_user=request.user)
    today = manila_localtime(server_now).date()

    with transaction.atomic():
        record = AttendanceRecord.objects.select_for_update().filter(
            employee=employee,
            work_date=today,
        ).first()
        if record is None:
            messages.error(request, "There is no active break to end.")
            return redirect("attendance-list")
        attendance_break = (
            AttendanceBreak.objects.select_for_update()
            .filter(attendance=record, ended_at__isnull=True)
            .first()
        )
        if attendance_break is None:
            messages.error(request, "There is no active break to end.")
            return redirect("attendance-list")
        attendance_break.ended_at = server_now
        attendance_break.save(update_fields=("ended_at",))
    messages.success(request, "Break ended.")
    return redirect("attendance-list")


@login_required
@require_POST
def attendance_time_out(request):
    if request.user.role not in ATTENDANCE_ROLES:
        return HttpResponse(status=403)
    employee = _employee_for_attendance(request.user)
    server_now = timezone.now()
    auto_end_overdue_breaks(now=server_now, employee_user=request.user)
    today = manila_localtime(server_now).date()
    settings = _settings()
    with transaction.atomic():
        record = _attendance_record_for_user(request.user, today, lock=True)
        if record is None or record.clock_out_at is not None:
            return redirect("attendance-list")
        try:
            apply_attendance_location(
                record,
                request.user,
                request.POST,
                require_reason=False,
            )
        except OutsidePremisesBlocked as error:
            create_outside_attendance_notifications(
                request.user,
                error.record,
                attempt_at=server_now,
                blocked=True,
            )
            request.session["attendance_location_status"] = {
                "is_outside_premises": True,
                "distance_from_office": error.record.distance_from_office,
                "reason": error.record.outside_premises_reason,
            }
            return _attendance_location_error(request, error)
        except ValidationError as error:
            return _attendance_location_error(request, error)
        end_active_break_for_attendance(record, server_now)
        record.clock_out_at = server_now
        if server_now > record.clock_in_at:
            record.hours = round(net_attendance_seconds(record, server_now) / 3600, 2)
        local_clock_out = manila_localtime(server_now)
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
        record.user = request.user
        record.save()
        if employee is not None:
            update_employee_attendance_rate(employee, settings, today.replace(day=1))
    messages.success(request, "Time Out recorded from the server clock.")
    return redirect("attendance-list")


@login_required
def attendance_correct(request, pk):
    record = get_object_or_404(AttendanceRecord, pk=pk)
    if request.user.role != User.Role.team_leader and not _can_manage_attendance(request.user):
        raise PermissionDenied
    if (
        record.employee_id is None
        or request.user.role != User.Role.admin
        and not get_managed_users(request.user).filter(pk=record.account.pk).exists()
    ):
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

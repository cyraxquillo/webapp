import calendar
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.utils import timezone

from .models import AttendanceRecord, Submission

DEFAULT_WEIGHTS = {
    "attendance": 20,
    "work": 30,
    "team_leader": 30,
    "management": 20,
}


def _round(value):
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def working_days_in_month(settings, month, date_hired=None):
    first_day = month.replace(day=1)
    last_day = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    if date_hired and date_hired > first_day:
        first_day = date_hired
    weekdays = {
        int(value.strip())
        for value in settings.attendance_working_days.split(",")
        if value.strip().isdigit() and 1 <= int(value.strip()) <= 7
    }
    holidays = {
        date.fromisoformat(value.strip())
        for value in settings.attendance_holidays.split(",")
        if value.strip()
    }
    count = 0
    current_date = first_day
    while current_date <= last_day:
        if current_date.isoweekday() in weekdays and current_date not in holidays:
            count += 1
        current_date += timedelta(days=1)
    return count


def attendance_points_for_record(record, settings):
    if record.status == AttendanceRecord.Status.excused:
        return _round(settings.attendance_excused_points)
    if record.status == AttendanceRecord.Status.absent:
        return _round(settings.attendance_absent_points)

    time_in_status = record.time_in_status
    if not time_in_status:
        time_in_status = (
            "late"
            if record.status == AttendanceRecord.Status.late
            else "on_time"
        )
    time_in_points = (
        settings.attendance_late_points
        if time_in_status == "late"
        else settings.attendance_on_time_points
    )

    time_out_status = record.time_out_status
    if not time_out_status and record.status == AttendanceRecord.Status.no_time_out:
        time_out_status = AttendanceRecord.TimeOutStatus.no_time_out
    if not time_out_status and record.status == AttendanceRecord.Status.early_out:
        time_out_status = AttendanceRecord.TimeOutStatus.early_out
    if not time_out_status and record.clock_out_at:
        local_clock_out = timezone.localtime(record.clock_out_at)
        time_out_status = (
            AttendanceRecord.TimeOutStatus.early_out
            if local_clock_out.time().replace(tzinfo=None) < settings.attendance_expected_time_out
            else AttendanceRecord.TimeOutStatus.on_time
        )

    time_out_points = 0.0
    if time_out_status in (
        AttendanceRecord.TimeOutStatus.on_time,
        AttendanceRecord.TimeOutStatus.excused,
    ):
        time_out_points = settings.attendance_time_out_points
    elif time_out_status == AttendanceRecord.TimeOutStatus.no_time_out:
        time_out_points = settings.attendance_no_time_out_points
    elif time_out_status == AttendanceRecord.TimeOutStatus.early_out:
        if record.clock_in_at and record.clock_out_at:
            local_clock_in = timezone.localtime(record.clock_in_at)
            expected_out = timezone.make_aware(
                datetime.combine(record.work_date, settings.attendance_expected_time_out)
            )
            expected_duration = (expected_out - record.clock_in_at).total_seconds()
            completed_duration = (record.clock_out_at - record.clock_in_at).total_seconds()
            fraction = (
                min(max(completed_duration / expected_duration, 0.0), 1.0)
                if expected_duration > 0 and local_clock_in.date() == record.work_date
                else 0.0
            )
            time_out_points = settings.attendance_time_out_points * fraction

    return _round(min(time_in_points + time_out_points, 1.0))


def calculate_attendance_rate(records, settings=None, working_days=None):
    if settings is None:
        on_time_points = 0.5
        late_points = 0.25
        timeout_points = 0.5
        no_timeout_points = 0.0
        excused_points = 1.0
        absent_points = 0.0
        settings_for_points = None
    else:
        on_time_points = settings.attendance_on_time_points
        late_points = settings.attendance_late_points
        timeout_points = settings.attendance_time_out_points
        no_timeout_points = settings.attendance_no_time_out_points
        excused_points = settings.attendance_excused_points
        absent_points = settings.attendance_absent_points
        settings_for_points = settings

    effective_working_days = len(records) if working_days is None else working_days
    if effective_working_days == 0:
        return {"earned_points": 0.0, "working_days": 0, "attendance_rate": None}

    if settings_for_points is None:
        earned_points = 0.0
        for record in records:
            if record.status == AttendanceRecord.Status.excused:
                earned_points += excused_points
            elif record.status == AttendanceRecord.Status.absent:
                earned_points += absent_points
            else:
                earned_points += (
                    late_points
                    if record.status == AttendanceRecord.Status.late
                    else on_time_points
                )
                if record.clock_out_at and record.status not in (
                    AttendanceRecord.Status.early_out,
                    AttendanceRecord.Status.no_time_out,
                ):
                    earned_points += timeout_points
                elif record.status == AttendanceRecord.Status.early_out:
                    earned_points += no_timeout_points
                elif record.status == AttendanceRecord.Status.no_time_out:
                    earned_points += no_timeout_points
    else:
        earned_points = sum(attendance_points_for_record(record, settings) for record in records)

    earned_points = min(earned_points, effective_working_days)
    attendance_rate = _round((earned_points / effective_working_days) * 100)
    return {
        "earned_points": _round(earned_points),
        "working_days": effective_working_days,
        "attendance_rate": attendance_rate,
    }


def update_employee_attendance_rate(employee, settings, month=None):
    month = month or timezone.localdate().replace(day=1)
    records = list(
        AttendanceRecord.objects.filter(
            employee=employee,
            work_date__year=month.year,
            work_date__month=month.month,
        )
    )
    denominator = working_days_in_month(settings, month, employee.date_hired)
    result = calculate_attendance_rate(records, settings, denominator)
    employee.attendance_rate = result["attendance_rate"]
    employee.save(update_fields=("attendance_rate",))
    return result


def calculate_work_score(submissions, monthly_target=2):
    scored_submissions = [
        submission
        for submission in submissions
        if submission.status == Submission.Status.reviewed
    ]
    earned_points = sum(1.0 if not submission.is_late else 0.5 for submission in scored_submissions)
    target_points = max(float(monthly_target), 1.0)
    work_score = _round(min(100.0, (earned_points / target_points) * 100))
    return {
        "earned_points": _round(earned_points),
        "target_points": _round(target_points),
        "work_score": work_score,
    }


def calculate_hybrid_score(
    attendance_rate,
    work_score,
    team_leader_rating,
    management_rating,
    weights=None,
):
    weights = {**DEFAULT_WEIGHTS, **(weights or {})}
    effective_weights = {
        category: float(weights.get(category, 0))
        for category in DEFAULT_WEIGHTS
    }
    scores = {
        "attendance": attendance_rate,
        "work": work_score,
        "team_leader": team_leader_rating,
        "management": management_rating,
    }
    # Missing manual ratings remain missing. Their weights are removed from the
    # denominator for the final score rather than being converted to zero.
    available_categories = [
        category
        for category in DEFAULT_WEIGHTS
        if scores[category] is not None
    ]
    denominator = sum(effective_weights[category] for category in available_categories)
    if denominator == 0:
        overall_score = None
    else:
        overall_score = _round(
            sum(
                scores[category] * effective_weights[category]
                for category in available_categories
            )
            / denominator
        )
    return {
        "overall_score": overall_score,
        "management_rating": management_rating,
        "management_weight": effective_weights["management"],
        "effective_weights": effective_weights,
    }

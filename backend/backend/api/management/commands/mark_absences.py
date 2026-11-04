from datetime import date, datetime, time, timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from api.models import AttendanceRecord, AutomationSettings, Employee
from api.hybrid import update_employee_attendance_rate


class Command(BaseCommand):
    help = "Close attendance days, mark missing Time Outs, and mark absences."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=date.fromisoformat,
            default=None,
            help="Process a specific date in YYYY-MM-DD format.",
        )
        parser.add_argument(
            "--all-days",
            action="store_true",
            help="Process the configured date range from the beginning of the current month.",
        )

    def handle(self, *args, **options):
        settings = AutomationSettings.objects.get_or_create_default()

        local_now = timezone.localtime(timezone.now())
        target_date = options["date"] or (local_now.date() - timedelta(days=1))
        if not isinstance(target_date, date):
            target_date = date.fromisoformat(target_date)
        if options["all_days"]:
            first_day = target_date.replace(day=1)
            last_day = (first_day.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
            working_dates = [
                first_day + timedelta(days=offset)
                for offset in range((last_day - first_day).days + 1)
            ]
        else:
            working_dates = [target_date]

        working_weekdays = {
            int(value.strip())
            for value in settings.attendance_working_days.split(",")
            if value.strip().isdigit()
        }
        holidays = {
            date.fromisoformat(value.strip())
            for value in settings.attendance_holidays.split(",")
            if value.strip()
        }
        dates = [
            current_date
            for current_date in working_dates
            if current_date.weekday() + 1 in working_weekdays
            and current_date not in holidays
        ]

        absences_marked = 0
        days_closed = 0
        for current_date in dates:
            if current_date == local_now.date() and local_now.time().replace(tzinfo=None) < time(23, 59):
                continue
            employees = Employee.objects.filter(
                date_hired__lte=current_date,
                status=Employee.Status.active,
            )
            if not employees.exists():
                continue
            with transaction.atomic():
                for employee in employees:
                    record = AttendanceRecord.objects.select_for_update().filter(
                        employee=employee,
                        work_date=current_date,
                    ).first()
                    if record is None:
                        record = AttendanceRecord.objects.create(
                            employee=employee,
                            work_date=current_date,
                            status=AttendanceRecord.Status.absent,
                            notes="Marked absent by the attendance automation command.",
                        )
                        absences_marked += 1
                    elif record.clock_in_at and not record.clock_out_at:
                        record.clock_out_at = timezone.make_aware(
                            datetime.combine(current_date, time(23, 59))
                        )
                        record.status = AttendanceRecord.Status.no_time_out
                        record.time_out_status = AttendanceRecord.TimeOutStatus.no_time_out
                        record.hours = None
                        record.notes = "Automatically closed at 11:59 PM; No Time Out."
                        record.save(
                            update_fields=("clock_out_at", "status", "time_out_status", "hours", "notes")
                        )
                        days_closed += 1
                    elif record.clock_in_at is None and record.status not in (
                        AttendanceRecord.Status.absent,
                        AttendanceRecord.Status.excused,
                    ):
                        record.status = AttendanceRecord.Status.absent
                        record.notes = "Marked absent by the attendance automation command."
                        record.save(update_fields=("status", "notes"))
                        absences_marked += 1
                    update_employee_attendance_rate(employee, settings, current_date.replace(day=1))

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {len(dates)} working date(s); marked {absences_marked} absent and closed {days_closed} open day(s)."
            )
        )

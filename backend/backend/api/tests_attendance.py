from datetime import date, datetime, time, timedelta
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.models import AttendanceRecord, AutomationSettings, Department, Employee, User


class AttendanceTimeInTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(name="Attendance Department")
        cls.employee_user = User.objects.create_user(
            email="employee@attendance.test",
            password="test",
            name="Employee User",
            role=User.Role.employee,
        )
        cls.employee = Employee.objects.create(
            employee_code="ATT-001",
            user=cls.employee_user,
            name="Test Employee",
            department=cls.department,
            position="Analyst",
            date_hired=date(2026, 1, 1),
        )
        cls.team_leader = User.objects.create_user(
            email="leader@attendance.test",
            password="test",
            name="Team Leader",
            role=User.Role.team_leader,
        )
        cls.employee.team_leader = cls.team_leader
        cls.employee.save(update_fields=("team_leader",))
        cls.management = User.objects.create_user(
            email="management@attendance.test",
            password="test",
            name="Management",
            role=User.Role.management,
        )
        cls.settings = AutomationSettings.objects.get_or_create_default()
        cls.settings.attendance_time_in_open_time = time(6, 0)
        cls.settings.attendance_cutoff_time = time(8, 0)
        cls.settings.attendance_expected_time_out = time(19, 0)
        cls.settings.attendance_grace_minutes = 0
        cls.settings.attendance_on_time_points = 0.5
        cls.settings.attendance_late_points = 0.25
        cls.settings.attendance_time_out_points = 0.5
        cls.settings.attendance_no_time_out_points = 0.0
        cls.settings.attendance_excused_points = 1.0
        cls.settings.attendance_absent_points = 0.0
        cls.settings.attendance_working_days = "1,2,3,4,5"
        cls.settings.attendance_holidays = ""
        cls.settings.save()

    def test_time_in_uses_server_time_and_prevents_second_time_in(self):
        server_now = timezone.make_aware(datetime(2026, 10, 8, 8, 30))
        self.client.force_login(self.employee_user)

        with patch("api.views_hybrid.timezone.now", return_value=server_now):
            response = self.client.post(reverse("attendance-time-in"))

        self.assertEqual(response.status_code, 302)
        record = AttendanceRecord.objects.get(employee=self.employee)
        self.assertEqual(record.work_date, date(2026, 10, 8))
        local_clock_in = timezone.localtime(record.clock_in_at)
        self.assertEqual(local_clock_in.hour, 8)
        self.assertEqual(local_clock_in.minute, 30)
        self.assertEqual(record.status, AttendanceRecord.Status.late)
        self.assertEqual(record.minutes_late, 30)
        self.assertEqual(record.time_in_status, "late")

        with patch("api.views_hybrid.timezone.now", return_value=server_now + timedelta(minutes=5)):
            second_response = self.client.post(reverse("attendance-time-in"))

        self.assertEqual(second_response.status_code, 409)
        self.assertEqual(AttendanceRecord.objects.filter(employee=self.employee).count(), 1)

    def test_time_in_is_blocked_before_six_and_available_at_six(self):
        self.client.force_login(self.employee_user)
        before_open = timezone.make_aware(datetime(2026, 10, 8, 5, 59))
        with patch("api.views_hybrid.timezone.now", return_value=before_open):
            page = self.client.get(reverse("attendance-list"))
            response = self.client.post(reverse("attendance-time-in"))
        self.assertContains(page, "Time In opens at 6:00 AM")
        self.assertContains(page, 'type="submit" disabled', html=False)
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.employee).exists())

        at_open = timezone.make_aware(datetime(2026, 10, 8, 6, 0))
        with patch("api.views_hybrid.timezone.now", return_value=at_open):
            response = self.client.post(reverse("attendance-time-in"))
        self.assertEqual(response.status_code, 302)
        record = AttendanceRecord.objects.get(employee=self.employee)
        self.assertEqual(timezone.localtime(record.clock_in_at).time(), time(6, 0))
        self.assertEqual(record.status, AttendanceRecord.Status.present)
        self.employee.refresh_from_db()
        self.assertIsNotNone(self.employee.attendance_rate)

    def test_time_out_calculates_hours_from_server_timestamps(self):
        server_now = timezone.make_aware(datetime(2026, 10, 8, 8, 0))
        self.client.force_login(self.employee_user)
        with patch("api.views_hybrid.timezone.now", return_value=server_now):
            self.client.post(reverse("attendance-time-in"))
        later = server_now + timedelta(hours=2, minutes=15)
        with patch("api.views_hybrid.timezone.now", return_value=later):
            response = self.client.post(reverse("attendance-time-out"))

        record = AttendanceRecord.objects.get(employee=self.employee)
        self.assertEqual(response.status_code, 302)
        local_clock_out = timezone.localtime(record.clock_out_at)
        self.assertEqual(local_clock_out.hour, 10)
        self.assertEqual(local_clock_out.minute, 15)
        self.assertEqual(record.hours, 2.25)
        self.assertEqual(record.status, AttendanceRecord.Status.early_out)
        self.assertEqual(record.time_out_status, AttendanceRecord.TimeOutStatus.early_out)

    def test_time_out_at_or_after_seven_gets_full_points_and_overtime_is_capped(self):
        from api.hybrid import attendance_points_for_record

        self.client.force_login(self.employee_user)
        time_in = timezone.make_aware(datetime(2026, 10, 8, 8, 0))
        with patch("api.views_hybrid.timezone.now", return_value=time_in):
            self.client.post(reverse("attendance-time-in"))
        time_out = timezone.make_aware(datetime(2026, 10, 8, 19, 15))
        with patch("api.views_hybrid.timezone.now", return_value=time_out):
            self.client.post(reverse("attendance-time-out"))

        record = AttendanceRecord.objects.get(employee=self.employee)
        self.assertEqual(record.time_out_status, AttendanceRecord.TimeOutStatus.on_time)
        self.assertEqual(record.hours, 11.25)
        self.assertEqual(attendance_points_for_record(record, self.settings), 1.0)

    def test_early_out_points_are_proportional_to_expected_hours(self):
        from api.hybrid import attendance_points_for_record

        self.client.force_login(self.employee_user)
        time_in = timezone.make_aware(datetime(2026, 10, 8, 8, 0))
        with patch("api.views_hybrid.timezone.now", return_value=time_in):
            self.client.post(reverse("attendance-time-in"))
        time_out = timezone.make_aware(datetime(2026, 10, 8, 16, 0))
        with patch("api.views_hybrid.timezone.now", return_value=time_out):
            self.client.post(reverse("attendance-time-out"))
        record = AttendanceRecord.objects.get(employee=self.employee)
        self.assertEqual(record.hours, 8.0)
        self.assertEqual(attendance_points_for_record(record, self.settings), 0.86)

    def test_automation_closes_open_days_as_no_time_out(self):
        from api.hybrid import attendance_points_for_record

        record = AttendanceRecord.objects.create(
            employee=self.employee,
            work_date=date(2026, 10, 8),
            status=AttendanceRecord.Status.present,
            time_in_status="on_time",
            clock_in_at=timezone.make_aware(datetime(2026, 10, 8, 8, 0)),
        )
        call_command("mark_absences", date="2026-10-08", verbosity=0)
        record.refresh_from_db()
        closed_at = timezone.localtime(record.clock_out_at)
        self.assertEqual(closed_at.date(), date(2026, 10, 8))
        self.assertEqual(closed_at.time(), time(23, 59))
        self.assertEqual(record.status, AttendanceRecord.Status.no_time_out)
        self.assertEqual(record.time_out_status, AttendanceRecord.TimeOutStatus.no_time_out)
        self.assertIsNone(record.hours)
        self.assertEqual(attendance_points_for_record(record, self.settings), 0.5)

    def test_attendance_page_renders_live_clock_and_server_time_controls(self):
        self.client.force_login(self.employee_user)
        response = self.client.get(reverse("attendance-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "id=\"attendance-clock\"")
        self.assertContains(response, "Time In")
        self.assertContains(response, "Time Out")

    def test_team_leader_attendance_page_exposes_role_gated_correction(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee,
            work_date=date(2026, 10, 8),
            status=AttendanceRecord.Status.present,
        )
        self.client.force_login(self.team_leader)
        response = self.client.get(reverse("attendance-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("attendance-correct", args=[record.pk]))
        self.assertNotContains(response, reverse("attendance-time-in"))
        self.assertNotContains(response, reverse("attendance-time-out"))

    def test_cutoff_and_grace_are_configurable(self):
        self.settings.attendance_cutoff_time = time(7, 0)
        self.settings.attendance_grace_minutes = 15
        self.settings.save(update_fields=("attendance_cutoff_time", "attendance_grace_minutes"))
        server_now = timezone.make_aware(datetime(2026, 10, 8, 7, 10))
        self.client.force_login(self.employee_user)

        with patch("api.views_hybrid.timezone.now", return_value=server_now):
            self.client.post(reverse("attendance-time-in"))

        record = AttendanceRecord.objects.get(employee=self.employee)
        self.assertEqual(record.status, AttendanceRecord.Status.late)
        self.assertEqual(record.minutes_late, 0)

    def test_mark_absences_skips_weekends_holidays_and_unhired_employees(self):
        call_command("mark_absences", date="2026-10-08", verbosity=0)
        absent_record = AttendanceRecord.objects.get(employee=self.employee, work_date=date(2026, 10, 8))
        self.assertEqual(absent_record.status, AttendanceRecord.Status.absent)

        self.settings.attendance_holidays = "2026-10-12"
        self.settings.save(update_fields=("attendance_holidays",))
        call_command("mark_absences", date="2026-10-12", verbosity=0)
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.employee, work_date=date(2026, 10, 12)).exists())

        self.employee.date_hired = date(2026, 10, 12)
        self.employee.save(update_fields=("date_hired",))
        call_command("mark_absences", date="2026-10-12", verbosity=0)
        self.assertFalse(AttendanceRecord.objects.filter(employee=self.employee, work_date=date(2026, 10, 12)).exists())

    def test_only_authorized_roles_can_correct_and_audit_changes(self):
        record = AttendanceRecord.objects.create(
            employee=self.employee,
            work_date=date(2026, 10, 8),
            status=AttendanceRecord.Status.present,
            clock_in_at=timezone.make_aware(datetime(2026, 10, 8, 8, 0)),
        )
        self.client.force_login(self.employee_user)
        response = self.client.post(
            reverse("attendance-correct", args=[record.pk]),
            {"status": AttendanceRecord.Status.excused, "reason": "Approved leave"},
        )
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.team_leader)
        response = self.client.post(
            reverse("attendance-correct", args=[record.pk]),
            {"status": AttendanceRecord.Status.excused, "reason": "Approved leave"},
        )
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        self.assertEqual(record.status, AttendanceRecord.Status.excused)
        self.assertEqual(record.correction_reason, "Approved leave")
        self.assertEqual(record.corrected_by, self.team_leader)
        self.assertIsNotNone(record.corrected_at)
        self.assertEqual(record.corrections.count(), 1)

    def test_team_leader_can_excuse_early_out_with_reason_and_audit_times(self):
        from api.hybrid import attendance_points_for_record

        clock_in = timezone.make_aware(datetime(2026, 10, 8, 8, 0))
        clock_out = timezone.make_aware(datetime(2026, 10, 8, 16, 0))
        record = AttendanceRecord.objects.create(
            employee=self.employee,
            work_date=date(2026, 10, 8),
            status=AttendanceRecord.Status.early_out,
            time_in_status="on_time",
            time_out_status=AttendanceRecord.TimeOutStatus.early_out,
            clock_in_at=clock_in,
            clock_out_at=clock_out,
            hours=8,
        )
        self.client.force_login(self.team_leader)
        response = self.client.post(
            reverse("attendance-correct", args=[record.pk]),
            {"status": AttendanceRecord.Status.early_out, "excuse_time_out": "on", "reason": "Approved appointment"},
        )
        self.assertEqual(response.status_code, 302)
        record.refresh_from_db()
        correction = record.corrections.get()
        self.assertEqual(record.time_out_status, AttendanceRecord.TimeOutStatus.excused)
        self.assertEqual(record.attendance_status, "Excused")
        self.assertEqual(correction.reason, "Approved appointment")
        self.assertEqual(correction.old_clock_out_at, clock_out)
        self.assertEqual(attendance_points_for_record(record, self.settings), 1.0)

    def test_team_leader_cannot_correct_another_team(self):
        other_leader = User.objects.create_user(
            email="other-leader@attendance.test",
            password="test",
            name="Other Team Leader",
            role=User.Role.team_leader,
        )
        self.employee.team_leader = other_leader
        self.employee.save(update_fields=("team_leader",))
        record = AttendanceRecord.objects.create(employee=self.employee, work_date=date(2026, 10, 8))
        self.client.force_login(self.team_leader)
        response = self.client.post(
            reverse("attendance-correct", args=[record.pk]),
            {"status": AttendanceRecord.Status.excused, "reason": "Not my team"},
        )
        self.assertEqual(response.status_code, 403)

    def test_automation_settings_are_management_only(self):
        self.client.force_login(self.employee_user)
        self.assertEqual(self.client.get(reverse("automation-settings")).status_code, 403)
        self.client.force_login(self.management)
        response = self.client.get(reverse("automation-settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "06:00")
        self.assertContains(response, "19:00")

    def test_attendance_points_are_configurable(self):
        self.assertEqual(self.settings.attendance_time_in_open_time, time(6, 0))
        self.assertEqual(self.settings.attendance_expected_time_out, time(19, 0))
        self.assertEqual(self.settings.attendance_on_time_points, 0.5)
        self.assertEqual(self.settings.attendance_late_points, 0.25)
        self.assertEqual(self.settings.attendance_time_out_points, 0.5)
        self.assertEqual(self.settings.attendance_no_time_out_points, 0.0)
        self.assertEqual(self.settings.attendance_excused_points, 1.0)
        self.assertEqual(self.settings.attendance_absent_points, 0.0)

    def test_default_automation_settings_are_recreated_without_overwriting_existing_data(self):
        existing = AutomationSettings.objects.get(pk=1)
        existing.attendance_cutoff_time = time(9, 0)
        existing.save(update_fields=("attendance_cutoff_time",))

        settings = AutomationSettings.objects.get_or_create_default()
        self.assertEqual(settings.pk, 1)
        self.assertEqual(settings.attendance_cutoff_time, time(9, 0))

        AutomationSettings.objects.filter(pk=1).delete()
        recreated = AutomationSettings.objects.get_or_create_default()
        self.assertEqual(recreated.pk, 1)
        self.assertEqual(recreated.attendance_cutoff_time, time(8, 0))
        self.assertEqual(recreated.attendance_time_in_open_time, time(6, 0))
        self.assertEqual(recreated.attendance_expected_time_out, time(19, 0))

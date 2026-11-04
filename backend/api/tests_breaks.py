from datetime import date, datetime, time
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from api.breaks import auto_end_overdue_breaks
from api.forms import AutomationSettingsForm
from api.models import (
    AttendanceBreak,
    AttendanceRecord,
    AutomationSettings,
    Department,
    Employee,
    Notification,
    User,
)


LOCATION_DATA = {"latitude": "0", "longitude": "0", "accuracy": "5"}


class AttendanceBreakTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(name="Break Department")
        cls.employee_user = User.objects.create_user(
            email="employee@break.test",
            password="Strong-test-password-123!",
            name="Break Employee",
            role=User.Role.staff,
        )
        cls.team_leader = User.objects.create_user(
            email="leader@break.test",
            password="Strong-test-password-123!",
            name="Employee Team Leader",
            role=User.Role.team_leader,
        )
        cls.employee = Employee.objects.create(
            employee_code="BRK-001",
            user=cls.employee_user,
            team_leader=cls.team_leader,
            name="Break Employee",
            department=cls.department,
            position="Analyst",
            date_hired=date(2026, 1, 1),
        )
        cls.management = User.objects.create_user(
            email="management@break.test",
            password="Strong-test-password-123!",
            name="Management",
            role=User.Role.management,
        )
        cls.settings = AutomationSettings.objects.get_or_create_default()
        cls.settings.attendance_break_start_time = time(12, 0)
        cls.settings.attendance_break_end_time = time(13, 0)
        cls.settings.save(update_fields=("attendance_break_start_time", "attendance_break_end_time"))

    def _local_time(self, hour, minute=0):
        return timezone.make_aware(datetime(2026, 10, 8, hour, minute))

    def _create_attendance(self, clock_in_at=None, clock_out_at=None):
        return AttendanceRecord.objects.create(
            employee=self.employee,
            work_date=date(2026, 10, 8),
            status=AttendanceRecord.Status.present,
            time_in_status="on_time",
            clock_in_at=clock_in_at or self._local_time(8),
            clock_out_at=clock_out_at,
        )

    def test_break_is_rejected_outside_window_before_time_in_and_after_time_out(self):
        self.client.force_login(self.employee_user)
        record = self._create_attendance()

        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(11, 59)):
            page = self.client.get(reverse("attendance-list"))
            self.assertContains(page, "Break is only available from 12:00 PM to 1:00 PM.")
            self.assertContains(page, "Start Break")
            self.assertContains(page, 'type="submit" disabled', html=False)
            response = self.client.post(reverse("attendance-break-start"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceBreak.objects.filter(attendance=record).exists())

        record.delete()
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 15)):
            response = self.client.post(reverse("attendance-break-start"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceBreak.objects.exists())

        record = self._create_attendance(clock_out_at=self._local_time(12, 10))
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 20)):
            response = self.client.post(reverse("attendance-break-start"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(AttendanceBreak.objects.filter(attendance=record).exists())

    def test_break_can_start_at_window_open_only_after_time_in(self):
        record = self._create_attendance()
        self.client.force_login(self.employee_user)
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12)):
            response = self.client.post(reverse("attendance-break-start"))

        self.assertEqual(response.status_code, 302)
        attendance_break = AttendanceBreak.objects.get(attendance=record)
        self.assertEqual(attendance_break.started_at, self._local_time(12))
        self.assertEqual(attendance_break.auto_end_at, self._local_time(13))

    def test_only_one_active_break_is_allowed_and_finished_breaks_can_repeat(self):
        record = self._create_attendance()
        self.client.force_login(self.employee_user)
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12)):
            self.client.post(reverse("attendance-break-start"))
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 15)):
            response = self.client.post(reverse("attendance-break-start"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(AttendanceBreak.objects.filter(attendance=record, ended_at__isnull=True).count(), 1)
        self.assertEqual(AttendanceBreak.objects.filter(attendance=record).count(), 1)

        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 30)):
            self.client.post(reverse("attendance-break-end"))
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 35)):
            self.client.post(reverse("attendance-break-start"))
        self.assertEqual(AttendanceBreak.objects.filter(attendance=record).count(), 2)
        self.assertEqual(AttendanceBreak.objects.filter(attendance=record, ended_at__isnull=True).count(), 1)

    def test_break_duration_is_deducted_from_hours_and_monthly_table(self):
        record = self._create_attendance()
        self.client.force_login(self.employee_user)
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12)):
            self.client.post(reverse("attendance-break-start"))
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 30)):
            self.client.post(reverse("attendance-break-end"))
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(18)):
            self.client.post(reverse("attendance-time-out"), data=LOCATION_DATA)

        record.refresh_from_db()
        self.assertEqual(record.hours, 9.5)
        with patch(
            "api.views_hybrid.timezone.now",
            return_value=self._local_time(18),
        ):
            page = self.client.get(reverse("attendance-list"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "30 min")
        self.assertContains(page, "9.50")

    def test_live_hours_subtract_active_break_time(self):
        record = self._create_attendance()
        AttendanceBreak.objects.create(
            attendance=record,
            started_at=self._local_time(12),
            auto_end_at=self._local_time(13),
        )
        self.client.force_login(self.employee_user)
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 30)):
            page = self.client.get(reverse("attendance-list"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "4.00</span> hours so far")
        self.assertContains(page, "On break since 12:00 PM")
        self.assertContains(page, "Your break ends automatically at 1:00 PM.")
        self.assertContains(page, "End Break")

    def test_auto_end_uses_exact_end_time_and_notifies_each_intended_recipient_once(self):
        admin = User.objects.create_user(
            email="admin@break.test",
            password="Strong-test-password-123!",
            name="Admin",
            role=User.Role.admin,
        )
        another_manager = User.objects.create_user(
            email="manager-two@break.test",
            password="Strong-test-password-123!",
            name="Second Management",
            role=User.Role.management,
        )
        unrelated_leader = User.objects.create_user(
            email="other-leader@break.test",
            password="Strong-test-password-123!",
            name="Other Team Leader",
            role=User.Role.team_leader,
        )
        inactive_manager = User.objects.create_user(
            email="inactive-manager@break.test",
            password="Strong-test-password-123!",
            name="Inactive Management",
            role=User.Role.management,
            is_active=False,
        )
        record = self._create_attendance()
        attendance_break = AttendanceBreak.objects.create(
            attendance=record,
            started_at=self._local_time(12, 30),
            auto_end_at=self._local_time(13),
        )

        self.assertEqual(auto_end_overdue_breaks(now=self._local_time(13)), 1)
        call_command("auto_end_breaks", verbosity=0)
        attendance_break.refresh_from_db()
        self.assertEqual(attendance_break.ended_at, self._local_time(13))
        self.assertTrue(attendance_break.auto_ended)

        expected_recipients = {
            self.employee_user.pk,
            self.team_leader.pk,
            self.management.pk,
            admin.pk,
            another_manager.pk,
        }
        notifications = Notification.objects.filter(
            dedupe_key__startswith=f"break-auto-end-{attendance_break.pk}-"
        )
        self.assertEqual(notifications.count(), len(expected_recipients))
        self.assertSetEqual(
            set(notifications.values_list("recipient_id", flat=True)),
            expected_recipients,
        )
        self.assertFalse(notifications.filter(recipient=unrelated_leader).exists())
        self.assertFalse(notifications.filter(recipient=inactive_manager).exists())
        self.assertEqual(
            notifications.get(recipient=self.employee_user).title,
            "Break not ended on time",
        )
        manager_notification = notifications.get(recipient=self.team_leader)
        self.assertEqual(manager_notification.title, "Employee did not end break on time")
        self.assertIn("Break Employee (BRK-001)", manager_notification.message)
        self.assertIn("1:00 PM", manager_notification.message)

    def test_break_ended_on_time_has_no_auto_end_notifications(self):
        record = self._create_attendance()
        attendance_break = AttendanceBreak.objects.create(
            attendance=record,
            started_at=self._local_time(12),
            auto_end_at=self._local_time(13),
        )
        self.client.force_login(self.employee_user)
        with patch("api.views_hybrid.timezone.now", return_value=self._local_time(12, 59)):
            response = self.client.post(reverse("attendance-break-end"))
        self.assertEqual(response.status_code, 302)

        self.assertEqual(auto_end_overdue_breaks(now=self._local_time(13)), 0)
        attendance_break.refresh_from_db()
        self.assertEqual(attendance_break.ended_at, self._local_time(12, 59))
        self.assertFalse(attendance_break.auto_ended)
        self.assertFalse(Notification.objects.exists())

    def test_employee_page_auto_ends_break_before_notification_count_is_rendered(self):
        record = self._create_attendance()
        AttendanceBreak.objects.create(
            attendance=record,
            started_at=self._local_time(12, 30),
            auto_end_at=self._local_time(13),
        )
        self.client.force_login(self.employee_user)

        response = self.client.get(reverse("notification-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Break not ended on time")
        self.assertContains(response, "1 unread")
        self.assertEqual(Notification.objects.filter(recipient=self.employee_user).count(), 1)

    def test_staff_can_open_notifications_page(self):
        self.client.force_login(self.employee_user)
        self.assertEqual(self.client.get(reverse("notification-list")).status_code, 200)

    def test_break_end_must_be_after_start_in_automation_settings(self):
        form = AutomationSettingsForm(instance=self.settings)
        data = {
            name: field.prepare_value(getattr(self.settings, name))
            for name, field in form.fields.items()
        }
        data["attendance_break_start_time"] = "12:00"
        data["attendance_break_end_time"] = "12:00"
        form = AutomationSettingsForm(data=data, instance=self.settings)

        self.assertFalse(form.is_valid())
        self.assertIn("attendance_break_end_time", form.errors)

import math
from datetime import datetime
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from api.attendance_locations import EARTH_RADIUS_METERS, haversine_distance_meters
from api.models import (
    AttendanceRecord,
    Department,
    Employee,
    Notification,
    OfficeLocation,
    User,
)


class AttendanceLocationApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(name="Location Test Department")
        cls.admin = User.objects.create_user(
            email="admin@location.test",
            password="test-password",
            name="Admin",
            role=User.Role.admin,
        )
        cls.admin_two = User.objects.create_user(
            email="admin-two@location.test",
            password="test-password",
            name="Second Admin",
            role=User.Role.admin,
        )
        cls.management = User.objects.create_user(
            email="management@location.test",
            password="test-password",
            name="Management",
            role=User.Role.management,
        )
        cls.hr_manager = User.objects.create_user(
            email="hr@location.test",
            password="test-password",
            name="HR Manager",
            role=User.Role.hr_manager,
        )
        cls.team_leader = User.objects.create_user(
            email="leader@location.test",
            password="test-password",
            name="Team Leader",
            role=User.Role.team_leader,
        )
        cls.other_team_leader = User.objects.create_user(
            email="other-leader@location.test",
            password="test-password",
            name="Other Team Leader",
            role=User.Role.team_leader,
        )
        cls.employee_user = User.objects.create_user(
            email="employee@location.test",
            password="test-password",
            name="Employee",
            role=User.Role.employee,
        )
        cls.other_employee_user = User.objects.create_user(
            email="other-employee@location.test",
            password="test-password",
            name="Other Employee",
            role=User.Role.staff,
        )
        cls.employee = Employee.objects.create(
            employee_code="LOC-001",
            user=cls.employee_user,
            team_leader=cls.team_leader,
            name="Employee",
            department=cls.department,
            position="Analyst",
            date_hired=datetime(2026, 1, 1).date(),
        )
        cls.other_employee = Employee.objects.create(
            employee_code="LOC-002",
            user=cls.other_employee_user,
            team_leader=cls.other_team_leader,
            name="Other Employee",
            department=cls.department,
            position="Analyst",
            date_hired=datetime(2026, 1, 1).date(),
        )
        cls.office = OfficeLocation.objects.create(
            name="Main Office",
            latitude=0,
            longitude=0,
            allowed_radius_meters=100,
            outside_time_in_policy=OfficeLocation.OutsideTimeInPolicy.block,
        )

    def setUp(self):
        self.client = APIClient()

    def _time_in(self, user, latitude=1, longitude=1, **extra):
        self.client.force_authenticate(user=user)
        now = timezone.make_aware(datetime(2026, 10, 8, 10, 0))
        with patch("api.attendance_api.timezone.now", return_value=now):
            return self.client.post(
                "/attendance/time-in",
                {
                    "latitude": latitude,
                    "longitude": longitude,
                    "accuracy": 5,
                    "outside_reason": "Traveling between sites",
                    **extra,
                },
                format="json",
            )

    def _create_record(self, user, employee=None):
        return AttendanceRecord.objects.create(
            user=user,
            employee=employee,
            work_date=datetime(2026, 10, 8).date(),
        )

    def _attempt_notifications(self, user):
        return Notification.objects.filter(
            dedupe_key__startswith=f"outside-attendance-attempt-{user.pk}-"
        )

    def test_outside_staff_attempt_notifies_only_own_team_leader_and_admins(self):
        response = self._time_in(self.employee_user)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.data["is_outside_premises"])
        self.assertFalse(AttendanceRecord.objects.filter(user=self.employee_user).exists())
        recipient_ids = set(self._attempt_notifications(self.employee_user).values_list("recipient_id", flat=True))
        self.assertSetEqual(
            recipient_ids,
            {
                self.team_leader.pk,
                self.admin.pk,
                self.admin_two.pk,
            },
        )
        notification = self.admin.notifications.get()
        self.assertIn("Employee", notification.message)
        self.assertIn("Team Leader", notification.message)
        self.assertIn("Coordinates: 1.000000, 1.000000", notification.message)
        self.assertIn("attempt was blocked", notification.message)

    def test_outside_team_leader_attempt_notifies_management_and_admins_only(self):
        response = self._time_in(self.team_leader)

        self.assertEqual(response.status_code, 400)
        recipients = set(self._attempt_notifications(self.team_leader).values_list("recipient_id", flat=True))
        self.assertSetEqual(
            recipients,
            {
                self.management.pk,
                self.hr_manager.pk,
                self.admin.pk,
                self.admin_two.pk,
            },
        )

    def test_outside_management_attempt_notifies_admins_only_admin_cannot_clock(self):
        self.assertEqual(self._time_in(self.management).status_code, 400)
        self.assertSetEqual(
            set(self._attempt_notifications(self.management).values_list("recipient_id", flat=True)),
            {self.admin.pk, self.admin_two.pk},
        )
        admin_response = self._time_in(self.admin)
        self.assertEqual(admin_response.status_code, 403)
        self.assertFalse(AttendanceRecord.objects.filter(user=self.admin).exists())

    def test_team_leader_sees_only_own_team_location_records(self):
        self._create_record(self.employee_user, self.employee)
        self._create_record(self.other_employee_user, self.other_employee)
        self.client.force_authenticate(user=self.team_leader)

        response = self.client.get("/attendance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["user_id"] for item in response.data], [self.employee_user.pk])
        self.assertNotIn(self.other_employee_user.name, str(response.data))

    def test_team_leader_is_not_notified_about_another_team_employee(self):
        response = self._time_in(self.other_employee_user)

        self.assertEqual(response.status_code, 400)
        recipients = set(self._attempt_notifications(self.other_employee_user).values_list("recipient_id", flat=True))
        self.assertIn(self.other_team_leader.pk, recipients)
        self.assertNotIn(self.team_leader.pk, recipients)

    def test_management_sees_only_team_leaders_and_self_not_staff_or_admin(self):
        self._create_record(self.management)
        self._create_record(self.team_leader)
        self._create_record(self.employee_user, self.employee)
        self._create_record(self.admin)
        self.client.force_authenticate(user=self.management)

        response = self.client.get("/attendance")

        self.assertEqual(response.status_code, 200)
        visible_ids = {item["user_id"] for item in response.data}
        self.assertSetEqual(visible_ids, {self.management.pk, self.team_leader.pk})
        self.assertNotIn(self.admin.pk, visible_ids)
        self.assertNotIn(self.employee_user.pk, visible_ids)

    def test_admin_can_view_all_attendance_records(self):
        self._create_record(self.management)
        self._create_record(self.team_leader)
        self._create_record(self.employee_user, self.employee)
        self.client.force_authenticate(user=self.admin)

        response = self.client.get("/attendance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            {item["user_id"] for item in response.data},
            {self.management.pk, self.team_leader.pk, self.employee_user.pk},
        )

    def test_employee_sees_only_own_location_records(self):
        self._create_record(self.employee_user, self.employee)
        self._create_record(self.other_employee_user, self.other_employee)
        self.client.force_authenticate(user=self.employee_user)

        response = self.client.get("/attendance")

        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["user_id"] for item in response.data], [self.employee_user.pk])

    def test_only_admin_can_read_or_edit_office_locations(self):
        for user in (
            self.management,
            self.team_leader,
            self.employee_user,
            self.hr_manager,
        ):
            self.client.force_authenticate(user=user)
            self.assertEqual(self.client.get("/office-locations").status_code, 403)
            self.assertEqual(
                self.client.post(
                    "/office-locations",
                    {"name": f"Unauthorized {user.pk}", "latitude": 0, "longitude": 0},
                    format="json",
                ).status_code,
                403,
            )

        self.client.force_authenticate(user=self.admin)
        self.assertEqual(self.client.get("/office-locations").status_code, 200)
        created = self.client.post(
            "/office-locations",
            {
                "name": "Admin Branch",
                "latitude": 10,
                "longitude": 20,
                "allowed_radius_meters": 150,
            },
            format="json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.data["allowed_radius_meters"], 150)

    def test_missing_and_invalid_coordinates_are_rejected(self):
        self.client.force_authenticate(user=self.employee_user)
        now = timezone.make_aware(datetime(2026, 10, 8, 10, 0))
        with patch("api.attendance_api.timezone.now", return_value=now):
            missing = self.client.post("/attendance/time-in", {}, format="json")
            invalid = self.client.post(
                "/attendance/time-in",
                {"latitude": 91, "longitude": 0, "accuracy": 5},
                format="json",
            )

        self.assertEqual(missing.status_code, 400)
        self.assertEqual(invalid.status_code, 400)
        self.assertFalse(AttendanceRecord.objects.exists())

    def test_office_radius_boundary_is_inside(self):
        longitude = math.degrees(100 / EARTH_RADIUS_METERS)
        self.assertAlmostEqual(
            haversine_distance_meters(0, 0, 0, longitude),
            100,
            places=8,
        )

        response = self._time_in(
            self.employee_user,
            latitude=0,
            longitude=longitude,
            outside_reason="",
        )

        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.data["is_outside_premises"])
        self.assertFalse(self._attempt_notifications(self.employee_user).exists())

    def test_require_reason_and_block_policies_are_enforced_server_side(self):
        self.office.outside_time_in_policy = OfficeLocation.OutsideTimeInPolicy.require_reason
        self.office.save(update_fields=("outside_time_in_policy",))
        response = self._time_in(self.employee_user, outside_reason="")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AttendanceRecord.objects.exists())

        self.office.outside_time_in_policy = OfficeLocation.OutsideTimeInPolicy.block
        self.office.save(update_fields=("outside_time_in_policy",))
        response = self._time_in(self.employee_user)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(AttendanceRecord.objects.exists())
        self.assertTrue(self._attempt_notifications(self.employee_user).exists())

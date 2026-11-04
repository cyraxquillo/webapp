import re
from datetime import date
from pathlib import Path
from uuid import UUID

from django.contrib.staticfiles import finders
from django.template.loader import get_template
from django.test import override_settings
from django.urls import URLPattern, URLResolver, get_resolver, resolve
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from .models import (
    AttendanceRecord,
    Client,
    Department,
    Employee,
    Evaluation,
    Notification,
    User,
)


TEMPLATE_ROOT = Path(__file__).resolve().parent / "templates" / "api"
URL_TAG = re.compile(r"{%\s*url\s+(['\"])([\w:-]+)\1")
STATIC_TAG = re.compile(r"{%\s*static\s+(['\"])([^'\"]+)\1")
LOCAL_HREF = re.compile(r"href=['\"](/[^'\"]*)['\"]")
PLACEHOLDER = re.compile(r"<(?:(?P<converter>\w+):)?(?P<name>\w+)")
DESTRUCTIVE_POST_ROUTES = {
    "department-delete",
    "employee-delete",
    "evaluation-delete",
    "web-logout",
}


def _leaf_routes(patterns=None, prefix=""):
    for entry in patterns or get_resolver().url_patterns:
        route = getattr(entry.pattern, "_route", None)
        if route is None:
            continue
        full_route = prefix + route
        if isinstance(entry, URLResolver):
            yield from _leaf_routes(entry.url_patterns, full_route)
        elif isinstance(entry, URLPattern):
            yield entry, full_route


def _template_url_names():
    for template_path in TEMPLATE_ROOT.glob("*.html"):
        source = template_path.read_text(encoding="utf-8")
        yield from (match.group(2) for match in URL_TAG.finditer(source))


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class RoleRouteMatrixTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="matrix-admin@example.test",
            password="Matrix-Test-2026!",
            name="Matrix Admin",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        cls.management = User.objects.create_user(
            email="matrix-management@example.test",
            password="Matrix-Test-2026!",
            name="Matrix Management",
            role=User.Role.management,
        )
        cls.team_leader = User.objects.create_user(
            email="matrix-leader@example.test",
            password="Matrix-Test-2026!",
            name="Matrix Team Leader",
            role=User.Role.team_leader,
        )
        cls.other_team_leader = User.objects.create_user(
            email="matrix-other-leader@example.test",
            password="Matrix-Test-2026!",
            name="Other Team Leader",
            role=User.Role.team_leader,
        )
        cls.staff_user = User.objects.create_user(
            email="matrix-staff@example.test",
            password="Matrix-Test-2026!",
            name="Matrix Staff",
            role=User.Role.staff,
        )
        cls.employee_user = User.objects.create_user(
            email="matrix-employee@example.test",
            password="Matrix-Test-2026!",
            name="Matrix Employee",
            role=User.Role.employee,
        )
        cls.client_user = User.objects.create_user(
            email="matrix-client@example.test",
            password="Matrix-Test-2026!",
            name="Matrix Client",
            role=User.Role.client,
        )
        cls.hr_manager = User.objects.create_user(
            email="matrix-hr@example.test",
            password="Matrix-Test-2026!",
            name="Matrix HR Manager",
            role=User.Role.hr_manager,
        )
        cls.department = Department.objects.create(name="Matrix Department")
        cls.employee = Employee.objects.create(
            employee_code="MATRIX-001",
            user=cls.employee_user,
            team_leader=cls.team_leader,
            name="Matrix Employee",
            department=cls.department,
            position="Analyst",
            date_hired="2025-01-01",
        )
        cls.staff_employee = Employee.objects.create(
            employee_code="MATRIX-002",
            user=cls.staff_user,
            team_leader=cls.other_team_leader,
            name="Matrix Staff",
            department=cls.department,
            position="Coordinator",
            date_hired="2025-01-01",
        )
        cls.evaluation = Evaluation.objects.create(
            employee=cls.employee,
            evaluator=cls.admin,
            date=date(2026, 1, 15),
            cycle_month=date(2026, 1, 1),
            productivity_score=80,
            attendance_score=85,
            quality_score=90,
            teamwork_score=88,
            communication_score=92,
            team_leader_rating=85,
        )
        cls.attendance = AttendanceRecord.objects.create(
            employee=cls.employee,
            work_date=date(2026, 1, 15),
            status=AttendanceRecord.Status.present,
        )
        cls.notification = Notification.objects.create(
            recipient=cls.employee_user,
            category=Notification.Category.manual,
            title="Matrix notification",
            message="Route test notification",
        )
        Client.objects.create(
            client_code="MATRIX-CLIENT",
            user=cls.client_user,
            company_name="Matrix Client Company",
            services_used="Testing",
            next_renewal_date=date(2026, 12, 31),
        )

    @property
    def role_users(self):
        return (
            self.admin,
            self.management,
            self.team_leader,
            self.staff_user,
            self.employee_user,
            self.client_user,
            self.hr_manager,
        )

    def _authenticate(self, user):
        self.client.force_login(user)
        token = RefreshToken.for_user(user).access_token
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def _path_for(self, pattern, route):
        def replace(match):
            name = match.group("name")
            converter = match.group("converter") or "str"
            if name == "pk":
                route_name = pattern.name or ""
                if "department" in route_name:
                    value = self.department.pk
                elif "evaluation" in route_name:
                    value = self.evaluation.pk
                elif "attendance" in route_name:
                    value = self.attendance.pk
                elif "notification" in route_name:
                    value = self.notification.pk
                elif "account" in route_name:
                    value = self.management.pk
                else:
                    value = self.employee.pk
            elif name == "employee_id":
                value = self.employee.pk
            elif name == "evaluation_id":
                value = self.evaluation.pk
            elif name == "object_id":
                value = self.admin.pk
            elif name == "app_label":
                value = "api"
            elif name == "model_name":
                value = "user"
            elif converter == "int":
                value = 1
            elif converter == "uuid":
                value = UUID("12345678-1234-5678-1234-567812345678")
            elif converter == "path":
                value = "api/user/1"
            else:
                value = "1"
            return str(value)

        return "/" + PLACEHOLDER.sub(replace, route)

    def _assert_no_server_error(self, response, user, method, path):
        self.assertLess(
            response.status_code,
            500,
            f"{user.role} {method} {path} returned {response.status_code}",
        )
        body = response.content.decode("utf-8", errors="replace")
        for error_name in (
            "NoReverseMatch",
            "OperationalError",
            "VariableDoesNotExist",
            "TemplateDoesNotExist",
            "Internal Server Error",
        ):
            self.assertNotIn(
                error_name,
                body,
                f"{user.role} {method} {path} rendered {error_name}",
            )

    def test_every_configured_route_handles_get_and_invalid_post_for_each_role(self):
        routes = list(_leaf_routes())
        self.assertGreaterEqual(len(routes), 70)
        for user in self.role_users:
            for pattern, route in routes:
                path = self._path_for(pattern, route)
                self._authenticate(user)
                get_response = self.client.get(path)
                self._assert_no_server_error(get_response, user, "GET", path)
                self._authenticate(user)
                post_response = self.client.post(path, {}, format="multipart")
                self._assert_no_server_error(post_response, user, "POST", path)

    def test_template_url_names_static_assets_and_local_links_resolve(self):
        route_names = {
            pattern.name
            for pattern, _ in _leaf_routes()
            if pattern.name
        }
        for template_name in sorted(path.stem for path in TEMPLATE_ROOT.glob("*.html")):
            get_template(f"api/{template_name}.html")
        for name in _template_url_names():
            self.assertIn(name, route_names, f"Template URL name {name!r} is not configured")

        for template_path in TEMPLATE_ROOT.glob("*.html"):
            source = template_path.read_text(encoding="utf-8")
            for match in STATIC_TAG.finditer(source):
                self.assertTrue(finders.find(match.group(2)), f"Missing static asset {match.group(2)!r}")
            for match in LOCAL_HREF.finditer(source):
                path = match.group(1).split("?", 1)[0].split("#", 1)[0]
                if path.startswith("/media/"):
                    continue
                try:
                    resolve(path)
                except Exception as error:
                    self.fail(f"Broken local link {path!r} in {template_path.name}: {error}")

    def test_role_boundaries_and_record_scopes(self):
        self._authenticate(self.admin)
        self.assertEqual(self.client.get("/system-tools/").status_code, 200)
        self.assertEqual(self.client.get("/clients").status_code, 200)

        self._authenticate(self.management)
        self.assertEqual(self.client.get("/automation-settings/").status_code, 200)
        self.assertEqual(self.client.get("/system-tools/").status_code, 403)
        self.assertEqual(self.client.get("/clients").status_code, 403)

        self._authenticate(self.team_leader)
        self.assertEqual(self.client.get(f"/employees/{self.employee.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/employees/{self.staff_employee.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/system-tools/").status_code, 403)
        self.assertEqual(self.client.get("/accounts/").status_code, 403)

        for user in (self.staff_user, self.employee_user):
            self._authenticate(user)
            self.assertEqual(self.client.get("/reports/").status_code, 403)
            self.assertEqual(self.client.get("/system-tools/").status_code, 403)
            self.assertEqual(self.client.get("/accounts/").status_code, 403)
        self._authenticate(self.employee_user)
        self.assertEqual(self.client.get(f"/employees/{self.staff_employee.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/employees/").status_code, 200)
        self.assertContains(self.client.get("/employees/"), "Matrix Employee")
        self.assertNotContains(self.client.get("/employees/"), "Matrix Staff")

        self._authenticate(self.client_user)
        self.assertEqual(self.client.get("/clients/me").status_code, 200)
        self.assertEqual(self.client.get("/dashboard/").status_code, 403)
        self.assertEqual(self.client.get("/clients").status_code, 403)
        response = self.client.get("/client/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Matrix Client Company")
        self.assertContains(response, "Testing")
        self.assertContains(response, "December 31, 2026")

        self._authenticate(self.admin)
        self.assertEqual(self.client.get("/client/").status_code, 403)
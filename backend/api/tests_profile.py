from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from .models import Department, Employee, User


MEDIA_ROOT = TemporaryDirectory()


class ProfileFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.department = Department.objects.create(name="Operations")
        cls.employee = User.objects.create_user(
            email="employee@example.com",
            password="CurrentPassword123!",
            name="Original Name",
            role=User.Role.employee,
            phone_number="+63 900 000 0000",
        )
        cls.team_leader = User.objects.create_user(
            email="leader@example.com",
            password="LeaderPassword123!",
            name="Team Leader",
            role=User.Role.team_leader,
        )
        cls.management = User.objects.create_user(
            email="management@example.com",
            password="ManagementPassword123!",
            name="Management User",
            role=User.Role.management,
        )
        cls.admin = User.objects.create_user(
            email="admin@example.com",
            password="AdminPassword123!",
            name="Admin User",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        cls.profile_employee = Employee.objects.create(
            employee_code="EMP-1001",
            user=cls.employee,
            name="Original Name",
            department=cls.department,
            position="Analyst",
            date_hired="2025-01-01",
        )

    def force_login(self, user):
        self.client.force_login(user)

    def png_upload(self, size=(100, 100), content_type="image/png"):
        buffer = BytesIO()
        Image.new("RGB", size, "#0e8f88").save(buffer, format="PNG")
        return SimpleUploadedFile(
            "profile.png",
            buffer.getvalue(),
            content_type=content_type,
        )

    @override_settings(MEDIA_ROOT=MEDIA_ROOT.name)
    def test_profile_photo_upload_is_validated_and_saved(self):
        self.force_login(self.employee)
        response = self.client.post(
            reverse("settings"),
            {
                "form_action": "profile",
                "name": "Updated Name",
                "email": "employee@example.com",
                "phone_number": "+63 991 234 5678",
                "photo": self.png_upload(),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.name, "Updated Name")
        self.assertEqual(self.employee.phone_number, "+63 991 234 5678")
        self.assertTrue(self.employee.photo)
        self.assertTrue(Path(self.employee.photo.path).is_file())
        self.assertIn("photo", self.employee.photo.name.lower())
        self.assertContains(self.client.get(reverse("settings")), "Updated Name")

    @override_settings(MEDIA_ROOT=MEDIA_ROOT.name)
    def test_profile_keeps_existing_photo_when_no_new_upload_is_sent(self):
        self.force_login(self.employee)
        self.employee.photo.save(
            "existing.png",
            self.png_upload(),
            save=True,
        )
        self.employee.refresh_from_db()

        response = self.client.post(
            reverse("settings"),
            {
                "form_action": "profile",
                "name": "Updated Name",
                "email": "employee@example.com",
                "phone_number": "+63 991 234 5678",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertTrue(self.employee.photo)
        self.assertEqual(self.employee.name, "Updated Name")

    @override_settings(MEDIA_ROOT=MEDIA_ROOT.name)
    def test_profile_rejects_non_png_jpg_and_files_over_2_mb(self):
        self.force_login(self.employee)
        bad_file = SimpleUploadedFile(
            "profile.pdf",
            b"not an image",
            content_type="application/pdf",
        )
        response = self.client.post(
            reverse("settings"),
            {
                "form_action": "profile",
                "name": "Updated Name",
                "email": "employee@example.com",
                "phone_number": "+63 991 234 5678",
                "photo": bad_file,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "JPG or PNG images")
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.photo)

    def test_seeded_user_must_change_password_before_accessing_other_pages(self):
        self.employee.must_change_password = True
        self.employee.save(update_fields=("must_change_password",))
        self.force_login(self.employee)

        response = self.client.get(reverse("employee-list"))
        self.assertRedirects(response, "/settings/?tab=security&force=1")
        response = self.client.get("/settings/?tab=security&force=1")
        self.assertContains(response, "Change your temporary password")
        self.assertNotContains(response, "Profile information")

        response = self.client.post(
            "/settings/?tab=security",
            {
                "form_action": "password",
                "old_password": "CurrentPassword123!",
                "new_password1": "New-Test-Password-123!",
                "new_password2": "New-Test-Password-123!",
            },
        )

        self.assertRedirects(response, "/dashboard/")
        self.employee.refresh_from_db()
        self.assertFalse(self.employee.must_change_password)
        self.assertTrue(self.employee.check_password("New-Test-Password-123!"))

    def test_seed_demo_uses_environment_passwords_and_requires_password_change(self):
        passwords = {
            "DEMO_ADMIN_PASSWORD": "Demo-admin-test-123!",
            "DEMO_CLIENT_PASSWORD": "Demo-client-test-123!",
            "DEMO_STAFF_PASSWORD": "Demo-staff-test-123!",
        }
        with patch.dict(os.environ, passwords):
            call_command("seed_demo")

        for email, password in (
            ("admin@lexie.ph", passwords["DEMO_ADMIN_PASSWORD"]),
            ("client@lexie.ph", passwords["DEMO_CLIENT_PASSWORD"]),
            ("maya.santos@lexie.ph", passwords["DEMO_STAFF_PASSWORD"]),
        ):
            with self.subTest(email=email):
                user = User.objects.get(email=email)
                self.assertTrue(user.check_password(password))
                self.assertTrue(user.must_change_password)

    def test_password_change_uses_django_validation_and_keeps_user_logged_in(self):
        self.force_login(self.employee)
        response = self.client.post(
            reverse("settings"),
            {
                "form_action": "password",
                "old_password": "CurrentPassword123!",
                "new_password1": "NewPassword123!",
                "new_password2": "NewPassword123!",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session.get("_auth_user_id"))
        self.client.logout()
        self.client.force_login(self.employee)
        self.assertTrue(self.client.session.get("_auth_user_id"))

    def test_password_change_rejects_invalid_password(self):
        self.force_login(self.employee)
        response = self.client.post(
            reverse("settings"),
            {
                "form_action": "password",
                "old_password": "CurrentPassword123!",
                "new_password1": "1234",
                "new_password2": "1234",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "password")

    def test_team_leader_can_only_edit_own_profile(self):
        self.force_login(self.team_leader)
        response = self.client.post(
            reverse("settings"),
            {
                "form_action": "profile",
                "name": "Changed Leader",
                "email": "leader@example.com",
                "phone_number": "",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.team_leader.refresh_from_db()
        self.assertEqual(self.team_leader.name, "Changed Leader")

        self.force_login(self.team_leader)
        response = self.client.get(reverse("account-edit", args=[self.employee.pk]))
        self.assertEqual(response.status_code, 403)

    def test_management_and_admin_can_edit_any_user_account_photo(self):
        self.force_login(self.management)
        response = self.client.post(
            reverse("account-edit", args=[self.employee.pk]),
            {
                "name": "Edited by Management",
                "email": "employee@example.com",
                "role": User.Role.staff,
                "photo": self.png_upload(),
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.name, "Edited by Management")
        self.assertTrue(self.employee.photo)

        self.force_login(self.admin)
        response = self.client.post(
            reverse("account-edit", args=[self.team_leader.pk]),
            {
                "name": "Edited by Admin",
                "email": "leader@example.com",
                "role": User.Role.team_leader,
                "is_active": "on",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.team_leader.refresh_from_db()
        self.assertEqual(self.team_leader.name, "Edited by Admin")

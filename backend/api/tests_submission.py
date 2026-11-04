from datetime import date
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import Department, Employee, Submission, User


@override_settings(MEDIA_ROOT="/tmp/lexie-submission-tests")
class SubmissionFeatureTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="admin@lexie.ph",
            password="Admin123!",
            name="Admin User",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        cls.employee = User.objects.create_user(
            email="employee@lexie.ph",
            password="Employee123!",
            name="Employee User",
            role=User.Role.employee,
        )
        cls.other_employee = User.objects.create_user(
            email="other@lexie.ph",
            password="Employee123!",
            name="Other Employee",
            role=User.Role.employee,
        )
        department = Department.objects.create(name="Operations")
        cls.employee_profile = Employee.objects.create(
            employee_code="EMP-1001",
            user=cls.employee,
            name="Employee User",
            department=department,
            position="Analyst",
            date_hired=date(2024, 1, 1),
        )
        cls.other_profile = Employee.objects.create(
            employee_code="EMP-1002",
            user=cls.other_employee,
            name="Other Employee",
            department=department,
            position="Coordinator",
            date_hired=date(2024, 2, 1),
        )

    def client_for(self, user):
        client = self.client
        client.force_login(user=user)
        return client

    def test_employee_can_upload_and_only_see_own_submission(self):
        client = self.client_for(self.employee)
        response = client.post(
            reverse("submission-create"),
            {
                "title": "Monthly report",
                "description": "Report for this month",
                "file": SimpleUploadedFile(
                    "report.pdf",
                    b"pdf-data",
                    content_type="application/pdf",
                ),
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Submission.objects.count(), 1)
        self.assertEqual(Submission.objects.get().employee, self.employee_profile)

        response = client.get(reverse("submission-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Monthly report")
        self.assertNotContains(response, "Other Employee")

    def test_manager_can_review_submission_and_comment(self):
        submission = Submission.objects.create(
            employee=self.employee_profile,
            title="Draft",
            description="Needs review",
            file=SimpleUploadedFile("draft.pdf", b"pdf-data"),
        )
        client = self.client_for(self.admin)
        response = client.post(
            reverse("submission-review", args=[submission.pk]),
            {"status": "reviewed", "reviewer_comment": "Approved"},
        )
        self.assertEqual(response.status_code, 302)
        submission.refresh_from_db()
        self.assertEqual(submission.status, Submission.Status.reviewed)
        self.assertEqual(submission.reviewer_comment, "Approved")

        response = client.get(reverse("submission-list"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Draft")

    def test_manager_submission_page_hides_upload_action_and_uses_admin_heading(self):
        client = self.client_for(self.admin)
        response = client.get(reverse("submission-list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Review queue")
        self.assertNotContains(response, "Upload work")

    def test_non_employee_redirects_with_styled_authorization_message(self):
        client = self.client_for(self.admin)
        response = client.get(reverse("submission-create"), follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse("submission-list"))
        self.assertContains(response, "Only employees can upload work submissions.")
        self.assertContains(response, "message-error")

    def test_invalid_file_type_and_size_are_rejected(self):
        client = self.client_for(self.employee)
        response = client.post(
            reverse("submission-create"),
            {
                "title": "Invalid file",
                "description": "Not allowed",
                "file": SimpleUploadedFile(
                    "script.exe",
                    b"not-a-supported-file",
                    content_type="application/x-msdownload",
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "PDF, DOCX, XLSX, PNG, or JPG")
        self.assertFalse(Submission.objects.exists())

        oversized = SimpleUploadedFile(
            "large.pdf",
            b"x" * (5 * 1024 * 1024 + 1),
            content_type="application/pdf",
        )
        response = client.post(
            reverse("submission-create"),
            {
                "title": "Oversized file",
                "description": "Too large",
                "file": oversized,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "5 MB")
        self.assertFalse(Submission.objects.exists())

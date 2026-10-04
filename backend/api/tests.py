from datetime import date
import json
from pathlib import Path
import tempfile
from urllib.parse import urlencode
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase

from api.ml.predict import (
    MODEL_SOURCE,
    MODEL_VERSION,
    build_feature_vector,
    loaded_model_artifacts,
    predict_performance,
)
from .models import Department, Employee, Evaluation, Prediction, User


@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"])
class BackendApiTests(APITestCase):
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
        cls.hr = User.objects.create_user(
            email="hr@lexie.ph",
            password="Manager123!",
            name="HR Manager",
            role=User.Role.hr_manager,
        )
        cls.employee_user = User.objects.create_user(
            email="staff@lexie.ph",
            password="Staff123!",
            name="Staff User",
            role=User.Role.staff,
        )
        cls.other_employee_user = User.objects.create_user(
            email="other@lexie.ph",
            password="Staff123!",
            name="Other User",
            role=User.Role.employee,
        )
        cls.department = Department.objects.create(name="Recruitment")
        cls.employee = Employee.objects.create(
            employee_code="EMP-1001",
            user=cls.employee_user,
            name="Staff User",
            department=cls.department,
            position="Recruiter",
            date_hired=date(2024, 1, 1),
        )
        cls.other_employee = Employee.objects.create(
            employee_code="EMP-1002",
            user=cls.other_employee_user,
            name="Other User",
            department=cls.department,
            position="Coordinator",
            date_hired=date(2024, 2, 1),
        )

    def authenticate(self, user):
        self.client.force_authenticate(user=user)

    def evaluation_payload(self, employee_id=None):
        return {
            "employee_id": employee_id or self.employee.id,
            "date": "2026-01-15",
            "productivity_score": 80,
            "attendance_score": 85,
            "quality_score": 90,
            "teamwork_score": 88,
            "communication_score": 92,
            "comments": "Consistent performance",
        }

    def test_login_accepts_frontend_form_and_returns_jwt_contract(self):
        response = self.client.post(
            "/auth/login",
            urlencode({"username": "admin@lexie.ph", "password": "Admin123!"}),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["access_token"])
        self.assertEqual(response.data["token_type"], "bearer")
        self.assertEqual(response.data["role"], "admin")
        profile = self.client.get(
            "/auth/me",
            HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}",
        )
        self.assertEqual(profile.status_code, 200)
        self.assertEqual(profile.data["email"], "admin@lexie.ph")

    def test_invalid_login_is_401(self):
        response = self.client.post(
            "/auth/login",
            urlencode({"username": "admin@lexie.ph", "password": "wrong"}),
            content_type="application/x-www-form-urlencoded",
        )
        self.assertEqual(response.status_code, 401)

    def test_staff_can_only_list_and_read_own_employee(self):
        self.authenticate(self.employee_user)
        response = self.client.get("/employees")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data], [self.employee.id])
        response = self.client.get(f"/employees/{self.other_employee.id}")
        self.assertEqual(response.status_code, 403)

    def test_staff_cannot_create_employee(self):
        self.authenticate(self.employee_user)
        response = self.client.post("/employees", {
            "name": "New Person",
            "department": "Finance",
            "position": "Analyst",
            "date_hired": "2026-01-01",
        }, format="json")
        self.assertEqual(response.status_code, 403)

    def test_hr_manager_can_manage_workforce_but_cannot_register_accounts(self):
        self.authenticate(self.hr)
        create_response = self.client.post("/employees", {
            "name": "New Person",
            "department": "Finance",
            "position": "Analyst",
            "date_hired": "2026-01-01",
        }, format="json")
        self.assertEqual(create_response.status_code, 201)
        register_response = self.client.post("/auth/register", {
            "name": "New User",
            "email": "new@lexie.ph",
            "password": "NewPassword123!",
            "role": "staff",
        }, format="json")
        self.assertEqual(register_response.status_code, 403)

    def test_admin_employee_crud(self):
        self.authenticate(self.admin)
        created = self.client.post("/employees", {
            "name": "New Person",
            "department": "Finance",
            "position": "Analyst",
            "date_hired": "2026-01-01",
        }, format="json")
        self.assertEqual(created.status_code, 201)
        employee_id = created.data["id"]
        self.assertEqual(created.data["department"], "Finance")

        updated = self.client.put(f"/employees/{employee_id}", {
            "name": "Updated Person",
            "department": "Finance",
        }, format="json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["name"], "Updated Person")

        deleted = self.client.delete(f"/employees/{employee_id}")
        self.assertEqual(deleted.status_code, 204)

    def test_admin_evaluation_crud_and_read_format(self):
        self.authenticate(self.admin)
        created = self.client.post("/evaluations", self.evaluation_payload(), format="json")
        self.assertEqual(created.status_code, 201)
        evaluation_id = created.data["id"]
        self.assertEqual(created.data["overall_score"], 87.0)
        self.assertEqual(created.data["evaluator_id"], self.admin.id)

        updated = self.client.put(f"/evaluations/{evaluation_id}", {"comments": "Updated"}, format="json")
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.data["comments"], "Updated")
        self.assertEqual(self.client.get("/evaluations").status_code, 200)
        self.assertEqual(self.client.delete(f"/evaluations/{evaluation_id}").status_code, 204)

    def test_employee_evaluations_are_scoped_to_owner(self):
        evaluation = Evaluation.objects.create(
            employee=self.employee,
            evaluator=self.admin,
            date=date(2026, 1, 1),
            productivity_score=80,
            attendance_score=80,
            quality_score=80,
            teamwork_score=80,
            communication_score=80,
        )
        self.authenticate(self.other_employee_user)
        self.assertEqual(self.client.get(f"/evaluations/{evaluation.id}").status_code, 403)

    def test_client_cannot_access_admin_reports(self):
        client_user = User.objects.create_user(
            email="client@lexie.ph",
            password="Client123!",
            name="Client User",
            role=User.Role.client,
        )
        self.authenticate(client_user)
        self.assertEqual(self.client.get("/reports/summary").status_code, 403)

    def test_departments_api_returns_seedable_catalog(self):
        self.authenticate(self.hr)
        response = self.client.get("/departments")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]["name"], "Recruitment")

    def test_loaded_random_forest_prediction_matches_prediction_api(self):
        self.authenticate(self.admin)
        evaluation = Evaluation.objects.create(
            employee=self.employee,
            evaluator=self.admin,
            date=date(2026, 1, 15),
            productivity_score=80,
            attendance_score=85,
            quality_score=90,
            teamwork_score=88,
            communication_score=92,
        )
        features = build_feature_vector(self.employee, evaluation)
        model, _, target_encoder, _ = loaded_model_artifacts()
        predicted_label = model.predict(features)[0]
        probabilities = model.predict_proba(features)[0]
        expected_category = str(target_encoder.inverse_transform([predicted_label])[0])
        expected_confidence = round(float(max(probabilities)), 4)
        expected = predict_performance(self.employee, evaluation)

        response = self.client.post(f"/predictions/{self.employee.id}")

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["predicted_category"], expected_category)
        self.assertEqual(response.data["predicted_category"], expected["predicted_category"])
        self.assertEqual(response.data["confidence_score"], expected_confidence)
        self.assertEqual(response.data["feature_importances"], expected["feature_importances"])
        self.assertEqual(response.data["model_source"], MODEL_SOURCE)
        self.assertEqual(response.data["model_version"], MODEL_VERSION)


@override_settings(ALLOWED_HOSTS=["127.0.0.1", "localhost", "testserver"])
class WebViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            email="web-admin@lexie.ph",
            password="WebTest123!",
            name="Web Admin",
            role=User.Role.admin,
            is_staff=True,
            is_superuser=True,
        )
        cls.hr_user = User.objects.create_user(
            email="web-hr@lexie.ph",
            password="WebTest123!",
            name="Web HR",
            role=User.Role.hr_manager,
        )
        cls.employee_user = User.objects.create_user(
            email="web-employee@lexie.ph",
            password="WebTest123!",
            name="Web Employee",
            role=User.Role.employee,
        )
        cls.other_user = User.objects.create_user(
            email="web-other@lexie.ph",
            password="WebTest123!",
            name="Other Employee",
            role=User.Role.staff,
        )
        cls.client_user = User.objects.create_user(
            email="web-client@lexie.ph",
            password="WebTest123!",
            name="Web Client",
            role=User.Role.client,
        )
        cls.department = Department.objects.create(name="Web Test Department")
        cls.employee = Employee.objects.create(
            employee_code="WEB-1001",
            user=cls.employee_user,
            name="Web Employee",
            department=cls.department,
            position="Analyst",
            date_hired=date(2025, 1, 1),
        )
        cls.other_employee = Employee.objects.create(
            employee_code="WEB-1002",
            user=cls.other_user,
            name="Other Employee",
            department=cls.department,
            position="Coordinator",
            date_hired=date(2025, 1, 1),
        )
        Evaluation.objects.create(
            employee=cls.employee,
            evaluator=cls.admin,
            date=date(2026, 1, 15),
            productivity_score=80,
            attendance_score=85,
            quality_score=90,
            teamwork_score=88,
            communication_score=92,
        )

    def test_dashboard_redirects_anonymous_users_to_login(self):
        response = self.client.get("/dashboard/")

        self.assertRedirects(response, "/login/?next=/dashboard/")

    def test_login_uses_session_and_logout_ends_session(self):
        response = self.client.post(
            "/login/",
            {"email": "WEB-ADMIN@LEXIE.PH", "password": "WebTest123!"},
        )

        self.assertRedirects(response, "/dashboard/")
        self.assertTrue(self.client.session.get("_auth_user_id"))
        self.assertEqual(self.client.get("/dashboard/").status_code, 200)

        response = self.client.post("/logout/")

        self.assertRedirects(response, "/login/")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_employee_dashboard_contains_only_own_profile(self):
        self.client.force_login(self.employee_user)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Employee")
        self.assertContains(response, "87.0")
        self.assertNotContains(response, "Other Employee")
        self.assertNotContains(response, "WEB-1002")

    def test_unsupported_role_is_denied_dashboard_access(self):
        self.client.force_login(self.client_user)

        self.assertEqual(self.client.get("/dashboard/").status_code, 403)

    def test_employee_views_are_scoped_and_write_views_require_workforce_role(self):
        self.client.force_login(self.employee_user)

        response = self.client.get("/employees/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Employee")
        self.assertNotContains(response, "Other Employee")
        self.assertEqual(self.client.get(f"/employees/{self.other_employee.pk}/").status_code, 404)
        self.assertEqual(self.client.get("/employees/add/").status_code, 403)
        self.assertEqual(self.client.get("/departments/").status_code, 403)
        self.assertEqual(self.client.get("/reports/").status_code, 403)
        self.assertEqual(self.client.get("/accounts/").status_code, 403)

    def test_hr_can_create_department_and_employee(self):
        self.client.force_login(self.hr_user)

        department_response = self.client.post(
            "/departments/add/",
            {"name": "Client Services", "description": "Client support", "is_active": "on"},
        )
        self.assertRedirects(department_response, "/departments/")
        department = Department.objects.get(name="Client Services")

        employee_response = self.client.post(
            "/employees/add/",
            {
                "name": "Created Employee",
                "department": department.pk,
                "position": "Coordinator",
                "date_hired": "2025-02-01",
                "status": "active",
                "attendance_rate": "96",
                "tenure_months": "12",
                "training_hours": "20",
            },
        )
        self.assertRedirects(employee_response, "/employees/")
        employee = Employee.objects.get(name="Created Employee")
        self.assertTrue(employee.employee_code.startswith("EMP-"))
        self.assertEqual(employee.department, department)

    def test_department_with_employees_cannot_be_deleted(self):
        self.client.force_login(self.admin)

        response = self.client.post(f"/departments/{self.department.pk}/delete/")

        self.assertRedirects(response, "/departments/")
        self.assertTrue(Department.objects.filter(pk=self.department.pk).exists())

    def test_performance_records_are_filtered_by_owner_and_can_be_created_by_hr(self):
        self.client.force_login(self.employee_user)
        response = self.client.get("/performance/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Web Employee")
        self.assertNotContains(response, "Other Employee")
        self.assertEqual(self.client.get("/performance/add/").status_code, 403)

        self.client.force_login(self.hr_user)
        response = self.client.post(
            "/performance/add/",
            {
                "employee": self.employee.pk,
                "date": "2026-02-01",
                "productivity_score": "90",
                "attendance_score": "90",
                "quality_score": "90",
                "teamwork_score": "90",
                "communication_score": "90",
            },
        )
        self.assertRedirects(response, "/performance/")
        self.assertTrue(Evaluation.objects.filter(employee=self.employee, date=date(2026, 2, 1)).exists())

        record = Evaluation.objects.get(employee=self.employee, date=date(2026, 2, 1))
        response = self.client.post(
            f"/performance/{record.pk}/edit/",
            {
                "employee": self.employee.pk,
                "date": "2026-02-01",
                "productivity_score": "91",
                "attendance_score": "90",
                "quality_score": "90",
                "teamwork_score": "90",
                "communication_score": "90",
            },
        )
        self.assertRedirects(response, "/performance/")
        record.refresh_from_db()
        self.assertEqual(record.productivity_score, 91)

    def test_evaluation_views_calculate_scores_and_protect_other_employees(self):
        self.client.force_login(self.employee_user)
        self.assertEqual(self.client.get("/evaluations/criteria/").status_code, 403)
        response = self.client.get("/evaluations/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "87.0")

        self.client.force_login(self.hr_user)
        response = self.client.post(
            "/evaluations/add/",
            {
                "employee": self.other_employee.pk,
                "date": "2026-03-01",
                "productivity_score": "80",
                "attendance_score": "85",
                "quality_score": "90",
                "teamwork_score": "88",
                "communication_score": "92",
                "comments": "Quarterly review",
            },
        )
        evaluation = Evaluation.objects.get(employee=self.other_employee, date=date(2026, 3, 1))
        self.assertRedirects(response, "/evaluations/")
        self.assertEqual(evaluation.overall_score, 87)

        self.client.force_login(self.other_user)
        self.assertEqual(self.client.get(f"/evaluations/{evaluation.pk}/").status_code, 200)
        self.assertEqual(self.client.get(f"/evaluations/{Evaluation.objects.filter(employee=self.employee).first().pk}/").status_code, 404)

    def test_admin_can_manage_criteria_and_employees_cannot_run_predictions(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/evaluations/criteria/").status_code, 200)

        self.client.force_login(self.employee_user)
        response = self.client.post(
            "/predictions/run/",
            {"action": "single", "employee_id": self.employee.pk},
        )
        self.assertEqual(response.status_code, 403)
        response = self.client.get("/predictions/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Other Employee")

    def test_admin_can_run_model_prediction_and_view_feature_importance(self):
        trained_department, _ = Department.objects.get_or_create(name="Recruitment")
        self.employee.department = trained_department
        self.employee.save(update_fields=("department",))
        self.client.force_login(self.admin)

        response = self.client.post(
            "/predictions/run/",
            {"action": "single", "employee_id": self.employee.pk},
        )

        self.assertRedirects(response, "/predictions/")
        prediction = Prediction.objects.filter(employee=self.employee).first()
        self.assertIsNotNone(prediction)
        self.assertTrue(0 <= prediction.confidence_score <= 1)
        self.assertTrue(prediction.feature_importances)
        self.assertContains(self.client.get("/predictions/"), prediction.predicted_category)

    def test_batch_prediction_saves_successes_and_reports_missing_evaluations(self):
        trained_department, _ = Department.objects.get_or_create(name="Recruitment")
        self.employee.department = trained_department
        self.employee.save(update_fields=("department",))
        self.client.force_login(self.admin)

        response = self.client.post("/predictions/run/", {"action": "batch"})

        self.assertRedirects(response, "/predictions/")
        self.assertTrue(Prediction.objects.filter(employee=self.employee).exists())
        self.assertContains(self.client.get("/predictions/"), self.employee.name)

    def test_reports_export_filtered_csv_and_pdf(self):
        self.client.force_login(self.hr_user)

        csv_response = self.client.get(
            "/reports/export.csv",
            {"department": self.department.pk, "start_date": "2026-01-01", "end_date": "2026-01-31"},
        )
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("text/csv", csv_response["Content-Type"])
        self.assertIn(b"WEB-1001", csv_response.content)
        self.assertNotIn(b"WEB-1002", csv_response.content)

        pdf_response = self.client.get("/reports/export.pdf")
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        self.assertTrue(pdf_response.content.startswith(b"%PDF-1.4"))

    def test_accounts_and_settings_are_role_scoped_and_forms_work(self):
        self.client.force_login(self.hr_user)
        self.assertEqual(self.client.get("/accounts/").status_code, 403)

        self.client.force_login(self.admin)
        unlinked_employee = Employee.objects.create(
            employee_code="WEB-1003",
            name="Unlinked Employee",
            department=self.department,
            position="Analyst",
            date_hired=date(2025, 3, 1),
        )
        response = self.client.post(
            "/accounts/add/",
            {
                "name": "New Account",
                "email": "new-account@lexie.ph",
                "role": User.Role.employee,
                "employee": unlinked_employee.pk,
                "password1": "Strong-Test-Password-123!",
                "password2": "Strong-Test-Password-123!",
            },
        )
        self.assertRedirects(response, "/accounts/")
        created_user = User.objects.get(email="new-account@lexie.ph")
        unlinked_employee.refresh_from_db()
        self.assertEqual(created_user.role, User.Role.employee)
        self.assertEqual(unlinked_employee.user, created_user)

        self.client.force_login(self.employee_user)
        response = self.client.post(
            "/settings/",
            {"form_action": "profile", "name": "Updated Web Employee", "email": self.employee_user.email},
        )
        self.assertRedirects(response, "/settings/")
        self.employee_user.refresh_from_db()
        self.assertEqual(self.employee_user.name, "Updated Web Employee")
        self.assertEqual(self.client.get("/settings/").status_code, 200)

    def test_staff_role_is_treated_as_employee_for_private_pages(self):
        self.client.force_login(self.other_user)
        response = self.client.get("/performance/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Other Employee")
        self.assertNotContains(response, "Web Employee")

    def test_admin_can_open_all_server_rendered_pages(self):
        self.client.force_login(self.admin)
        evaluation = Evaluation.objects.filter(employee=self.employee).first()
        paths = (
            "/dashboard/",
            "/employees/",
            f"/employees/{self.employee.pk}/",
            f"/employees/{self.employee.pk}/edit/",
            f"/employees/{self.employee.pk}/delete/",
            "/employees/add/",
            "/departments/",
            "/departments/add/",
            f"/departments/{self.department.pk}/edit/",
            f"/departments/{self.department.pk}/delete/",
            "/performance/",
            "/performance/add/",
            f"/performance/{evaluation.pk}/edit/",
            "/evaluations/",
            "/evaluations/add/",
            "/evaluations/criteria/",
            f"/evaluations/{evaluation.pk}/",
            f"/evaluations/{evaluation.pk}/edit/",
            f"/evaluations/{evaluation.pk}/delete/",
            "/predictions/",
            "/reports/",
            "/reports/export.csv",
            "/reports/export.pdf",
            "/accounts/",
            "/accounts/add/",
            "/settings/",
        )
        for path in paths:
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)

    def test_admin_can_update_criteria_without_changing_the_database(self):
        self.client.force_login(self.admin)
        fields = {
            "productivity_score": "Output",
            "attendance_score": "Reliability",
            "quality_score": "Quality",
            "teamwork_score": "Team contribution",
            "communication_score": "Communication",
        }
        with tempfile.TemporaryDirectory() as directory:
            criteria_path = Path(directory) / "criteria.json"
            criteria_path.write_text(
                json.dumps(
                    {
                        "productivity_score": "Productivity",
                        "attendance_score": "Attendance",
                        "quality_score": "Quality of work",
                        "teamwork_score": "Teamwork",
                        "communication_score": "Communication",
                    }
                ),
                encoding="utf-8",
            )
            with patch("api.web_views.CRITERIA_PATH", criteria_path):
                response = self.client.post("/evaluations/criteria/", fields)
                self.assertRedirects(response, "/evaluations/criteria/")
                self.assertEqual(json.loads(criteria_path.read_text(encoding="utf-8")), fields)

    def test_password_can_be_changed_without_losing_the_session(self):
        self.client.force_login(self.employee_user)

        response = self.client.post(
            "/settings/",
            {
                "form_action": "password",
                "old_password": "WebTest123!",
                "new_password1": "New-WebPass-123!",
                "new_password2": "New-WebPass-123!",
            },
        )

        self.assertRedirects(response, "/settings/")
        self.employee_user.refresh_from_db()
        self.assertTrue(self.employee_user.check_password("New-WebPass-123!"))
        self.assertEqual(self.client.get("/settings/").status_code, 200)

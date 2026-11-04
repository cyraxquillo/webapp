from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.db import transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from django.utils import timezone

from api.firestore_sync import employee_document, evaluation_document, sync_outbox_entry
from api.models import Department, Employee, FirestoreSyncOutbox


class FirestorePayloadTests(SimpleTestCase):
    def test_employee_document_contains_only_approved_fields(self):
        employee = SimpleNamespace(
            employee_code="EMP-10",
            user_id=21,
            team_leader_id=8,
            name="Employee Name",
            department_id=4,
            department=SimpleNamespace(name="Operations"),
            position="Analyst",
            date_hired=date(2023, 5, 2),
            status="active",
            attendance_rate=98.5,
            tenure_months=41,
            training_hours=16.0,
            email="should-not-copy@example.test",
            password="should-not-copy",
            role="employee",
        )

        document = employee_document(employee)

        self.assertEqual(
            set(document),
            {
                "employee_code",
                "user_id",
                "team_leader_id",
                "name",
                "department_id",
                "department_name",
                "position",
                "date_hired",
                "status",
                "attendance_rate",
                "tenure_months",
                "training_hours",
            },
        )
        self.assertEqual(document["date_hired"], "2023-05-02")

    def test_evaluation_document_contains_no_user_credentials(self):
        evaluation = SimpleNamespace(
            employee_id=10,
            evaluator_id=8,
            assigned_to_id=8,
            employee_user_id=None,
            team_leader_id=8,
            date=date(2026, 10, 1),
            productivity_score=80.0,
            attendance_score=90.0,
            days_present=20,
            working_days=22,
            quality_score=80.0,
            teamwork_score=85.0,
            communication_score=75.0,
            comments="Review comments",
            status="completed",
            phase="finalized",
            team_leader_rating=82.0,
            management_rating=84.0,
            hybrid_score=83.0,
            cycle_month=date(2026, 10, 1),
            auto_score=81.0,
            auto_data={"attendance_rate": 90.0, "submission_count": 2},
            reminder_sent_at=None,
            created_at=timezone.now(),
            employee=SimpleNamespace(user_id=None, team_leader_id=8),
            email="should-not-copy@example.test",
            password="should-not-copy",
            role="management",
        )

        document = evaluation_document(evaluation)

        self.assertEqual(
            set(document),
            {
                "employee_id",
                "evaluator_id",
                "assigned_to_id",
                "employee_user_id",
                "team_leader_id",
                "date",
                "productivity_score",
                "attendance_score",
                "days_present",
                "working_days",
                "quality_score",
                "teamwork_score",
                "communication_score",
                "comments",
                "status",
                "phase",
                "team_leader_rating",
                "management_rating",
                "hybrid_score",
                "cycle_month",
                "auto_score",
                "auto_data",
                "reminder_sent_at",
                "created_at",
            },
        )
        self.assertEqual(document["date"], "2026-10-01")
        self.assertNotIn("email", document)
        self.assertNotIn("password", document)
        self.assertNotIn("role", document)


@override_settings(FIRESTORE_SYNC_ENABLED=True)
class FirestoreSyncTransactionTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.department = Department.objects.create(name="Sync Test")

    def _create_employee(self):
        return Employee.objects.create(
            employee_code="SYNC-EMP",
            name="Sync Employee",
            department=self.department,
            position="Analyst",
            date_hired=date(2024, 1, 1),
        )

    def test_committed_save_attempts_sync_after_commit(self):
        with patch("api.firestore_sync.sync_outbox_entry") as sync_entry:
            employee = self._create_employee()

        sync_entry.assert_called_once()
        queued = FirestoreSyncOutbox.objects.get(
            entity=FirestoreSyncOutbox.Entity.employee,
            record_id=employee.pk,
        )
        self.assertIsNone(queued.synced_at)
        self.assertTrue(
            FirestoreSyncOutbox.objects.filter(
                entity=FirestoreSyncOutbox.Entity.summaries,
                record_id=0,
                synced_at__isnull=True,
            ).exists()
        )

    def test_rolled_back_save_never_attempts_firestore_sync(self):
        sync_entry = Mock()
        with patch("api.firestore_sync.sync_outbox_entry", sync_entry):
            with self.assertRaises(RuntimeError):
                with transaction.atomic():
                    employee = self._create_employee()
                    employee_id = employee.pk
                    raise RuntimeError("rollback test")

        sync_entry.assert_not_called()
        self.assertFalse(
            FirestoreSyncOutbox.objects.filter(
                entity=FirestoreSyncOutbox.Entity.employee,
                record_id=employee_id,
            ).exists()
        )


@override_settings(FIRESTORE_SYNC_ENABLED=False)
class FirestoreSyncRetryTests(TransactionTestCase):
    reset_sequences = True

    def test_firestore_failure_is_queued_for_retry_without_raising(self):
        department = Department.objects.create(name="Retry Test")
        employee = Employee.objects.create(
            employee_code="RETRY-EMP",
            name="Retry Employee",
            department=department,
            position="Analyst",
            date_hired=date(2024, 1, 1),
        )
        outbox_entry = FirestoreSyncOutbox.objects.create(
            entity=FirestoreSyncOutbox.Entity.employee,
            record_id=employee.pk,
            operation=FirestoreSyncOutbox.Operation.upsert,
        )
        reference = Mock()
        reference.set.side_effect = RuntimeError("network unavailable")
        collection = Mock()
        collection.document.return_value = reference
        client = Mock()
        client.collection.return_value = collection

        with patch("api.firestore_sync.get_firestore_client", return_value=client):
            result = sync_outbox_entry(outbox_entry.pk)

        self.assertFalse(result)
        outbox_entry.refresh_from_db()
        self.assertIsNone(outbox_entry.synced_at)
        self.assertEqual(outbox_entry.attempts, 1)
        self.assertEqual(outbox_entry.last_error_type, "RuntimeError")
        self.assertGreater(outbox_entry.next_attempt_at, timezone.now())

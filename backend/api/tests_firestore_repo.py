from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from api import firestore_repo
from api.models import User


class FakeSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeDocument:
    def __init__(self, document_id, documents):
        self.id = document_id
        self.documents = documents

    def get(self):
        return FakeSnapshot(self.id, self.documents.get(self.id))

    def set(self, data):
        self.documents[self.id] = dict(data)

    def update(self, data):
        self.documents[self.id].update(data)

    def delete(self):
        self.documents.pop(self.id, None)


class FakeQuery:
    def __init__(self, documents):
        self.documents = documents
        self.filters = []
        self.max_results = None

    def where(self, *, field_path, op_string, value):
        self.filters.append((field_path, op_string, value))
        return self

    def limit(self, count):
        self.max_results = count
        return self

    def stream(self):
        results = [
            FakeSnapshot(document_id, data)
            for document_id, data in self.documents.items()
            if all(data.get(field) == value for field, _, value in self.filters)
        ]
        return results[: self.max_results]


class FakeCollection(FakeQuery):
    def __init__(self, documents):
        super().__init__(documents)
        self.counter = 0

    def document(self, document_id=None):
        if document_id is None:
            self.counter += 1
            document_id = f"new-{self.counter}"
        return FakeDocument(str(document_id), self.documents)


class FakeFirestore:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection({})
        return self.collections[name]


def actor(role, user_id):
    return SimpleNamespace(is_authenticated=True, role=role, pk=user_id)


@override_settings(USE_FIRESTORE=True)
class FirestoreRepositoryTests(SimpleTestCase):
    def setUp(self):
        self.client = FakeFirestore()
        self.client.collection("employees").documents.update(
            {
                "1": {"name": "Staff One", "user_id": 10, "team_leader_id": 30},
                "2": {"name": "Staff Two", "user_id": 20, "team_leader_id": 40},
            }
        )
        self.client.collection("evaluations").documents.update(
            {
                "11": {"employee_id": "1", "employee_user_id": 10, "team_leader_id": 30},
                "12": {"employee_id": "2", "employee_user_id": 20, "team_leader_id": 40},
            }
        )
        self.client_patcher = patch(
            "api.firestore_repo.get_firestore_client", return_value=self.client
        )
        self.client_patcher.start()
        self.addCleanup(self.client_patcher.stop)

    def test_staff_reads_only_their_employee_and_evaluations(self):
        staff = actor(User.Role.staff, 10)

        employees = firestore_repo.list_employees(staff)
        evaluations = firestore_repo.list_evaluations(staff)

        self.assertEqual([item["id"] for item in employees], ["1"])
        self.assertEqual([item["id"] for item in evaluations], ["11"])

    def test_team_leader_reads_only_their_team(self):
        leader = actor(User.Role.team_leader, 30)

        self.assertEqual(
            [item["id"] for item in firestore_repo.list_employees(leader)], ["1"]
        )
        self.assertEqual(
            [item["id"] for item in firestore_repo.list_evaluations(leader)], ["11"]
        )

    def test_management_reads_all_and_unknown_roles_fail_closed(self):
        management = actor(User.Role.management, 50)
        client_user = actor(User.Role.client, 60)

        self.assertEqual(len(firestore_repo.list_employees(management)), 2)
        self.assertEqual(firestore_repo.list_employees(client_user), [])
        self.assertIsNone(firestore_repo.get_employee("1", client_user))

    def test_employee_crud_uses_firestore(self):
        management = actor(User.Role.admin, 50)
        employee = firestore_repo.create_employee(
            {"name": "Created", "department_id": 7}, management
        )

        self.assertEqual(employee["id"], "new-1")
        updated = firestore_repo.update_employee(
            employee["id"], {"position": "Analyst"}, management
        )
        self.assertEqual(updated["position"], "Analyst")
        self.assertTrue(firestore_repo.delete_employee(employee["id"], management))
        self.assertIsNone(firestore_repo.get_employee(employee["id"], management))

    def test_evaluation_crud_and_owner_scope(self):
        leader = actor(User.Role.team_leader, 30)
        evaluation = firestore_repo.create_evaluation(
            {"employee_id": "1", "date": "2026-10-01", "status": "completed"},
            leader,
        )

        self.assertEqual(evaluation["employee_user_id"], 10)
        self.assertEqual(evaluation["evaluator_id"], 30)
        updated = firestore_repo.update_evaluation(
            evaluation["id"], {"comments": "Reviewed"}, leader
        )
        self.assertEqual(updated["comments"], "Reviewed")
        self.assertTrue(
            firestore_repo.delete_evaluation(
                evaluation["id"], actor(User.Role.management, 50)
            )
        )

    def test_unauthenticated_actor_cannot_read(self):
        anonymous = SimpleNamespace(is_authenticated=False, role=None, pk=None)

        self.assertEqual(firestore_repo.list_employees(anonymous), [])
        self.assertIsNone(firestore_repo.get_evaluation("11", anonymous))

    def test_employee_read_falls_back_to_scoped_sql_on_firestore_failure(self):
        staff = actor(User.Role.staff, 10)
        queryset = Mock()
        queryset.filter.return_value = queryset
        queryset.select_related.return_value = queryset
        queryset.first.return_value = SimpleNamespace(
            pk=1,
            employee_code="EMP-1",
            user_id=10,
            team_leader_id=30,
            name="Staff One",
            department_id=4,
            department=SimpleNamespace(name="Operations"),
            position="Analyst",
            date_hired=date(2025, 1, 1),
            status="active",
            attendance_rate=98.0,
            tenure_months=21,
            training_hours=12.0,
        )
        document = Mock()
        document.get.side_effect = RuntimeError("Firestore unavailable")
        collection = Mock()
        collection.document.return_value = document
        self.client.collection = Mock(return_value=collection)

        with patch("api.firestore_repo.employee_queryset_for", return_value=queryset):
            with self.assertLogs("api.firestore_repo", level="ERROR"):
                result = firestore_repo.get_employee("1", staff)

        self.assertEqual(result["id"], 1)
        self.assertEqual(result["user_id"], staff.pk)
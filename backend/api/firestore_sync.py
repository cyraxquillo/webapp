import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .firebase_client import get_firestore_client
from .models import Employee, Evaluation, FirestoreSyncOutbox


logger = logging.getLogger(__name__)

EMPLOYEE_FIELDS = (
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
)
EVALUATION_FIELDS = (
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
)


def _firestore_value(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _firestore_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_firestore_value(item) for item in value]
    return value


def employee_document(employee):
    return {
        "employee_code": employee.employee_code,
        "user_id": employee.user_id,
        "team_leader_id": employee.team_leader_id,
        "name": employee.name,
        "department_id": employee.department_id,
        "department_name": employee.department.name,
        "position": employee.position,
        "date_hired": employee.date_hired.isoformat(),
        "status": employee.status,
        "attendance_rate": employee.attendance_rate,
        "tenure_months": employee.tenure_months,
        "training_hours": employee.training_hours,
    }


def evaluation_document(evaluation):
    data = {field: getattr(evaluation, field) for field in EVALUATION_FIELDS if field not in {
        "employee_user_id",
        "team_leader_id",
    }}
    data["employee_user_id"] = evaluation.employee.user_id
    data["team_leader_id"] = evaluation.employee.team_leader_id
    return _firestore_value(data)


def _queue(entity, record_id, operation):
    entry, _ = FirestoreSyncOutbox.objects.update_or_create(
        entity=entity,
        record_id=record_id,
        defaults={
            "operation": operation,
            "attempts": 0,
            "next_attempt_at": timezone.now(),
            "last_error_type": "",
            "synced_at": None,
        },
    )
    return entry


def enqueue_record_sync(entity, record_id, operation):
    if not getattr(settings, "FIRESTORE_SYNC_ENABLED", False):
        return
    entry = _queue(entity, record_id, operation)
    _queue(FirestoreSyncOutbox.Entity.summaries, 0, FirestoreSyncOutbox.Operation.rebuild)
    transaction.on_commit(lambda entry_id=entry.pk: sync_outbox_entry(entry_id))


def _mark_failed(entry, error):
    entry.attempts += 1
    delay_minutes = min(2 ** min(entry.attempts, 10), 1440)
    entry.next_attempt_at = timezone.now() + timedelta(minutes=delay_minutes)
    entry.last_error_type = type(error).__name__[:120]
    entry.save(
        update_fields=("attempts", "next_attempt_at", "last_error_type", "updated_at")
    )
    logger.warning(
        "Firestore sync failed for entity=%s record_id=%s; queued for retry (error_type=%s)",
        entry.entity,
        entry.record_id,
        entry.last_error_type,
    )


def _mark_synced(entry):
    entry.synced_at = timezone.now()
    entry.attempts = 0
    entry.last_error_type = ""
    entry.save(
        update_fields=("synced_at", "attempts", "last_error_type", "updated_at")
    )


def sync_outbox_entry(entry_id):
    entry = FirestoreSyncOutbox.objects.filter(pk=entry_id, synced_at__isnull=True).first()
    if entry is None:
        return True

    try:
        client = get_firestore_client()
        if entry.entity == FirestoreSyncOutbox.Entity.summaries:
            rebuild_dashboard_summaries(client)
        else:
            collection_name = entry.entity + "s"
            reference = client.collection(collection_name).document(str(entry.record_id))
            if entry.operation == FirestoreSyncOutbox.Operation.delete:
                reference.delete(timeout=8)
            elif entry.entity == FirestoreSyncOutbox.Entity.employee:
                employee = Employee.objects.select_related("department").filter(
                    pk=entry.record_id
                ).first()
                if employee is None:
                    reference.delete(timeout=8)
                else:
                    reference.set(employee_document(employee), timeout=8)
            elif entry.entity == FirestoreSyncOutbox.Entity.evaluation:
                evaluation = Evaluation.objects.select_related(
                    "employee__department"
                ).filter(pk=entry.record_id).first()
                if evaluation is None:
                    reference.delete(timeout=8)
                else:
                    reference.set(evaluation_document(evaluation), timeout=8)
            else:
                raise ValueError(f"Unsupported outbox entity: {entry.entity}")
        _mark_synced(entry)
        return True
    except Exception as error:
        try:
            _mark_failed(entry, error)
        except Exception as outbox_error:
            logger.error(
                "Could not record Firestore retry state for entity=%s record_id=%s "
                "(error_type=%s)",
                entry.entity,
                entry.record_id,
                type(outbox_error).__name__,
            )
        return False


def sync_pending_outbox(batch_size=100, include_retries=False):
    now = timezone.now()
    pending = FirestoreSyncOutbox.objects.filter(synced_at__isnull=True)
    if not include_retries:
        pending = pending.filter(next_attempt_at__lte=now)
    entries = list(pending.order_by("created_at", "pk")[:batch_size])
    succeeded = failed = 0
    for entry in entries:
        if sync_outbox_entry(entry.pk):
            succeeded += 1
        else:
            failed += 1
    return {"processed": len(entries), "succeeded": succeeded, "failed": failed}


def _summary_data(employees, latest_by_employee, recent_evaluations):
    scores = [
        latest_by_employee[employee.pk].overall_score
        for employee in employees
        if employee.pk in latest_by_employee
        and latest_by_employee[employee.pk].overall_score is not None
    ]
    return {
        "active_employee_count": len(employees),
        "scored_employee_count": len(scores),
        "average_latest_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "at_risk_count": sum(score < 70 for score in scores),
        "recent_evaluation_ids": [str(item.pk) for item in recent_evaluations[:5]],
        "rebuilt_at": timezone.now().isoformat(),
    }


def rebuild_dashboard_summaries(client=None):
    client = client or get_firestore_client()
    employees = list(
        Employee.objects.filter(status=Employee.Status.active).select_related("team_leader")
    )
    employee_ids = {employee.pk for employee in employees}
    evaluations = list(
        Evaluation.objects.filter(
            employee_id__in=employee_ids,
            status=Evaluation.Status.completed,
        ).select_related("employee").order_by("-date", "-pk")
    )
    latest_by_employee = {}
    for evaluation in evaluations:
        latest_by_employee.setdefault(evaluation.employee_id, evaluation)

    global_summary = _summary_data(employees, latest_by_employee, evaluations)
    collection = client.collection("dashboard_summaries")
    collection.document("global").set(global_summary, timeout=8)
    team_members = defaultdict(list)
    for employee in employees:
        if employee.team_leader_id is not None:
            team_members[employee.team_leader_id].append(employee)

    expected_ids = {"global"}
    for team_leader_id, members in team_members.items():
        member_ids = {employee.pk for employee in members}
        recent = [item for item in evaluations if item.employee_id in member_ids]
        document_id = f"team_{team_leader_id}"
        expected_ids.add(document_id)
        collection.document(document_id).set(
            _summary_data(members, latest_by_employee, recent),
            timeout=8,
        )

    deleted = 0
    for snapshot in collection.stream():
        if snapshot.id.startswith("team_") and snapshot.id not in expected_ids:
            collection.document(snapshot.id).delete(timeout=8)
            deleted += 1
    return {"written": len(expected_ids), "deleted_stale_teams": deleted}
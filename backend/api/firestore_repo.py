import logging
from datetime import date, datetime
from uuid import uuid4

from django.conf import settings

from .access import (
    TEAM_ROLES,
    employee_queryset_for,
    evaluation_queryset_for,
    firestore_scope_for,
    is_management,
)
from .firebase_client import get_firestore_client
from .models import Employee, Evaluation


logger = logging.getLogger(__name__)

EMPLOYEES = "employees"
EVALUATIONS = "evaluations"
EMPLOYEE_FIELDS = {
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
}
EVALUATION_FIELDS = {
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
}
EMPLOYEE_FILTERS = {"status", "department_id"}
EVALUATION_FILTERS = {"employee_id", "status"}


def _firestore_client():
    if not getattr(settings, "USE_FIRESTORE", False):
        return None
    try:
        return get_firestore_client()
    except Exception:
        logger.exception("Firestore is unavailable; using the SQL repository fallback")
        return None


def _filter_values(filters, allowed_fields):
    filters = filters or {}
    unknown = set(filters) - allowed_fields
    if unknown:
        raise ValueError(f"Unsupported repository filter(s): {', '.join(sorted(unknown))}")
    return filters


def _apply_firestore_scope(query, scope):
    for field, value in scope.items():
        query = query.where(field_path=field, op_string="==", value=value)
    return query


def _scoped_document_data(snapshot, scope):
    if not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    if any(data.get(field) != value for field, value in scope.items()):
        return None
    return {"id": snapshot.id, **data}


def _firestore_values(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _firestore_values(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_firestore_values(item) for item in value]
    return value


def _serialize_employee(employee):
    return {
        "id": employee.pk,
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


def _serialize_evaluation(evaluation):
    fields = (
        "employee_id",
        "evaluator_id",
        "assigned_to_id",
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
    data = {field: getattr(evaluation, field) for field in fields}
    data["id"] = evaluation.pk
    data["employee_user_id"] = evaluation.employee.user_id
    data["team_leader_id"] = evaluation.employee.team_leader_id
    return _firestore_values(data)


def _limit(queryset, limit):
    if limit is not None:
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        queryset = queryset[:limit]
    return queryset


def _sql_employee_values(values):
    return {key: value for key, value in values.items() if key != "department_name"}


def get_employee(employee_id, actor):
    scope = firestore_scope_for(actor, EMPLOYEES)
    if scope is None:
        return None
    client = _firestore_client()
    if client is None:
        employee = employee_queryset_for(actor).filter(pk=employee_id).select_related("department").first()
        return _serialize_employee(employee) if employee else None
    try:
        snapshot = client.collection(EMPLOYEES).document(str(employee_id)).get()
        return _scoped_document_data(snapshot, scope)
    except Exception:
        logger.exception("Firestore employee read failed; using the SQL repository fallback")
        employee = employee_queryset_for(actor).filter(pk=employee_id).select_related("department").first()
        return _serialize_employee(employee) if employee else None


def list_employees(actor, filters=None, limit=None):
    scope = firestore_scope_for(actor, EMPLOYEES)
    if scope is None:
        return []
    filters = _filter_values(filters, EMPLOYEE_FILTERS)
    client = _firestore_client()
    if client is None:
        queryset = employee_queryset_for(actor).select_related("department")
        queryset = queryset.filter(**filters)
        return [_serialize_employee(item) for item in _limit(queryset, limit)]
    try:
        query = _apply_firestore_scope(client.collection(EMPLOYEES), scope)
        for field, value in filters.items():
            query = query.where(field_path=field, op_string="==", value=value)
        if limit is not None:
            if not isinstance(limit, int) or limit < 1:
                raise ValueError("limit must be a positive integer")
            query = query.limit(limit)
        return [{"id": item.id, **(item.to_dict() or {})} for item in query.stream()]
    except Exception:
        logger.exception("Firestore employee query failed; using the SQL repository fallback")
        queryset = employee_queryset_for(actor).select_related("department").filter(**filters)
        return [_serialize_employee(item) for item in _limit(queryset, limit)]


def create_employee(data, actor):
    if not is_management(actor):
        raise PermissionError("Only management roles can create employees")
    values = {key: value for key, value in data.items() if key in EMPLOYEE_FIELDS}
    client = _firestore_client()
    if client is None:
        employee = Employee.objects.create(**_sql_employee_values(values))
        employee = Employee.objects.select_related("department").get(pk=employee.pk)
        return _serialize_employee(employee)
    values.setdefault("employee_code", f"EMP-{uuid4().hex[:12].upper()}")
    try:
        reference = client.collection(EMPLOYEES).document()
        reference.set(_firestore_values(values))
        return {"id": reference.id, **_firestore_values(values)}
    except Exception:
        logger.exception("Firestore employee create failed; using the SQL repository fallback")
        employee = Employee.objects.create(**_sql_employee_values(values))
        employee = Employee.objects.select_related("department").get(pk=employee.pk)
        return _serialize_employee(employee)


def update_employee(employee_id, data, actor):
    if actor.role not in TEAM_ROLES:
        raise PermissionError("Only team or management roles can update employees")
    employee = get_employee(employee_id, actor)
    if employee is None:
        return None
    values = {key: value for key, value in data.items() if key in EMPLOYEE_FIELDS}
    if not is_management(actor):
        values = {
            key: value
            for key, value in values.items()
            if key not in {"user_id", "team_leader_id", "department_id", "department_name"}
        }
    client = _firestore_client()
    if client is None:
        instance = employee_queryset_for(actor).filter(pk=employee_id).first()
        if instance is None:
            return None
        for field, value in _sql_employee_values(values).items():
            setattr(instance, field, value)
        instance.save()
        return _serialize_employee(
            Employee.objects.select_related("department").get(pk=instance.pk)
        )
    try:
        reference = client.collection(EMPLOYEES).document(str(employee_id))
        reference.update(_firestore_values(values))
        return {**employee, **_firestore_values(values)}
    except Exception:
        logger.exception("Firestore employee update failed; using the SQL repository fallback")
        instance = employee_queryset_for(actor).filter(pk=employee_id).first()
        if instance is None:
            return None
        for field, value in _sql_employee_values(values).items():
            setattr(instance, field, value)
        instance.save()
        return _serialize_employee(
            Employee.objects.select_related("department").get(pk=instance.pk)
        )


def delete_employee(employee_id, actor):
    if not is_management(actor):
        raise PermissionError("Only management roles can delete employees")
    employee = get_employee(employee_id, actor)
    if employee is None:
        return False
    client = _firestore_client()
    if client is None:
        instance = employee_queryset_for(actor).filter(pk=employee_id).first()
        if instance is None:
            return False
        instance.delete()
        return True
    try:
        client.collection(EMPLOYEES).document(str(employee_id)).delete()
        return True
    except Exception:
        logger.exception("Firestore employee delete failed; using the SQL repository fallback")
        instance = employee_queryset_for(actor).filter(pk=employee_id).first()
        if instance is None:
            return False
        instance.delete()
        return True


def get_evaluation(evaluation_id, actor):
    scope = firestore_scope_for(actor, EVALUATIONS)
    if scope is None:
        return None
    client = _firestore_client()
    if client is None:
        evaluation = evaluation_queryset_for(actor).filter(pk=evaluation_id).select_related(
            "employee__department"
        ).first()
        return _serialize_evaluation(evaluation) if evaluation else None
    try:
        snapshot = client.collection(EVALUATIONS).document(str(evaluation_id)).get()
        return _scoped_document_data(snapshot, scope)
    except Exception:
        logger.exception("Firestore evaluation read failed; using the SQL repository fallback")
        evaluation = evaluation_queryset_for(actor).filter(pk=evaluation_id).select_related(
            "employee__department"
        ).first()
        return _serialize_evaluation(evaluation) if evaluation else None


def list_evaluations(actor, filters=None, limit=None):
    scope = firestore_scope_for(actor, EVALUATIONS)
    if scope is None:
        return []
    filters = _filter_values(filters, EVALUATION_FILTERS)
    client = _firestore_client()
    if client is None:
        queryset = evaluation_queryset_for(actor).select_related("employee__department")
        queryset = queryset.filter(**filters)
        return [_serialize_evaluation(item) for item in _limit(queryset, limit)]
    try:
        query = _apply_firestore_scope(client.collection(EVALUATIONS), scope)
        for field, value in filters.items():
            query = query.where(field_path=field, op_string="==", value=value)
        if limit is not None:
            if not isinstance(limit, int) or limit < 1:
                raise ValueError("limit must be a positive integer")
            query = query.limit(limit)
        return [{"id": item.id, **(item.to_dict() or {})} for item in query.stream()]
    except Exception:
        logger.exception("Firestore evaluation query failed; using the SQL repository fallback")
        queryset = evaluation_queryset_for(actor).select_related("employee__department").filter(
            **filters
        )
        return [_serialize_evaluation(item) for item in _limit(queryset, limit)]


def create_evaluation(data, actor):
    if actor.role not in TEAM_ROLES:
        raise PermissionError("Only team or management roles can create evaluations")
    values = {key: value for key, value in data.items() if key in EVALUATION_FIELDS}
    employee = get_employee(values.get("employee_id"), actor)
    if employee is None:
        raise ValueError("Employee is not available to this user")
    values["evaluator_id"] = actor.pk
    values["employee_user_id"] = employee.get("user_id")
    values["team_leader_id"] = employee.get("team_leader_id")
    client = _firestore_client()
    if client is None:
        values.pop("employee_user_id", None)
        values.pop("team_leader_id", None)
        evaluation = Evaluation.objects.create(**values)
        return _serialize_evaluation(
            Evaluation.objects.select_related("employee__department").get(pk=evaluation.pk)
        )
    try:
        reference = client.collection(EVALUATIONS).document()
        reference.set(_firestore_values(values))
        return {"id": reference.id, **_firestore_values(values)}
    except Exception:
        logger.exception("Firestore evaluation create failed; using the SQL repository fallback")
        values.pop("employee_user_id", None)
        values.pop("team_leader_id", None)
        evaluation = Evaluation.objects.create(**values)
        return _serialize_evaluation(
            Evaluation.objects.select_related("employee__department").get(pk=evaluation.pk)
        )


def update_evaluation(evaluation_id, data, actor):
    if actor.role not in TEAM_ROLES:
        raise PermissionError("Only team or management roles can update evaluations")
    evaluation = get_evaluation(evaluation_id, actor)
    if evaluation is None:
        return None
    values = {key: value for key, value in data.items() if key in EVALUATION_FIELDS}
    for immutable_field in (
        "employee_id",
        "evaluator_id",
        "employee_user_id",
        "team_leader_id",
    ):
        values.pop(immutable_field, None)
    client = _firestore_client()
    if client is None:
        instance = evaluation_queryset_for(actor).filter(pk=evaluation_id).first()
        if instance is None:
            return None
        for field, value in values.items():
            setattr(instance, field, value)
        instance.save()
        return _serialize_evaluation(
            Evaluation.objects.select_related("employee__department").get(pk=instance.pk)
        )
    try:
        client.collection(EVALUATIONS).document(str(evaluation_id)).update(
            _firestore_values(values)
        )
        return get_evaluation(evaluation_id, actor)
    except Exception:
        logger.exception("Firestore evaluation update failed; using the SQL repository fallback")
        instance = evaluation_queryset_for(actor).filter(pk=evaluation_id).first()
        if instance is None:
            return None
        for field, value in values.items():
            setattr(instance, field, value)
        instance.save()
        return _serialize_evaluation(
            Evaluation.objects.select_related("employee__department").get(pk=instance.pk)
        )


def delete_evaluation(evaluation_id, actor):
    if not is_management(actor):
        raise PermissionError("Only management roles can delete evaluations")
    evaluation = get_evaluation(evaluation_id, actor)
    if evaluation is None:
        return False
    client = _firestore_client()
    if client is None:
        instance = evaluation_queryset_for(actor).filter(pk=evaluation_id).first()
        if instance is None:
            return False
        instance.delete()
        return True
    try:
        client.collection(EVALUATIONS).document(str(evaluation_id)).delete()
        return True
    except Exception:
        logger.exception("Firestore evaluation delete failed; using the SQL repository fallback")
        instance = evaluation_queryset_for(actor).filter(pk=evaluation_id).first()
        if instance is None:
            return False
        instance.delete()
        return True
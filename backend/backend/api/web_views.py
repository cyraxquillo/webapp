from functools import wraps
import csv
import json
import logging
from pathlib import Path
from collections import defaultdict
from datetime import date

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .access import (
    MANAGEMENT_ROLES,
    TEAM_ROLES,
    VIEWER_ROLES,
    employee_queryset_for,
    evaluation_queryset_for,
    is_management,
    is_staff_user,
    prediction_queryset_for,
    submission_queryset_for,
)
from .forms import (
    AccountEditForm,
    AccountCreateForm,
        AutomationSettingsForm,
    CurrentPasswordChangeForm,
    DepartmentForm,
    EmployeeForm,
    EmployeeStatusForm,
    EvaluationForm,
    NotificationPreferencesForm,
    NotificationComposeForm,
    ProfileForm,
    SubmissionForm,
    SubmissionReviewForm,
)
from .models import Client, Department, Employee, Evaluation, Prediction, Submission, User
from .models import (
    AutomationSettings,
    Department,
    Employee,
    Evaluation,
    Notification,
    Prediction,
    Submission,
    User,
)
from .automation import create_notification
from .login_throttling import (
    LOGIN_THROTTLE_MESSAGE,
    clear_web_login_failures,
    is_web_login_throttled,
    record_web_login_failure,
)


logger = logging.getLogger(__name__)
EMPLOYEE_ROLES = (User.Role.staff, User.Role.employee)
WORKFORCE_ROLES = MANAGEMENT_ROLES
CRITERIA_PATH = Path(__file__).resolve().parent / "criteria.json"


def employee_scope(user):
    return employee_queryset_for(user)


def evaluation_scope(user):
    return evaluation_queryset_for(user)


def prediction_scope(user):
    return prediction_queryset_for(user)


def latest_prediction_map(user):
    latest = {}
    for prediction in prediction_scope(user).select_related("employee").order_by(
        "-created_at", "-id"
    ):
        latest.setdefault(prediction.employee_id, prediction)
    return latest


def attach_latest_predictions(user, employees):
    latest = latest_prediction_map(user)
    for employee in employees:
        employee.latest_prediction = latest.get(employee.pk)
    return employees


def department_scope(user):
    departments = Department.objects.filter(is_active=True)
    if not is_management(user):
        departments = departments.filter(employees__in=employee_scope(user)).distinct()
    return departments


def parse_period_filters(request, queryset):
    start_date = request.GET.get("start_date", "").strip()
    end_date = request.GET.get("end_date", "").strip()
    for field_name, value in (("start date", start_date), ("end date", end_date)):
        if value:
            try:
                date.fromisoformat(value)
            except ValueError:
                messages.error(request, f"Enter a valid {field_name}.")
                return queryset.none()
    if start_date:
        queryset = queryset.filter(date__gte=start_date)
    if end_date:
        queryset = queryset.filter(date__lte=end_date)
    if start_date and end_date and start_date > end_date:
        messages.error(request, "The start date must be on or before the end date.")
        return queryset.none()
    return queryset


def table_rows(objects, fields, detail_name, detail_kwarg="pk"):
    result = []
    for obj in objects:
        cells = []
        for field in fields:
            value = getattr(obj, field)
            if field == "is_active":
                value = "Active" if value else "Inactive"
            elif field == "status":
                value = str(value).replace("_", " ").title()
            elif field == "employee_count":
                value = str(value)
            elif hasattr(value, "name"):
                value = value.name
            cells.append(value if value is not None else "—")
        result.append(
            {
                "cells": cells,
                "url": reverse(detail_name, kwargs={detail_kwarg: obj.pk}),
            }
        )
    return result


def home(request):
    if request.user.is_authenticated:
        if request.user.role == User.Role.client:
            return redirect("client-portal")
        return redirect("dashboard")
    return redirect("web-login")


@login_required
def client_portal(request):
    if request.user.role != User.Role.client:
        return HttpResponseForbidden("You do not have permission to view the client portal.")
    client = get_object_or_404(Client, user=request.user)
    return render(request, "api/client_portal.html", {"client": client})


def login_view(request):
    if request.user.is_authenticated:
        if request.user.must_change_password:
            return redirect("settings")
        if request.user.role == User.Role.client:
            return redirect("client-portal")
        return redirect("dashboard")

    email = ""
    if request.method == "POST":
        if is_web_login_throttled(request):
            messages.error(request, LOGIN_THROTTLE_MESSAGE)
            return render(request, "api/login.html", {"email": request.POST.get("email", "")})
        email = request.POST.get("email", "").strip().lower()
        password = request.POST.get("password", "")
        user = authenticate(request, email=email, password=password)
        if user is not None:
            clear_web_login_failures(request)
            login(request, user)
            next_url = request.POST.get("next") or request.GET.get("next", "")
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            if user.must_change_password:
                return redirect("/settings/?tab=security&force=1")
            if user.role == User.Role.client:
                return redirect("client-portal")
            return redirect("dashboard")
        record_web_login_failure(request)
        messages.error(request, "Email or password was not recognized.")

    return render(request, "api/login.html", {"email": email})


@login_required
def logout_view(request):
    if request.method != "POST":
        return HttpResponseForbidden("Sign out requires a POST request.")
    logout(request)
    return redirect("web-login")


def roles_required(*roles):
    def decorator(view_func):
        @login_required
        @wraps(view_func)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in roles:
                return HttpResponseForbidden("You do not have permission to view this page.")
            return view_func(request, *args, **kwargs)

        return wrapped

    return decorator


@roles_required(
    User.Role.admin,
    User.Role.management,
    User.Role.hr_manager,
    User.Role.team_leader,
    User.Role.staff,
    User.Role.employee,
)
def dashboard(request):
    if is_staff_user(request.user):
        employee = (
            Employee.objects.select_related("department")
            .filter(user=request.user)
            .first()
        )
        evaluations = (
            Evaluation.objects.filter(employee=employee).order_by("-date", "-id")
            if employee
            else Evaluation.objects.none()
        )
        latest_evaluation = evaluations.first()
        predictions = Prediction.objects.filter(employee=employee) if employee else Prediction.objects.none()
        latest_prediction = predictions.order_by("-created_at", "-id").first()
        employee_trend = defaultdict(list)
        for evaluation in evaluations:
            employee_trend[evaluation.date.strftime("%Y-%m")].append(evaluation.overall_score)
        trend_labels = sorted(employee_trend)
        trend_values = [
            round(sum(employee_trend[month]) / len(employee_trend[month]), 2)
            for month in trend_labels
        ]
        return render(
            request,
            "api/dashboard.html",
            {
                "is_employee": True,
                "employee": employee,
                "evaluation_count": evaluations.count(),
                "latest_evaluation": latest_evaluation,
                "prediction_count": predictions.count(),
                                "latest_prediction": latest_prediction,
                "trend_labels": trend_labels,
                "trend_values": trend_values,
            },
        )

    active_employees = list(
        employee_scope(request.user).filter(status="active").order_by("name")
    )
    attach_latest_predictions(request.user, active_employees)
    department_count = department_scope(request.user).count()
    all_evaluations = list(
        evaluation_scope(request.user)
        .filter(status=Evaluation.Status.completed)
        .exclude(productivity_score__isnull=True)
        .order_by("-date", "-id")
    )
    latest_by_employee = {}
    monthly_scores = defaultdict(list)
    department_scores = defaultdict(list)
    for evaluation in all_evaluations:
        latest_by_employee.setdefault(evaluation.employee_id, evaluation)
        monthly_scores[evaluation.date.strftime("%Y-%m")].append(evaluation.overall_score)

    for employee in active_employees:
        latest = latest_by_employee.get(employee.pk)
        employee.latest_evaluation = latest
        employee.dashboard_score = latest.overall_score if latest else None
        if latest:
            department_scores[employee.department.name].append(latest.overall_score)

    scored_employees = [employee for employee in active_employees if employee.dashboard_score is not None]
    top_performers = sorted(
        scored_employees,
        key=lambda employee: employee.dashboard_score,
        reverse=True,
    )[:5]
    at_risk_employees = [
        employee for employee in scored_employees if employee.dashboard_score < 70
    ][:5]
    average_score = (
        round(sum(employee.dashboard_score for employee in scored_employees) / len(scored_employees), 2)
        if scored_employees
        else 0
    )
    department_labels = sorted(department_scores)
    department_averages = [
        round(sum(department_scores[name]) / len(department_scores[name]), 2)
        for name in department_labels
    ]
    trend_labels = sorted(monthly_scores)
    trend_values = [
        round(sum(monthly_scores[month]) / len(monthly_scores[month]), 2)
        for month in trend_labels
    ]
    evaluation_count = len(all_evaluations)
    prediction_count = prediction_scope(request.user).count()
    return render(
        request,
        "api/dashboard.html",
        {
            "is_employee": False,
            "active_employee_count": len(active_employees),
            "department_count": department_count,
            "evaluation_count": evaluation_count,
            "prediction_count": prediction_count,
            "average_score": average_score,
            "at_risk_count": len(at_risk_employees),
            "top_performers": top_performers,
            "at_risk_employees": at_risk_employees,
            "recent_evaluations": all_evaluations[:5],
            "department_labels": department_labels,
            "department_averages": department_averages,
            "is_team_leader": request.user.role == User.Role.team_leader,
            "trend_labels": trend_labels,
            "trend_values": trend_values,
            "pending_evaluations": Evaluation.objects.filter(
                assigned_to=request.user,
                status=Evaluation.Status.pending,
            ).select_related("employee", "employee__department").order_by("date", "employee__name")
            if request.user.role == User.Role.team_leader
            else [],
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def department_list(request):
    departments = Department.objects.annotate(employee_count=Count("employees"))
    search = request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "")
    if search:
        departments = departments.filter(
            Q(name__icontains=search) | Q(description__icontains=search)
        )
    if status_filter in {"active", "inactive"}:
        departments = departments.filter(is_active=status_filter == "active")
    return render(
        request,
        "api/list.html",
        {
            "title": "Departments",
            "description": "Organize your workforce by department.",
            "columns": ("name", "description", "employee_count", "is_active"),
            "headers": ("Department", "Description", "Employees", "Status"),
            "rows": table_rows(
                departments,
                ("name", "description", "employee_count", "is_active"),
                "department-edit",
            ),
            "column_labels": {
                "name": "Department",
                "description": "Description",
                "employee_count": "Employees",
                "is_active": "Status",
            },
            "search": search,
            "status_filter": status_filter,
            "create_url": "department-create",
            "empty_message": "No departments match your filters.",
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def department_create(request):
    form = DepartmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("name", "A department with this name already exists.")
        else:
            if form.department_warning:
                messages.warning(request, form.department_warning)
            messages.success(request, "Department created.")
            return redirect("department-list")
    return render(
        request,
        "api/form.html",
        {"title": "Add department", "form": form, "cancel_url": "department-list"},
    )


@roles_required(*MANAGEMENT_ROLES)
def department_edit(request, pk):
    department = get_object_or_404(Department, pk=pk)
    form = DepartmentForm(request.POST or None, instance=department)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("name", "A department with this name already exists.")
        else:
            if form.department_warning:
                messages.warning(request, form.department_warning)
            messages.success(request, "Department updated.")
            return redirect("department-list")
    return render(
        request,
        "api/form.html",
        {"title": f"Edit {department.name}", "form": form, "cancel_url": "department-list"},
    )


@roles_required(*MANAGEMENT_ROLES)
def department_delete(request, pk):
    department = get_object_or_404(Department, pk=pk)
    if request.method == "POST":
        try:
            department.delete()
        except IntegrityError:
            messages.error(
                request,
                "This department still has employees and cannot be deleted. Reassign them first.",
            )
        else:
            messages.success(request, "Department deleted.")
        return redirect("department-list")
    return render(
        request,
        "api/confirm_delete.html",
        {
            "title": "Delete department",
            "object_label": department.name,
            "cancel_url": "department-list",
            "delete_url": "department-delete",
            "pk": department.pk,
        },
    )


@login_required
def employee_list(request):
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view employees.")
    employees = employee_scope(request.user).select_related("department", "team_leader")
    search = request.GET.get("q", "").strip()
    department_id = request.GET.get("department", "").strip()
    status_filter = request.GET.get("status", "").strip()
    if search:
        employees = employees.filter(
            Q(name__icontains=search)
            | Q(employee_code__icontains=search)
            | Q(position__icontains=search)
        )
    if department_id.isdigit():
        employees = employees.filter(department_id=int(department_id))
    if status_filter in {"active", "inactive"}:
        employees = employees.filter(status=status_filter)
    employees = list(employees.order_by("name"))
    attach_latest_predictions(request.user, employees)
    rows = table_rows(
        employees,
        ("employee_code", "name", "department", "position", "status"),
        "employee-profile",
    )
    for row, employee in zip(rows, employees):
        row["employee"] = employee
    can_manage = is_management(request.user)
    return render(
        request,
        "api/list.html",
        {
            "title": "Employees" if can_manage else "My team" if request.user.role == User.Role.team_leader else "My profile",
            "description": "Search and manage employee profiles."
            if can_manage
            else "Your employee profile.",
            "columns": ("employee_code", "name", "department", "position", "status"),
            "headers": ("Employee ID", "Employee", "Department", "Position", "Status"),
            "rows": rows,
            "column_labels": {
                "employee_code": "Employee ID",
                "name": "Employee",
                "department": "Department",
                "position": "Position",
                "status": "Status",
            },
            "search": search,
            "department_filter": department_id,
            "status_filter": status_filter,
            "departments": department_scope(request.user),
            "create_url": "employee-create" if can_manage else "",
            "empty_message": "No employee profiles match your filters.",
        },
    )


@roles_required(User.Role.team_leader)
def my_team(request):
    employees = list(employee_scope(request.user).order_by("name"))
    attach_latest_predictions(request.user, employees)
    latest_by_employee = {}
    for evaluation in evaluation_scope(request.user).order_by("-date", "-id"):
        latest_by_employee.setdefault(evaluation.employee_id, evaluation)
    for employee in employees:
        employee.latest_evaluation = latest_by_employee.get(employee.pk)
    return render(request, "api/my_team.html", {"employees": employees})


@roles_required(*MANAGEMENT_ROLES)
def employee_create(request):
    form = EmployeeForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error("employee_code", "That employee ID is already in use.")
        else:
            if form.department_warning:
                messages.warning(request, form.department_warning)
            messages.success(request, "Employee created.")
            return redirect("employee-list")
    return render(
        request,
        "api/form.html",
        {"title": "Add employee", "form": form, "cancel_url": "employee-list"},
    )


@roles_required(*TEAM_ROLES)
def employee_edit(request, pk):
    employee = get_object_or_404(employee_scope(request.user), pk=pk)
    form = EmployeeForm(request.POST or None, instance=employee, actor=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("employee_code", "The employee ID or linked account is already in use.")
        else:
            if form.department_warning:
                messages.warning(request, form.department_warning)
            messages.success(request, "Employee profile updated.")
            return redirect("employee-profile", pk=employee.pk)
    return render(
        request,
        "api/employee_edit.html",
        {
            "title": f"Edit {employee.name}",
            "form": form,
            "employee": employee,
            "cancel_url": "employee-profile",
            "cancel_pk": employee.pk,
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def employee_status(request, pk):
    employee = get_object_or_404(employee_scope(request.user), pk=pk)
    form = EmployeeStatusForm(request.POST or None, instance=employee)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Employee status changed to {employee.get_status_display()}.")
        return redirect("employee-profile", pk=employee.pk)
    return render(
        request,
        "api/employee_status.html",
        {"form": form, "employee": employee},
    )


@login_required
def employee_profile(request, pk):
    employee = get_object_or_404(employee_scope(request.user), pk=pk)
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view this profile.")
    evaluations = evaluation_scope(request.user).filter(employee=employee)
    predictions = prediction_scope(request.user).filter(employee=employee)
    employee.latest_prediction = predictions.order_by("-created_at", "-id").first()
    return render(
        request,
        "api/employee_profile.html",
        {
            "employee": employee,
            "evaluations": evaluations[:10],
            "predictions": predictions[:5],
            "can_manage": request.user.role in TEAM_ROLES,
            "can_delete": is_management(request.user),
        },
    )


@login_required
def submission_list(request):
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view submissions.")
    submissions = submission_queryset_for(request.user)
    return render(
        request,
        "api/submission_list.html",
        {
            "submissions": submissions,
            "can_review": request.user.role in TEAM_ROLES,
        },
    )


@login_required
def submission_create(request):
    if not is_staff_user(request.user):
        messages.error(request, "Only employees can upload work submissions.")
        return redirect("submission-list")
    employee = get_object_or_404(Employee, user=request.user)
    form = SubmissionForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        submission = form.save(commit=False)
        submission.employee = employee
        submission.save()
        messages.success(request, "Work submission uploaded.")
        return redirect("submission-list")
    return render(
        request,
        "api/submission_form.html",
        {
            "title": "Upload Work",
            "form": form,
            "cancel_url": "submission-list",
        },
    )


@roles_required(*TEAM_ROLES)
def submission_review(request, pk):
    submission = get_object_or_404(submission_queryset_for(request.user), pk=pk)
    if (
        request.method == "GET"
        and request.user.role == User.Role.team_leader
        and submission.status == Submission.Status.pending
    ):
        submission.status = Submission.Status.under_review
        submission.save(update_fields=("status",))
    if request.method == "POST":
        review_form = SubmissionReviewForm(request.POST)
        if review_form.is_valid():
            submission.status = review_form.cleaned_data["status"]
            submission.reviewer_comment = review_form.cleaned_data["reviewer_comment"]
            submission.save(update_fields=("status", "reviewer_comment"))
            messages.success(request, "Submission review saved.")
            return redirect("submission-list")
    else:
        review_form = SubmissionReviewForm(
            initial={
                "status": submission.status,
                "reviewer_comment": submission.reviewer_comment,
            }
        )
    return render(
        request,
        "api/submission_review.html",
        {
            "title": f"Review {submission.title}",
            "submission": submission,
            "form": review_form,
            "cancel_url": "submission-list",
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def employee_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        employee.delete()
        messages.success(request, f"{employee.name}'s profile and associated records were deleted.")
        return redirect("employee-list")
    return render(
        request,
        "api/confirm_delete.html",
        {
            "title": "Delete employee",
            "object_label": employee.name,
            "cancel_url": "employee-profile",
            "cancel_pk": employee.pk,
            "delete_url": "employee-delete",
            "pk": employee.pk,
        },
    )


def get_evaluation_criteria():
    with CRITERIA_PATH.open(encoding="utf-8") as criteria_file:
        criteria = json.load(criteria_file)
    expected = {
        "productivity_score",
        "attendance_score",
        "quality_score",
        "teamwork_score",
        "communication_score",
    }
    if not isinstance(criteria, dict) or set(criteria) != expected:
        raise ValueError("Evaluation criteria configuration is invalid.")
    return criteria


def label_evaluation_form(form, criteria):
    for field_name, label in criteria.items():
        if field_name in form.fields:
            form.fields[field_name].label = label


@login_required
def performance_list(request):
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view performance records.")
    records = evaluation_scope(request.user)
    employee_id = request.GET.get("employee", "").strip()
    department_id = request.GET.get("department", "").strip()
    if employee_id.isdigit():
        records = records.filter(employee_id=int(employee_id))
    if department_id.isdigit():
        records = records.filter(employee__department_id=int(department_id))
    records = parse_period_filters(request, records)
    records = list(records.order_by("-date", "-id"))
    attach_latest_predictions(request.user, [record.employee for record in records])
    return render(
        request,
        "api/performance_list.html",
        {
            "records": records,
            "employees": employee_scope(request.user).order_by("name"),
            "departments": department_scope(request.user),
            "selected_employee": employee_id,
            "selected_department": department_id,
            "start_date": request.GET.get("start_date", ""),
            "end_date": request.GET.get("end_date", ""),
            "can_manage": request.user.role in TEAM_ROLES,
        },
    )


@roles_required(*TEAM_ROLES)
def performance_create(request):
    form = EvaluationForm(request.POST or None, actor=request.user)
    try:
        criteria = get_evaluation_criteria()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load evaluation criteria")
        criteria = {}
        messages.error(request, "Evaluation criteria could not be loaded.")
    label_evaluation_form(form, criteria)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Performance record created.")
        return redirect("performance-list")
    return render(
        request,
        "api/form.html",
        {
            "title": "Add performance record",
            "form": form,
            "cancel_url": "performance-list",
            "score_labels": criteria,
        },
    )


@roles_required(*TEAM_ROLES)
def performance_edit(request, pk):
    record = get_object_or_404(evaluation_scope(request.user), pk=pk)
    form = EvaluationForm(
        request.POST or None,
        instance=record,
        actor=record.evaluator,
        scope_user=request.user,
    )
    try:
        criteria = get_evaluation_criteria()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load evaluation criteria")
        criteria = {}
        messages.error(request, "Evaluation criteria could not be loaded.")
    label_evaluation_form(form, criteria)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Performance record updated.")
        return redirect("performance-list")
    return render(
        request,
        "api/form.html",
        {
            "title": f"Edit performance record · {record.employee.name}",
            "form": form,
            "cancel_url": "performance-list",
        },
    )


@login_required
def evaluation_list(request):
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view evaluations.")
    evaluations = evaluation_scope(request.user)
    employee_id = request.GET.get("employee", "").strip()
    if employee_id.isdigit():
        evaluations = evaluations.filter(employee_id=int(employee_id))
    evaluations = list(parse_period_filters(request, evaluations).order_by("-date", "-id"))
    attach_latest_predictions(request.user, [evaluation.employee for evaluation in evaluations])
    try:
        criteria = get_evaluation_criteria()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load evaluation criteria")
        criteria = {}
        messages.error(request, "Evaluation criteria could not be loaded.")
    return render(
        request,
        "api/evaluation_list.html",
        {
            "evaluations": evaluations,
            "employees": employee_scope(request.user).order_by("name"),
            "selected_employee": employee_id,
            "start_date": request.GET.get("start_date", ""),
            "end_date": request.GET.get("end_date", ""),
            "can_manage": request.user.role in TEAM_ROLES,
            "can_manage_criteria": is_management(request.user),
            "criteria": criteria,
        },
    )


@roles_required(*TEAM_ROLES)
def evaluation_create(request):
    form = EvaluationForm(request.POST or None, actor=request.user)
    try:
        criteria = get_evaluation_criteria()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load evaluation criteria")
        criteria = {}
        messages.error(request, "Evaluation criteria could not be loaded.")
    label_evaluation_form(form, criteria)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Evaluation created and overall score calculated.")
        return redirect("evaluation-list")
    return render(
        request,
        "api/form.html",
        {
            "title": "Create evaluation",
            "form": form,
            "cancel_url": "evaluation-list",
            "score_labels": criteria,
        },
    )


@login_required
def evaluation_detail(request, pk):
    evaluation = get_object_or_404(evaluation_scope(request.user), pk=pk)
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view evaluations.")
    try:
        criteria = get_evaluation_criteria()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load evaluation criteria")
        criteria = {}
    return render(
        request,
        "api/evaluation_detail.html",
        {
            "evaluation": evaluation,
            "scores": [
                (label, getattr(evaluation, field_name))
                for field_name, label in criteria.items()
            ],
            "can_manage": request.user.role in TEAM_ROLES,
            "can_delete": is_management(request.user),
        },
    )


@roles_required(*TEAM_ROLES)
def evaluation_edit(request, pk):
    evaluation = get_object_or_404(evaluation_scope(request.user), pk=pk)
    if (
        evaluation.status == Evaluation.Status.pending
        and not is_management(request.user)
        and evaluation.assigned_to_id != request.user.pk
    ):
        return HttpResponseForbidden("Only the assigned Team Leader can complete this evaluation.")
    form = EvaluationForm(
        request.POST or None,
        instance=evaluation,
        actor=evaluation.evaluator,
        scope_user=request.user,
    )
    try:
        criteria = get_evaluation_criteria()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load evaluation criteria")
        criteria = {}
        messages.error(request, "Evaluation criteria could not be loaded.")
    label_evaluation_form(form, criteria)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Evaluation updated.")
        return redirect("evaluation-detail", pk=evaluation.pk)
    return render(
        request,
        "api/form.html",
        {
            "title": f"Edit evaluation for {evaluation.employee.name}",
            "form": form,
            "cancel_url": "evaluation-detail",
            "cancel_pk": evaluation.pk,
            "score_labels": criteria,
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def evaluation_delete(request, pk):
    evaluation = get_object_or_404(evaluation_scope(request.user), pk=pk)
    if request.method == "POST":
        evaluation.delete()
        messages.success(request, "Evaluation and corresponding performance record deleted.")
        return redirect("evaluation-list")
    return render(
        request,
        "api/confirm_delete.html",
        {
            "title": "Delete evaluation",
            "object_label": f"{evaluation.employee.name} · {evaluation.date}",
            "cancel_url": "evaluation-detail",
            "cancel_pk": evaluation.pk,
            "delete_url": "evaluation-delete",
            "pk": evaluation.pk,
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def criteria_manage(request):
    try:
        criteria = get_evaluation_criteria()
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Unable to load evaluation criteria")
        messages.error(request, "Evaluation criteria could not be loaded.")
        criteria = {}
    if request.method == "POST":
        fields = (
            "productivity_score",
            "attendance_score",
            "quality_score",
            "teamwork_score",
            "communication_score",
        )
        updated = {field: request.POST.get(field, "").strip() for field in fields}
        if any(not value or len(value) > 80 for value in updated.values()):
            messages.error(request, "Each criterion name is required and must be 80 characters or fewer.")
        elif len({value.casefold() for value in updated.values()}) != len(updated):
            messages.error(request, "Criterion names must be unique.")
        else:
            temporary_path = CRITERIA_PATH.with_suffix(".json.tmp")
            with temporary_path.open("w", encoding="utf-8") as criteria_file:
                json.dump(updated, criteria_file, ensure_ascii=False, indent=2)
                criteria_file.write("\n")
            temporary_path.replace(CRITERIA_PATH)
            messages.success(request, "Evaluation criteria updated.")
            return redirect("criteria-manage")
        criteria = updated
    return render(request, "api/criteria.html", {"criteria": criteria})


def run_prediction_for_employee(employee):
    from .automation import classify_evaluation

    evaluation = employee.evaluations.filter(
        status=Evaluation.Status.completed,
    ).exclude(productivity_score__isnull=True).order_by("-date", "-id").first()
    if evaluation is None:
        raise ValueError(f"{employee.name} has no evaluation to predict from.")
    prediction = classify_evaluation(evaluation)
    if prediction is None:
        raise ValueError(f"A classification could not be created for {employee.name}.")
    return prediction


@login_required
def prediction_list(request):
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view predictions.")
    predictions = prediction_scope(request.user)
    employee_id = request.GET.get("employee", "").strip()
    if employee_id.isdigit():
        predictions = predictions.filter(employee_id=int(employee_id))
    latest_predictions = []
    seen_evaluations = set()
    for prediction in predictions.select_related(
        "employee",
        "employee__department",
        "evaluation",
    ).order_by("evaluation_id", "-created_at", "-id"):
        if prediction.evaluation_id is None:
            latest_predictions.append(prediction)
        elif prediction.evaluation_id not in seen_evaluations:
            latest_predictions.append(prediction)
            seen_evaluations.add(prediction.evaluation_id)
    latest_predictions.sort(
        key=lambda prediction: (prediction.created_at, prediction.pk),
        reverse=True,
    )
    employees = list(employee_scope(request.user).select_related("department").order_by("name"))
    if employee_id.isdigit():
        employees = [employee for employee in employees if employee.pk == int(employee_id)]
    return render(
        request,
        "api/prediction_list.html",
        {
            "employees": employees,
            "employee_options": employee_scope(request.user).order_by("name"),
            "selected_employee": employee_id,
            "can_predict": is_management(request.user),
            "predictions": latest_predictions,
            "importance": sorted(
                latest_predictions[0].feature_importances.items(),
                key=lambda item: item[1],
                reverse=True,
            ) if latest_predictions else [],
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def prediction_run(request):
    if request.method != "POST":
        return HttpResponseForbidden("Predictions must be submitted using POST.")
    from .ml.predict import ModelUnavailableError, UnknownDepartmentError

    action = request.POST.get("action", "single")
    if action == "batch":
        employees = employee_scope(request.user).filter(status="active").order_by("name")
        created = 0
        failures = []
        try:
            from .ml.predict import loaded_model_artifacts

            loaded_model_artifacts()
        except ModelUnavailableError as error:
            logger.exception("Batch prediction requested while the model is unavailable")
            messages.error(request, str(error))
            return redirect("prediction-list")
        for employee in employees:
            try:
                run_prediction_for_employee(employee)
            except UnknownDepartmentError as error:
                logger.warning(
                    "Skipping prediction for employee %s (%s): %s",
                    employee.pk,
                    employee.name,
                    error,
                )
                failures.append(str(error))
            except ValueError as error:
                failures.append(str(error))
            else:
                created += 1
        if created:
            messages.success(request, f"Saved predictions for {created} employees.")
        if failures:
            messages.warning(
                request,
                f"{len(failures)} employees were skipped. First issue: {failures[0]}",
            )
        if not created and not failures:
            messages.info(request, "There are no active employees to predict.")
        return redirect("prediction-list")

    employee_id = request.POST.get("employee_id", "").strip()
    if not employee_id.isdigit():
        messages.error(request, "Select an employee for an individual prediction.")
        return redirect("prediction-list")
    employee = employee_scope(request.user).filter(pk=int(employee_id)).first()
    if employee is None:
        messages.error(request, "The selected employee is not available for prediction.")
        return redirect("prediction-list")
    try:
        prediction = run_prediction_for_employee(employee)
    except UnknownDepartmentError as error:
        logger.warning(
            "Unable to predict employee %s (%s): %s",
            employee.pk,
            employee.name,
            error,
        )
        messages.error(request, str(error))
    except (ModelUnavailableError, ValueError) as error:
        if isinstance(error, ModelUnavailableError):
            logger.exception("Prediction model is unavailable")
        messages.error(request, str(error))
    else:
        messages.success(
            request,
            f"Prediction saved: {prediction.predicted_category} "
            f"({prediction.confidence_score:.1%} confidence).",
        )
    return redirect("prediction-list")


def filtered_report_evaluations(request):
    evaluations = evaluation_scope(request.user).order_by("date", "employee__name")
    department_id = request.GET.get("department", "").strip()
    if department_id.isdigit():
        evaluations = evaluations.filter(employee__department_id=int(department_id))
    return parse_period_filters(request, evaluations)


def report_export_filename(extension):
    return f"lexie-performance-report-{date.today().strftime('%Y-%m-%d')}.{extension}"


def report_filter_label(request):
    department_id = request.GET.get("department", "").strip()
    department_name = "All departments"
    if department_id.isdigit():
        department = Department.objects.filter(pk=int(department_id)).first()
        if department:
            department_name = department.name
    start_date = request.GET.get("start_date", "").strip() or "Any"
    end_date = request.GET.get("end_date", "").strip() or "Any"
    return department_name, start_date, end_date


@roles_required(*TEAM_ROLES)
def report_view(request):
    evaluations = filtered_report_evaluations(request)
    rows = [
        {
            "employee": item.employee.name,
            "employee_code": item.employee.employee_code,
            "department": item.employee.department.name,
            "date": item.date,
            "score": item.overall_score,
            "evaluator": item.evaluator.name,
        }
        for item in evaluations
    ]
    average_score = (
        round(sum(row["score"] for row in rows) / len(rows), 2) if rows else 0
    )
    return render(
        request,
        "api/reports.html",
        {
            "rows": rows,
            "departments": department_scope(request.user),
            "selected_department": request.GET.get("department", ""),
            "start_date": request.GET.get("start_date", ""),
            "end_date": request.GET.get("end_date", ""),
            "average_score": average_score,
            "record_count": len(rows),
        },
    )


@roles_required(*TEAM_ROLES)
def report_csv(request):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="{report_export_filename('csv')}"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(
        ("Employee ID", "Employee", "Department", "Period", "Overall score", "Evaluator")
    )
    for item in filtered_report_evaluations(request):
        writer.writerow(
            (
                item.employee.employee_code,
                item.employee.name,
                item.employee.department.name,
                item.date.isoformat(),
                f"{item.overall_score:.2f}",
                item.evaluator.name,
            )
        )
    return response


def build_pdf(lines):
    def pdf_escape(value):
        return str(value).encode("ascii", "replace").decode("ascii").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    pages = [lines[index : index + 48] for index in range(0, len(lines), 48)] or [["No records found."]]
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [" + b" ".join(
            f"{4 + index * 2} 0 R".encode("ascii") for index in range(len(pages))
        ) + b"] /Count " + str(len(pages)).encode("ascii") + b" >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for page_index, page_lines in enumerate(pages):
        page_id = 4 + page_index * 2
        content_id = page_id + 1
        text_commands = ["BT", "/F1 10 Tf", "45 790 Td", "14 TL"]
        for line in page_lines:
            text_commands.append(f"({pdf_escape(line)}) Tj")
            text_commands.append("T*")
        text_commands.append("ET")
        stream = "\n".join(text_commands).encode("ascii")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>".encode("ascii")
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        )
    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{index} 0 obj\n".encode("ascii"))
        output.extend(obj)
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    output.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)


@roles_required(*TEAM_ROLES)
def report_pdf(request):
    evaluations = filtered_report_evaluations(request)
    department_name, start_date, end_date = report_filter_label(request)
    lines = [
        "Lexie Performance Report",
        f"Generated: {date.today().isoformat()}",
        f"Department: {department_name}",
        f"Period: {start_date} to {end_date}",
        "",
        "Employee ID | Employee | Department | Date | Score | Evaluator",
        "-" * 85,
    ]
    for item in evaluations:
        lines.append(
            f"{item.employee.employee_code} | {item.employee.name} | "
            f"{item.employee.department.name} | {item.date.isoformat()} | "
            f"{item.overall_score:.2f} | {item.evaluator.name}"
        )
    response = HttpResponse(build_pdf(lines), content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="{report_export_filename('pdf')}"'
    )
    return response


@roles_required(User.Role.admin)
def system_tools(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "sync_predictions":
            try:
                from .ml.predict import ModelUnavailableError, loaded_model_artifacts

                loaded_model_artifacts()
            except ModelUnavailableError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, "Prediction model cache refreshed.")
            return redirect("system-tools")
        if action == "reset_demo_data":
            messages.info(request, "Demo data reset is available through the admin maintenance workflow.")
            return redirect("system-tools")
    users = User.objects.exclude(role=User.Role.client).count()
    employees = Employee.objects.count()
    submissions = Submission.objects.count()
    predictions = Prediction.objects.count()
    return render(
        request,
        "api/system_tools.html",
        {
            "user_count": users,
            "employee_count": employees,
            "submission_count": submissions,
            "prediction_count": predictions,
            "admin_label": request.user.get_role_display(),
        },
    )


@roles_required(User.Role.admin)
def activity_log(request):
    recent_users = User.objects.exclude(role=User.Role.client).order_by("-created_at")[:10]
    return render(
        request,
        "api/activity_log.html",
        {"recent_users": recent_users},
    )


@roles_required(User.Role.admin, User.Role.management)
def account_list(request):
    queryset = User.objects.exclude(role=User.Role.client)
    if request.user.role == User.Role.management:
        queryset = queryset.exclude(role__in=[User.Role.admin, User.Role.management, User.Role.hr_manager])
    users = queryset.prefetch_related(
        "employee", "employee__team_leader"
    ).order_by("name")
    return render(
        request,
        "api/account_list.html",
        {"users": users, "accounts": queryset.count()},
    )


@roles_required(User.Role.admin, User.Role.management)
def account_create(request):
    form = AccountCreateForm(request.POST or None, actor=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                user = form.save()
        except IntegrityError:
            form.add_error("email", "This email or employee profile is already linked.")
        else:
            messages.success(request, f"Account created for {user.name}.")
            return redirect("account-list")
    return render(
        request,
        "api/form.html",
        {"title": "Create user account", "form": form, "cancel_url": "account-list"},
    )


@roles_required(User.Role.admin, User.Role.management)
def account_edit(request, pk):
    queryset = User.objects.exclude(role=User.Role.client)
    if request.user.role == User.Role.management:
        queryset = queryset.exclude(role__in=[User.Role.admin, User.Role.management, User.Role.hr_manager])
    account = get_object_or_404(queryset, pk=pk)
    form = AccountEditForm(
        request.POST or None,
        request.FILES or None,
        instance=account,
        actor=request.user,
    )
    if request.method == "POST" and form.is_valid():
        try:
            form.save()
        except IntegrityError:
            form.add_error("email", "That email or employee profile is already linked.")
        else:
            messages.success(request, "Account updated.")
            return redirect("account-list")
    return render(
        request,
        "api/form.html",
        {"title": f"Edit account · {account.name}", "form": form, "cancel_url": "account-list"},
    )


@roles_required(*VIEWER_ROLES, User.Role.client)
def settings_view(request):
    form_action = request.POST.get("form_action")
    force_password_change = request.user.must_change_password
    tab = "security" if force_password_change else request.GET.get("tab", "profile")
    profile_form = ProfileForm(
        request.POST if form_action == "profile" else None,
        request.FILES if form_action == "profile" else None,
        instance=request.user,
    )
    password_form = CurrentPasswordChangeForm(
        request.user,
        request.POST if form_action == "password" else None,
    )
    preference_form = NotificationPreferencesForm(
        request.POST if form_action == "notifications" else None,
        preferences=request.user.notification_preferences,
    )
    if request.method == "POST":
        if force_password_change and form_action != "password":
            messages.error(request, "Change your password before continuing.")
        elif form_action == "profile" and profile_form.is_valid():
            profile_form.save()
            messages.success(request, "Profile updated.")
            return redirect("settings")
        if form_action == "password" and password_form.is_valid():
            user = password_form.save()
            if force_password_change:
                user.must_change_password = False
                user.save(update_fields=("must_change_password",))
            update_session_auth_hash(request, user)
            messages.success(request, "Password changed.")
            if force_password_change:
                if user.role == User.Role.client:
                    return redirect("client-portal")
                return redirect("dashboard")
            return redirect("settings")
        if not force_password_change and form_action == "notifications" and preference_form.is_valid():
            request.user.notification_preferences = preference_form.cleaned_data
            request.user.save(update_fields=("notification_preferences",))
            messages.success(request, "Notification preference saved.")
            return redirect("/settings/?tab=notifications")
    if force_password_change:
        profile_form = ProfileForm(instance=request.user)
        preference_form = NotificationPreferencesForm(
            preferences=request.user.notification_preferences,
        )
    return render(
        request,
        "api/settings.html",
        {
            "profile_form": profile_form,
            "password_form": password_form,
            "preference_form": preference_form,
            "active_tab": tab if tab in {"profile", "notifications", "security"} else "profile",
            "force_password_change": force_password_change,
        },
    )


@roles_required(*TEAM_ROLES)
def notification_list(request):
    if request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("You do not have permission to view notifications.")
    notifications = Notification.objects.filter(recipient=request.user)
    return render(
        request,
        "api/notifications.html",
        {
            "notifications": notifications,
            "unread_count": notifications.filter(read_at__isnull=True).count(),
        },
    )


@login_required
def notification_read(request, pk):
    if request.method != "POST" or request.user.role not in VIEWER_ROLES:
        return HttpResponseForbidden("Notifications can only be marked read by their recipient.")
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=("read_at",))
    return redirect("notification-list")


@roles_required(*TEAM_ROLES)
def notification_send(request):
    if not is_management(request.user) and request.user.role != User.Role.team_leader:
        return HttpResponseForbidden("You do not have permission to send notifications.")
    employee_id = request.GET.get("employee", "").strip()
    employee = (
        get_object_or_404(employee_scope(request.user), pk=employee_id)
        if employee_id.isdigit()
        else None
    )
    form = NotificationComposeForm(
        request.POST or None,
        actor=request.user,
        employee=employee,
    )
    if request.method == "POST" and form.is_valid():
        recipient = form.cleaned_data["recipient"]
        create_notification(
            recipient,
            form.cleaned_data["title"],
            form.cleaned_data["message"],
            Notification.Category.manual,
            sender=request.user,
        )
        messages.success(request, f"Notification sent to {recipient.name}.")
        if employee is not None:
            return redirect("employee-profile", pk=employee.pk)
        return redirect("notification-list")
    return render(
        request,
        "api/notification_form.html",
        {
            "form": form,
            "employee": employee,
            "cancel_url": "employee-profile" if employee else "notification-list",
        },
    )


@roles_required(*MANAGEMENT_ROLES)
def automation_settings(request):
    settings_row = AutomationSettings.objects.get_or_create_default()
    form = AutomationSettingsForm(request.POST or None, instance=settings_row)
    if request.method == "POST" and form.is_valid():
        settings_row = form.save(commit=False)
        settings_row.updated_by = request.user
        settings_row.save()
        messages.success(request, "Automation settings updated.")
        return redirect("automation-settings")
    return render(request, "api/automation_settings.html", {"form": form})

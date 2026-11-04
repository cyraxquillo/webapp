from collections import defaultdict
import logging

from django.contrib.auth import authenticate
from django.db import IntegrityError, transaction
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken

from .access import (
    MANAGEMENT_ROLES,
    TEAM_ROLES,
    VIEWER_ROLES,
    employee_queryset_for,
    evaluation_queryset_for,
    is_management,
    prediction_queryset_for,
)
from .models import Client, Department, Employee, Evaluation, Prediction, User
from .login_throttling import LoginRateThrottle
from .permissions import IsAdmin, IsAdminOrManager, IsClient, IsManagementOrTeamLeader
from .serializers import (
    ClientSerializer,
    DepartmentSerializer,
    EmployeeSerializer,
    EvaluationSerializer,
    NotificationPreferencesSerializer,
    PasswordUpdateSerializer,
    PredictionSerializer,
    UserCreateSerializer,
    UserProfileSerializer,
    UserSerializer,
)

logger = logging.getLogger(__name__)


def parse_pagination(request):
    try:
        skip = int(request.query_params.get("skip", "0"))
        limit = int(request.query_params.get("limit", "50"))
    except ValueError:
        return None, None, Response({"detail": "skip and limit must be integers"}, status=422)
    if skip < 0 or not 1 <= limit <= 200:
        return None, None, Response({"detail": "skip must be non-negative and limit must be between 1 and 200"}, status=422)
    return skip, limit, None


def role_denied():
    return Response(
        {"detail": "You do not have permission to perform this action"},
        status=status.HTTP_403_FORBIDDEN,
    )


def user_has_role(user, *roles):
    return user.is_authenticated and user.role in roles


def find_employee(employee_id):
    queryset = Employee.objects.select_related("department", "user")
    if employee_id.isdigit():
        return queryset.filter(pk=int(employee_id)).first()
    return queryset.filter(employee_code=employee_id).first()


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([LoginRateThrottle])
def login_view(request):
    username = request.data.get("username", "")
    password = request.data.get("password", "")
    if not username or not password:
        return Response(
            {"detail": "Both username and password are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    user = authenticate(request, email=username.lower(), password=password)
    if user is None:
        return Response(
            {"detail": "Incorrect email or password"},
            status=status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )
    refresh = RefreshToken.for_user(user)
    return Response({
        "access_token": str(refresh.access_token),
        "token_type": "bearer",
        "role": user.role,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def auth_me(request):
    return Response(UserSerializer(request.user).data)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def register_user(request):
    if request.user.role != User.Role.admin:
        return role_denied()
    serializer = UserCreateSerializer(data=request.data)
    if not serializer.is_valid():
        if "email" in serializer.errors and "An account with this email already exists" in str(serializer.errors["email"]):
            return Response({"detail": "An account with this email already exists"}, status=409)
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        with transaction.atomic():
            user = serializer.save()
    except IntegrityError:
        return Response({"detail": "An account with this email already exists"}, status=409)
    return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def user_profile(request):
    user = request.user
    if request.method == "GET":
        return Response(UserProfileSerializer(user).data)
    serializer = UserProfileSerializer(user, data=request.data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    try:
        serializer.save()
    except IntegrityError:
        return Response({"detail": "An account with this email already exists"}, status=409)
    return Response(serializer.data)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def update_password(request):
    serializer = PasswordUpdateSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    if not request.user.check_password(serializer.validated_data["current_password"]):
        return Response({"detail": "Current password is incorrect"}, status=400)
    request.user.set_password(serializer.validated_data["new_password"])
    request.user.save(update_fields=("password",))
    return Response({"message": "Password updated successfully"})


@api_view(["GET", "PUT"])
@permission_classes([IsAuthenticated])
def notification_preferences(request):
    defaults = {"email_evaluations": True, "email_predictions": True, "weekly_summary": False}
    if request.method == "GET":
        return Response({**defaults, **(request.user.notification_preferences or {})})
    serializer = NotificationPreferencesSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    request.user.notification_preferences = serializer.validated_data
    request.user.save(update_fields=("notification_preferences",))
    return Response(serializer.validated_data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def departments(request):
    if not is_management(request.user):
        return role_denied()
    return Response(DepartmentSerializer(Department.objects.filter(is_active=True), many=True).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def employees(request):
    user = request.user
    if request.method == "GET":
        if user.role not in VIEWER_ROLES:
            return role_denied()
        queryset = employee_queryset_for(user)
        search = request.query_params.get("search")
        department = request.query_params.get("department")
        status_filter = request.query_params.get("status")
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search.strip())
                | Q(employee_code__icontains=search.strip())
                | Q(position__icontains=search.strip())
            )
        if department:
            queryset = queryset.filter(department__name__iexact=department.strip())
        if status_filter in {"active", "inactive"}:
            queryset = queryset.filter(status=status_filter)
        skip, limit, error = parse_pagination(request)
        if error:
            return error
        if status_filter is not None and status_filter not in {"active", "inactive"}:
            return Response({"detail": "status must be active or inactive"}, status=422)
        return Response(EmployeeSerializer(queryset.order_by("name")[skip:skip + limit], many=True).data)
    if not is_management(user):
        return role_denied()
    serializer = EmployeeSerializer(data=request.data)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    try:
        employee = serializer.save()
    except IntegrityError:
        return Response({"detail": "Employee code or linked staff account is already in use"}, status=409)
    return Response(EmployeeSerializer(employee).data, status=201)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def employee_detail(request, employee_id):
    queryset = employee_queryset_for(request.user)
    employee = (
        queryset.filter(pk=int(employee_id)).first()
        if employee_id.isdigit()
        else queryset.filter(employee_code=employee_id).first()
    )
    if employee is None:
        if employee_id.isdigit():
            exists = Employee.objects.filter(pk=int(employee_id)).exists()
        else:
            exists = Employee.objects.filter(employee_code=employee_id).exists()
        if exists:
            return Response({"detail": "You do not have permission to access this employee"}, status=403)
        return Response({"detail": "Employee not found"}, status=404)
    if request.method == "GET":
        if request.user.role not in VIEWER_ROLES:
            return role_denied()
        return Response(EmployeeSerializer(employee).data)
    if request.method == "DELETE" and not is_management(request.user):
        return role_denied()
    if request.method == "PUT" and request.user.role not in TEAM_ROLES:
        return role_denied()
    if request.method == "DELETE":
        employee.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    data = request.data
    if request.user.role == User.Role.team_leader:
        data = request.data.copy()
        data.pop("user_id", None)
        data.pop("team_leader_id", None)
        data.pop("department", None)
    serializer = EmployeeSerializer(employee, data=data, partial=True)
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    try:
        employee = serializer.save()
    except IntegrityError:
        return Response({"detail": "Linked staff account is already assigned to another employee"}, status=409)
    return Response(EmployeeSerializer(employee).data)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def evaluations(request):
    user = request.user
    if request.method == "GET":
        if user.role not in VIEWER_ROLES:
            return role_denied()
        queryset = evaluation_queryset_for(user)
        employee_id = request.query_params.get("employee_id")
        if employee_id is not None:
            try:
                queryset = queryset.filter(employee_id=int(employee_id))
            except ValueError:
                return Response({"detail": "employee_id must be an integer"}, status=422)
        skip, limit, error = parse_pagination(request)
        if error:
            return error
        return Response(EvaluationSerializer(queryset.order_by("-date", "-id")[skip:skip + limit], many=True).data)
    if user.role not in TEAM_ROLES:
        return role_denied()
    employee_id = request.data.get("employee_id")
    if not str(employee_id or "").isdigit() or not employee_queryset_for(user).filter(pk=int(employee_id)).exists():
        return Response({"detail": "Employee not found"}, status=404)
    serializer = EvaluationSerializer(data=request.data, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    evaluation = serializer.save()
    return Response(EvaluationSerializer(evaluation).data, status=201)


@api_view(["GET", "PUT", "DELETE"])
@permission_classes([IsAuthenticated])
def evaluation_detail(request, evaluation_id):
    evaluation = evaluation_queryset_for(request.user).filter(pk=evaluation_id).first()
    if evaluation is None:
        if Evaluation.objects.filter(pk=evaluation_id).exists():
            return Response({"detail": "You do not have permission to access this evaluation"}, status=403)
        return Response({"detail": "Evaluation not found"}, status=404)
    if request.method == "GET":
        if request.user.role not in VIEWER_ROLES:
            return role_denied()
        return Response(EvaluationSerializer(evaluation).data)
    if request.method == "DELETE" and not is_management(request.user):
        return role_denied()
    if request.method == "PUT" and request.user.role not in TEAM_ROLES:
        return role_denied()
    if request.method == "DELETE":
        evaluation.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    serializer = EvaluationSerializer(evaluation, data=request.data, partial=True, context={"request": request})
    if not serializer.is_valid():
        return Response(serializer.errors, status=422)
    evaluation = serializer.save()
    return Response(EvaluationSerializer(evaluation).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def predictions(request):
    user = request.user
    if user.role not in VIEWER_ROLES:
        return role_denied()
    queryset = prediction_queryset_for(user)
    employee_id = request.query_params.get("employee_id")
    if employee_id is not None:
        try:
            queryset = queryset.filter(employee_id=int(employee_id))
        except ValueError:
            return Response({"detail": "employee_id must be an integer"}, status=422)
    skip, limit, error = parse_pagination(request)
    if error:
        return error
    return Response(PredictionSerializer(queryset.order_by("-created_at", "-id")[skip:skip + limit], many=True).data)


@api_view(["POST"])
@permission_classes([IsManagementOrTeamLeader])
def create_prediction(request, employee_id):
    queryset = employee_queryset_for(request.user)
    employee = (
        queryset.filter(pk=int(employee_id)).first()
        if employee_id.isdigit()
        else queryset.filter(employee_code=employee_id).first()
    )
    if employee is None:
        return Response({"detail": "Employee not found"}, status=404)
    evaluation = employee.evaluations.order_by("-date", "-id").first()
    if evaluation is None:
        return Response({"detail": "Cannot predict performance without an evaluation"}, status=422)
    try:
        from .ml import ModelUnavailableError, UnknownDepartmentError, predict_performance

        result = predict_performance(employee, evaluation)
    except ModelUnavailableError as error:
        return Response({"detail": str(error)}, status=503)
    except UnknownDepartmentError as error:
        return Response({"detail": str(error)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
    except ValueError as error:
        return Response({"detail": str(error)}, status=422)
    prediction = Prediction.objects.create(employee=employee, **result)
    logger.info(
        "Created prediction for employee_id=%s model_source=%s model_version=%s category=%s confidence=%s",
        employee.pk,
        prediction.model_source,
        prediction.model_version,
        prediction.predicted_category,
        prediction.confidence_score,
    )
    return Response(PredictionSerializer(prediction).data, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def report_summary(request):
    if request.user.role not in TEAM_ROLES:
        return role_denied()
    active_employees = employee_queryset_for(request.user).filter(status="active")
    evaluations = evaluation_queryset_for(request.user).order_by("-date", "-id")
    latest_by_employee = {}
    for evaluation in evaluations:
        latest_by_employee.setdefault(evaluation.employee_id, evaluation)
    scores_by_department = defaultdict(list)
    latest_scores = []
    at_risk_count = 0
    for employee in active_employees:
        evaluation = latest_by_employee.get(employee.id)
        if evaluation is None:
            continue
        score = evaluation.overall_score
        latest_scores.append(score)
        scores_by_department[employee.department.name].append(score)
        at_risk_count += score < 70
    department_breakdown = [
        {"name": name, "score": round(sum(scores) / len(scores), 2), "employee_count": len(scores)}
        for name, scores in sorted(scores_by_department.items())
    ]
    return Response({
        "average_score": round(sum(latest_scores) / len(latest_scores), 2) if latest_scores else 0,
        "at_risk_count": at_risk_count,
        "active_employee_count": active_employees.count(),
        "department_breakdown": department_breakdown,
    })


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def performance_trend(request):
    if request.user.role not in TEAM_ROLES:
        return role_denied()
    month_scores = defaultdict(list)
    for evaluation in evaluation_queryset_for(request.user).order_by("date"):
        month_scores[evaluation.date.strftime("%Y-%m")].append(evaluation.overall_score)
    return Response([
        {"month": month, "score": round(sum(scores) / len(scores), 2)}
        for month, scores in sorted(month_scores.items())
    ])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def clients(request):
    if not user_has_role(request.user, User.Role.admin):
        return role_denied()
    return Response(ClientSerializer(Client.objects.select_related("user").all(), many=True).data)


@api_view(["GET"])
@permission_classes([IsClient])
def my_client(request):
    client = Client.objects.filter(user=request.user).first()
    if client is None:
        return Response({"detail": "Client account record not found"}, status=404)
    return Response(ClientSerializer(client).data)

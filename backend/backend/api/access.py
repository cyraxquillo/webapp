from .models import Employee, Evaluation, Prediction, Submission, User


MANAGEMENT_ROLES = (
    User.Role.admin,
    User.Role.management,
    User.Role.hr_manager,
)
TEAM_ROLES = (*MANAGEMENT_ROLES, User.Role.team_leader)
STAFF_ROLES = (User.Role.staff, User.Role.employee)
VIEWER_ROLES = (*TEAM_ROLES, *STAFF_ROLES)


def is_management(user):
    return user.is_authenticated and user.role in MANAGEMENT_ROLES


def is_team_leader(user):
    return user.is_authenticated and user.role == User.Role.team_leader


def is_staff_user(user):
    return user.is_authenticated and user.role in STAFF_ROLES


def employee_queryset_for(user):
    queryset = Employee.objects.select_related("department", "user", "team_leader")
    if user.role == User.Role.team_leader:
        return queryset.filter(team_leader_id=user.pk)
    if user.role in STAFF_ROLES:
        return queryset.filter(user_id=user.pk)
    if user.role in MANAGEMENT_ROLES:
        return queryset
    return queryset.none()


def evaluation_queryset_for(user):
    queryset = Evaluation.objects.select_related(
        "employee", "employee__department", "employee__user", "employee__team_leader", "evaluator"
    )
    if user.role == User.Role.team_leader:
        return queryset.filter(employee__team_leader_id=user.pk)
    if user.role in STAFF_ROLES:
        return queryset.filter(employee__user_id=user.pk)
    if user.role in MANAGEMENT_ROLES:
        return queryset
    return queryset.none()


def prediction_queryset_for(user):
    queryset = Prediction.objects.select_related(
        "employee", "employee__department", "employee__user", "employee__team_leader"
    )
    if user.role == User.Role.team_leader:
        return queryset.filter(employee__team_leader_id=user.pk)
    if user.role in STAFF_ROLES:
        return queryset.filter(employee__user_id=user.pk)
    if user.role in MANAGEMENT_ROLES:
        return queryset
    return queryset.none()


def submission_queryset_for(user):
    queryset = Submission.objects.select_related(
        "employee", "employee__department", "employee__user", "employee__team_leader"
    )
    if user.role == User.Role.team_leader:
        return queryset.filter(employee__team_leader_id=user.pk)
    if user.role in STAFF_ROLES:
        return queryset.filter(employee__user_id=user.pk)
    if user.role in MANAGEMENT_ROLES:
        return queryset
    return queryset.none()
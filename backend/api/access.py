from django.db.models import Q

from .models import Employee, Evaluation, Prediction, Submission, User


MANAGEMENT_ROLES = (
    User.Role.admin,
    User.Role.management,
    User.Role.hr_manager,
)
TEAM_ROLES = (*MANAGEMENT_ROLES, User.Role.team_leader)
STAFF_ROLES = (User.Role.staff, User.Role.employee)
VIEWER_ROLES = (*TEAM_ROLES, *STAFF_ROLES)
ATTENDANCE_ROLES = (
    User.Role.management,
    User.Role.hr_manager,
    User.Role.team_leader,
    *STAFF_ROLES,
)


def get_managed_users(actor):
    if not actor or not actor.is_authenticated:
        return User.objects.none()
    if actor.role == User.Role.admin:
        return User.objects.all()
    if actor.role in (User.Role.management, User.Role.hr_manager):
        return User.objects.filter(role=User.Role.team_leader)
    if actor.role == User.Role.team_leader:
        return User.objects.filter(
            role__in=STAFF_ROLES,
            employee__team_leader_id=actor.pk,
        )
    return User.objects.none()


def get_managers_of(user):
    managers = User.objects.filter(is_active=True)
    admins = managers.filter(role=User.Role.admin)
    if user.role in STAFF_ROLES:
        try:
            team_leader_id = user.employee.team_leader_id
        except Employee.DoesNotExist:
            team_leader_id = None
        if team_leader_id:
            return (admins | managers.filter(pk=team_leader_id)).distinct()
        return admins
    if user.role == User.Role.team_leader:
        return (
            admins
            | managers.filter(role__in=(User.Role.management, User.Role.hr_manager))
        ).distinct()
    if user.role in (User.Role.management, User.Role.hr_manager):
        return admins
    return User.objects.none()


def attendance_visible_users(actor):
    if not actor or not actor.is_authenticated:
        return User.objects.none()
    if actor.role == User.Role.admin:
        return User.objects.all()
    return User.objects.filter(
        Q(pk=actor.pk) | Q(pk__in=get_managed_users(actor).values("pk"))
    )


def managed_user_queryset(actor):
    users = User.objects.all()
    if not actor or not actor.is_authenticated:
        return users.none()
    if actor.role == User.Role.admin:
        return users.filter(
            role__in=(
                User.Role.management,
                User.Role.hr_manager,
                User.Role.team_leader,
                *STAFF_ROLES,
            )
        )
    if actor.role in (User.Role.management, User.Role.hr_manager):
        return users.filter(role__in=(User.Role.team_leader, *STAFF_ROLES))
    if actor.role == User.Role.team_leader:
        return users.filter(
            role__in=STAFF_ROLES,
            employee__team_leader_id=actor.pk,
        )
    return users.none()


def visible_user_queryset(actor):
    if not actor or not actor.is_authenticated:
        return User.objects.none()
    return User.objects.filter(
        Q(pk=actor.pk) | Q(pk__in=managed_user_queryset(actor).values("pk"))
    )


def can_manage_user(actor, subject):
    return bool(
        actor
        and actor.is_authenticated
        and subject
        and actor.pk != subject.pk
        and managed_user_queryset(actor).filter(pk=subject.pk).exists()
    )


def can_manage_device(actor, device):
    if not actor or not actor.is_authenticated:
        return False
    if actor.role == User.Role.admin:
        return True
    if actor.role == User.Role.management:
        return device.user.role in (
            User.Role.team_leader,
            *STAFF_ROLES,
            User.Role.client,
        )
    if actor.role == User.Role.team_leader:
        return device.user.role in STAFF_ROLES and Employee.objects.filter(
            user_id=device.user_id,
            team_leader_id=actor.pk,
        ).exists()
    return False


def is_management(user):
    return user.is_authenticated and user.role in MANAGEMENT_ROLES


def is_team_leader(user):
    return user.is_authenticated and user.role == User.Role.team_leader


def is_staff_user(user):
    return user.is_authenticated and user.role in STAFF_ROLES


def firestore_scope_for(user, collection):
    if not user or not user.is_authenticated:
        return None
    if user.role in MANAGEMENT_ROLES:
        return {}
    if user.role == User.Role.team_leader:
        return {"team_leader_id": user.pk}
    if user.role in STAFF_ROLES:
        field = "user_id" if collection == "employees" else "employee_user_id"
        return {field: user.pk}
    return None


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
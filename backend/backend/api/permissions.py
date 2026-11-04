from rest_framework.permissions import BasePermission


class HasRole(BasePermission):
    allowed_roles = ()

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
            and request.user.role in self.allowed_roles
        )


class IsAdmin(HasRole):
    allowed_roles = ("admin",)


class IsAdminOrManager(HasRole):
    allowed_roles = ("admin", "management", "hr_manager")


class IsManagementOrTeamLeader(HasRole):
    allowed_roles = ("admin", "management", "hr_manager", "team_leader")


class IsAdminManagerOrEmployee(HasRole):
    allowed_roles = ("admin", "hr_manager", "staff", "employee")


class IsClient(HasRole):
    allowed_roles = ("client",)

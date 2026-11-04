from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import (
    Client,
    Department,
    Employee,
    Evaluation,
    OfficeLocation,
    Prediction,
    Submission,
    TrustedDevice,
    User,
)


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "name", "role", "is_active", "is_staff")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("email", "name")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("name", "role", "notification_preferences")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Dates", {"fields": ("last_login", "created_at")}),
    )
    add_fieldsets = (
        (None, {"classes": ("wide",), "fields": ("email", "name", "role", "password1", "password2")}),
    )
    readonly_fields = ("created_at",)
    filter_horizontal = ("groups", "user_permissions")


admin.site.register((Department, Employee, Evaluation, Prediction, Submission, Client))


@admin.register(OfficeLocation)
class OfficeLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "latitude", "longitude", "allowed_radius_meters", "outside_time_in_policy", "is_active")
    list_filter = ("is_active", "outside_time_in_policy")
    search_fields = ("name",)

    def has_module_permission(self, request):
        return request.user.is_authenticated and request.user.role == User.Role.admin

    def has_add_permission(self, request):
        return request.user.is_authenticated and request.user.role == User.Role.admin

    def has_change_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.role == User.Role.admin

    def has_delete_permission(self, request, obj=None):
        return request.user.is_authenticated and request.user.role == User.Role.admin


@admin.register(TrustedDevice)
class TrustedDeviceAdmin(admin.ModelAdmin):
    list_display = ("label", "user", "status", "created_at", "last_used_at", "approved_by")
    list_filter = ("status", "created_at")
    search_fields = ("label", "user__email", "ip_address")
    readonly_fields = ("token_hash",)

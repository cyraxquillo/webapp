from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path("auth/login", views.login_view, name="login"),
    path("auth/login/", views.login_view),
    path("auth/refresh", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/register", views.register_user, name="register"),
    path("auth/me", views.auth_me, name="auth_me"),
    path("users/me", views.user_profile, name="user_profile"),
    path("users/me/password", views.update_password, name="update_password"),
    path("users/me/notifications", views.notification_preferences, name="notification_preferences"),
    path("departments", views.departments, name="departments"),
    path("employees", views.employees, name="employees"),
    path("employees/<str:employee_id>", views.employee_detail, name="employee_detail"),
    path("evaluations", views.evaluations, name="evaluations"),
    path("evaluations/<int:evaluation_id>", views.evaluation_detail, name="evaluation_detail"),
    path("predictions", views.predictions, name="predictions"),
    path("predictions/<str:employee_id>", views.create_prediction, name="create_prediction"),
    path("reports/summary", views.report_summary, name="report_summary"),
    path("reports/performance-trend", views.performance_trend, name="performance_trend"),
    path("clients", views.clients, name="clients"),
    path("clients/me", views.my_client, name="my_client"),
]

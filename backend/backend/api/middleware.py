from django.shortcuts import redirect
from django.urls import reverse


class RequirePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        allowed_paths = {
            reverse("settings"),
            reverse("web-logout"),
        }
        if (
            user.is_authenticated
            and user.must_change_password
            and request.path_info not in allowed_paths
        ):
            return redirect(f"{reverse('settings')}?tab=security&force=1")
        return self.get_response(request)

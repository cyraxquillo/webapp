from django.core.cache import cache
from rest_framework.exceptions import Throttled
from rest_framework.throttling import UserRateThrottle


LOGIN_ATTEMPT_LIMIT = 5
LOGIN_ATTEMPT_WINDOW_SECONDS = 60
LOGIN_THROTTLE_MESSAGE = "Too many attempts. Try again later."


class LoginThrottled(Throttled):
    default_detail = LOGIN_THROTTLE_MESSAGE


class LoginRateThrottle(UserRateThrottle):
    scope = "login"

    def throttle_failure(self):
        raise LoginThrottled(wait=self.wait())


def login_attempt_cache_key(request):
    remote_address = request.META.get("REMOTE_ADDR", "unknown")
    return f"login-attempts:{remote_address}"


def is_web_login_throttled(request):
    return cache.get(login_attempt_cache_key(request), 0) >= LOGIN_ATTEMPT_LIMIT


def record_web_login_failure(request):
    key = login_attempt_cache_key(request)
    if not cache.add(key, 1, timeout=LOGIN_ATTEMPT_WINDOW_SECONDS):
        cache.incr(key)


def clear_web_login_failures(request):
    cache.delete(login_attempt_cache_key(request))

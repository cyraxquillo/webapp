from django.apps import AppConfig
from importlib import import_module


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "api"

    def ready(self):
        import_module("api.ml.predict")
        import_module("api.signals")

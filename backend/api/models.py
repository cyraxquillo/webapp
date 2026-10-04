from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("An email address is required")
        user = self.model(email=self.normalize_email(email).lower(), **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("role", User.Role.admin)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    class Role(models.TextChoices):
        admin = "admin", "Admin"
        hr_manager = "hr_manager", "HR / Manager"
        staff = "staff", "Staff"
        employee = "employee", "Employee"
        client = "client", "Client"

    name = models.CharField(max_length=160)
    email = models.EmailField(max_length=255, unique=True, db_index=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.employee)
    notification_preferences = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        db_table = "users"
        ordering = ("name",)

    def __str__(self):
        return self.email

    @property
    def password_hash(self):
        return self.password


class Department(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return self.name


class Employee(models.Model):
    employee_code = models.CharField(max_length=32, unique=True, db_index=True)
    user = models.OneToOneField(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employee",
        limit_choices_to={"role__in": [User.Role.staff, User.Role.employee]},
    )
    name = models.CharField(max_length=160, db_index=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    position = models.CharField(max_length=120)
    date_hired = models.DateField()
    status = models.CharField(max_length=20, default="active")
    attendance_rate = models.FloatField(default=100)
    tenure_months = models.PositiveIntegerField(default=0)
    training_hours = models.FloatField(default=0)

    class Meta:
        ordering = ("name",)

    def __str__(self):
        return f"{self.name} ({self.employee_code})"


class Evaluation(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="evaluations")
    evaluator = models.ForeignKey(User, on_delete=models.PROTECT, related_name="evaluations_written")
    date = models.DateField(db_index=True)
    productivity_score = models.FloatField()
    attendance_score = models.FloatField()
    quality_score = models.FloatField()
    teamwork_score = models.FloatField()
    communication_score = models.FloatField()
    comments = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-date", "-id")

    @property
    def overall_score(self):
        scores = (
            self.productivity_score,
            self.attendance_score,
            self.quality_score,
            self.teamwork_score,
            self.communication_score,
        )
        return round(sum(scores) / len(scores), 2)


class Prediction(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="predictions")
    predicted_category = models.CharField(max_length=80)
    confidence_score = models.FloatField()
    feature_importances = models.JSONField(default=dict)
    model_source = models.CharField(max_length=255, default="")
    model_version = models.CharField(max_length=40, default="")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-id")


class Client(models.Model):
    client_code = models.CharField(max_length=32, unique=True, db_index=True)
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="client")
    company_name = models.CharField(max_length=180)
    services_used = models.TextField()
    account_status = models.CharField(max_length=30, default="active")
    next_renewal_date = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ("company_name",)

    def __str__(self):
        return self.company_name

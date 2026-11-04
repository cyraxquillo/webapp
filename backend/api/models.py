from datetime import time
from pathlib import Path
from uuid import uuid4

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import ImageField
from django.utils import timezone


def validate_score_drop_threshold(value):
    if value <= 0 or value > 100:
        raise ValidationError(
            "The score-drop threshold must be greater than 0 and at most 100."
        )


def profile_photo_upload_to(instance, filename):
    extension = Path(filename).suffix.lower()
    if extension not in {".jpg", ".jpeg", ".png"}:
        extension = ".jpg"
    return f"profiles/{instance.pk or 'new-user'}/photo-{uuid4().hex}{extension}"


class AutomationSettingsManager(models.Manager):
    def get_or_create_default(self):
        return self.get_or_create(
            pk=1,
            defaults={
                "scoring_mode": AutomationSettings.ScoringMode.team_leader,
                "score_drop_threshold": 10,
                "attendance_time_in_open_time": time(6, 0),
                "attendance_cutoff_time": time(8, 0),
                "attendance_expected_time_out": time(19, 0),
                "attendance_break_start_time": time(12, 0),
                "attendance_break_end_time": time(13, 0),
                "attendance_grace_minutes": 0,
                "attendance_on_time_points": 0.5,
                "attendance_late_points": 0.25,
                "attendance_time_out_points": 0.5,
                "attendance_no_time_out_points": 0.0,
                "attendance_excused_points": 1.0,
                "attendance_absent_points": 0.0,
                "attendance_working_days": "1,2,3,4,5",
                "attendance_holidays": "",
            },
        )[0]


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
        management = "management", "Management"
        team_leader = "team_leader", "Team Leader"
        staff = "staff", "Staff"
        hr_manager = "hr_manager", "Management"
        employee = "employee", "Staff"
        client = "client", "Client"

    name = models.CharField(max_length=160)
    email = models.EmailField(max_length=255, unique=True, db_index=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.employee)
    phone_number = models.CharField(max_length=32, blank=True)
    photo = ImageField(
        upload_to=profile_photo_upload_to,
        blank=True,
        null=True,
        help_text="JPG or PNG, maximum 2 MB.",
    )
    notification_preferences = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    must_change_password = models.BooleanField(default=False)

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
    team_leader = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="team_members",
        limit_choices_to={"role": User.Role.team_leader},
    )
    name = models.CharField(max_length=160, db_index=True)
    department = models.ForeignKey(
        Department,
        on_delete=models.PROTECT,
        related_name="employees",
    )
    position = models.CharField(max_length=120)
    date_hired = models.DateField()

    class Status(models.TextChoices):
        active = "active", "Active"
        inactive = "inactive", "Inactive"
        on_leave = "on_leave", "On leave"

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.active,
    )
    attendance_rate = models.FloatField(null=True, blank=True)
    tenure_months = models.PositiveIntegerField(default=0)
    training_hours = models.FloatField(null=True, blank=True)

    class Meta:
        ordering = ("name",)

    def save(self, *args, **kwargs):
        if not self.employee_code:
            self.employee_code = f"EMP-{uuid4().hex[:12].upper()}"
        if self.date_hired:
            from datetime import date

            if isinstance(self.date_hired, str):
                self.date_hired = date.fromisoformat(self.date_hired)
            current_year, current_month = timezone.localdate().year, timezone.localdate().month
            hired_year, hired_month = self.date_hired.year, self.date_hired.month
            self.tenure_months = max(
                0,
                (current_year - hired_year) * 12 + (current_month - hired_month),
            )
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} ({self.employee_code})"


class AttendanceRecord(models.Model):
    class Status(models.TextChoices):
        present = "present", "On time"
        late = "late", "Late"
        early_out = "early_out", "Early out"
        no_time_out = "no_time_out", "No Time Out"
        excused = "excused", "Excused"
        absent = "absent", "Absent"

    class TimeOutStatus(models.TextChoices):
        on_time = "on_time", "On time"
        early_out = "early_out", "Early out"
        no_time_out = "no_time_out", "No Time Out"
        excused = "excused", "Excused"

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="attendance_records",
        null=True,
        blank=True,
    )
    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name="attendance_records",
        null=True,
        blank=True,
    )
    work_date = models.DateField()
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.present,
    )
    time_in_status = models.CharField(max_length=12, blank=True, default="")
    time_out_status = models.CharField(
        max_length=16,
        choices=TimeOutStatus.choices,
        blank=True,
        default="",
    )
    clock_in_at = models.DateTimeField(null=True, blank=True)
    clock_out_at = models.DateTimeField(null=True, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    accuracy = models.FloatField(null=True, blank=True)
    distance_from_office = models.FloatField(null=True, blank=True)
    is_outside_premises = models.BooleanField(null=True, blank=True)
    outside_premises_reason = models.TextField(blank=True, default="")
    minutes_late = models.PositiveIntegerField(null=True, blank=True)
    hours = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    notes = models.TextField(blank=True)
    correction_reason = models.TextField(blank=True)
    corrected_at = models.DateTimeField(null=True, blank=True)
    corrected_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_corrections",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-work_date", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "work_date"),
                name="unique_employee_attendance_date",
            ),
            models.UniqueConstraint(
                fields=("user", "work_date"),
                condition=models.Q(user__isnull=False),
                name="unique_user_attendance_date",
            ),
        ]

    def __str__(self):
        account = self.user or (self.employee.user if self.employee_id else None)
        name = account.name if account else self.employee.name if self.employee_id else "Unknown user"
        return f"{name} · {self.work_date} · {self.get_status_display()}"

    @property
    def account(self):
        if self.user_id:
            return self.user
        if self.employee_id:
            return self.employee.user
        return None

    @property
    def display_name(self):
        account = self.account
        return account.name if account else self.employee.name if self.employee_id else "Unknown user"

    @property
    def attendance_status(self):
        if self.status in (self.Status.absent, self.Status.excused):
            return self.get_status_display()
        if self.time_out_status == self.TimeOutStatus.excused:
            return self.TimeOutStatus.excused.label
        if self.status == self.Status.no_time_out or self.time_out_status == self.TimeOutStatus.no_time_out:
            return self.Status.no_time_out.label
        if self.status == self.Status.early_out or self.time_out_status == self.TimeOutStatus.early_out:
            return self.Status.early_out.label
        if self.status == self.Status.late or self.time_in_status == "late":
            return self.Status.late.label
        return self.Status.present.label


class OfficeLocation(models.Model):
    class OutsideTimeInPolicy(models.TextChoices):
        allow = "allow", "Allow and notify"
        require_reason = "require_reason", "Require a reason"
        block = "block", "Block Time In"

    name = models.CharField(max_length=120, unique=True)
    latitude = models.FloatField()
    longitude = models.FloatField()
    allowed_radius_meters = models.PositiveIntegerField(default=100)
    outside_time_in_policy = models.CharField(
        max_length=20,
        choices=OutsideTimeInPolicy.choices,
        default=OutsideTimeInPolicy.block,
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("name",)

    def clean(self):
        super().clean()
        errors = {}
        if self.latitude is None or not -90 <= self.latitude <= 90:
            errors["latitude"] = "Latitude must be between -90 and 90."
        if self.longitude is None or not -180 <= self.longitude <= 180:
            errors["longitude"] = "Longitude must be between -180 and 180."
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.name


class AttendanceBreak(models.Model):
    attendance = models.ForeignKey(
        AttendanceRecord,
        on_delete=models.CASCADE,
        related_name="breaks",
    )
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    auto_end_at = models.DateTimeField()
    auto_ended = models.BooleanField(default=False)

    class Meta:
        ordering = ("started_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("attendance",),
                condition=models.Q(ended_at__isnull=True),
                name="unique_active_attendance_break",
            )
        ]


class AttendanceCorrection(models.Model):
    record = models.ForeignKey(
        AttendanceRecord,
        on_delete=models.CASCADE,
        related_name="corrections",
    )
    changed_at = models.DateTimeField(auto_now_add=True)
    changed_by = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="attendance_correction_audit",
    )
    old_status = models.CharField(max_length=16, choices=AttendanceRecord.Status.choices)
    new_status = models.CharField(max_length=16, choices=AttendanceRecord.Status.choices)
    old_clock_in_at = models.DateTimeField(null=True, blank=True)
    new_clock_in_at = models.DateTimeField(null=True, blank=True)
    old_clock_out_at = models.DateTimeField(null=True, blank=True)
    new_clock_out_at = models.DateTimeField(null=True, blank=True)
    old_time_out_status = models.CharField(max_length=16, blank=True, default="")
    new_time_out_status = models.CharField(max_length=16, blank=True, default="")
    reason = models.TextField()

    class Meta:
        ordering = ("-changed_at", "-id")

    def __str__(self):
        return f"{self.record} correction by {self.changed_by}"


class Submission(models.Model):
    class Status(models.TextChoices):
        pending = "pending", "Pending"
        under_review = "under_review", "Under review"
        reviewed = "reviewed", "Reviewed"

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="submissions",
    )
    title = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    file = models.FileField(upload_to="submissions/%Y/%m/%d/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.pending,
    )
    reviewer_comment = models.TextField(blank=True)
    is_late = models.BooleanField(default=False)

    class Meta:
        ordering = ("-uploaded_at", "-id")

    def __str__(self):
        return f"{self.title} · {self.employee.name}"


class Evaluation(models.Model):
    class Phase(models.TextChoices):
        draft = "draft", "Draft"
        team_leader = "team_leader", "Team Leader review"
        management = "management", "Management review"
        finalized = "finalized", "Finalized"

    class Status(models.TextChoices):
        draft = "draft", "Draft"
        pending = "pending", "Pending"
        completed = "completed", "Completed"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="evaluations")
    evaluator = models.ForeignKey(User, on_delete=models.PROTECT, related_name="evaluations_written")
    date = models.DateField(db_index=True)
    productivity_score = models.FloatField(null=True, blank=True)
    attendance_score = models.FloatField(null=True, blank=True)
    days_present = models.PositiveIntegerField(null=True, blank=True)
    working_days = models.PositiveIntegerField(null=True, blank=True)
    quality_score = models.FloatField(null=True, blank=True)
    teamwork_score = models.FloatField(null=True, blank=True)
    communication_score = models.FloatField(null=True, blank=True)
    comments = models.TextField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.completed)
    phase = models.CharField(
        max_length=20,
        choices=Phase.choices,
        default=Phase.draft,
    )
    team_leader_rating = models.FloatField(null=True, blank=True)
    management_rating = models.FloatField(null=True, blank=True)
    hybrid_score = models.FloatField(null=True, blank=True)
    cycle_month = models.DateField(null=True, blank=True, db_index=True)
    assigned_to = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assigned_evaluations",
    )
    auto_score = models.FloatField(null=True, blank=True)
    auto_data = models.JSONField(default=dict, blank=True)
    reminder_sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-date", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("employee", "cycle_month"),
                condition=models.Q(cycle_month__isnull=False),
                name="unique_employee_evaluation_cycle",
            )
        ]

    @property
    def overall_score(self):
        if self.auto_score is not None:
            return round(self.auto_score, 2)
        scores = (
            self.productivity_score,
            self.attendance_score,
            self.quality_score,
            self.teamwork_score,
            self.communication_score,
        )
        if any(score is None for score in scores):
            return None
        return round(sum(scores) / len(scores), 2)


class Prediction(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="predictions")
    evaluation = models.ForeignKey(
        Evaluation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="predictions",
    )
    predicted_category = models.CharField(max_length=80)
    confidence_score = models.FloatField()
    feature_importances = models.JSONField(default=dict)
    model_source = models.CharField(max_length=255, default="")
    model_version = models.CharField(max_length=40, default="")
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-id")
        constraints = [
            models.UniqueConstraint(
                fields=("evaluation",),
                condition=models.Q(evaluation__isnull=False),
                name="unique_prediction_per_evaluation",
            )
        ]

    @property
    def classification_tone(self):
        category = self.predicted_category.casefold()
        if "low" in category or "risk" in category:
            return "low"
        if "average" in category:
            return "average"
        return "high"


class AutomationSettings(models.Model):
    class ScoringMode(models.TextChoices):
        team_leader = "team_leader", "Team Leader ratings"
        automatic = "automatic", "Auto-scored from tracked data"

    scoring_mode = models.CharField(
        max_length=20,
        choices=ScoringMode.choices,
        default=ScoringMode.team_leader,
    )
    score_drop_threshold = models.FloatField(
        default=10,
        validators=[validate_score_drop_threshold],
    )
    attendance_time_in_open_time = models.TimeField(default="06:00")
    attendance_cutoff_time = models.TimeField(default="08:00")
    attendance_expected_time_out = models.TimeField(default="19:00")
    attendance_break_start_time = models.TimeField(default="12:00")
    attendance_break_end_time = models.TimeField(default="13:00")
    attendance_grace_minutes = models.PositiveIntegerField(default=0)
    attendance_on_time_points = models.FloatField(default=0.5)
    attendance_late_points = models.FloatField(default=0.25)
    attendance_time_out_points = models.FloatField(default=0.5)
    attendance_no_time_out_points = models.FloatField(default=0.0)
    attendance_excused_points = models.FloatField(default=1.0)
    attendance_absent_points = models.FloatField(default=0.0)
    attendance_working_days = models.CharField(
        max_length=100,
        default="1,2,3,4,5",
        help_text="Comma-separated ISO weekday numbers: 1=Monday through 7=Sunday.",
    )
    attendance_holidays = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated YYYY-MM-DD dates.",
    )
    updated_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="automation_settings_updates",
    )
    updated_at = models.DateTimeField(auto_now=True)

    objects = AutomationSettingsManager()

    class Meta:
        verbose_name = "automation settings"
        verbose_name_plural = "automation settings"


class Notification(models.Model):
    class Category(models.TextChoices):
        automatic = "automatic", "Performance alert"
        reminder = "reminder", "Evaluation reminder"
        manual = "manual", "Message"

    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    sender = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="sent_notifications",
    )
    category = models.CharField(max_length=16, choices=Category.choices)
    title = models.CharField(max_length=160)
    message = models.TextField()
    dedupe_key = models.CharField(max_length=200, null=True, blank=True, unique=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")


class TrustedDevice(models.Model):
    class Status(models.TextChoices):
        pending = "pending", "Pending"
        approved = "approved", "Approved"
        revoked = "revoked", "Revoked"

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="trusted_devices")
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField(max_length=160)
    user_agent = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.pending)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    last_used_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="approved_trusted_devices",
    )

    class Meta:
        ordering = ("-created_at", "-pk")

    def __str__(self):
        return f"{self.label} ({self.user.email})"


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


class FirestoreSyncOutbox(models.Model):
    class Entity(models.TextChoices):
        employee = "employee", "Employee"
        evaluation = "evaluation", "Evaluation"
        summaries = "summaries", "Dashboard summaries"

    class Operation(models.TextChoices):
        upsert = "upsert", "Upsert"
        delete = "delete", "Delete"
        rebuild = "rebuild", "Rebuild summaries"

    entity = models.CharField(max_length=16, choices=Entity.choices)
    record_id = models.PositiveBigIntegerField()
    operation = models.CharField(max_length=16, choices=Operation.choices)
    attempts = models.PositiveIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error_type = models.CharField(max_length=120, blank=True, default="")
    synced_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=("entity", "record_id"),
                name="unique_firestore_sync_entity_record",
            )
        ]

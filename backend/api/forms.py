from io import BytesIO
from pathlib import Path
from uuid import uuid4

from django import forms
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.fields.files import FieldFile
from PIL import Image, UnidentifiedImageError

from .access import employee_queryset_for, is_management
from .ml import ModelUnavailableError, supported_department_names
from .models import AutomationSettings, Department, Employee, Evaluation, Submission, User


PROFILE_IMAGE_MAX_BYTES = 2 * 1024 * 1024
PROFILE_IMAGE_TYPES = {"image/jpeg", "image/png"}


def unsupported_department_warning(name):
    if not name:
        return ""
    try:
        supported = supported_department_names()
    except ModelUnavailableError:
        return ""
    if name.casefold() in {department.casefold() for department in supported}:
        return ""
    return (
        f"Warning: the trained prediction model does not support '{name}'. "
        f"Supported departments: {', '.join(supported)}. Predictions cannot be "
        "generated for employees in this department."
    )


def infer_profile_photo_extension(uploaded_file):
    content_type = getattr(uploaded_file, "content_type", "") or ""
    content_type = content_type.lower()
    if content_type == "image/png":
        return ".png"
    if content_type in {"image/jpeg", "image/jpg"}:
        return ".jpg"
    suffix = Path(getattr(uploaded_file, "name", "")).suffix.lower()
    if suffix == ".png":
        return ".png"
    return ".jpg"


def validate_profile_image(uploaded_file):
    if not uploaded_file or isinstance(uploaded_file, FieldFile):
        return None
    uploaded_file.seek(0)
    raw_data = uploaded_file.read()
    uploaded_file.seek(0)
    if uploaded_file.size > PROFILE_IMAGE_MAX_BYTES:
        raise ValidationError("Profile photos must be 2 MB or smaller.")
    content_type = getattr(uploaded_file, "content_type", "") or ""
    if content_type and content_type.lower() not in PROFILE_IMAGE_TYPES:
        raise ValidationError("Profile photos must be JPG or PNG images.")
    try:
        stream = BytesIO(raw_data)
        image = Image.open(stream)
        actual_format = (image.format or "").upper()
        image.verify()
        if actual_format not in {"PNG", "JPEG", "JPG"}:
            raise ValidationError("The selected file is not a valid JPG or PNG image.")
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError("The selected file is not a valid JPG or PNG image.")
    finally:
        uploaded_file.seek(0)
    return uploaded_file


INPUT_CLASS = (
    "mt-1 block w-full rounded-lg border border-slate-200 bg-white px-3 py-2.5 "
    "text-sm text-lexie-ink outline-none focus:border-lexie-teal focus:ring-4 "
    "focus:ring-lexie-teal/10"
)


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASS


class DepartmentForm(StyledModelForm):
    is_active = forms.ChoiceField(
        label="Status",
        choices=(("active", "Active"), ("inactive", "Inactive")),
        initial="active",
        widget=forms.Select(attrs={"class": "whitespace-nowrap"}),
    )

    class Meta:
        model = Department
        fields = ("name", "description", "is_active")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            self.fields.pop("is_active")
        self.department_warning = ""

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        self.department_warning = unsupported_department_warning(name)
        if self.department_warning:
            self.fields["name"].help_text = self.department_warning
        return name

    def clean_is_active(self):
        status = self.cleaned_data.get("is_active")
        if status == "inactive" and self.instance.pk:
            if Employee.objects.filter(
                department=self.instance,
                status=Employee.Status.active,
            ).exists():
                raise ValidationError(
                    "Cannot deactivate this department while active employees remain."
                )
            return False
        return status == "active"


class EmployeeForm(StyledModelForm):
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="No linked account",
    )

    class Meta:
        model = Employee
        fields = (
            "user",
            "team_leader",
            "name",
            "department",
            "position",
            "date_hired",
        )
        widgets = {"date_hired": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.department_warning = ""
        self.fields["user"].queryset = User.objects.filter(
            role__in=(User.Role.staff, User.Role.employee),
            is_active=True,
        ).order_by("name")
        self.fields["team_leader"].queryset = User.objects.filter(
            role=User.Role.team_leader,
            is_active=True,
        ).order_by("name")
        if actor is not None and not is_management(actor):
            self.fields.pop("user")
            self.fields.pop("team_leader")
            self.fields.pop("department")

    def clean_department(self):
        department = self.cleaned_data.get("department")
        if department:
            self.department_warning = unsupported_department_warning(department.name)
            if self.department_warning:
                self.fields["department"].help_text = self.department_warning
        return department

    def clean_user(self):
        user = self.cleaned_data.get("user")
        if user and Employee.objects.filter(user=user).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This account is already linked to an employee.")
        return user

    def save(self, commit=True):
        employee = super().save(commit=False)
        employee.status = Employee.Status.active
        if commit:
            employee.save()
            self.save_m2m()
        return employee


class EmployeeStatusForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = ("status",)
        choices = Employee.Status.choices


class EvaluationForm(StyledModelForm):
    class Meta:
        model = Evaluation
        fields = (
            "employee",
            "date",
            "productivity_score",
            "attendance_score",
            "quality_score",
            "teamwork_score",
            "communication_score",
            "comments",
        )
        widgets = {
            "date": forms.DateInput(attrs={"type": "date"}),
            "comments": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, actor=None, scope_user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        scoped_user = scope_user or actor
        self.fields["employee"].queryset = employee_queryset_for(scoped_user).order_by("name") if scoped_user else Employee.objects.select_related("department").order_by("name")
        for field in (
            "productivity_score",
            "attendance_score",
            "quality_score",
            "teamwork_score",
            "communication_score",
        ):
            self.fields[field].required = True
            self.fields[field].widget.attrs.update(
                {"min": "0", "max": "100", "step": "0.01"}
            )

    def clean(self):
        cleaned_data = super().clean()
        for field in (
            "productivity_score",
            "attendance_score",
            "quality_score",
            "teamwork_score",
            "communication_score",
        ):
            score = cleaned_data.get(field)
            if score is not None and not 0 <= score <= 100:
                self.add_error(field, "Score must be between 0 and 100.")
        return cleaned_data

    def save(self, commit=True):
        evaluation = super().save(commit=False)
        if self.actor is not None:
            evaluation.evaluator = self.actor
        score_fields = (
            evaluation.productivity_score,
            evaluation.attendance_score,
            evaluation.quality_score,
            evaluation.teamwork_score,
            evaluation.communication_score,
        )
        evaluation.status = (
            Evaluation.Status.completed
            if all(score is not None for score in score_fields)
            else Evaluation.Status.pending
        )
        evaluation.auto_score = None
        if commit:
            evaluation.save()
            self.save_m2m()
        return evaluation


class NotificationComposeForm(forms.Form):
    recipient = forms.ModelChoiceField(queryset=User.objects.none())
    title = forms.CharField(max_length=160, initial="A message from your manager")
    message = forms.CharField(widget=forms.Textarea(attrs={"rows": 4}))

    def __init__(self, *args, actor, employee=None, **kwargs):
        super().__init__(*args, **kwargs)
        recipients = User.objects.filter(is_active=True)
        if is_management(actor):
            recipients = recipients.filter(
                Q(role=User.Role.team_leader)
                | Q(employee__isnull=False, role__in=(User.Role.staff, User.Role.employee))
            )
        else:
            recipients = recipients.filter(
                pk__in=employee_queryset_for(actor)
                .filter(user__isnull=False)
                .values_list("user_id", flat=True)
            )
        self.fields["recipient"].queryset = recipients.order_by("name")
        if employee is not None and employee.user_id:
            self.initial.setdefault("recipient", employee.user_id)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASS


class AutomationSettingsForm(StyledModelForm):
    class Meta:
        model = AutomationSettings
        fields = (
            "scoring_mode",
            "score_drop_threshold",
            "attendance_time_in_open_time",
            "attendance_cutoff_time",
            "attendance_expected_time_out",
            "attendance_break_start_time",
            "attendance_break_end_time",
            "attendance_grace_minutes",
            "attendance_on_time_points",
            "attendance_late_points",
            "attendance_time_out_points",
            "attendance_no_time_out_points",
            "attendance_excused_points",
            "attendance_absent_points",
            "attendance_working_days",
            "attendance_holidays",
        )
        widgets = {
            "attendance_time_in_open_time": forms.TimeInput(attrs={"type": "time"}),
            "attendance_cutoff_time": forms.TimeInput(attrs={"type": "time"}),
            "attendance_expected_time_out": forms.TimeInput(attrs={"type": "time"}),
            "attendance_break_start_time": forms.TimeInput(attrs={"type": "time"}),
            "attendance_break_end_time": forms.TimeInput(attrs={"type": "time"}),
            "attendance_holidays": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "attendance_break_start_time": "Break start time (Asia/Manila)",
            "attendance_break_end_time": "Break end time (Asia/Manila)",
        }

    def clean_score_drop_threshold(self):
        threshold = self.cleaned_data["score_drop_threshold"]
        if threshold <= 0 or threshold > 100:
            raise ValidationError("The score-drop threshold must be greater than 0 and at most 100.")
        return threshold

    def clean_attendance_grace_minutes(self):
        value = self.cleaned_data["attendance_grace_minutes"]
        if value < 0:
            raise ValidationError("Grace period cannot be negative.")
        return value

    def clean(self):
        cleaned_data = super().clean()
        point_fields = (
            "attendance_on_time_points",
            "attendance_late_points",
            "attendance_time_out_points",
            "attendance_no_time_out_points",
            "attendance_excused_points",
            "attendance_absent_points",
        )
        for field_name in point_fields:
            value = cleaned_data.get(field_name)
            if value is not None and not 0 <= value <= 1:
                self.add_error(field_name, "Attendance points must be between 0 and 1.")
        open_time = cleaned_data.get("attendance_time_in_open_time")
        cutoff = cleaned_data.get("attendance_cutoff_time")
        expected_out = cleaned_data.get("attendance_expected_time_out")
        break_start = cleaned_data.get("attendance_break_start_time")
        break_end = cleaned_data.get("attendance_break_end_time")
        if open_time and cutoff and open_time > cutoff:
            self.add_error("attendance_cutoff_time", "The on-time cutoff must not be before Time In opens.")
        if open_time and expected_out and expected_out <= open_time:
            self.add_error("attendance_expected_time_out", "Expected Time Out must be after Time In opens.")
        if break_start and break_end and break_end <= break_start:
            self.add_error("attendance_break_end_time", "Break end time must be after break start time.")
        return cleaned_data


class SubmissionForm(StyledModelForm):
    class Meta:
        model = Submission
        fields = ("title", "description", "file")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].widget.attrs.update(
            {"class": "mt-1 block w-full text-sm text-slate-600 file:mr-3"}
        )

    def clean_file(self):
        uploaded_file = self.cleaned_data.get("file")
        if not uploaded_file:
            raise ValidationError("Please select a file to upload.")
        allowed_extensions = {".pdf", ".docx", ".xlsx", ".png", ".jpg", ".jpeg"}
        extension = Path(uploaded_file.name).suffix.lower()
        if extension not in allowed_extensions:
            raise ValidationError(
                "Only PDF, DOCX, XLSX, PNG, or JPG files are supported."
            )
        if uploaded_file.size > 5 * 1024 * 1024:
            raise ValidationError("The file must be 5 MB or smaller.")
        return uploaded_file


class SubmissionReviewForm(forms.Form):
    status = forms.ChoiceField(
        choices=(("pending", "Pending"), ("under_review", "Under review"), ("reviewed", "Reviewed")),
        required=True,
    )
    reviewer_comment = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3}),
    )


class AccountCreateForm(UserCreationForm):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        empty_label="No linked employee",
        help_text="Employee accounts must be linked to their employee profile.",
    )
    role = forms.ChoiceField(choices=())

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("name", "email", "role", "employee")

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        roles = [
            (User.Role.team_leader, "Team Leader"),
            (User.Role.staff, "Staff"),
            (User.Role.employee, "Staff (legacy)"),
        ]
        if actor is None or actor.role == User.Role.admin:
            roles = [
                (User.Role.admin, "Admin"),
                (User.Role.management, "Management"),
                *roles,
            ]
        self.fields["role"].choices = roles
        self.fields["employee"].queryset = Employee.objects.filter(
            user__isnull=True
        ).order_by("name")
        self.fields["email"].widget.attrs["class"] = INPUT_CLASS
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASS

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def clean(self):
        cleaned_data = super().clean()
        role = cleaned_data.get("role")
        employee = cleaned_data.get("employee")
        if role not in {User.Role.staff, User.Role.employee} and employee is not None:
            self.add_error("employee", "Only Staff accounts can be linked to an employee profile.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.role = self.cleaned_data["role"]
        user.is_staff = user.role == User.Role.admin
        user.is_superuser = user.role == User.Role.admin
        if commit:
            user.save()
            employee = self.cleaned_data.get("employee")
            if employee is not None:
                employee.user = user
                employee.save(update_fields=("user",))
        return user


class AccountEditForm(StyledModelForm):
    photo = forms.FileField(
        required=False,
        label="Profile photo",
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png"}),
    )
    remove_photo = forms.BooleanField(required=False, label="Remove photo")
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        empty_label="No linked employee",
    )
    team_leader = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="No Team Leader",
    )
    role = forms.ChoiceField(
        choices=(
            (User.Role.admin, "Admin"),
            (User.Role.management, "Management"),
            (User.Role.team_leader, "Team Leader"),
            (User.Role.staff, "Staff"),
            (User.Role.employee, "Staff (legacy)"),
        )
    )

    class Meta:
        model = User
        fields = ("name", "email", "role", "photo", "is_active")

    def __init__(self, *args, **kwargs):
        self.actor = kwargs.pop("actor", None)
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.select_related("department").order_by("name")
        self.fields["team_leader"].queryset = User.objects.filter(
            role=User.Role.team_leader,
            is_active=True,
        ).order_by("name")
        if self.actor is not None and self.actor.role == User.Role.management:
            self.fields["role"].choices = (
                (User.Role.team_leader, "Team Leader"),
                (User.Role.staff, "Staff"),
                (User.Role.employee, "Staff (legacy)"),
            )
        linked_employee = Employee.objects.filter(user=self.instance).first()
        if linked_employee:
            self.initial["employee"] = linked_employee
            self.initial["team_leader"] = linked_employee.team_leader_id
        legacy_roles = {
            User.Role.hr_manager: User.Role.management,
            User.Role.employee: User.Role.staff,
        }
        self.initial["role"] = legacy_roles.get(self.instance.role, self.instance.role)

    def clean_photo(self):
        uploaded_file = self.cleaned_data.get("photo")
        if isinstance(uploaded_file, FieldFile):
            return uploaded_file
        if uploaded_file:
            validate_profile_image(uploaded_file)
        return uploaded_file

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get("role") not in {User.Role.staff, User.Role.employee} and cleaned_data.get("employee"):
            self.add_error("employee", "Only Staff accounts can be linked to an employee profile.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("remove_photo"):
            if user.photo:
                user.photo.delete(save=False)
            user.photo = None
        elif self.cleaned_data.get("photo") and not isinstance(self.cleaned_data["photo"], FieldFile):
            uploaded_file = self.cleaned_data["photo"]
            extension = infer_profile_photo_extension(uploaded_file)
            safe_name = f"user-{user.pk or 'new'}-photo-{uuid4().hex}{extension}"
            user.photo.save(safe_name, uploaded_file, save=False)
        user.is_staff = user.role == User.Role.admin
        user.is_superuser = user.role == User.Role.admin
        if commit:
            user.save()
            previous = Employee.objects.filter(user=user).first()
            employee = self.cleaned_data.get("employee")
            if previous and previous != employee:
                previous.user = None
                previous.save(update_fields=("user",))
            if employee:
                employee.user = user
                employee.team_leader = self.cleaned_data.get("team_leader")
                employee.save(update_fields=("user", "team_leader"))
        return user


class ProfileForm(StyledModelForm):
    photo = forms.FileField(
        required=False,
        label="Profile photo",
        widget=forms.FileInput(attrs={"accept": "image/jpeg,image/png"}),
    )
    remove_photo = forms.BooleanField(required=False, label="Remove photo")

    class Meta:
        model = User
        fields = ("name", "email", "phone_number", "photo")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["photo"].help_text = "JPG or PNG, maximum 2 MB."

    def clean_photo(self):
        uploaded_file = self.cleaned_data.get("photo")
        if isinstance(uploaded_file, FieldFile):
            return uploaded_file
        if uploaded_file:
            validate_profile_image(uploaded_file)
        return uploaded_file

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        duplicate = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise ValidationError("An account with this email already exists.")
        return email

    def save(self, commit=True):
        user = super().save(commit=False)
        if self.cleaned_data.get("remove_photo"):
            if user.photo:
                user.photo.delete(save=False)
            user.photo = None
        elif self.cleaned_data.get("photo") and not isinstance(self.cleaned_data["photo"], FieldFile):
            uploaded_file = self.cleaned_data["photo"]
            extension = infer_profile_photo_extension(uploaded_file)
            safe_name = f"user-{user.pk or 'new'}-photo-{uuid4().hex}{extension}"
            user.photo.save(safe_name, uploaded_file, save=False)
        if commit:
            user.save()
        return user


class NotificationPreferencesForm(forms.Form):
    email_evaluations = forms.BooleanField(required=False)
    email_predictions = forms.BooleanField(required=False)
    weekly_summary = forms.BooleanField(required=False)

    def __init__(self, *args, preferences=None, **kwargs):
        initial_preferences = {
            "email_evaluations": True,
            "email_predictions": True,
            "weekly_summary": False,
        }
        if isinstance(preferences, dict):
            initial_preferences.update(
                {
                    key: preferences[key]
                    for key in initial_preferences
                    if isinstance(preferences.get(key), bool)
                }
            )
        kwargs.setdefault("initial", initial_preferences)
        super().__init__(*args, **kwargs)


class CurrentPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASS

    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1")
        validate_password(password, self.user)
        return password

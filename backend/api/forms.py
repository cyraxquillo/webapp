from uuid import uuid4

from django import forms
from django.contrib.auth.forms import PasswordChangeForm, UserCreationForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from .models import Department, Employee, Evaluation, User


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
    class Meta:
        model = Department
        fields = ("name", "description", "is_active")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
            "is_active": forms.CheckboxInput(
                attrs={"class": "h-4 w-4 rounded border-slate-300 text-lexie-teal"}
            ),
        }


class EmployeeForm(StyledModelForm):
    user = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="No linked account",
    )

    class Meta:
        model = Employee
        fields = (
            "employee_code",
            "user",
            "name",
            "department",
            "position",
            "date_hired",
            "status",
            "attendance_rate",
            "tenure_months",
            "training_hours",
        )
        widgets = {"date_hired": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["user"].queryset = User.objects.filter(
            role__in=(User.Role.staff, User.Role.employee),
            is_active=True,
        ).order_by("name")
        self.fields["employee_code"].required = False
        self.fields["attendance_rate"].help_text = "Enter a percentage from 0 to 100."

    def clean_employee_code(self):
        code = self.cleaned_data.get("employee_code")
        return code or f"EMP-{uuid4().hex[:12].upper()}"

    def clean_attendance_rate(self):
        value = self.cleaned_data["attendance_rate"]
        if not 0 <= value <= 100:
            raise ValidationError("Attendance must be between 0 and 100.")
        return value

    def clean_tenure_months(self):
        value = self.cleaned_data["tenure_months"]
        if value < 0:
            raise ValidationError("Tenure cannot be negative.")
        return value

    def clean_training_hours(self):
        value = self.cleaned_data["training_hours"]
        if value < 0:
            raise ValidationError("Training hours cannot be negative.")
        return value

    def clean_user(self):
        user = self.cleaned_data.get("user")
        if user and Employee.objects.filter(user=user).exclude(pk=self.instance.pk).exists():
            raise ValidationError("This account is already linked to an employee.")
        return user

    def save(self, commit=True):
        employee = super().save(commit=False)
        if commit:
            employee.save()
            self.save_m2m()
        return employee


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

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        self.fields["employee"].queryset = Employee.objects.select_related(
            "department"
        ).order_by("name")
        for field in (
            "productivity_score",
            "attendance_score",
            "quality_score",
            "teamwork_score",
            "communication_score",
        ):
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
        if commit:
            evaluation.save()
            self.save_m2m()
        return evaluation


class AccountCreateForm(UserCreationForm):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=False,
        empty_label="No linked employee",
        help_text="Employee accounts must be linked to their employee profile.",
    )
    role = forms.ChoiceField(
        choices=(
            (User.Role.admin, "Admin"),
            (User.Role.hr_manager, "HR / Manager"),
            (User.Role.employee, "Employee"),
        )
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("name", "email", "role", "employee")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
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
        if role == User.Role.employee and employee is None:
            self.add_error("employee", "Link an employee profile to this account.")
        if role != User.Role.employee and employee is not None:
            self.add_error("employee", "Only employee accounts can be linked here.")
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data["email"]
        user.role = self.cleaned_data["role"]
        user.is_staff = user.role == User.Role.admin
        if commit:
            user.save()
            employee = self.cleaned_data.get("employee")
            if employee is not None:
                employee.user = user
                employee.save(update_fields=("user",))
        return user


class ProfileForm(StyledModelForm):
    class Meta:
        model = User
        fields = ("name", "email")

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        duplicate = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise ValidationError("An account with this email already exists.")
        return email


class CurrentPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs["class"] = INPUT_CLASS

    def clean_new_password1(self):
        password = self.cleaned_data.get("new_password1")
        validate_password(password, self.user)
        return password

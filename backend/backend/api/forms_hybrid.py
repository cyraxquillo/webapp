from django import forms

from .access import employee_queryset_for, is_management
from .models import AttendanceRecord, Employee, Evaluation, User


class AttendanceRecordForm(forms.ModelForm):
    class Meta:
        model = AttendanceRecord
        fields = ("employee", "work_date", "status", "hours", "notes")
        widgets = {
            "work_date": forms.DateInput(attrs={"type": "date"}),
            "notes": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        if actor is not None:
            self.fields["employee"].queryset = employee_queryset_for(actor)


class HybridRatingForm(forms.ModelForm):
    class Meta:
        model = Evaluation
        fields = ("team_leader_rating", "management_rating", "comments")
        widgets = {
            "team_leader_rating": forms.NumberInput(
                attrs={"min": 0, "max": 100, "step": "0.01"}
            ),
            "management_rating": forms.NumberInput(
                attrs={"min": 0, "max": 100, "step": "0.01"}
            ),
            "comments": forms.Textarea(attrs={"rows": 4}),
        }

    def __init__(self, *args, actor=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.actor = actor
        if actor is not None:
            if actor.role == User.Role.team_leader:
                self.fields.pop("management_rating")
            elif actor.role in (User.Role.admin, User.Role.management, User.Role.hr_manager):
                self.fields.pop("team_leader_rating")
            else:
                self.fields.pop("team_leader_rating")
                self.fields.pop("management_rating")
        for field in self.fields.values():
            if field.name != "comments":
                field.required = False

    def clean(self):
        cleaned = super().clean()
        for field_name in ("team_leader_rating", "management_rating"):
            if field_name in self.cleaned_data:
                value = self.cleaned_data[field_name]
                if value is not None and not 0 <= value <= 100:
                    self.add_error(field_name, "Rating must be between 0 and 100.")
        return cleaned

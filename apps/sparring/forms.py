"""
Sparring forms.
"""

from django import forms

from apps.users.models import SkillLevel

from .models import SparringRequest


class SparringRequestForm(forms.ModelForm):
    """Form for creating/editing sparring request."""

    class Meta:
        model = SparringRequest
        fields = (
            "city",
            "desired_category",
            "description",
            "preferred_days",
            "preferred_time",
            "desired_partner_age_min",
            "desired_partner_age_max",
            "preferred_location",
        )
        widgets = {
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Город"}
            ),
            "desired_category": forms.Select(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "preferred_days": forms.TextInput(attrs={"class": "form-control"}),
            "preferred_time": forms.TextInput(attrs={"class": "form-control"}),
            "desired_partner_age_min": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 100}
            ),
            "desired_partner_age_max": forms.NumberInput(
                attrs={"class": "form-control", "min": 1, "max": 100}
            ),
            "preferred_location": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Название корта или района",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["desired_category"].required = False
        self.fields["desired_category"].choices = [("", "Любой уровень")] + [
            c for c in SkillLevel.choices
        ]
        self.fields["desired_partner_age_min"].required = False
        self.fields["desired_partner_age_max"].required = False
        self.fields["preferred_location"].required = False

    def clean(self):
        cleaned_data = super().clean()
        age_min = cleaned_data.get("desired_partner_age_min")
        age_max = cleaned_data.get("desired_partner_age_max")

        if age_min and age_max and age_min > age_max:
            raise forms.ValidationError(
                "Минимальный возраст не может быть больше максимального."
            )

        return cleaned_data

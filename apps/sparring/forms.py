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
        fields = ("city", "desired_category", "description", "preferred_days", "preferred_time")
        widgets = {
            "city": forms.TextInput(attrs={"class": "form-control", "placeholder": "Город"}),
            "desired_category": forms.Select(attrs={"class": "form-control"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 4}),
            "preferred_days": forms.TextInput(attrs={"class": "form-control"}),
            "preferred_time": forms.TextInput(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["desired_category"].required = False
        self.fields["desired_category"].choices = [("", "Любой уровень")] + [
            c for c in SkillLevel.choices
        ]

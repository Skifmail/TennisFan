"""
Sparring forms.
"""

from django import forms

from .models import SparringRequest


class SparringRequestForm(forms.ModelForm):
    """Form for creating sparring request."""

    class Meta:
        model = SparringRequest
        fields = ('city', 'desired_category', 'description', 'preferred_days', 'preferred_time')
        widgets = {
            'city': forms.Select(attrs={'class': 'form-control'}),
            'desired_category': forms.Select(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'preferred_days': forms.TextInput(attrs={'class': 'form-control'}),
            'preferred_time': forms.TextInput(attrs={'class': 'form-control'}),
        }

"""
Content app forms.
"""

from django import forms


class NewsCommentForm(forms.Form):
    """Form for adding a comment to a news article."""

    text = forms.CharField(
        label="Комментарий",
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 4,
                "placeholder": "Напишите ваш комментарий...",
            }
        ),
        max_length=2000,
    )

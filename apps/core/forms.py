"""
Core forms.
"""

from django import forms


class FeedbackForm(forms.Form):
    """Форма обратной связи. Только для зарегистрированных пользователей."""

    subject = forms.CharField(
        label="Тема",
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Кратко о чём сообщение"}
        ),
    )
    message = forms.CharField(
        label="Сообщение *",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 5, "placeholder": "Ваше сообщение"}
        ),
    )

    def clean_message(self):
        msg = (self.cleaned_data.get("message") or "").strip()
        if not msg:
            raise forms.ValidationError("Введите сообщение.")
        return msg

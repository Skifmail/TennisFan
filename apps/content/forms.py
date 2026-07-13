"""
Content app forms.
"""

from django import forms

from apps.content.models import RulesSection
from apps.content.rules_defaults import get_default_rules_body


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


class RulesSectionAdminForm(forms.ModelForm):
    """
    Форма раздела правил в админке.

    Если ``body`` в БД пустой, подставляет HTML из шаблона-фолбэка,
    чтобы редактор видел и мог править текущий текст сайта.
    """

    class Meta:
        model = RulesSection
        fields = ("slug", "title", "body")

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        instance = self.instance
        if not instance.pk:
            return
        if (instance.body or "").strip():
            return
        default_body = get_default_rules_body(instance.slug)
        if not default_body:
            return
        # Показываем захардкоженный текст в textarea при открытии формы.
        self.initial["body"] = default_body
        self.fields["body"].initial = default_body

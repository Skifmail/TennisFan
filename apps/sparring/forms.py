"""
Sparring forms.
"""

from django import forms

from apps.users.models import Player, SkillLevel

from .models import SparringPreferredGender, SparringRequest


class SparringRequestForm(forms.ModelForm):
    """Форма создания/редактирования заявки на одиночный спарринг (1×1)."""

    class Meta:
        model = SparringRequest
        fields = (
            "city",
            "preferred_gender",
            "desired_category",
            "description",
            "preferred_days",
            "preferred_time",
            "desired_partner_age_min",
            "desired_partner_age_max",
            "preferred_location",
            "is_friendly",
        )
        widgets = {
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Город"}
            ),
            "preferred_gender": forms.Select(attrs={"class": "form-control"}),
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
            "is_friendly": forms.CheckboxInput(attrs={"class": "form-check-input"}),
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


# ---------------------------------------------------------------------------
# Парный спарринг 2×2
# ---------------------------------------------------------------------------


class DoublesMatchRequestForm(forms.Form):
    """Форма создания заявки на парный матч 2×2."""

    city = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Город"}),
    )
    preferred_gender = forms.ChoiceField(
        label="Категория по полу",
        choices=[("", "Любой (без ограничений)")]
        + list(SparringPreferredGender.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="Любой — подходят любые составы. Смешанный — открытая категория. Микст — пары мужчина + женщина.",
    )
    desired_level = forms.ChoiceField(
        label="Желаемый уровень партнёров",
        choices=[("", "Любой уровень")] + list(SkillLevel.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "placeholder": "Описание"}
        ),
    )
    preferred_days = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Например: Пн, Ср, Пт"}
        ),
    )
    preferred_time = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Например: 18:00-21:00"}
        ),
    )
    desired_age_min = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 100}),
    )
    desired_age_max = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 100}),
    )
    preferred_location = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Название корта или района"}
        ),
    )
    is_friendly = forms.BooleanField(
        required=False,
        initial=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def clean(self):
        cleaned_data = super().clean()
        age_min = cleaned_data.get("desired_age_min")
        age_max = cleaned_data.get("desired_age_max")

        if age_min and age_max and age_min > age_max:
            raise forms.ValidationError(
                "Минимальный возраст не может быть больше максимального."
            )

        return cleaned_data


class DoublesJoinRequestForm(forms.Form):
    """Форма отклика на парный матч: в какую команду и с кем (один или пара)."""

    TARGET_AUTHOR = "author"
    TARGET_OPPONENT = "opponent"

    target_side = forms.ChoiceField(
        choices=[
            (TARGET_OPPONENT, "Команда соперников (играть против автора)"),
            (TARGET_AUTHOR, "В команду автора (играть вместе с автором)"),
        ],
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    partner_id = forms.IntegerField(
        required=False,
        widget=forms.HiddenInput,
    )


class DoublesAddPartnerForm(forms.Form):
    """Добавить партнёра в команду автора (выбор игрока)."""

    player_id = forms.ChoiceField(
        label="Игрок",
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    def __init__(self, *args, exclude_player_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = (
            Player.objects.filter(is_bye=False)
            .select_related("user")
            .order_by("user__last_name", "user__first_name")
        )
        if exclude_player_ids:
            qs = qs.exclude(pk__in=exclude_player_ids)
        self.fields["player_id"].choices = [("", "Выберите игрока")] + [
            (str(p.pk), str(p)) for p in qs[:500]
        ]

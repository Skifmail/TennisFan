"""
Sparring forms.
"""

from django import forms

from apps.core.sports import Sport, parse_sport_filter
from apps.users.models import Player, SkillLevel

from .models import SparringPreferredGender, SparringRequest


def sparring_form_error_messages(form: forms.BaseForm) -> list[str]:
    """Собрать тексты ошибок формы без повторов.

    Args:
        form: Форма создания или правки заявки на спарринг.

    Returns:
        list[str]: Сообщения, которые нужно показать над кнопкой.
    """
    seen: list[str] = []
    for errors in form.errors.values():
        for error in errors:
            text = str(error).strip()
            if text and text not in seen:
                seen.append(text)
    return seen


def _reject_age_out_of_range(
    form: forms.BaseForm,
    field_name: str,
    message: str,
) -> bool:
    """Добавить ошибку, если возраст указан и выходит за 1–100.

    Args:
        form: Форма с уже собранным ``cleaned_data``.
        field_name: Имя поля возраста.
        message: Текст ошибки.

    Returns:
        bool: True, если значение пригодно для сравнения «от» и «до».
    """
    value = form.cleaned_data.get(field_name)
    if value is None:
        return False
    if 1 <= value <= 100:
        return True
    form.add_error(field_name, message)
    return False


class SparringRequestForm(forms.ModelForm):
    """Форма создания/редактирования заявки на одиночный спарринг (1×1)."""

    class Meta:
        model = SparringRequest
        fields = (
            "sport",
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
            "sport": forms.HiddenInput(),
            "city": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Населённый пункт"}
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
        self.fields["sport"] = forms.CharField(
            required=False,
            widget=forms.HiddenInput(),
        )
        self.fields["desired_partner_age_min"].required = False
        self.fields["desired_partner_age_max"].required = False
        self.fields["preferred_location"].required = False
        self.fields["city"].error_messages["required"] = "Укажите населённый пункт."
        self.fields["description"].error_messages[
            "required"
        ] = "Напишите описание: когда и где хотите играть."
        self.fields["desired_partner_age_min"].error_messages[
            "invalid"
        ] = "Возраст партнёра «от» — целое число от 1 до 100."
        self.fields["desired_partner_age_max"].error_messages[
            "invalid"
        ] = "Возраст партнёра «до» — целое число от 1 до 100."
        self.fields["city"].widget.attrs[
            "data-sparring-required"
        ] = "Укажите населённый пункт."
        self.fields["description"].widget.attrs[
            "data-sparring-required"
        ] = "Напишите описание: когда и где хотите играть."
        self.fields["desired_partner_age_min"].widget.attrs.update(
            {
                "data-sparring-age": "min",
                "data-sparring-age-error": (
                    "Возраст партнёра «от» — целое число от 1 до 100."
                ),
            }
        )
        self.fields["desired_partner_age_max"].widget.attrs.update(
            {
                "data-sparring-age": "max",
                "data-sparring-age-error": (
                    "Возраст партнёра «до» — целое число от 1 до 100."
                ),
            }
        )

    def clean_sport(self) -> str:
        """Подставить теннис, если вид спорта не пришёл вместе с формой.

        Returns:
            str: Код вида спорта.
        """
        parsed = parse_sport_filter(
            str(self.cleaned_data.get("sport") or ""),
            default="tennis",
        )
        if parsed == "all":
            return "tennis"
        return parsed

    def clean(self):
        cleaned_data = super().clean()
        min_ok = _reject_age_out_of_range(
            self,
            "desired_partner_age_min",
            "Возраст партнёра «от» — целое число от 1 до 100.",
        )
        max_ok = _reject_age_out_of_range(
            self,
            "desired_partner_age_max",
            "Возраст партнёра «до» — целое число от 1 до 100.",
        )
        age_min = cleaned_data.get("desired_partner_age_min")
        age_max = cleaned_data.get("desired_partner_age_max")
        if min_ok and max_ok and age_min > age_max:
            self.add_error(
                None,
                "Минимальный возраст не может быть больше максимального.",
            )
        return cleaned_data


class SparringInviteForm(forms.Form):
    """Форма отправки приглашения на спарринг выбранному игроку."""

    invitee_id = forms.IntegerField(min_value=1, label="Игрок")
    is_friendly = forms.BooleanField(
        required=False,
        initial=False,
        label="Дружеская игра (без влияния на рейтинг и силу)",
    )
    proposed_date = forms.DateField(
        required=False,
        label="Предполагаемая дата игры",
        widget=forms.DateInput(
            attrs={"type": "date", "class": "form-control"},
        ),
    )


# ---------------------------------------------------------------------------
# Парный спарринг 2×2
# ---------------------------------------------------------------------------


class DoublesMatchRequestForm(forms.Form):
    """Форма создания заявки на парный матч 2×2."""

    sport = forms.ChoiceField(
        label="Вид спорта",
        choices=Sport.choices,
        required=False,
        widget=forms.HiddenInput(),
    )
    city = forms.CharField(
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Населённый пункт"}
        ),
    )
    preferred_gender = forms.ChoiceField(
        label="Категория по полу",
        choices=[("", "Любой (без ограничений)")]
        + list(SparringPreferredGender.choices),
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text=(
            "«Любой (без ограничений)» — без фильтра по составу. "
            "«Любой» — открытая категория по полу. "
            "«Микст» — пары мужчина + женщина."
        ),
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
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": 1,
                "max": 100,
                "data-sparring-age": "min",
                "data-sparring-age-error": (
                    "Возраст партнёров «от» — целое число от 1 до 100."
                ),
            }
        ),
        error_messages={
            "invalid": "Возраст партнёров «от» — целое число от 1 до 100.",
        },
    )
    desired_age_max = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "min": 1,
                "max": 100,
                "data-sparring-age": "max",
                "data-sparring-age-error": (
                    "Возраст партнёров «до» — целое число от 1 до 100."
                ),
            }
        ),
        error_messages={
            "invalid": "Возраст партнёров «до» — целое число от 1 до 100.",
        },
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
        min_ok = _reject_age_out_of_range(
            self,
            "desired_age_min",
            "Возраст партнёров «от» — целое число от 1 до 100.",
        )
        max_ok = _reject_age_out_of_range(
            self,
            "desired_age_max",
            "Возраст партнёров «до» — целое число от 1 до 100.",
        )
        age_min = cleaned_data.get("desired_age_min")
        age_max = cleaned_data.get("desired_age_max")
        if min_ok and max_ok and age_min > age_max:
            self.add_error(
                None,
                "Минимальный возраст не может быть больше максимального.",
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

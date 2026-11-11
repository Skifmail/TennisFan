"""Понятные формы семейного кабинета без скрытых полей полномочий."""

from collections.abc import Iterable
from typing import Any

from django import forms

from apps.users.forms import UserRegistrationForm

from .legal import ACCURACY_CONFIRMATION, REPRESENTATIVE_CONFIRMATION
from .models import (
    ChildProfile,
    GuardianLink,
    JuniorSparringRequest,
    JuniorTournament,
    LegalRelease,
)
from .rules import Ball


class ParentRegistrationForm(UserRegistrationForm):
    """Обычный аккаунт взрослого без теста уровня силы и профиля игрока."""

    adult_confirm = forms.BooleanField(label="Мне исполнилось 18 лет")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for key in ("city", "ntrp_level", "ntrp_quiz_payload"):
            self.fields.pop(key, None)
        self.fields["agree_legal"].label = (
            "Я согласен с обработкой моих персональных данных"
        )
        self.fields["agree_terms"].label = "Я принимаю пользовательское соглашение"


class ChildForm(forms.ModelForm):
    processing_accepted = forms.BooleanField(
        label="Я согласен с обработкой данных ребёнка по приведённому тексту"
    )
    legal_release = forms.ModelChoiceField(
        queryset=LegalRelease.objects.none(), widget=forms.HiddenInput
    )

    relation = forms.ChoiceField(
        label="Кем вы приходитесь ребёнку", choices=GuardianLink.Relation.choices
    )
    representative = forms.BooleanField(label=REPRESENTATIVE_CONFIRMATION)

    accurate = forms.BooleanField(label=ACCURACY_CONFIRMATION)

    def __init__(self, *args, identity_locked=False, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields.pop("processing_accepted")
            self.fields.pop("legal_release")
            self.fields.pop("relation")
            self.fields.pop("representative")
            self.fields.pop("accurate")
            if identity_locked:
                for key in ("first_name", "last_name", "birth_date", "gender"):
                    self.fields[key].disabled = True
                    self.fields[key].help_text = (
                        "Для исправления проверенных данных напишите на tennis@tennisfan.ru."
                    )
        else:
            from .services import ensure_default_legal_release

            release = ensure_default_legal_release()
            self.fields["legal_release"].queryset = LegalRelease.objects.filter(
                pk=release.pk
            )
            self.fields["legal_release"].initial = release
        self.fields["gender"].choices = [
            ("", "Выберите пол"),
            ("male", "Мальчик"),
            ("female", "Девочка"),
        ]

    class Meta:
        model = ChildProfile
        fields = (
            "first_name",
            "last_name",
            "birth_date",
            "city",
            "gender",
            "ball",
            "photo",
        )
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "photo": forms.FileInput(
                attrs={"accept": "image/jpeg,image/png,image/webp"}
            ),
        }
        help_texts = {
            "birth_date": "Нужна для проверки возраста. Публично не показывается.",
            "photo": "JPG, PNG или WebP, до 5 МБ. Фото видно только в семейном кабинете.",
            "ball": "Текущая ступень подготовки. Возрастные условия турниров проверяются отдельно.",
        }

    def clean_first_name(self) -> str:
        return str(self.cleaned_data["first_name"]).strip()

    def clean_last_name(self) -> str:
        return str(self.cleaned_data["last_name"]).strip()

    remove_photo = forms.BooleanField(label="Удалить фото", required=False)
    photo_accepted = forms.BooleanField(
        label="Я разрешаю хранить фото ребёнка и показывать его только в семейном кабинете",
        required=False,
    )

    def clean_photo(self) -> Any:
        from .photos import prepare_photo

        photo = self.cleaned_data.get("photo")
        if photo and photo != self.instance.photo:
            return prepare_photo(photo)
        return photo

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if self.files.get("photo") and not data.get("photo_accepted"):
            self.add_error(
                "photo_accepted",
                "Подтвердите разрешение на фото для семейного кабинета.",
            )
        if self.files.get("photo") and data.get("remove_photo"):
            self.add_error(
                "photo",
                "Выберите одно действие: загрузить новое фото или удалить текущее.",
            )
        return data


class VerificationForm(forms.Form):
    approved = forms.ChoiceField(
        label="Решение",
        choices=[
            ("yes", "Подтвердить"),
            ("no", "Отклонить"),
            ("revoke", "Прекратить полномочия"),
        ],
    )
    evidence = forms.CharField(
        label="Способ проверки",
        max_length=255,
        required=False,
        help_text="Подтвердите личность и совершеннолетие взрослого, родство или опеку и дату рождения ребёнка. Без номеров и копий документов.",
    )
    reason = forms.CharField(label="Причина отказа", max_length=255, required=False)
    checked = forms.BooleanField(label="Проверка выполнена лично, сведения достоверны")


class RegistrationForm(forms.Form):
    child = forms.ModelChoiceField(
        label="Кого записать", queryset=ChildProfile.objects.none()
    )
    regulation_version = forms.IntegerField(widget=forms.HiddenInput)
    accepted = forms.BooleanField(
        label="Я прочитал и принимаю регламент, условия оплаты, снятия и возврата"
    )

    def __init__(self, *args, user, tournament, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = ChildProfile.objects.filter(
            guardians__user=user,
            guardians__status__in=["verified", "pending"],
            status__in=["draft", "active"],
        )
        self.fields["regulation_version"].initial = tournament.regulation_version


class ResultForm(forms.Form):
    score = forms.CharField(
        label="Счёт",
        required=False,
        max_length=80,
        help_text="Например 7:5 или 7:5; 3:7; 8:6. Каждая партия — через точку с запятой.",
    )
    walkover = forms.TypedChoiceField(
        label="Технический результат",
        choices=[
            ("", "Нет, матч сыгран"),
            ("1", "Победа участника 1"),
            ("2", "Победа участника 2"),
        ],
        coerce=int,
        empty_value=None,
        required=False,
    )
    reason = forms.CharField(
        label="Основание / причина исправления", max_length=255, required=False
    )

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        scores = []
        if not data.get("walkover"):
            try:
                for part in data.get("score", "").split(";"):
                    pair = [int(value.strip()) for value in part.split(":")]
                    if len(pair) != 2:
                        raise ValueError
                    scores.append(pair)
            except ValueError:
                self.add_error("score", "Введите счёт в формате 7:5; 3:7; 8:6.")
        data["scores"] = scores
        return data


class ProfileSupportForm(forms.Form):
    action = forms.ChoiceField(
        label="Операция",
        choices=[
            ("correct", "Исправить проверенные данные / сменить мяч"),
            ("add_guardian", "Добавить второго представителя на проверку"),
            ("transfer", "Передать историю совершеннолетнему владельцу"),
        ],
    )
    user_id = forms.IntegerField(
        label="ID существующего аккаунта второго представителя / совершеннолетнего владельца",
        required=False,
        min_value=1,
    )
    relation = forms.ChoiceField(
        label="Вид представительства", choices=GuardianLink.Relation.choices
    )
    first_name = forms.CharField(label="Новое имя", max_length=100, required=False)
    last_name = forms.CharField(label="Новая фамилия", max_length=100, required=False)
    birth_date = forms.DateField(
        label="Исправленная дата рождения",
        required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    city = forms.CharField(label="Новый город", max_length=100, required=False)
    ball = forms.ChoiceField(
        label="Новая ступень",
        required=False,
        choices=[("", "Без изменений"), *Ball.choices],
    )
    reason = forms.CharField(label="Причина операции", max_length=255)
    evidence = forms.CharField(
        label="Как проверены личность, полномочия и основания",
        max_length=255,
        help_text="Без номеров и копий документов. При передаче проверьте, что аккаунт принадлежит самому совершеннолетнему участнику и он подтвердил передачу.",
    )


class RescheduleForm(forms.Form):
    starts_at = forms.DateTimeField(
        label="Новая дата", widget=forms.DateTimeInput(attrs={"type": "datetime-local"})
    )
    reason = forms.CharField(label="Причина", max_length=255)


class ScheduleForm(forms.Form):
    scheduled_at = forms.DateTimeField(
        label="Дата и время матча",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )


class TournamentFilterForm(forms.Form):
    ball = forms.ChoiceField(
        label="Мяч", required=False, choices=[("", "Все мячи"), *Ball.choices]
    )
    city = forms.CharField(label="Город", required=False, max_length=100)
    age = forms.IntegerField(
        label="Возраст участника", required=False, min_value=0, max_value=17
    )
    gender = forms.ChoiceField(
        label="Категория",
        required=False,
        choices=[
            ("", "Все категории"),
            ("open", "Все участники"),
            ("male", "Мальчики"),
            ("female", "Девочки"),
        ],
    )
    date_from = forms.DateField(
        label="С даты", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    date_to = forms.DateField(
        label="По дату", required=False, widget=forms.DateInput(attrs={"type": "date"})
    )
    status = forms.ChoiceField(
        label="Статус",
        required=False,
        choices=[
            ("", "Все статусы"),
            ("open", "Регистрация открыта"),
            ("closed", "Регистрация закрыта"),
            ("active", "Идёт"),
            ("completed", "Завершён"),
            ("cancelled", "Отменён"),
        ],
    )
    available = forms.BooleanField(label="Есть свободные места", required=False)

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        if (
            data.get("date_from")
            and data.get("date_to")
            and data["date_from"] > data["date_to"]
        ):
            self.add_error("date_to", "Конец периода должен быть не раньше начала.")
        return data


class RegistrationDeadlineForm(forms.Form):
    """Проверяет дату продления или повторного открытия регистрации."""

    deadline = forms.DateTimeField(
        label="Принимать заявки до",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )


class ManualDrawForm(forms.Form):
    """Выбирает каждого подтверждённого ребёнка ровно один раз для первого круга."""

    def __init__(self, *args: Any, tournament: JuniorTournament, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        entries = list(
            tournament.entries.filter(status="confirmed").select_related("child")
        )
        count = len(entries)
        first_count = (1 << (count - 1).bit_length()) // 2 if count >= 2 else 0
        choices = [(entry.pk, str(entry.child)) for entry in entries]
        for pos in range(first_count):
            for index, side in ((pos, 1), (first_count + pos, 2)):
                if index < count:
                    self.fields[f"slot_{index}"] = forms.TypedChoiceField(
                        label=(
                            f"Матч {pos + 1} · проходит без игры"
                            if side == 1 and first_count + pos >= count
                            else f"Матч {pos + 1} · участник {side}"
                        ),
                        choices=[("", "Выберите ребёнка"), *choices],
                        coerce=int,
                    )
        self.entry_count = count

    def clean(self) -> dict[str, Any]:
        data = super().clean() or {}
        ids = [data.get(f"slot_{index}") for index in range(self.entry_count)]
        if self.entry_count < 2:
            raise forms.ValidationError(
                "Для первого круга нужны минимум два подтверждённых участника."
            )
        if len(set(ids)) != len(ids):
            raise forms.ValidationError("Каждого ребёнка выберите только один раз.")
        data["entry_order"] = ids
        return data


class JuniorSparringForm(forms.ModelForm):
    """Заявка представителя на товарищескую игру без влияния на рейтинг лиги."""

    child = forms.ModelChoiceField(
        label="Кто играет", queryset=ChildProfile.objects.none()
    )

    class Meta:
        model = JuniorSparringRequest
        fields = [
            "child",
            "city",
            "description",
            "partner_kind",
            "gender_preference",
            "desired_age_min",
            "desired_age_max",
            "preferred_days",
            "preferred_time",
            "preferred_location",
        ]
        widgets = {
            "description": forms.Textarea(
                attrs={
                    "rows": 4,
                    "placeholder": "Опыт ребёнка, мяч, формат игры. Контакты указывать не нужно.",
                }
            ),
        }

    def __init__(
        self, *args: Any, children: Iterable[ChildProfile], **kwargs: Any
    ) -> None:
        """Ограничивает выбор детей проверенными профилями этого взрослого."""
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = ChildProfile.objects.filter(
            pk__in=[child.pk for child in children]
        ).order_by("first_name", "pk")
        for field in ("desired_age_min", "desired_age_max"):
            self.fields[field].widget.attrs.update({"min": 3, "max": 99})
        self.fields["desired_age_min"].help_text = (
            "Пожелание, а не ограничение допуска."
        )
        self.fields["desired_age_max"].help_text = (
            "Пожелание, а не ограничение допуска."
        )


class JuniorSparringResponseForm(forms.Form):
    """Отклик взрослого игрока или представителя другого ребёнка."""

    child = forms.ModelChoiceField(
        label="Кто играет", queryset=ChildProfile.objects.none(), required=False
    )
    message = forms.CharField(
        label="Сообщение",
        max_length=500,
        required=False,
        widget=forms.Textarea(
            attrs={"rows": 3, "placeholder": "Формат игры, удобное время, корт."}
        ),
    )

    def __init__(
        self,
        *args: Any,
        children: Iterable[ChildProfile],
        allow_adult: bool,
        **kwargs: Any,
    ) -> None:
        """Показывает взрослую опцию только там, где заявка её допускает."""
        super().__init__(*args, **kwargs)
        self.fields["child"].queryset = ChildProfile.objects.filter(
            pk__in=[child.pk for child in children]
        ).order_by("first_name", "pk")
        self.fields["child"].required = not allow_adult
        self.fields["child"].empty_label = (
            "Играю сам(а) — взрослый игрок" if allow_adult else "Выберите ребёнка"
        )

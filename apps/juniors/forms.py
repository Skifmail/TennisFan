"""Понятные формы семейного кабинета без скрытых полей полномочий."""

from django import forms

from apps.users.forms import UserRegistrationForm

from .models import ChildProfile, GuardianLink
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
    relation = forms.ChoiceField(
        label="Кем вы приходитесь ребёнку", choices=GuardianLink.Relation.choices
    )
    representative = forms.BooleanField(
        label="Я совершеннолетний законный представитель ребёнка"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["gender"].choices = [
            ("", "Выберите пол"),
            ("male", "Мальчик"),
            ("female", "Девочка"),
        ]

    class Meta:
        model = ChildProfile
        fields = ("first_name", "last_name", "birth_date", "city", "gender", "ball")
        widgets = {
            "birth_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
        }
        help_texts = {
            "birth_date": "Нужна для проверки возраста. Публично не показывается.",
            "ball": "Текущая ступень подготовки. Возрастные условия турниров проверяются отдельно.",
        }

    def clean_first_name(self) -> str:
        return str(self.cleaned_data["first_name"]).strip()

    def clean_last_name(self) -> str:
        return str(self.cleaned_data["last_name"]).strip()


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
            guardians__user=user, guardians__status="verified", status="active"
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

    def clean(self):
        data = super().clean()
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

    def clean(self):
        data = super().clean()
        if (
            data.get("date_from")
            and data.get("date_to")
            and data["date_from"] > data["date_to"]
        ):
            self.add_error("date_to", "Конец периода должен быть не раньше начала.")
        return data

"""Понятные формы админки: создание кемпа в одном окне и подсказки к полям."""

from __future__ import annotations

from typing import Any

from django import forms
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .models import Camp, CampPackage, CampSession

DEFAULT_TERMS = (
    "Участие подтверждается координатором после согласования условий. "
    "Оплата и отмена — по договорённости с координатором."
)

SLUG_TRANSLITERATION_MAP: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def transliterate(value: str) -> str:
    """Переводит кириллицу в латиницу для безопасного адреса страницы."""
    return "".join(SLUG_TRANSLITERATION_MAP.get(char, char) for char in value.lower())


def build_unique_slug(title: str, instance_pk: int | None = None) -> str:
    """Создаёт уникальный адрес страницы из названия кемпа."""
    base = slugify(transliterate(title)) or "camp"
    candidate = base
    counter = 2
    queryset = Camp.objects.filter(slug=candidate)
    if instance_pk is not None:
        queryset = queryset.exclude(pk=instance_pk)
    while queryset.exists():
        candidate = f"{base}-{counter}"
        counter += 1
        queryset = Camp.objects.filter(slug=candidate)
        if instance_pk is not None:
            queryset = queryset.exclude(pk=instance_pk)
    return candidate


class SlugFromTitleMixin(forms.ModelForm):
    """Разрешает оставить адрес пустым и создаёт его из названия."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False

    def clean_slug(self) -> str:
        """Оставляет введённый адрес или создаёт уникальный из названия."""
        slug = str(self.cleaned_data.get("slug") or "").strip()
        if slug:
            return slug
        title = str(self.cleaned_data.get("title") or "").strip()
        return build_unique_slug(title, self.instance.pk)


class CampCreateForm(SlugFromTitleMixin):
    """Создаёт программу кемпа и сам придумывает адрес страницы."""

    class Meta:
        model = Camp
        fields = (
            "title",
            "slug",
            "subtitle",
            "audience",
            "description",
            "image",
            "levels",
            "program",
            "preparation",
        )
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "program": forms.Textarea(attrs={"rows": 5}),
            "preparation": forms.Textarea(attrs={"rows": 4}),
        }
        help_texts = {
            "title": "Как кемп будет называться на сайте. Например: «Теннисный кемп в Сочи».",
            "slug": "Адрес страницы. Можно оставить пустым — создадим автоматически из названия.",
            "subtitle": "Одна короткая фраза для карточки кемпа.",
            "audience": "Для кого программа: взрослые, дети или вся семья.",
            "description": "Что ждёт участников. Этот текст увидят на публичной странице.",
            "image": "Необязательно. Горизонтальная фотография до 2 МБ.",
            "levels": "Необязательно. Например: «Новички и любители».",
            "program": "Необязательно. Расписание или программа по дням.",
            "preparation": "Необязательно. Что взять с собой и как подготовиться.",
        }


class CampSessionCreateForm(forms.ModelForm):
    """Собирает даты, место и условия первого заезда."""

    class Meta:
        model = CampSession
        fields = (
            "start_date",
            "end_date",
            "registration_deadline",
            "country",
            "city",
            "venue",
            "surface",
            "capacity",
            "minimum_players",
            "min_age",
            "max_age",
            "tennis_hours",
            "players_per_court",
            "auto_reserve",
            "accommodation",
            "included",
            "excluded",
            "terms",
            "weather_policy",
            "contact_email",
            "contact_phone",
        )
        widgets = {
            "start_date": forms.DateInput(attrs={"type": "date"}),
            "end_date": forms.DateInput(attrs={"type": "date"}),
            "registration_deadline": forms.DateInput(attrs={"type": "date"}),
            "terms": forms.Textarea(attrs={"rows": 4}),
            "accommodation": forms.Textarea(attrs={"rows": 3}),
            "included": forms.Textarea(attrs={"rows": 3}),
            "excluded": forms.Textarea(attrs={"rows": 3}),
            "weather_policy": forms.Textarea(attrs={"rows": 3}),
        }
        help_texts = {
            "start_date": "Первый день заезда.",
            "end_date": "Последний день заезда.",
            "registration_deadline": "Необязательно. Последний день, когда можно подать заявку. Не позже даты начала.",
            "country": "Страна проведения. По умолчанию Россия.",
            "city": "Город, который увидят на сайте.",
            "venue": "Площадка и адрес: корты, отель или сборный пункт.",
            "surface": "Необязательно. Покрытие кортов и условия.",
            "capacity": "Сколько игроков можно принять. Сопровождающие не занимают спортивные места.",
            "minimum_players": "Минимум игроков, при котором заезд состоится.",
            "min_age": "Минимальный возраст участника.",
            "max_age": "Необязательно. Максимальный возраст участника.",
            "tennis_hours": "Сколько часов тенниса входит в программу.",
            "players_per_court": "Сколько игроков одновременно на одном корте.",
            "auto_reserve": "Если мест хватает всей группе, они сразу резервируются без оплаты. Иначе заявка попадает в лист ожидания.",
            "accommodation": "Необязательно. Где живут и как питаются участники.",
            "included": "Необязательно. Что уже входит в стоимость.",
            "excluded": "Необязательно. Что оплачивается отдельно.",
            "terms": "Как подтверждается участие, когда и как оплачивать, как отменить поездку.",
            "weather_policy": "Необязательно. Что происходит при непогоде и изменении программы.",
            "contact_email": "Необязательно. Показывается на странице, если заявка недоступна.",
            "contact_phone": "Необязательно. Показывается на странице, если заявка недоступна.",
        }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        if not self.is_bound:
            self.fields["terms"].initial = DEFAULT_TERMS


class CampPackageCreateForm(forms.ModelForm):
    """Описывает один вариант участия и его цену."""

    class Meta:
        model = CampPackage
        fields = (
            "name",
            "description",
            "price",
            "currency",
            "with_accommodation",
            "is_active",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class BaseCampPackageFormSet(forms.BaseModelFormSet):
    """Требует хотя бы один пакет и активный пакет для публикации."""

    require_active: bool = False

    def clean(self) -> None:
        """Проверяет, что пакеты вообще есть и готовы к публикации."""
        super().clean()
        if any(self.errors):
            return
        filled = [
            form
            for form in self.forms
            if form.cleaned_data
            and not form.cleaned_data.get("DELETE")
            and form.cleaned_data.get("name")
        ]
        if not filled:
            raise ValidationError("Добавьте хотя бы один пакет участия.")
        if self.require_active and not any(
            form.cleaned_data.get("is_active") for form in filled
        ):
            raise ValidationError(
                "Для публикации нужен хотя бы один пакет с галочкой "
                "«Доступен для заявок»."
            )


CampPackageFormSet = forms.modelformset_factory(
    CampPackage,
    form=CampPackageCreateForm,
    formset=BaseCampPackageFormSet,
    extra=3,
    can_delete=True,
)


class CampAdminForm(SlugFromTitleMixin):
    """Редактор уже созданного кемпа с теми же подсказками."""

    class Meta:
        model = Camp
        fields = "__all__"
        help_texts = {
            "title": "Название на сайте.",
            "slug": "Адрес страницы. Меняйте осторожно: старые ссылки перестанут работать.",
            "subtitle": "Короткая фраза для карточки кемпа.",
            "audience": "Для кого программа.",
            "description": "Что ждёт участников. Текст виден на публичной странице.",
            "image": "Горизонтальная фотография до 2 МБ.",
            "coaches": "Тренеры, которые ведут кемп.",
            "levels": "Необязательно. Уровень подготовки.",
            "program": "Необязательно. Программа по дням.",
            "preparation": "Необязательно. Что взять с собой.",
            "is_published": "Показывает кемп на сайте. Заявки принимает заезд со статусом «Идёт набор» и активным пакетом.",
        }


class CampSessionAdminForm(forms.ModelForm):
    """Редактор заезда с пояснениями к каждому блоку."""

    class Meta:
        model = CampSession
        fields = "__all__"
        help_texts = {
            "camp": "Кемп, к которому относится этот заезд.",
            "start_date": "Первый день заезда.",
            "end_date": "Последний день заезда.",
            "registration_deadline": "Последний день приёма заявок. Не позже даты начала.",
            "country": "Страна проведения.",
            "city": "Город на сайте.",
            "venue": "Площадка и адрес.",
            "court": "Необязательно. Корт платформы.",
            "surface": "Необязательно. Покрытие и условия кортов.",
            "capacity": "Сколько игроков можно принять.",
            "minimum_players": "Минимум игроков для проведения заезда.",
            "min_age": "Минимальный возраст участника.",
            "max_age": "Необязательно. Максимальный возраст.",
            "tennis_hours": "Часов тенниса за заезд.",
            "players_per_court": "Игроков одновременно на корте.",
            "auto_reserve": "Резервировать места без оплаты, если их хватает всей группе.",
            "accommodation": "Необязательно. Проживание и питание.",
            "included": "Необязательно. Что входит в стоимость.",
            "excluded": "Необязательно. Что оплачивается отдельно.",
            "terms": "Подтверждение, оплата и отмена.",
            "weather_policy": "Необязательно. Правила при непогоде.",
            "coordinator": "Сотрудник, который отвечает на заявки.",
            "contact_email": "Показывается на сайте, если заявка недоступна.",
            "contact_phone": "Показывается на сайте, если заявка недоступна.",
            "status": "«Идёт набор» — заезд виден на сайте и принимает заявки, если есть активный пакет. «Черновик» — скрыт.",
        }


class CampPackageInlineForm(forms.ModelForm):
    """Пакет внутри заезда с короткими пояснениями."""

    class Meta:
        model = CampPackage
        fields = (
            "name",
            "description",
            "price",
            "currency",
            "with_accommodation",
            "is_active",
        )
        help_texts = {
            "name": "Например: «Стандарт» или «С проживанием».",
            "description": "Что входит в пакет.",
            "price": "За игрока за весь заезд. Пустое поле — стоимость по запросу.",
            "currency": "Валюта цены.",
            "with_accommodation": "В пакет входит проживание.",
            "is_active": "Пакет виден на сайте и доступен для заявок.",
        }

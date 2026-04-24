from typing import Any, cast

from django.contrib import admin
from django.db.models import Q, QuerySet
from django.http import HttpRequest

from .models import (
    FancoinTransaction,
    RegionalTierPrice,
    SubscriptionTier,
    UserSubscription,
)


class RegionalTierPriceInline(admin.TabularInline):
    model = RegionalTierPrice
    extra = 1
    fields = ("name", "price", "original_price", "original_price_ends_at")


@admin.register(SubscriptionTier)
class SubscriptionTierAdmin(admin.ModelAdmin):
    list_display = (
        "display_name",
        "card_theme",
        "is_popular",
        "duration_days",
        "sort_order",
        "is_visible",
        "price",
        "original_price",
        "original_price_ends_at",
        "first_subscription_one_ruble",
        "fancoin_per_purchase",
        "is_unlimited",
        "one_day_tournament_discount",
        "has_badge",
    )
    exclude = ("name",)
    list_editable = (
        "card_theme",
        "is_popular",
        "duration_days",
        "sort_order",
        "is_visible",
        "price",
        "original_price",
        "original_price_ends_at",
        "first_subscription_one_ruble",
        "fancoin_per_purchase",
        "is_unlimited",
        "one_day_tournament_discount",
    )
    search_fields = ("display_name",)
    list_filter = (
        "is_popular",
        "is_visible",
        "is_unlimited",
        "has_badge",
        "has_private_chat",
    )
    ordering = ("sort_order", "price", "id")
    inlines = [RegionalTierPriceInline]

    def has_delete_permission(
        self, request: HttpRequest, obj: SubscriptionTier | None = None
    ) -> bool:
        """Проверить возможность удаления тарифа.

        Args:
            request: Текущий HTTP-запрос администратора.
            obj: Объект тарифа или ``None`` для общего списка.

        Returns:
            bool: ``False`` для системных тарифов, иначе стандартное поведение.
        """
        if obj and obj.is_system_tier:
            return False
        return cast(bool, super().has_delete_permission(request, obj))

    def get_actions(self, request: HttpRequest) -> dict[str, tuple[Any, str, str]]:
        """Вернуть доступные массовые действия для списка тарифов.

        Args:
            request: Текущий HTTP-запрос администратора.

        Returns:
            dict[str, tuple]: Доступные действия админки без массового удаления.
        """
        actions = cast(
            dict[str, tuple[Any, str, str]],
            super().get_actions(request),
        )
        actions.pop("delete_selected", None)
        return actions


@admin.register(UserSubscription)
class UserSubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "user_email",
        "user_last_name",
        "user_first_name",
        "tier",
        "purchase_city",
        "status_display",
        "fancoin_balance",
        "registrations_limit_display",
        "end_date",
        "cancelled_at",
    )
    list_filter = ("tier", "is_active", "end_date")
    # В кастомной модели User поля `username` нет (username = None),
    # поэтому поиск по нему приводит к FieldError.
    search_fields = ("user__email", "user__last_name", "user__first_name")
    readonly_fields = (
        "fancoin_balance",
        "cancelled_at",
        "purchase_city",
    )
    autocomplete_fields = ("user",)

    @admin.display(description="Email", ordering="user__email")
    def user_email(self, obj: UserSubscription) -> str:
        """Отобразить email пользователя подписки."""
        return cast(str, obj.user.email)

    @admin.display(description="Фамилия", ordering="user__last_name")
    def user_last_name(self, obj: UserSubscription) -> str:
        """Отобразить фамилию пользователя подписки."""
        return cast(str, obj.user.last_name)

    @admin.display(description="Имя", ordering="user__first_name")
    def user_first_name(self, obj: UserSubscription) -> str:
        """Отобразить имя пользователя подписки."""
        return cast(str, obj.user.first_name)

    @staticmethod
    def _case_variants_for_cyrillic(value: str) -> list[str]:
        """
        Сгенерировать варианты регистра для поиска по кириллице.

        Args:
            value: Исходная строка запроса.

        Returns:
            Список уникальных вариантов регистра, пригодных для `__contains`.
        """

        variants = [value, value.lower(), value.upper(), value.title()]
        # Убираем дубликаты с сохранением порядка.
        return list(dict.fromkeys(v for v in variants if v))

    @staticmethod
    def _phone_variants_for_search(token: str) -> list[str]:
        """
        Нормализовать телефонный токен для поиска в `user.phone`.

        Учитывает варианты ввода:
        - `+79381138222`
        - `79381138222`
        - `9381138222`

        В базе телефон, как правило, хранится в нормализованном виде `+7XXXXXXXXXX`,
        но поиск делаем по `__contains`, поэтому добавляем варианты:
        - с `+`
        - без `+`
        - и альтернативную версию с ведущей `8` (на случай если где-то
          сохранялись номера без полной нормализации).

        Args:
            token: Строка из поля поиска админки.

        Returns:
            Список уникальных вариантов, которые можно подставлять в `__contains`.
        """

        digits = "".join(ch for ch in token if ch.isdigit())
        if not digits:
            return []

        normalized_digits: str | None = None
        if len(digits) == 11:
            if digits.startswith("8"):
                normalized_digits = "7" + digits[1:]
            elif digits.startswith("7"):
                normalized_digits = digits
        elif len(digits) == 10:
            # Для РФ: 10 цифр обычно начинаются с 9 (9XXXXXXXXX), приводим к 7XXXXXXXXXX.
            normalized_digits = "7" + digits

        if not normalized_digits:
            return []

        alt8_digits = "8" + normalized_digits[1:]

        variants = [
            f"+{normalized_digits}",
            normalized_digits,
            f"+{alt8_digits}",
            alt8_digits,
        ]
        return list(dict.fromkeys(variants))

    def get_search_results(
        self,
        request: HttpRequest,
        queryset: QuerySet[UserSubscription],
        search_term: str,
    ) -> tuple[QuerySet[UserSubscription], bool]:
        """
        Выполнить поиск по email, ФИО и телефону с учетом регистра кириллицы.

        На SQLite в некоторых ситуациях `icontains` для кириллицы ломается
        из-за SQL-преобразований вроде `LOWER()`, поэтому здесь делаем:
        - разбиение запроса на токены;
        - для каждого токена ищем:
          - по `email` через `icontains`;
          - по `last_name`/`first_name` через case-sensitive `contains` для
            вариантов регистра (`lower/upper/title`);
          - по `phone` через `contains` для нормализованных вариантов
            (поддерживаются вводы с `+`, без `+`, с ведущей `8` и без `7`).

        Args:
            request: Текущий HTTP-запрос администратора.
            queryset: Исходный queryset для фильтрации.
            search_term: Строка поиска из формы админки.

        Returns:
            Кортеж (queryset, may_have_duplicates).

        Raises:
            None: Метод полагается на ORM и может выбросить FieldError, если
                схема БД не соответствует ожидаемым полям.
        """

        if not search_term:
            return queryset, False

        tokens = [t for t in search_term.split() if t]
        if not tokens:
            return queryset, False

        # Django admin обычно делает AND между токенами и OR по полям.
        combined_q = Q()
        for token in tokens:
            email_q = Q(user__email__icontains=token)

            # Сравнение `contains` case-sensitive, но с набором регистров.
            name_q = Q()
            for variant in self._case_variants_for_cyrillic(token):
                name_q |= Q(user__last_name__contains=variant)
                name_q |= Q(user__first_name__contains=variant)

            token_q = email_q | name_q

            phone_variants = self._phone_variants_for_search(token)
            if phone_variants:
                phone_q = Q()
                for variant in phone_variants:
                    phone_q |= Q(user__phone__contains=variant)
                token_q |= phone_q

            combined_q &= token_q

        return queryset.filter(combined_q), False

    def registrations_limit_display(self, obj: UserSubscription) -> str:
        """Отобразить лимит FAN-token по тарифу."""
        if obj.tier.is_unlimited:
            return "Безлимит"
        return f"{obj.tier.fancoin_per_purchase}"

    registrations_limit_display.short_description = "FAN-token за покупку"


@admin.register(FancoinTransaction)
class FancoinTransactionAdmin(admin.ModelAdmin):
    """Админка журнала FAN-token транзакций."""

    list_display = (
        "created_at",
        "user",
        "direction",
        "reason",
        "amount",
        "balance_after",
        "tournament",
        "match",
        "doubles_request",
    )
    list_filter = ("direction", "reason", "created_at")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    readonly_fields = (
        "created_at",
        "user",
        "direction",
        "reason",
        "amount",
        "balance_after",
        "tournament",
        "match",
        "doubles_request",
    )

    def has_add_permission(self, request: HttpRequest) -> bool:
        """Запретить ручное создание транзакций в админке.

        Args:
            request (HttpRequest): Текущий HTTP-запрос администратора.

        Returns:
            bool: Всегда ``False``.
        """
        return False

    def has_change_permission(
        self, request: HttpRequest, obj: FancoinTransaction | None = None
    ) -> bool:
        """Запретить редактирование транзакций в админке.

        Args:
            request (HttpRequest): Текущий HTTP-запрос администратора.
            obj (FancoinTransaction | None): Объект транзакции или ``None``.

        Returns:
            bool: Всегда ``False``.
        """
        return False

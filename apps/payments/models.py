from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class SavedPaymentMethod(models.Model):
    """Сохранённый способ оплаты для автоплатежей ЮKassa.

    Модель хранит только обезличенные данные банковской карты и идентификатор
    способа оплаты в ЮKassa. Полные реквизиты карты никогда не сохраняются.

    Args:
        models.Model: Базовый класс Django-модели.

    Returns:
        None: Экземпляры используются через Django ORM.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_payment_methods",
        verbose_name="Пользователь",
    )
    club = models.ForeignKey(
        "clubs.Club",
        on_delete=models.CASCADE,
        related_name="saved_payment_methods",
        null=True,
        blank=True,
        verbose_name="Клуб",
        help_text=(
            "Для клубных автосписаний карта привязывается к конкретному клубу "
            "и его ЮKassa-мерчанту. Для глобальной подписки поле пустое."
        ),
    )
    payment_method_id = models.CharField(
        "ID способа оплаты в ЮKassa",
        max_length=128,
        unique=True,
        help_text="Идентификатор payment_method из API ЮKassa для автоплатежей.",
    )
    card_last4 = models.CharField(
        "Последние 4 цифры карты",
        max_length=4,
        blank=True,
        help_text="Используется только для отображения пользователю.",
    )
    card_exp_month = models.CharField(
        "Месяц окончания",
        max_length=2,
        blank=True,
        help_text="Месяц окончания срока действия карты в формате MM.",
    )
    card_exp_year = models.CharField(
        "Год окончания",
        max_length=4,
        blank=True,
        help_text="Год окончания срока действия карты в формате YYYY.",
    )
    card_network = models.CharField(
        "Платёжная система",
        max_length=32,
        blank=True,
        help_text="Например: Visa, Mastercard, Mir.",
    )
    is_active = models.BooleanField(
        "Активен",
        default=True,
        help_text=(
            "Если выключено — карта больше не используется для автосписаний и "
            "не показывается пользователю."
        ),
    )
    is_default_for_subscriptions = models.BooleanField(
        "Использовать для автопродления подписки",
        default=True,
        help_text=(
            "Если включено — этот способ оплаты используется для автосписаний за подписку."
        ),
    )
    is_default_for_club_plans = models.BooleanField(
        "Использовать для автопродления клубного тарифа",
        default=False,
        help_text=(
            "Если включено — этот способ оплаты используется для автосписаний за клубные тарифы."
        ),
    )
    is_default_for_club_fees = models.BooleanField(
        "Использовать для автосписания членского взноса клуба",
        default=False,
        help_text=(
            "Если включено — этот способ оплаты используется для автосписаний за членские взносы клуба."
        ),
    )
    created_at = models.DateTimeField("Создано", default=timezone.now)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Сохранённый способ оплаты"
        verbose_name_plural = "Сохранённые способы оплаты"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        """Вернуть человекочитаемое представление сохранённой карты.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            str: Строка с маской карты и датой окончания действия.
        """
        parts: list[str] = []
        if self.card_network:
            parts.append(self.card_network)
        if self.card_last4:
            parts.append(f"**** {self.card_last4}")
        expiry: list[str] = []
        if self.card_exp_month:
            expiry.append(self.card_exp_month)
        if self.card_exp_year:
            expiry.append(self.card_exp_year)
        expiry_str = "/".join(expiry)
        if expiry_str:
            parts.append(f"до {expiry_str}")
        if not parts:
            return f"Способ оплаты {self.payment_method_id}"
        return " ".join(parts)

    def deactivate_for_subscriptions(self) -> None:
        """Отключить использование способа оплаты для автопродления подписок.

        Args:
            None: Метод использует состояние текущего экземпляра.

        Returns:
            None: Обновляет флаги активности в базе данных.
        """
        self.is_default_for_subscriptions = False
        update_fields = ["is_default_for_subscriptions", "updated_at"]
        if not self.is_default_for_club_plans and not self.is_default_for_club_fees:
            self.is_active = False
            update_fields.insert(0, "is_active")
        self.save(update_fields=update_fields)

    def deactivate_for_club_plans(self) -> None:
        """Отключить использование способа оплаты для автопродления клубных тарифов."""
        self.is_default_for_club_plans = False
        update_fields = ["is_default_for_club_plans", "updated_at"]
        if not self.is_default_for_subscriptions and not self.is_default_for_club_fees:
            self.is_active = False
            update_fields.insert(0, "is_active")
        self.save(update_fields=update_fields)

    def deactivate_for_club_fees(self) -> None:
        """Отключить использование способа оплаты для автосписания членских взносов клуба."""
        self.is_default_for_club_fees = False
        update_fields = ["is_default_for_club_fees", "updated_at"]
        if not self.is_default_for_subscriptions and not self.is_default_for_club_plans:
            self.is_active = False
            update_fields.insert(0, "is_active")
        self.save(update_fields=update_fields)


class PaymentRecord(models.Model):
    """Журнал успешных оплат пользователя.

    Модель хранит краткую историю оплат, связанных с сайтом: подписки,
    турнирные взносы и донаты. Запись создаётся после подтверждения успеха
    платежа или после успешного рекуррентного списания.
    """

    class PaymentType(models.TextChoices):
        """Типы оплат, фиксируемые в журнале."""

        SUBSCRIPTION = "subscription", "Подписка"
        CLUB_PLAN = "club_plan", "Тариф клуба"
        TOURNAMENT = "tournament", "Турнир"
        DONATION = "donation", "Донат"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payment_records",
        verbose_name="Пользователь",
    )
    payment_type = models.CharField(
        "Тип оплаты",
        max_length=32,
        choices=PaymentType.choices,
        db_index=True,
    )
    item_id = models.CharField(
        "ID объекта",
        max_length=64,
        blank=True,
        help_text="ID тарифа, турнира или другого объекта оплаты.",
    )
    item_label = models.CharField(
        "Наименование",
        max_length=255,
        blank=True,
    )
    amount = models.DecimalField(
        "Сумма",
        max_digits=10,
        decimal_places=2,
    )
    currency = models.CharField(
        "Валюта",
        max_length=8,
        default="RUB",
    )
    status = models.CharField(
        "Статус",
        max_length=32,
        default="succeeded",
        db_index=True,
    )
    yookassa_payment_id = models.CharField(
        "ID платежа в ЮKassa",
        max_length=128,
        blank=True,
        db_index=True,
    )
    is_recurring = models.BooleanField(
        "Рекуррентный платёж",
        default=False,
    )
    autopay_enabled = models.BooleanField(
        "С автосписанием",
        default=False,
        help_text="Показывает, включал ли пользователь автопродление при оплате.",
    )
    metadata = models.JSONField(
        "Дополнительные данные",
        default=dict,
        blank=True,
    )
    paid_at = models.DateTimeField(
        "Дата оплаты",
        default=timezone.now,
        db_index=True,
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)

    class Meta:
        verbose_name = "запись об оплате"
        verbose_name_plural = "записи об оплатах"
        ordering = ["-paid_at", "-id"]

    def __str__(self) -> str:
        """Вернуть краткое описание платежа.

        Returns:
            str: Тип, сумма и пользователь.
        """
        return (
            f"{self.get_payment_type_display()} {self.amount} {self.currency} / "
            f"{self.user}"
        )

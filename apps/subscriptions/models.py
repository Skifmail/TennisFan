from dateutil.relativedelta import relativedelta
from django.conf import settings
from django.db import models
from django.utils import timezone


class SubscriptionTier(models.Model):
    """Subscription tier levels."""

    class Level(models.TextChoices):
        FREE = "free", "Free"
        SILVER = "silver", "Silver"
        GOLD = "gold", "Gold"
        DIAMOND = "diamond", "Diamond"

    name = models.CharField(
        "Название тарифа", max_length=50, choices=Level.choices, unique=True
    )
    price = models.DecimalField(
        "Стоимость (руб)", max_digits=10, decimal_places=2, default=0
    )
    original_price = models.DecimalField(
        "Цена до скидки (руб)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Если указана — на странице тарифов показывается перечёркнутой, рядом актуальная цена (акция).",
    )
    original_price_ends_at = models.DateTimeField(
        "Акционная цена действует до",
        null=True,
        blank=True,
        help_text="Дата и время, после которых перечёркнутая цена не показывается. Пусто — акция без срока.",
    )

    # Registration limits
    max_tournaments = models.PositiveIntegerField(
        "Максимум турниров в месяц",
        help_text="Количество турниров, на которые можно зарегистрироваться в месяц. 0 = регистрации запрещены.",
        default=0,
    )
    is_unlimited = models.BooleanField("Неограниченные регистрации", default=False)

    # Discounts
    one_day_tournament_discount = models.PositiveIntegerField(
        "Скидка на однодневные турниры (%)",
        default=0,
        help_text="Процент скидки (0-100)",
    )

    # Features (booleans for easier permission checks)
    can_see_stats = models.BooleanField("Видеть статистику", default=True)
    can_read_comments = models.BooleanField("Читать комментарии", default=True)
    can_write_comments = models.BooleanField("Писать комментарии", default=False)
    can_rate_opponents = models.BooleanField("Оценивать соперников", default=False)
    has_private_chat = models.BooleanField("Доступ в закрытый чат", default=False)
    has_sparring = models.BooleanField("Доступ к спаррингам", default=False)
    has_admin_support = models.BooleanField("Поддержка администратора", default=False)
    has_badge = models.BooleanField("Особый значок", default=False)

    first_subscription_one_ruble = models.BooleanField(
        "Первая подписка за 1 ₽",
        default=False,
        help_text="Если включено: игрок, который ни разу не покупал подписку, может купить этот тариф за 1 ₽. Все последующие покупки — по обычной цене.",
    )

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        ordering = ["price"]

    def __str__(self):
        return self.get_name_display()


class UserSubscription(models.Model):
    """User's active subscription."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscription",
        verbose_name="Пользователь",
    )
    tier = models.ForeignKey(
        SubscriptionTier,
        on_delete=models.PROTECT,
        verbose_name="Тариф",
    )
    start_date = models.DateTimeField("Дата начала", default=timezone.now)
    end_date = models.DateTimeField("Дата окончания")
    is_active = models.BooleanField("Активна", default=True)
    cancelled_at = models.DateTimeField(
        "Дата отмены",
        null=True,
        blank=True,
        help_text="Если заполнено — подписка отменена, но действует до end_date.",
    )

    # Registration tracking for the current period
    tournaments_registered_count = models.PositiveIntegerField(
        "Использовано регистраций в этом месяце",
        default=0,
    )
    # Город при покупке (для защиты от смены города на Москву после покупки по региональному тарифу)
    purchase_city = models.CharField(
        "Город при покупке",
        max_length=100,
        blank=True,
        help_text="Нормализованное значение города на момент оплаты подписки (moscow / иное). Не менять вручную.",
    )

    class Meta:
        verbose_name = "Подписка пользователя"
        verbose_name_plural = "Подписки пользователей"

    def __str__(self) -> str:
        return f"{self.user} - {self.tier} ({self.status_display})"

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    @property
    def is_cancelled(self) -> bool:
        """Подписка была отменена пользователем."""
        return self.cancelled_at is not None

    @property
    def status_display(self) -> str:
        now = timezone.now()
        if self.is_cancelled:
            if self.end_date > now:
                return "Отменена (действует до окончания периода)"
            return "Отменена"
        if not self.is_active:
            return "Неактивна"
        if self.end_date < now:
            return "Истекла"
        return "Активна"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, *args, **kwargs):
        if not self.end_date:
            self.end_date = self.start_date + relativedelta(months=1)
        super().save(*args, **kwargs)

    # ------------------------------------------------------------------
    # Validity & limits
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """
        Подписка считается действующей, если:
        - is_active = True
        - end_date ещё не наступил
        Отменённая подписка (cancelled_at != None) продолжает действовать
        до end_date — пользователь сохраняет доступ до конца оплаченного
        периода.
        """
        return bool(self.is_active and self.end_date > timezone.now())

    def can_register_for_tournament(self) -> bool:
        """Check if user has registration slots left."""
        if self.tier.is_unlimited:
            return True
        if self.tier.max_tournaments == 0:
            return False
        return bool(self.tournaments_registered_count < self.tier.max_tournaments)

    def increment_usage(self) -> None:
        self.tournaments_registered_count += 1
        self.save(update_fields=["tournaments_registered_count"])

    def decrement_usage(self) -> None:
        """Восстановить одну регистрацию (например, при удалении из турнира)."""
        if self.tournaments_registered_count > 0:
            self.tournaments_registered_count -= 1
            self.save(update_fields=["tournaments_registered_count"])

    def get_remaining_slots(self) -> int:
        if self.tier.is_unlimited:
            return 999
        if self.tier.max_tournaments == 0:
            return 0
        return int(
            max(0, self.tier.max_tournaments - self.tournaments_registered_count)
        )


class RegionalTierPrice(models.Model):
    """Regional price override for a subscription tier."""

    tier = models.ForeignKey(
        SubscriptionTier,
        on_delete=models.CASCADE,
        related_name="regional_prices",
        verbose_name="Тариф",
    )
    price = models.DecimalField(
        "Стоимость (руб)", max_digits=10, decimal_places=2, default=0
    )
    original_price = models.DecimalField(
        "Цена до скидки (руб)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Перечёркнутая цена для этого региона. Пусто — использовать из тарифа.",
    )
    original_price_ends_at = models.DateTimeField(
        "Акция действует до",
        null=True,
        blank=True,
        help_text="Пусто — использовать срок из тарифа.",
    )
    name = models.CharField("Название региона", max_length=100, default="Регионы")

    class Meta:
        verbose_name = "Региональная цена"
        verbose_name_plural = "Региональные цены"

    def __str__(self):
        return f"{self.tier} - {self.name}: {self.price}"

from datetime import datetime, timedelta
from typing import Any, ClassVar, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class SubscriptionTier(models.Model):
    """Модель тарифа подписки.

    Args:
        models.Model: Базовый класс Django-модели.

    Returns:
        None: Экземпляр модели используется Django ORM.
    """

    class Level(models.TextChoices):
        FREE = "free", "Free"
        SILVER = "silver", "Silver"
        GOLD = "gold", "Gold"
        DIAMOND = "diamond", "Diamond"

    SYSTEM_NAME_LABELS: ClassVar[dict[str, str]] = {
        "free": "Free",
        "silver": "Silver",
        "gold": "Gold",
        "diamond": "Diamond",
    }
    SYSTEM_TIER_CODES: ClassVar[set[str]] = set(SYSTEM_NAME_LABELS.keys())
    SLUG_TRANSLITERATION_MAP: ClassVar[dict[str, str]] = {
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

    name = models.SlugField(
        "Код тарифа",
        max_length=50,
        unique=True,
        blank=True,
        help_text=(
            "Внутренний код тарифа. Для новых тарифов можно оставить пустым — "
            "он сгенерируется из названия. Для системных тарифов используйте "
            "free, silver, gold или diamond."
        ),
    )
    display_name = models.CharField(
        "Название тарифа на сайте",
        max_length=100,
        blank=True,
        help_text=(
            "Отображаемое название тарифа в интерфейсе. "
            "Если оставить пустым, будет использовано системное название или код."
        ),
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
    is_popular = models.BooleanField(
        "Популярный тариф",
        default=False,
        help_text="Если включено, на карточке тарифа будет показан бейдж «Популярный».",
    )

    class CardTheme(models.TextChoices):
        NONE = "none", "Без темы"
        BRONZE = "bronze", "Бронза"
        SILVER = "silver_theme", "Серебро"
        GOLD = "gold_theme", "Золото"
        PLATINUM = "platinum_theme", "Платина"
        DIAMOND_THEME = "diamond_theme", "Бриллиант"

    card_theme = models.CharField(
        "Тема оформления карточки",
        max_length=20,
        choices=CardTheme.choices,
        default=CardTheme.NONE,
        help_text="Металлический стиль карточки тарифа на странице подписок.",
    )
    duration_days = models.PositiveIntegerField(
        "Срок действия (дней)",
        default=30,
        help_text="На сколько дней активируется или продлевается подписка по этому тарифу.",
    )
    is_visible = models.BooleanField("Показывать тариф на сайте", default=True)
    sort_order = models.PositiveIntegerField("Порядок отображения", default=0)

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        ordering = ["sort_order", "price", "id"]

    @property
    def is_system_tier(self) -> bool:
        """Проверить, является ли тариф системным.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            bool: ``True``, если тариф относится к системным кодам.
        """
        return self.name in self.SYSTEM_TIER_CODES

    @property
    def is_free_tier(self) -> bool:
        """Проверить, является ли тариф бесплатным системным тарифом.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            bool: ``True``, если код тарифа равен ``free``.
        """
        return str(self.name) == "free"

    @property
    def is_diamond_tier(self) -> bool:
        """Проверить, является ли тариф системным Diamond.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            bool: ``True``, если код тарифа равен ``diamond``.
        """
        return str(self.name) == "diamond"

    @property
    def badge_variant(self) -> str:
        """Вернуть вариант бейджа для отображения.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            str: Код варианта бейджа. Для тарифа с платиновой темой возвращается
                ``platinum``. Для остальных кастомных тарифов возвращается ``custom``.
        """
        tier_name = str(self.name)
        if tier_name in self.SYSTEM_TIER_CODES:
            return tier_name
        if str(self.card_theme) == str(self.CardTheme.PLATINUM):
            return "platinum"
        return "custom"

    @property
    def duration_label(self) -> str:
        """Вернуть человекочитаемый срок действия тарифа.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            str: Срок действия в виде текста, например ``30 дней``.
        """
        days = self.duration_days
        remainder_10 = days % 10
        remainder_100 = days % 100
        if remainder_10 == 1 and remainder_100 != 11:
            suffix = "день"
        elif remainder_10 in (2, 3, 4) and remainder_100 not in (12, 13, 14):
            suffix = "дня"
        else:
            suffix = "дней"
        return f"{days} {suffix}"

    def get_duration_delta(self) -> timedelta:
        """Вернуть временной интервал действия тарифа.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            timedelta: Интервал, на который активируется подписка.
        """
        return timedelta(days=self.duration_days)

    def apply_duration(self, base_datetime: datetime) -> datetime:
        """Рассчитать дату окончания тарифа от базовой даты.

        Args:
            base_datetime (datetime): Базовая дата и время начала отсчёта.

        Returns:
            datetime: Дата окончания с учётом срока действия тарифа.
        """
        return base_datetime + self.get_duration_delta()

    @classmethod
    def build_code_from_display_name(cls, value: str) -> str:
        """Сгенерировать безопасный код тарифа из названия.

        Args:
            value (str): Название тарифа, введённое администратором.

        Returns:
            str: Нормализованный slug-код тарифа.
        """
        normalized_value = value.strip().lower()
        transliterated = "".join(
            cls.SLUG_TRANSLITERATION_MAP.get(char, char) for char in normalized_value
        )
        generated_slug = str(slugify(transliterated))
        return generated_slug[:50]

    @classmethod
    def generate_unique_code_from_display_name(
        cls, value: str, instance_pk: int | None = None
    ) -> str:
        """Сгенерировать уникальный код тарифа из названия.

        Args:
            value (str): Название тарифа, введённое администратором.
            instance_pk (int | None): Идентификатор текущего тарифа при редактировании.

        Returns:
            str: Уникальный slug-код тарифа.
        """
        base_slug = cls.build_code_from_display_name(value)
        if not base_slug:
            return ""

        slug_candidate = base_slug
        suffix = 2
        queryset = cls.objects.all()
        if instance_pk is not None:
            queryset = queryset.exclude(pk=instance_pk)

        while queryset.filter(name=slug_candidate).exists():
            suffix_text = f"-{suffix}"
            slug_candidate = f"{base_slug[: 50 - len(suffix_text)]}{suffix_text}"
            suffix += 1

        return slug_candidate

    def clean(self) -> None:
        """Провалидировать безопасные изменения тарифа.

        Args:
            None: Метод использует состояние текущего экземпляра.

        Returns:
            None: Метод ничего не возвращает.

        Raises:
            ValidationError: Если попытаться переименовать системный тариф
                или сохранить тариф без срока/кода.
        """
        super().clean()
        if not self.name:
            generated_name = self.generate_unique_code_from_display_name(
                self.display_name, self.pk
            )
            if not generated_name:
                raise ValidationError(
                    {
                        "name": (
                            "Укажите «Название тарифа на сайте», чтобы код тарифа "
                            "сгенерировался автоматически, или заполните код вручную."
                        )
                    }
                )
            self.name = generated_name

        if self.duration_days <= 0:
            raise ValidationError(
                {"duration_days": "Срок действия тарифа должен быть больше нуля."}
            )

        if not self.pk:
            return

        original = type(self).objects.filter(pk=self.pk).only("name").first()
        if original is None:
            return

        # Системные коды участвуют в ключевой бизнес-логике и не должны
        # изменяться после создания, иначе можно сломать отображение и проверки.
        if original.name in self.SYSTEM_TIER_CODES and self.name != original.name:
            raise ValidationError(
                {
                    "name": (
                        "Нельзя изменять код системного тарифа. "
                        "Меняйте только «Название тарифа на сайте»."
                    )
                }
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Сохранить тариф с полной валидацией.

        Args:
            *args: Позиционные аргументы Django ORM.
            **kwargs: Именованные аргументы Django ORM.

        Returns:
            None: Метод сохраняет объект в базе данных.

        Raises:
            ValidationError: Если данные тарифа нарушают правила безопасности.
        """
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Удалить тариф, если это разрешено правилами безопасности.

        Args:
            *args: Позиционные аргументы Django ORM.
            **kwargs: Именованные аргументы Django ORM.

        Returns:
            None: Метод удаляет объект из базы данных.

        Raises:
            ValidationError: Если попытаться удалить системный тариф.
        """
        if self.is_system_tier:
            raise ValidationError(
                "Нельзя удалять системные тарифы free, silver, gold и diamond."
            )
        return cast(tuple[int, dict[str, int]], super().delete(*args, **kwargs))

    def get_name_display(self) -> str:
        """Вернуть отображаемое название тарифа.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            str: Название тарифа для интерфейса и уведомлений.
        """
        display_name = str(self.display_name)
        if display_name:
            return display_name
        tier_name = str(self.name)
        return self.SYSTEM_NAME_LABELS.get(tier_name, tier_name)

    def __str__(self) -> str:
        return self.get_name_display()


class UserSubscription(models.Model):
    """Модель активной подписки пользователя.

    Args:
        models.Model: Базовый класс Django-модели.

    Returns:
        None: Экземпляр модели используется Django ORM.
    """

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

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Сохранить подписку пользователя.

        Args:
            *args: Позиционные аргументы Django ORM.
            **kwargs: Именованные аргументы Django ORM.

        Returns:
            None: Метод сохраняет объект в базе данных.
        """
        if not self.end_date:
            self.end_date = self.tier.apply_duration(self.start_date)
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
        """Проверить, остались ли у пользователя слоты регистрации.

        Args:
            None: Метод использует поля текущего экземпляра.

        Returns:
            bool: ``True``, если регистрация на турнир доступна.
        """
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
    """Региональная цена для тарифа подписки.

    Args:
        models.Model: Базовый класс Django-модели.

    Returns:
        None: Экземпляр модели используется Django ORM.
    """

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

    def __str__(self) -> str:
        return f"{self.tier} - {self.name}: {self.price}"

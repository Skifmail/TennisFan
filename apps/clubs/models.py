"""
Модели клубного раздела: клубы, подписки, участники, инвайты, взносы, рейтинги, заявки на турниры.
"""

import decimal
from typing import cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from config.validators import CompressImageFieldsMixin, validate_image_max_2mb


class ClubStatus(models.TextChoices):
    """Статус клуба на платформе."""

    ACTIVE = "active", "Активен"
    SUSPENDED = "suspended", "Заблокирован"
    TRIAL = "trial", "Пробный период"


class ClubPlan(models.TextChoices):
    """Тариф подписки клуба на платформу."""

    START = "start", "Старт"
    BASIC = "basic", "Базовый"
    PRO = "pro", "Про"


class ClubSubscriptionPeriod(models.TextChoices):
    """Период оплаты подписки клуба."""

    MONTHLY = "monthly", "Помесячно"
    YEARLY = "yearly", "Ежегодно"


class ClubSubscriptionStatus(models.TextChoices):
    """Статус подписки клуба."""

    ACTIVE = "active", "Активна"
    EXPIRED = "expired", "Истекла"
    CANCELLED = "cancelled", "Отменена"


class ClubMemberRole(models.TextChoices):
    """Роль участника в клубе."""

    ADMIN = "admin", "Администратор"
    MANAGER = "manager", "Менеджер"
    PLAYER = "player", "Игрок"


class ClubMemberStatus(models.TextChoices):
    """Статус участия в клубе."""

    ACTIVE = "active", "Активен"
    INVITED = "invited", "Приглашён"
    REMOVED = "removed", "Исключён"


class ClubMemberPlanStatus(models.TextChoices):
    """Статус назначения клубного тарифа участнику."""

    ACTIVE = "active", "Активен"
    PENDING = "pending", "Ожидает активации"
    ENDED = "ended", "Завершён"


class ClubRegistrationLimitPeriod(models.TextChoices):
    """Период обновления лимита регистраций клубного тарифа."""

    MONTHLY = "monthly", "В месяц"
    PLAN_PERIOD = "plan_period", "За весь срок тарифа"


class FeePeriod(models.TextChoices):
    """Период членского взноса."""

    MONTHLY = "monthly", "Ежемесячно"
    QUARTERLY = "quarterly", "Ежеквартально"
    YEARLY = "yearly", "Ежегодно"


class FeePaymentMethod(models.TextChoices):
    """Способ оплаты взноса."""

    ONLINE = "online", "Онлайн"
    BALANCE = "balance", "Баланс клуба"
    MANUAL = "manual", "Вручную"


class ClubApplicationStatus(models.TextChoices):
    """Статус заявки клуба на межклубный турнир."""

    PENDING = "pending", "На рассмотрении"
    APPROVED = "approved", "Одобрена"
    REJECTED = "rejected", "Отклонена"


class ClubJoinRequestStatus(models.TextChoices):
    """Статус заявки игрока на вступление в клуб."""

    PENDING = "pending", "На рассмотрении"
    APPROVED = "approved", "Одобрена"
    REJECTED = "rejected", "Отклонена"


class PlatformPlan(models.Model):
    """
    Редактируемый тариф подписки клуба на платформу (Старт/Базовый/Про).

    slug должен совпадать со значением ClubPlan (start, basic, pro) для совместимости
    с ClubSubscription.plan. Владелец платформы редактирует цены и возможности в админке.
    """

    slug = models.SlugField(
        "Код тарифа",
        max_length=20,
        unique=True,
        help_text="Должен совпадать с ClubPlan: start, basic, pro",
    )
    name = models.CharField("Название", max_length=100)
    description = models.TextField(
        "Описание возможностей",
        blank=True,
        help_text="Краткое описание для отображения при выборе тарифа",
    )
    price_monthly = models.DecimalField(
        "Цена за месяц (₽)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    price_yearly = models.DecimalField(
        "Цена за год (₽)",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    max_tournaments_per_month = models.PositiveIntegerField(
        "Лимит турниров в месяц",
        null=True,
        blank=True,
        help_text="Пусто — без лимита",
    )
    max_members = models.PositiveIntegerField(
        "Лимит участников клуба",
        null=True,
        blank=True,
        help_text="Пусто — без лимита. Учитываются активные и приглашённые.",
    )
    trial_days = models.PositiveIntegerField(
        "Пробный период (дней)",
        default=0,
        help_text="Для тарифа Старт обычно 14",
    )
    is_public_page = models.BooleanField(
        "Публичная страница клуба",
        default=True,
        help_text="На тарифе Старт — обычно False",
    )
    is_open_interclub = models.BooleanField(
        "Межклубные турниры",
        default=False,
        help_text="Доступно только для тарифа Про",
    )
    is_active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveSmallIntegerField("Порядок отображения", default=0)

    class Meta:
        verbose_name = "Тариф платформы"
        verbose_name_plural = "Тарифы платформы"
        ordering = ["sort_order", "slug"]

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    def get_price_for_period(self, period: str) -> decimal.Decimal:
        """Возвращает цену для периода (monthly/yearly)."""
        if period == "monthly":
            return cast(decimal.Decimal, self.price_monthly)
        return cast(decimal.Decimal, self.price_yearly)


class Club(CompressImageFieldsMixin, models.Model):
    """
    Клуб — организация (теннисный клуб), зарегистрированная на платформе по подписке.
    """

    name = models.CharField("Официальное название", max_length=255)
    slug = models.SlugField("URL-идентификатор", max_length=100, unique=True)
    city = models.CharField("Город", max_length=100)
    address = models.TextField("Адрес")
    logo = models.ImageField(
        "Логотип",
        upload_to="clubs/",
        blank=True,
        validators=[validate_image_max_2mb],
    )
    hero_image = models.ImageField(
        "Баннер публичной страницы",
        upload_to="clubs/banners/",
        blank=True,
        validators=[validate_image_max_2mb],
        help_text="Рекомендуется широкое изображение 1920×600 px, без текста, JPG/PNG/WebP до 2 МБ.",
    )
    email = models.EmailField("Контактный email")
    phone = models.CharField("Телефон", max_length=50, blank=True)
    admin_name = models.CharField("ФИО ответственного", max_length=255)
    description = models.TextField("Описание", blank=True)
    is_public = models.BooleanField("Публичная страница", default=True)
    use_player_plans = models.BooleanField(
        "Использовать клубные тарифы",
        default=True,
        help_text="Если выключено, тарифы не ограничивают регистрацию на турниры и не обязательны для участников клуба.",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=ClubStatus.choices,
        default=ClubStatus.ACTIVE,
    )
    trial_ends_at = models.DateTimeField(
        "Конец пробного периода", null=True, blank=True
    )
    created_at = models.DateTimeField("Дата регистрации", auto_now_add=True)

    class Meta:
        verbose_name = "Клуб"
        verbose_name_plural = "Клубы"
        ordering = ["name"]

    def __str__(self) -> str:
        return str(self.name)


class ClubLegalDocument(models.Model):
    """
    Юридический документ клуба (публичная оферта клуба).

    Хранится в БД; публикация обязательна для приёма платежей на счёт клуба.
    """

    club = models.OneToOneField(
        Club,
        on_delete=models.CASCADE,
        related_name="legal_document",
        verbose_name="Клуб",
    )
    title = models.CharField("Заголовок", max_length=255)
    content = models.TextField("Текст (HTML/Markdown)")
    version = models.CharField("Версия", max_length=32, default="1.0")
    is_published = models.BooleanField("Опубликовано", default=False)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Оферта клуба"
        verbose_name_plural = "Оферты клубов"

    def __str__(self) -> str:
        return f"{self.club.name} — {self.title} (v{self.version})"


class ClubSubscription(models.Model):
    """
    Подписка клуба на платформу (тариф Старт/Базовый/Про, период, срок действия).
    """

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="subscriptions",
        verbose_name="Клуб",
    )
    plan = models.CharField(
        "Тариф",
        max_length=20,
        choices=ClubPlan.choices,
    )
    period = models.CharField(
        "Период оплаты",
        max_length=20,
        choices=ClubSubscriptionPeriod.choices,
    )
    price = models.DecimalField(
        "Сумма оплаты",
        max_digits=10,
        decimal_places=2,
    )
    started_at = models.DateTimeField("Начало подписки")
    ends_at = models.DateTimeField("Конец подписки")
    auto_renew = models.BooleanField("Автопродление", default=False)
    payment_provider = models.CharField(
        "Провайдер оплаты",
        max_length=50,
        blank=True,
    )
    payment_ref = models.CharField(
        "Ссылка на транзакцию",
        max_length=255,
        blank=True,
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=ClubSubscriptionStatus.choices,
    )

    class Meta:
        verbose_name = "Подписка клуба"
        verbose_name_plural = "Подписки клубов"
        ordering = ["-ends_at", "id"]

    def __str__(self) -> str:
        return (
            f"{self.club.name} — {self.get_plan_display()}, {self.get_period_display()}"
        )


class ClubMember(models.Model):
    """
    Участник клуба: связь пользователя платформы с клубом, роль и статус.
    """

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name="Клуб",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="club_memberships",
        verbose_name="Пользователь",
    )
    role = models.CharField(
        "Роль в клубе",
        max_length=20,
        choices=ClubMemberRole.choices,
    )
    status = models.CharField(
        "Статус участия",
        max_length=20,
        choices=ClubMemberStatus.choices,
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="invited_club_members",
        verbose_name="Кто пригласил",
    )
    joined_at = models.DateTimeField("Дата вступления", null=True, blank=True)
    balance = models.DecimalField(
        "Баланс игрока",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "Участник клуба"
        verbose_name_plural = "Участники клуба"
        unique_together = [("club", "user")]
        ordering = ["club", "user"]

    def __str__(self) -> str:
        return f"{self.user.email} — {self.club.name}"


class ClubPlayerPlan(models.Model):
    """
    Клубный тариф для участников (игроков).

    Attributes:
        club: Клуб-владелец тарифа.
        name: Название тарифа (например, Юниор/VIP).
        monthly_fee: Ежемесячный взнос по тарифу.
        duration_days: Срок действия тарифа в днях.
        max_tournaments_per_month: Лимит турниров в месяц.
        has_unlimited_registrations: Безлимитные регистрации по тарифу.

    Логика лимитов (аналогично глобальной платформе):
        - Однодневные турниры: лимит НЕ расходуется (оплата взноса, если есть)
        - Многодневные с взносом: лимит расходуется, если есть; иначе оплата взноса
        - Многодневные без взноса: лимит обязателен и расходуется
    """

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="player_plans",
        verbose_name="Клуб",
    )
    name = models.CharField("Название тарифа", max_length=120)
    description = models.TextField("Описание", blank=True)
    is_active = models.BooleanField("Активен", default=True)
    monthly_fee = models.DecimalField(
        "Ежемесячный взнос",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    duration_days = models.PositiveIntegerField(
        "Срок действия в днях",
        default=30,
        help_text="Сколько дней действует оплаченный тариф.",
    )
    max_tournaments_per_month = models.PositiveIntegerField(
        "Лимит турниров",
        null=True,
        blank=True,
        help_text="Обязателен, если безлимитные регистрации выключены.",
    )
    registration_limit_period = models.CharField(
        "Период лимита регистраций",
        max_length=20,
        choices=ClubRegistrationLimitPeriod.choices,
        default=ClubRegistrationLimitPeriod.MONTHLY,
        help_text=(
            "В месяц — лимит обновляется каждый календарный месяц. "
            "За весь срок тарифа — общий лимит на весь оплаченный период."
        ),
    )
    has_unlimited_registrations = models.BooleanField(
        "Безлимитные регистрации",
        default=False,
        help_text="Если включено, лимит турниров в месяц не применяется.",
    )
    allow_self_change = models.BooleanField(
        "Разрешить самостоятельную смену тарифа игроком",
        default=True,
    )
    sort_order = models.PositiveIntegerField("Порядок", default=0)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        db_table = "club_plans"
        verbose_name = "Клубный тариф игрока"
        verbose_name_plural = "Клубные тарифы игроков"
        ordering = ["club", "sort_order", "name", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["club", "name"],
                name="uniq_club_player_plan_name",
            ),
            models.CheckConstraint(
                condition=models.Q(monthly_fee__gte=0),
                name="club_player_plan_monthly_fee_gte_0",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_days__gte=1),
                name="club_player_plan_duration_days_gte_1",
            ),
            models.CheckConstraint(
                condition=models.Q(max_tournaments_per_month__gte=0)
                | models.Q(max_tournaments_per_month__isnull=True),
                name="club_player_plan_tournaments_gte_0_or_null",
            ),
            models.CheckConstraint(
                condition=models.Q(has_unlimited_registrations=True)
                | models.Q(max_tournaments_per_month__isnull=False),
                name="club_player_plan_limit_required_without_unlimited",
            ),
        ]

    def clean(self) -> None:
        """Проверяет согласованность срока и правил регистраций тарифа."""
        super().clean()
        if self.duration_days < 1:
            raise ValidationError(
                {"duration_days": "Срок действия тарифа должен быть не меньше 1 дня."}
            )
        if self.has_unlimited_registrations:
            self.max_tournaments_per_month = None
            return
        if self.max_tournaments_per_month is None:
            raise ValidationError(
                {
                    "max_tournaments_per_month": (
                        "Укажите лимит турниров в месяц или включите безлимитные регистрации."
                    )
                }
            )

    @property
    def registrations_limit(self) -> int | None:
        """Возвращает лимит регистраций или None для безлимитного тарифа."""
        if self.has_unlimited_registrations:
            return None
        # Тип Django-моделей для null-поля может определяться как Any.
        return cast(int | None, self.max_tournaments_per_month)

    @property
    def registrations_limit_display(self) -> str:
        """Возвращает человекочитаемое описание лимита регистраций."""
        if self.has_unlimited_registrations:
            return "Безлимит"
        limit = self.registrations_limit or 0
        if self.registration_limit_period == ClubRegistrationLimitPeriod.PLAN_PERIOD:
            return f"{limit} за срок тарифа"
        return f"{limit} в месяц"

    def __str__(self) -> str:
        return f"{self.club.name} — {self.name}"


class ClubMemberPlan(models.Model):
    """
    Назначение тарифа участнику клуба.

    Attributes:
        club_member: Участник клуба, которому назначен тариф.
        plan: Выбранный клубный тариф.
        status: Статус назначения (active/pending/ended).
        started_at: Дата начала действия.
        ended_at: Дата окончания действия.
        bonus_tournaments_balance: Перенесённый остаток регистраций сверх базового лимита.
        auto_renew: Автопродление на следующий период.
    """

    club_member = models.ForeignKey(
        ClubMember,
        on_delete=models.CASCADE,
        related_name="plan_assignments",
        verbose_name="Участник клуба",
    )
    plan = models.ForeignKey(
        ClubPlayerPlan,
        on_delete=models.CASCADE,
        related_name="member_assignments",
        verbose_name="Тариф",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=ClubMemberPlanStatus.choices,
        default=ClubMemberPlanStatus.ACTIVE,
    )
    started_at = models.DateTimeField("Дата начала", auto_now_add=True)
    ended_at = models.DateTimeField("Дата окончания", null=True, blank=True)
    bonus_tournaments_balance = models.PositiveIntegerField(
        "Перенесённый остаток регистраций",
        default=0,
    )
    auto_renew = models.BooleanField("Автопродление", default=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assigned_club_member_plans",
        verbose_name="Назначил",
    )
    change_reason = models.CharField("Причина изменения", max_length=255, blank=True)

    class Meta:
        db_table = "club_member_plans"
        verbose_name = "Тариф участника клуба"
        verbose_name_plural = "Тарифы участников клуба"
        ordering = ["-started_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["club_member"],
                condition=models.Q(status=ClubMemberPlanStatus.ACTIVE),
                name="uniq_active_plan_per_member",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.club_member} — {self.plan.name} ({self.status})"


class ClubPlanTournamentAccess(models.Model):
    """
    Доступ тарифа к конкретному турниру клуба.

    Attributes:
        plan: Клубный тариф игрока.
        tournament: Турнир клуба.
        is_allowed: Разрешена ли регистрация для тарифа.
    """

    plan = models.ForeignKey(
        ClubPlayerPlan,
        on_delete=models.CASCADE,
        related_name="tournament_access_rules",
        verbose_name="Тариф",
    )
    tournament = models.ForeignKey(
        "tournaments.Tournament",
        on_delete=models.CASCADE,
        related_name="club_plan_access_rules",
        verbose_name="Турнир",
    )
    is_allowed = models.BooleanField("Доступ разрешён", default=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        db_table = "club_plan_tournament_access"
        verbose_name = "Доступ тарифа к турниру"
        verbose_name_plural = "Доступы тарифов к турнирам"
        unique_together = [("plan", "tournament")]
        ordering = ["plan", "tournament"]

    def __str__(self) -> str:
        return f"{self.plan} → {self.tournament.name}"


class ClubPlanSlotUsage(models.Model):
    """
    Учёт использования лимитов тарифа участником по месяцам.

    Attributes:
        club_member: Участник клуба.
        plan: Тариф, по которому учитывается usage.
        period_year: Год учётного периода.
        period_month: Месяц учётного периода.
        tournaments_used: Сколько турниров (лимитных) использовано.
            Платные однодневные турниры не учитываются.
    """

    club_member = models.ForeignKey(
        ClubMember,
        on_delete=models.CASCADE,
        related_name="plan_slot_usages",
        verbose_name="Участник клуба",
    )
    plan = models.ForeignKey(
        ClubPlayerPlan,
        on_delete=models.CASCADE,
        related_name="slot_usages",
        verbose_name="Тариф",
    )
    period_year = models.PositiveIntegerField("Год периода")
    period_month = models.PositiveSmallIntegerField("Месяц периода")
    tournaments_used = models.PositiveIntegerField("Турниров использовано", default=0)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)
    updated_at = models.DateTimeField("Дата обновления", auto_now=True)

    class Meta:
        db_table = "club_plan_slot_usage"
        verbose_name = "Учёт лимитов тарифа"
        verbose_name_plural = "Учёт лимитов тарифов"
        ordering = ["-period_year", "-period_month", "club_member_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["club_member", "period_year", "period_month"],
                name="uniq_slot_usage_period",
            ),
            models.CheckConstraint(
                condition=models.Q(period_month__gte=1, period_month__lte=12),
                name="club_plan_slot_usage_period_month_1_12",
            ),
        ]

    def __str__(self) -> str:
        return (
            f"{self.club_member} — {self.period_year:04d}-{self.period_month:02d} "
            f"(турниров={self.tournaments_used})"
        )


class ClubInviteLink(models.Model):
    """
    Инвайт-ссылка для вступления в клуб (токен, срок действия, лимит использований).
    """

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="invite_links",
        verbose_name="Клуб",
    )
    token = models.CharField("Токен", max_length=64, unique=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="Создал",
    )
    expires_at = models.DateTimeField("Срок действия", null=True, blank=True)
    max_uses = models.PositiveIntegerField("Лимит переходов", null=True, blank=True)
    use_count = models.PositiveIntegerField("Счётчик использований", default=0)
    is_active = models.BooleanField("Активна", default=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "Инвайт-ссылка"
        verbose_name_plural = "Инвайт-ссылки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.club.name} — {self.token[:8]}..."


class ClubJoinRequest(models.Model):
    """Заявка игрока на вступление в клуб с модерацией администратором."""

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="join_requests",
        verbose_name="Клуб",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="club_join_requests",
        verbose_name="Пользователь",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=ClubJoinRequestStatus.choices,
        default=ClubJoinRequestStatus.PENDING,
    )
    message = models.CharField("Комментарий игрока", max_length=255, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reviewed_club_join_requests",
        verbose_name="Кто обработал",
    )
    reviewed_at = models.DateTimeField("Дата обработки", null=True, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Заявка на вступление в клуб"
        verbose_name_plural = "Заявки на вступление в клуб"
        ordering = ["-created_at"]
        unique_together = [("club", "user")]

    def __str__(self) -> str:
        return f"{self.user} → {self.club.name} ({self.get_status_display()})"


class ClubMembershipFee(models.Model):
    """
    Настройка членского взноса клуба (сумма, период, провайдер оплаты).
    """

    class PaymentProvider(models.TextChoices):
        YOOKASSA = "yookassa", "ЮKassa"

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="membership_fees",
        verbose_name="Клуб",
    )
    amount = models.DecimalField("Сумма взноса", max_digits=10, decimal_places=2)
    currency = models.CharField("Валюта", max_length=3, default="RUB")
    period = models.CharField(
        "Период",
        max_length=20,
        choices=FeePeriod.choices,
    )
    period_start_day = models.PositiveSmallIntegerField(
        "День начала периода (1–28)",
        default=1,
    )
    description = models.TextField("Описание для игроков", blank=True)
    restrict_tournament_access = models.BooleanField(
        "Блокировать доступ к турнирам неоплатившим",
        default=False,
    )
    payment_provider = models.CharField(
        "Провайдер оплаты",
        max_length=50,
        choices=PaymentProvider.choices,
        blank=True,
    )
    payment_api_key = models.TextField("API-ключ (хранить зашифрованным)", blank=True)
    payment_shop_id = models.CharField(
        "ID магазина (ЮKassa)", max_length=255, blank=True
    )
    is_active = models.BooleanField("Включена система взносов", default=False)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "Настройка взносов клуба"
        verbose_name_plural = "Настройки взносов клуба"
        ordering = ["club", "id"]

    def __str__(self) -> str:
        return f"{self.club.name} — {self.amount} {self.currency} / {self.get_period_display()}"


class ClubFeePayment(models.Model):
    """
    Оплата членского взноса участником клуба (онлайн или ручная отметка).
    """

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="fee_payments",
        verbose_name="Клуб",
    )
    member = models.ForeignKey(
        ClubMember,
        on_delete=models.CASCADE,
        related_name="fee_payments",
        verbose_name="Участник",
    )
    fee = models.ForeignKey(
        ClubMembershipFee,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="Настройка взноса",
    )
    amount = models.DecimalField("Сумма", max_digits=10, decimal_places=2)
    period_label = models.CharField("Период (напр. 2026-03)", max_length=50)
    paid_at = models.DateTimeField("Дата оплаты")
    method = models.CharField(
        "Способ",
        max_length=20,
        choices=FeePaymentMethod.choices,
    )
    payment_ref = models.CharField(
        "ID транзакции провайдера",
        max_length=255,
        blank=True,
    )
    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Кто отметил вручную",
    )

    class Meta:
        verbose_name = "Оплата взноса"
        verbose_name_plural = "Оплаты взносов"
        ordering = ["-paid_at"]

    def __str__(self) -> str:
        return f"{self.member.user.email} — {self.period_label}"


class ClubMemberBalanceTransaction(models.Model):
    """Операция по балансу участника клуба."""

    class Direction(models.TextChoices):
        """Направление движения средств."""

        CREDIT = "credit", "Пополнение"
        DEBIT = "debit", "Списание"

    class Source(models.TextChoices):
        """Источник или назначение операции."""

        TOURNAMENT_PAYMENT = "tournament_payment", "Оплата турнира"
        TOURNAMENT_REFUND = "tournament_refund", "Возврат за турнир"
        CLUB_PLAN_PAYMENT = "club_plan_payment", "Оплата тарифа клуба"
        CLUB_FEE_PAYMENT = "club_fee_payment", "Оплата членского взноса"
        MANUAL = "manual", "Ручная корректировка"

    class Status(models.TextChoices):
        """Статус операции."""

        PENDING = "pending", "Ожидает"
        COMPLETED = "completed", "Завершена"
        CANCELLED = "cancelled", "Отменена"

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="member_balance_transactions",
        verbose_name="Клуб",
    )
    member = models.ForeignKey(
        ClubMember,
        on_delete=models.CASCADE,
        related_name="balance_transactions",
        verbose_name="Участник",
    )
    direction = models.CharField(
        "Направление",
        max_length=12,
        choices=Direction.choices,
    )
    source = models.CharField(
        "Источник",
        max_length=32,
        choices=Source.choices,
    )
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=Status.choices,
        default=Status.COMPLETED,
    )
    amount = models.DecimalField("Сумма", max_digits=10, decimal_places=2)
    description = models.CharField("Описание", max_length=255, blank=True)
    reference = models.CharField("Внешний идентификатор", max_length=128, blank=True)
    metadata = models.JSONField("Дополнительные данные", default=dict, blank=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    completed_at = models.DateTimeField("Завершено", null=True, blank=True)

    class Meta:
        verbose_name = "Операция по балансу участника клуба"
        verbose_name_plural = "Операции по балансу участников клуба"
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.member} — {self.get_direction_display()} {self.amount}"


class ClubRating(models.Model):
    """
    Рейтинг участника в клубе (текущие очки и место).
    """

    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="ratings",
        verbose_name="Клуб",
    )
    member = models.OneToOneField(
        ClubMember,
        on_delete=models.CASCADE,
        related_name="club_rating",
        verbose_name="Участник",
    )
    points = models.IntegerField("Текущий рейтинг (очки)", default=0)
    rank = models.PositiveIntegerField("Место в рейтинге", null=True, blank=True)
    updated_at = models.DateTimeField("Обновлено", auto_now=True)

    class Meta:
        verbose_name = "Рейтинг в клубе"
        verbose_name_plural = "Рейтинги в клубе"
        ordering = ["club", "-points"]

    def __str__(self) -> str:
        return f"{self.member.user.email} в {self.club.name}: {self.points} очков"


class ClubRatingHistory(models.Model):
    """
    История изменения рейтинга участника (после турнира или ручной корректировки).
    """

    club_rating = models.ForeignKey(
        ClubRating,
        on_delete=models.CASCADE,
        related_name="history",
        verbose_name="Рейтинг",
    )
    tournament = models.ForeignKey(
        "tournaments.Tournament",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Турнир",
    )
    points_before = models.IntegerField("Очки до")
    points_after = models.IntegerField("Очки после")
    delta = models.IntegerField("Изменение (±)")
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "История рейтинга"
        verbose_name_plural = "История рейтингов"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.club_rating} — Δ{self.delta}"


class ClubTournamentApplication(models.Model):
    """
    Заявка клуба на участие в межклубном турнире.
    """

    tournament = models.ForeignKey(
        "tournaments.Tournament",
        on_delete=models.CASCADE,
        related_name="club_applications",
        verbose_name="Турнир",
    )
    applicant_club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="tournament_applications",
        verbose_name="Клуб-заявитель",
    )
    status = models.CharField(
        "Статус",
        max_length=20,
        choices=ClubApplicationStatus.choices,
    )
    message = models.TextField("Комментарий при заявке", blank=True)
    responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Кто ответил",
    )
    responded_at = models.DateTimeField("Дата ответа", null=True, blank=True)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "Заявка клуба на турнир"
        verbose_name_plural = "Заявки клубов на турниры"
        unique_together = [("tournament", "applicant_club")]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.applicant_club.name} → {self.tournament.name} ({self.get_status_display()})"


class ClubFeePaymentPending(models.Model):
    """
    Ожидающий платёж взноса: связь payment_id → member/fee/period для return и webhook.
    """

    payment_id = models.CharField("ID платежа ЮKassa", max_length=255, unique=True)
    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="fee_payments_pending",
        verbose_name="Клуб",
    )
    fee = models.ForeignKey(
        ClubMembershipFee,
        on_delete=models.CASCADE,
        related_name="payments_pending",
        verbose_name="Настройка взноса",
    )
    member = models.ForeignKey(
        ClubMember,
        on_delete=models.CASCADE,
        related_name="fee_payments_pending",
        verbose_name="Участник",
    )
    amount = models.DecimalField("Сумма", max_digits=10, decimal_places=2)
    period_label = models.CharField("Период (напр. 2026-03)", max_length=50)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "Ожидающий платёж взноса"
        verbose_name_plural = "Ожидающие платежи взносов"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.payment_id[:12]}… → {self.member.user.email}"


class ClubSubscriptionPaymentPending(models.Model):
    """
    Ожидающий платёж подписки клуба: связь payment_id → club/plan/period/amount.
    """

    payment_id = models.CharField("ID платежа ЮKassa", max_length=255, unique=True)
    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="subscription_payments_pending",
        verbose_name="Клуб",
    )
    plan = models.CharField("Тариф", max_length=20)
    period = models.CharField("Период оплаты", max_length=20)
    amount = models.DecimalField("Сумма", max_digits=10, decimal_places=2)
    created_at = models.DateTimeField("Дата создания", auto_now_add=True)

    class Meta:
        verbose_name = "Ожидающий платёж подписки"
        verbose_name_plural = "Ожидающие платежи подписок"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.payment_id[:12]}… → {self.club.name}"


class ClubNotificationChannel(models.TextChoices):
    """Канал уведомлений для клуба или участника."""

    EMAIL = "email", "Email"
    TELEGRAM = "telegram", "Telegram"


class ClubNotificationType(models.TextChoices):
    """Тип события для уведомлений клубного модуля."""

    FEE_REMINDER = "fee_reminder", "Напоминание о взносе"
    FEE_OVERDUE = "fee_overdue", "Просрочка взноса"
    FEE_PAID = "fee_paid", "Оплата взноса"
    SUBSCRIPTION_EXPIRING = "subscription_expiring", "Истекает подписка клуба"
    SUBSCRIPTION_SUSPENDED = "subscription_suspended", "Подписка приостановлена"
    TOURNAMENT_REMINDER = "tournament_reminder", "Напоминание о турнире"
    TOURNAMENT_RESULT = "tournament_result", "Результаты турнира"
    NEW_MEMBER = "new_member", "Новый участник клуба"
    DEBTORS_SUMMARY = "debtors_summary", "Сводка должников"


class ClubNotificationSettings(models.Model):
    """
    Индивидуальные настройки уведомлений участника клуба.

    Модель хранит, включены ли уведомления для пользователя в целом и по клубу,
    а также отдельно по email и Telegram.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="club_notification_settings",
        verbose_name="Пользователь",
    )
    club = models.ForeignKey(
        Club,
        on_delete=models.CASCADE,
        related_name="member_notification_settings",
        verbose_name="Клуб",
    )
    is_enabled = models.BooleanField("Уведомления включены", default=True)
    email_enabled = models.BooleanField("Разрешить email-уведомления", default=True)
    telegram_enabled = models.BooleanField(
        "Разрешить Telegram-уведомления", default=False
    )

    class Meta:
        verbose_name = "Настройки уведомлений участника клуба"
        verbose_name_plural = "Настройки уведомлений участников клубов"
        unique_together = [("user", "club")]

    def __str__(self) -> str:
        return f"Настройки уведомлений {self.user_id} в {self.club_id}"


class ClubNotificationConfig(models.Model):
    """
    Глобальные настройки уведомлений для клуба.

    Определяет, какие типы событий клуб в принципе отправляет участникам
    и администраторам по каждому из поддерживаемых каналов.
    """

    club = models.OneToOneField(
        Club,
        on_delete=models.CASCADE,
        related_name="notification_config",
        verbose_name="Клуб",
    )
    notify_by_email = models.BooleanField("Включить email-уведомления", default=True)
    notify_by_telegram = models.BooleanField(
        "Включить Telegram-уведомления", default=False
    )

    fee_reminders_enabled = models.BooleanField("Напоминания о взносах", default=True)
    fee_overdue_enabled = models.BooleanField("Просрочка взносов", default=True)
    fee_paid_enabled = models.BooleanField("Уведомления об оплате взноса", default=True)
    subscription_expiring_enabled = models.BooleanField(
        "Истечение подписки клуба", default=True
    )
    tournament_reminders_enabled = models.BooleanField(
        "Напоминания о турнирах", default=True
    )
    new_member_enabled = models.BooleanField("Новый участник клуба", default=True)
    debtors_summary_enabled = models.BooleanField("Сводка должников", default=True)

    class Meta:
        verbose_name = "Настройки уведомлений клуба"
        verbose_name_plural = "Настройки уведомлений клубов"

    def __str__(self) -> str:
        return f"Настройки уведомлений клуба {self.club_id}"


class PlatformAuditAction(models.TextChoices):
    """Типы действий платформенного администратора."""

    CLUB_BLOCKED = "club_blocked", "Клуб заблокирован"
    CLUB_UNBLOCKED = "club_unblocked", "Клуб разблокирован"
    PLAN_CHANGED = "plan_changed", "Тариф изменён"
    SUBSCRIPTION_EXTENDED = "subscription_extended", "Подписка продлена"
    CLUB_DELETED = "club_deleted", "Клуб удалён"
    TRIAL_RESET = "trial_reset", "Trial сброшен"
    CLUB_AUTO_SUSPENDED = "club_auto_suspended", "Клуб автоматически приостановлен"
    CLUB_AUTO_DELETED = "club_auto_deleted", "Клуб автоматически удалён"
    SETTINGS_CHANGED = "settings_changed", "Настройки платформы изменены"


class PlatformAuditLog(models.Model):
    """
    Лог действий администратора платформы (platform_admin).

    Записывает действия вроде блокировки клубов, смены тарифа,
    продления подписки, удаления и прочих административных операций.
    """

    action = models.CharField(
        "Действие",
        max_length=50,
        choices=PlatformAuditAction.choices,
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_audit_logs",
        verbose_name="Кто выполнил",
    )
    club = models.ForeignKey(
        Club,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="Клуб",
    )
    details = models.TextField("Детали", blank=True)
    created_at = models.DateTimeField("Дата", auto_now_add=True)

    class Meta:
        verbose_name = "Запись аудит-лога платформы"
        verbose_name_plural = "Аудит-лог платформы"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        club_name = self.club.name if self.club else "—"
        return f"{self.get_action_display()} — {club_name} ({self.created_at:%d.%m.%Y %H:%M})"


class PlatformSettings(models.Model):
    """
    Глобальные настройки платформы (singleton — одна запись).

    Хранит параметры trial, retention suspended клубов,
    автоудаление и управление регистрацией.
    """

    trial_days = models.PositiveIntegerField(
        "Дней trial-периода",
        default=14,
    )
    suspended_data_retention_days = models.PositiveIntegerField(
        "Хранение данных suspended клубов (дней)",
        default=30,
    )
    auto_delete_suspended = models.BooleanField(
        "Автоудаление suspended клубов",
        default=False,
    )
    registration_open = models.BooleanField(
        "Регистрация клубов открыта",
        default=True,
    )

    class Meta:
        verbose_name = "Настройки платформы"
        verbose_name_plural = "Настройки платформы"

    def __str__(self) -> str:
        return "Настройки платформы"

    def save(self, *args, **kwargs):
        """Гарантирует singleton: pk всегда = 1."""
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "PlatformSettings":
        """Загружает или создаёт единственный экземпляр настроек."""
        obj, _ = cls.objects.get_or_create(pk=1)
        return cast("PlatformSettings", obj)


# Импорт после объявления моделей клубов — FK в Tournament задаётся строкой «clubs.Club».
from apps.tournaments.models import Tournament  # noqa: E402


class ClubTournament(Tournament):
    """
    Прокси к Tournament: записи с заполненным клубом-организатором.

    Отображается в админке в приложении «Клубы»; общий список «Турниры → Многодневные»
    показывает только турниры платформы (club пустой).
    """

    class Meta:
        proxy = True
        verbose_name = "Турнир клуба"
        verbose_name_plural = "Турниры клубов"

    def __str__(self) -> str:
        return f"{self.name} ({self.city})"

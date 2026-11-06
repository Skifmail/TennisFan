"""Программы собственных сборов и операционный учёт заездов."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import Q, Sum
from django.urls import reverse
from django.utils import timezone

from config.validators import validate_image_max_2mb

if TYPE_CHECKING:
    from apps.courts.models import Court
    from apps.tournaments.models import Tournament
    from apps.training.models import Coach
    from apps.users.models import Player, User

CURRENCY_CHOICES = [("RUB", "₽"), ("EUR", "€"), ("USD", "$"), ("GEL", "₾")]


class Camp(models.Model):
    """Публичная программа кемпа TennisFan."""

    if TYPE_CHECKING:
        sessions: models.Manager[CampSession]

    class Audience(models.TextChoices):
        ADULTS = "adults", "Для взрослых"
        CHILDREN = "children", "Для детей"
        FAMILY = "family", "Для всей семьи"

    title: models.CharField[str, str] = models.CharField("Название", max_length=180)
    slug: models.SlugField[str, str] = models.SlugField(
        "Адрес страницы", unique=True, max_length=180
    )
    subtitle: models.CharField[str, str] = models.CharField(
        "Коротко о кемпе", max_length=300
    )
    description: models.TextField[str, str] = models.TextField("О программе")
    audience: models.CharField[str, str] = models.CharField(
        "Для кого", max_length=16, choices=Audience.choices, default=Audience.ADULTS
    )
    levels: models.CharField[str, str] = models.CharField(
        "Уровень подготовки", max_length=200, blank=True
    )
    image = models.ImageField(
        "Обложка", upload_to="camps/", blank=True, validators=[validate_image_max_2mb]
    )
    coaches: models.ManyToManyField[Coach, Coach] = models.ManyToManyField(
        "training.Coach",
        blank=True,
        verbose_name="Тренеры",
        limit_choices_to={"is_active": True},
    )
    organizers: models.ManyToManyField[User, User] = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="organized_camps",
        verbose_name="Организаторы",
        limit_choices_to={"is_active": True},
    )
    program: models.TextField[str, str] = models.TextField(
        "Программа по дням", blank=True
    )
    preparation: models.TextField[str, str] = models.TextField(
        "Что взять и как подготовиться", blank=True
    )
    is_published: models.BooleanField[bool, bool] = models.BooleanField(
        "Опубликован", default=False
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Обновлено", auto_now=True
    )

    class Meta:
        verbose_name = "Кемп"
        verbose_name_plural = "Кемпы"
        ordering = ["title"]

    def __str__(self) -> str:
        return str(self.title)

    def get_absolute_url(self) -> str:
        """Возвращает публичный адрес программы."""
        return cast(str, reverse("camps:detail", kwargs={"slug": self.slug}))


class CampSession(models.Model):
    """Заезд с отдельным набором, вместимостью и условиями."""

    if TYPE_CHECKING:
        camp_id: int
        court_id: int | None
        coordinator_id: int | None
        packages: models.Manager[CampPackage]
        applications: models.Manager[CampApplication]
        expenses: models.Manager[CampExpense]
        games: models.Manager[CampGame]
        tournament_links: models.Manager[CampTournament]

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        OPEN = "open", "Идёт набор"
        CLOSED = "closed", "Набор закрыт"
        COMPLETED = "completed", "Завершён"
        CANCELLED = "cancelled", "Отменён"

    camp: models.ForeignKey[Camp, Camp] = models.ForeignKey(
        Camp,
        on_delete=models.PROTECT,
        related_name="sessions",
        verbose_name="Программа",
    )
    start_date: models.DateField[date, date] = models.DateField("Начало")
    end_date: models.DateField[date, date] = models.DateField("Окончание")
    registration_deadline: models.DateField[date | None, date | None] = (
        models.DateField("Заявки до", null=True, blank=True)
    )
    country: models.CharField[str, str] = models.CharField(
        "Страна", max_length=100, default="Россия"
    )
    city: models.CharField[str, str] = models.CharField("Город", max_length=100)
    venue: models.CharField[str, str] = models.CharField(
        "Площадка и адрес", max_length=300
    )
    court: models.ForeignKey[Court | None, Court | None] = models.ForeignKey(
        "courts.Court",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Корт платформы",
    )
    surface: models.CharField[str, str] = models.CharField(
        "Покрытие и условия кортов", max_length=200, blank=True
    )
    tennis_hours: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField("Часов тенниса за заезд", default=0)
    )
    players_per_court: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField(
            "Игроков на корте", default=4, validators=[MinValueValidator(1)]
        )
    )
    capacity: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField(
            "Мест для игроков", validators=[MinValueValidator(1)]
        )
    )
    auto_reserve: models.BooleanField[bool, bool] = models.BooleanField(
        "Временная бронь при подаче заявки",
        default=True,
        help_text="Если мест достаточно для всей группы, они резервируются без оплаты. Иначе заявка попадает в лист ожидания.",
    )
    minimum_players: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField(
            "Минимум игроков для проведения",
            default=1,
            validators=[MinValueValidator(1)],
        )
    )
    min_age: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField("Возраст от", default=18)
    )
    max_age: models.PositiveSmallIntegerField[int | None, int | None] = (
        models.PositiveSmallIntegerField("Возраст до", null=True, blank=True)
    )
    accommodation: models.TextField[str, str] = models.TextField(
        "Проживание и питание", blank=True
    )
    included: models.TextField[str, str] = models.TextField(
        "Входит в программу", blank=True
    )
    excluded: models.TextField[str, str] = models.TextField(
        "Оплачивается отдельно", blank=True
    )
    terms: models.TextField[str, str] = models.TextField(
        "Подтверждение, оплата и отмена"
    )
    weather_policy: models.TextField[str, str] = models.TextField(
        "Непогода и изменение программы", blank=True
    )
    coordinator: models.ForeignKey[User | None, User | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="coordinated_camps",
        verbose_name="Координатор",
    )
    contact_email: models.EmailField[str, str] = models.EmailField(
        "Email координатора", blank=True
    )
    contact_phone: models.CharField[str, str] = models.CharField(
        "Телефон координатора", max_length=30, blank=True
    )
    status: models.CharField[str, str] = models.CharField(
        "Набор", max_length=16, choices=Status.choices, default=Status.DRAFT
    )

    class Meta:
        verbose_name = "Заезд"
        verbose_name_plural = "Заезды"
        ordering = ["start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="camp_session_date_order",
            )
        ]

    def __str__(self) -> str:
        return f"{self.camp} · {self.city} · {self.start_date:%d.%m.%Y}"

    def clean(self) -> None:
        """Проверяет согласованность дат, возраста и вместимости."""
        super().clean()
        errors: dict[str, str] = {}
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "Окончание не может быть раньше начала."
        if (
            self.registration_deadline
            and self.start_date
            and self.registration_deadline > self.start_date
        ):
            errors["registration_deadline"] = (
                "Приём заявок должен закончиться не позднее начала заезда."
            )
        if self.capacity and self.minimum_players > self.capacity:
            errors["minimum_players"] = "Минимум не может превышать вместимость."
        if self.max_age is not None and self.max_age < self.min_age:
            errors["max_age"] = "Верхняя граница возраста меньше нижней."
        if self.coordinator is not None and not (
            self.coordinator.is_staff
            and self.coordinator.is_active
            and self.coordinator.has_perm("camps.change_campapplication")
        ):
            errors["coordinator"] = (
                "Выберите действующего сотрудника с правом изменения заявок кемпа."
            )
        if (
            self.pk
            and self.capacity
            and self.capacity < self.confirmed_players + self.reserved_players
        ):
            errors["capacity"] = (
                "Вместимость меньше числа подтверждённых и временно забронированных мест."
            )
        if (
            self.pk
            and self.status == self.Status.CANCELLED
            and (
                self.applications.filter(status="confirmed").exists()
                or self.reserved_players > 0
            )
        ):
            errors["status"] = (
                "Сначала отмените подтверждённые заявки и освободите действующие временные брони."
            )
        if self.pk and self.status == self.Status.OPEN and not self.has_active_packages:
            errors["status"] = (
                "Для открытого заезда добавьте хотя бы один активный пакет участия."
            )
        if errors:
            raise ValidationError(errors)

    @transaction.atomic
    def save(self, *args: Any, **kwargs: Any) -> None:
        """При переносе начала переносит действующие брони под блокировкой заезда."""
        previous_start = None
        if self.pk:
            previous_start = (
                type(self)
                .objects.select_for_update()
                .filter(pk=self.pk)
                .values_list("start_date", flat=True)
                .first()
            )
        super().save(*args, **kwargs)
        update_fields = kwargs.get("update_fields")
        if (
            previous_start is not None
            and previous_start != self.start_date
            and (update_fields is None or "start_date" in update_fields)
        ):
            from .communication import invalidate_camp_reminders
            from .reservations import reservation_limit

            for app in self.applications.all().iterator():
                invalidate_camp_reminders(app, departure_only=True)
            self.applications.filter(
                status__in=["new", "contacted", "agreed"],
                reservation_expires_at__gt=timezone.now(),
            ).update(reservation_expires_at=reservation_limit(self))

    @property
    def has_active_packages(self) -> bool:
        """Проверяет наличие хотя бы одного пакета, доступного для заявок."""
        prefetched = getattr(self, "active_packages", None)
        if prefetched is not None:
            return bool(prefetched)
        return bool(self.packages.filter(is_active=True).exists())

    @property
    def application_block_reason(self) -> str | None:
        """Объясняет, почему заявка сейчас недоступна, или возвращает None."""
        today = timezone.localdate()
        if self.status == self.Status.DRAFT:
            return "Заезд ещё готовится."
        if self.status == self.Status.CANCELLED:
            return "Заезд отменён."
        if self.status == self.Status.COMPLETED:
            return "Заезд завершён."
        if self.status == self.Status.CLOSED:
            return "Набор на этот заезд закрыт."
        if self.start_date < today:
            return "Заезд уже начался."
        if self.registration_deadline and self.registration_deadline < today:
            return "Срок приёма заявок истёк."
        if not self.has_active_packages:
            return "Пакеты участия ещё не опубликованы."
        return None

    @property
    def accepts_applications(self) -> bool:
        """Проверяет открытость набора без обещания свободного места."""
        return self.application_block_reason is None

    @property
    def confirmed_players(self) -> int:
        """Считает только подтверждённых игроков, исключая сопровождающих."""
        return int(
            CampParticipant.objects.filter(
                application__session=self,
                application__status=CampApplication.Status.CONFIRMED,
            )
            .exclude(role=CampParticipant.Role.COMPANION)
            .count()
        )

    @property
    def reserved_players(self) -> int:
        """Считает действующие временные брони, исключая сопровождающих и истёкшие сроки."""
        return int(
            CampParticipant.objects.filter(
                application__session=self,
                application__status__in=["new", "contacted", "agreed"],
                application__reservation_expires_at__gt=timezone.now(),
            )
            .exclude(role="companion")
            .count()
        )

    @property
    def available_places(self) -> int:
        """Вычитает подтверждённые места и только действующие временные брони."""
        return int(
            max(0, self.capacity - self.confirmed_players - self.reserved_players)
        )


class CampPackage(models.Model):
    """Цена за игрока и состав пакета конкретного заезда."""

    if TYPE_CHECKING:
        session_id: int
        get_currency_display: Callable[[], str]

    session: models.ForeignKey[CampSession, CampSession] = models.ForeignKey(
        CampSession,
        on_delete=models.PROTECT,
        related_name="packages",
        verbose_name="Заезд",
    )
    name: models.CharField[str, str] = models.CharField("Пакет", max_length=120)
    description: models.TextField[str, str] = models.TextField("Состав пакета")
    price: models.DecimalField[Decimal | None, Decimal | None] = models.DecimalField(
        "Цена за игрока за заезд",
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency: models.CharField[str, str] = models.CharField(
        "Валюта",
        max_length=3,
        choices=CURRENCY_CHOICES,
        default="RUB",
    )
    with_accommodation: models.BooleanField[bool, bool] = models.BooleanField(
        "С проживанием", default=False
    )
    is_active: models.BooleanField[bool, bool] = models.BooleanField(
        "Доступен для заявок", default=True
    )

    class Meta:
        verbose_name = "Пакет участия"
        verbose_name_plural = "Пакеты участия"
        ordering = ["price", "pk"]

    def __str__(self) -> str:
        return f"{self.name} · {self.session}"

    @property
    def price_label(self) -> str:
        """Показывает валюту и отличает отсутствие цены от бесплатного участия."""
        if self.price is None:
            return "Стоимость по запросу"
        amount = f"{self.price:,.2f}"
        if amount.endswith(".00"):
            amount = amount[:-3]
        amount = amount.replace(",", " ").replace(".", ",")
        return f"{amount} {self.get_currency_display()}"


class CampApplication(models.Model):
    """Обращение контактного лица с неизменяемым снимком предложения."""

    if TYPE_CHECKING:
        session_id: int
        package_id: int
        user_id: int | None
        participants: models.Manager[CampParticipant]
        cancellation_requests: models.Manager[CampCancellation]
        reminders: models.Manager[CampReminder]
        payments: models.Manager[CampPayment]
        get_status_display: Callable[[], str]

    class Status(models.TextChoices):
        NEW = "new", "Новая заявка"
        CONTACTED = "contacted", "Связались"
        AGREED = "agreed", "Условия согласованы"
        CONFIRMED = "confirmed", "Участие подтверждено"
        WAITLIST = "waitlist", "Лист ожидания"
        CANCELLED = "cancelled", "Отменена"
        COMPLETED = "completed", "Поездка завершена"

    id: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        primary_key=True, default=uuid.uuid4, editable=False
    )
    submission_key: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        unique=True, editable=False
    )
    session: models.ForeignKey[CampSession, CampSession] = models.ForeignKey(
        CampSession,
        on_delete=models.PROTECT,
        related_name="applications",
        verbose_name="Заезд",
    )
    package: models.ForeignKey[CampPackage, CampPackage] = models.ForeignKey(
        CampPackage, on_delete=models.PROTECT, verbose_name="Пакет"
    )
    user: models.ForeignKey[User | None, User | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="camp_applications",
        verbose_name="Аккаунт заявителя",
    )
    contact_name: models.CharField[str, str] = models.CharField(
        "Контактное лицо", max_length=160
    )
    email: models.EmailField[str, str] = models.EmailField("Email")
    phone: models.CharField[str, str] = models.CharField("Телефон", max_length=30)
    comment: models.TextField[str, str] = models.TextField(
        "Пожелания", blank=True, max_length=2000
    )
    offer_snapshot: models.JSONField[dict[str, Any], dict[str, Any]] = models.JSONField(
        "Условия при подаче", default=dict, editable=False
    )
    quoted_total: models.DecimalField[Decimal | None, Decimal | None] = (
        models.DecimalField(
            "Согласованная сумма за всю заявку",
            max_digits=12,
            decimal_places=2,
            null=True,
            blank=True,
            validators=[MinValueValidator(Decimal("0"))],
        )
    )
    currency: models.CharField[str, str] = models.CharField(
        "Валюта расчётов", max_length=3, default="RUB", editable=False
    )
    reservation_expires_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(
            "Временная бронь до",
            null=True,
            blank=True,
            db_index=True,
        )
    )
    attendance_confirmed_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField("Поездка подтверждена участником", null=True, blank=True)
    )
    attendance_start_date: models.DateField[date | None, date | None] = (
        models.DateField("Подтверждённая дата начала", null=True, blank=True)
    )
    payment_due: models.DateField[date | None, date | None] = models.DateField(
        "Срок доплаты", null=True, blank=True
    )
    accommodation_notes: models.TextField[str, str] = models.TextField(
        "Размещение и трансфер (внутреннее)", blank=True
    )
    staff_notes: models.TextField[str, str] = models.TextField(
        "Заметки координатора", blank=True
    )
    status: models.CharField[str, str] = models.CharField(
        "Статус участия", max_length=16, choices=Status.choices, default=Status.NEW
    )
    consent_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Согласие на обработку заявки", default=timezone.now, editable=False
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Подана", auto_now_add=True
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Обновлена", auto_now=True
    )

    class Meta:
        verbose_name = "Заявка"
        verbose_name_plural = "Заявки"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(reservation_expires_at__isnull=True)
                | Q(status__in=["new", "contacted", "agreed", "waitlist"]),
                name="camp_reservation_pending_only",
            )
        ]

    def __str__(self) -> str:
        return f"{self.contact_name} · {self.session}"

    def clean(self) -> None:
        """Не позволяет выбрать пакет другого заезда."""
        super().clean()
        if (
            self.package_id
            and self.session_id
            and self.package.session_id != self.session_id
        ):
            raise ValidationError({"package": "Пакет относится к другому заезду."})

    @property
    def has_active_reservation(self) -> bool:
        """Проверяет срок брони независимо от своевременности запуска cron."""
        return bool(
            self.status in {"new", "contacted", "agreed"}
            and self.reservation_expires_at is not None
            and self.reservation_expires_at > timezone.now()
        )

    @property
    def reservation_expired(self) -> bool:
        """Позволяет объяснить освобождение мест, сохраняя первоначальный срок в истории заявки."""
        return bool(
            self.reservation_expires_at is not None
            and self.reservation_expires_at <= timezone.now()
            and self.status in {"new", "contacted", "agreed", "waitlist"}
        )

    @property
    def attendance_confirmed(self) -> bool:
        """При переносе даты начала запрашивает новое подтверждение поездки."""
        return bool(
            self.attendance_confirmed_at is not None
            and self.attendance_start_date == self.session.start_date
        )

    @property
    def can_confirm_attendance(self) -> bool:
        """Разрешает ответ только на актуальную бронь или согласованное участие."""
        return bool(
            (self.status == "confirmed" or self.has_active_reservation)
            and self.session.status in {"open", "closed"}
            and self.session.start_date >= timezone.localdate()
            and not self.has_pending_cancellation
        )

    @property
    def has_pending_cancellation(self) -> bool:
        """Подсвечивает координатору необработанное обращение об отмене."""
        return bool(self.cancellation_requests.filter(status="pending").exists())

    def _snapshot_date(self, key: str) -> date | None:
        """Разбирает дату из неизменяемого снимка предложения."""
        raw = self.offer_snapshot.get(key)
        if not isinstance(raw, str):
            return None
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None

    @property
    def snapshot_start_date(self) -> date | None:
        """Возвращает дату начала, зафиксированную при подаче заявки."""
        return self._snapshot_date("start_date")

    @property
    def snapshot_end_date(self) -> date | None:
        """Возвращает дату окончания, зафиксированную при подаче заявки."""
        return self._snapshot_date("end_date")

    @property
    def dates_changed_since_application(self) -> bool:
        """Показывает, что координатор перенёс даты после подачи заявки."""
        start = self.snapshot_start_date
        end = self.snapshot_end_date
        return bool(
            start is not None
            and end is not None
            and (start != self.session.start_date or end != self.session.end_date)
        )

    @property
    def paid_amount(self) -> Decimal:
        """Суммирует ручные поступления за вычетом возвратов."""
        return self.payments.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    @property
    def balance(self) -> Decimal | None:
        """Возвращает остаток после согласования стоимости."""
        return (
            None if self.quoted_total is None else self.quoted_total - self.paid_amount
        )


class CampParticipant(models.Model):
    """Участник заявки; сопровождающий не занимает спортивное место."""

    if TYPE_CHECKING:
        application_id: uuid.UUID
        player_id: int | None
        get_role_display: Callable[[], str]

    class Role(models.TextChoices):
        ADULT = "adult", "Взрослый игрок"
        CHILD = "child", "Ребёнок"
        COMPANION = "companion", "Сопровождающий без тренировок"

    application: models.ForeignKey[CampApplication, CampApplication] = (
        models.ForeignKey(
            CampApplication,
            on_delete=models.CASCADE,
            related_name="participants",
            verbose_name="Заявка",
        )
    )
    name: models.CharField[str, str] = models.CharField("Имя и фамилия", max_length=160)
    role: models.CharField[str, str] = models.CharField(
        "Участие", max_length=16, choices=Role.choices, default=Role.ADULT
    )
    age: models.PositiveSmallIntegerField[int | None, int | None] = (
        models.PositiveSmallIntegerField(
            "Возраст на начало заезда", null=True, blank=True
        )
    )
    level: models.CharField[str, str] = models.CharField(
        "Уровень игры", max_length=120, blank=True
    )
    player: models.ForeignKey[Player | None, Player | None] = models.ForeignKey(
        "users.Player",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="camp_participations",
        verbose_name="Профиль игрока",
    )
    training_group: models.CharField[str, str] = models.CharField(
        "Тренировочная группа", max_length=80, blank=True
    )

    class Meta:
        verbose_name = "Участник"
        verbose_name_plural = "Участники"
        ordering = ["pk"]

    def __str__(self) -> str:
        return str(self.name)

    @property
    def public_label(self) -> str:
        """Не раскрывает имя ребёнка другим участникам в журнале встреч."""
        return (
            f"Юный игрок №{self.pk}" if self.role == self.Role.CHILD else str(self.name)
        )

    def clean(self) -> None:
        """Проверяет роль и возраст игрока относительно заезда."""
        super().clean()
        if self.role == self.Role.COMPANION or not self.application_id:
            return
        session = self.application.session
        if self.role == self.Role.CHILD and (self.age is None or self.age >= 18):
            raise ValidationError({"age": "Для ребёнка укажите возраст младше 18 лет."})
        if self.role == self.Role.ADULT and self.age is not None and self.age < 18:
            raise ValidationError({"role": "Выберите роль ребёнка."})
        if self.role == self.Role.CHILD and session.min_age >= 18:
            raise ValidationError({"role": "Заезд только для взрослых."})
        if self.age is not None and (
            self.age < session.min_age
            or (session.max_age is not None and self.age > session.max_age)
        ):
            raise ValidationError({"age": "Возраст не соответствует условиям заезда."})


class CampPayment(models.Model):
    """Неизменяемая ручная запись поступления или возврата; не банковский платёж."""

    if TYPE_CHECKING:
        application_id: uuid.UUID
        recorded_by_id: int

    application: models.ForeignKey[CampApplication, CampApplication] = (
        models.ForeignKey(
            CampApplication,
            on_delete=models.PROTECT,
            related_name="payments",
            verbose_name="Заявка",
        )
    )
    operation_key: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        unique=True, editable=False, default=uuid.uuid4
    )
    amount: models.DecimalField[Decimal, Decimal] = models.DecimalField(
        "Сумма (+ поступление, − возврат)", max_digits=12, decimal_places=2
    )
    reference: models.CharField[str, str] = models.CharField(
        "Основание / номер операции", max_length=180
    )
    recorded_by: models.ForeignKey[User, User] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        verbose_name="Внёс сотрудник",
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Внесено", auto_now_add=True
    )

    class Meta:
        verbose_name = "Платёж"
        verbose_name_plural = "Платежи"
        ordering = ["created_at"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(amount=0), name="camp_payment_not_zero"
            )
        ]

    def __str__(self) -> str:
        return f"{self.amount} {self.application.currency} · {self.reference}"


class CampExpense(models.Model):
    """План расходов заезда в одной явно выбранной валюте."""

    if TYPE_CHECKING:
        session_id: int

    session: models.ForeignKey[CampSession, CampSession] = models.ForeignKey(
        CampSession,
        on_delete=models.PROTECT,
        related_name="expenses",
        verbose_name="Заезд",
    )
    name: models.CharField[str, str] = models.CharField(
        "Статья расхода", max_length=160
    )
    amount: models.DecimalField[Decimal, Decimal] = models.DecimalField(
        "Сумма",
        max_digits=12,
        decimal_places=2,
        validators=[MinValueValidator(Decimal("0"))],
    )
    currency: models.CharField[str, str] = models.CharField(
        "Валюта",
        max_length=3,
        default="RUB",
        choices=CURRENCY_CHOICES,
    )

    class Meta:
        verbose_name = "Расход"
        verbose_name_plural = "Расходы"

    def __str__(self) -> str:
        return str(self.name)


class CampGame(models.Model):
    """Журнал товарищеских встреч без изменения официального рейтинга."""

    if TYPE_CHECKING:
        session_id: int
        player1_id: int
        player2_id: int

    session: models.ForeignKey[CampSession, CampSession] = models.ForeignKey(
        CampSession,
        on_delete=models.PROTECT,
        related_name="games",
        verbose_name="Заезд",
    )
    player1: models.ForeignKey[CampParticipant, CampParticipant] = models.ForeignKey(
        CampParticipant,
        on_delete=models.PROTECT,
        related_name="games_as_first",
        verbose_name="Первый игрок",
    )
    player2: models.ForeignKey[CampParticipant, CampParticipant] = models.ForeignKey(
        CampParticipant,
        on_delete=models.PROTECT,
        related_name="games_as_second",
        verbose_name="Второй игрок",
    )
    played_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        "Дата и время"
    )
    score: models.CharField[str, str] = models.CharField(
        "Счёт",
        max_length=100,
        blank=True,
        help_text="Например: 6:4, 6:3. Для учебных игр допустим укороченный формат.",
    )
    notes: models.CharField[str, str] = models.CharField(
        "Формат / комментарий", max_length=200, blank=True
    )

    class Meta:
        verbose_name = "Товарищеская встреча"
        verbose_name_plural = "Товарищеские встречи"
        ordering = ["-played_at"]

    def clean(self) -> None:
        """Допускает только разных подтверждённых игроков этого заезда."""
        super().clean()
        if self.player1_id and self.player1_id == self.player2_id:
            raise ValidationError("Выберите двух разных игроков.")
        for field in ("player1", "player2"):
            if not getattr(self, f"{field}_id"):
                continue
            eligible = (
                CampParticipant.objects.filter(
                    pk=getattr(self, f"{field}_id"),
                    application__session_id=self.session_id,
                    application__status__in=[
                        CampApplication.Status.CONFIRMED,
                        CampApplication.Status.COMPLETED,
                    ],
                )
                .exclude(role=CampParticipant.Role.COMPANION)
                .exists()
            )
            if not eligible:
                raise ValidationError(
                    {field: "Выберите подтверждённого игрока этого заезда."}
                )


class CampTournament(models.Model):
    """Ссылка на официальный турнир; матчи и рейтинг ведёт турнирный модуль."""

    if TYPE_CHECKING:
        session_id: int
        tournament_id: int

    session: models.ForeignKey[CampSession, CampSession] = models.ForeignKey(
        CampSession,
        on_delete=models.PROTECT,
        related_name="tournament_links",
        verbose_name="Заезд",
    )
    tournament: models.OneToOneField[Tournament, Tournament] = models.OneToOneField(
        "tournaments.Tournament",
        on_delete=models.PROTECT,
        related_name="camp_link",
        verbose_name="Турнир",
    )

    class Meta:
        verbose_name = "Турнир кемпа"
        verbose_name_plural = "Турниры кемпов"

    def __str__(self) -> str:
        return str(self.tournament)


class CampGuestAccess(models.Model):
    """Одноразовое подтверждение доступа к гостевым заявкам через почту."""

    token_hash: models.CharField[str, str] = models.CharField(
        max_length=64, unique=True, editable=False
    )
    email: models.EmailField[str, str] = models.EmailField(editable=False)
    application_ids: models.JSONField[list[str], list[str]] = models.JSONField(
        default=list, editable=False
    )
    expires_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        editable=False
    )
    used_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, editable=False)
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        verbose_name = "Одноразовый доступ гостя"
        verbose_name_plural = "Одноразовые доступы гостей"


class CampCancellation(models.Model):
    """Запрос на отмену всей заявки; место освобождается только после решения команды."""

    if TYPE_CHECKING:
        application_id: uuid.UUID
        resolved_by_id: int | None
        get_status_display: Callable[[], str]

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает решения"
        APPROVED = "approved", "Отмена подтверждена"
        REJECTED = "rejected", "Запрос отклонён"

    application: models.ForeignKey[CampApplication, CampApplication] = (
        models.ForeignKey(
            CampApplication,
            on_delete=models.PROTECT,
            related_name="cancellation_requests",
            verbose_name="Заявка",
        )
    )
    submission_key: models.UUIDField[uuid.UUID, uuid.UUID] = models.UUIDField(
        unique=True, default=uuid.uuid4, editable=False
    )
    reason: models.TextField[str, str] = models.TextField("Причина", max_length=2000)
    status: models.CharField[str, str] = models.CharField(
        "Решение", max_length=16, choices=Status.choices, default=Status.PENDING
    )
    resolution: models.TextField[str, str] = models.TextField(
        "Ответ участнику", max_length=2000, blank=True
    )
    resolved_by: models.ForeignKey[User | None, User | None] = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Решение принял",
    )
    created_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        auto_now_add=True
    )
    resolved_at: models.DateTimeField[datetime | None, datetime | None] = (
        models.DateTimeField(null=True, blank=True)
    )

    class Meta:
        verbose_name = "Запрос на отмену"
        verbose_name_plural = "Запросы на отмену"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["application"],
                condition=models.Q(status="pending"),
                name="camp_one_pending_cancellation",
            )
        ]


class CampReminder(models.Model):
    """Журнал однократных напоминаний с повтором явных ошибок доставки."""

    if TYPE_CHECKING:
        application_id: uuid.UUID

    application: models.ForeignKey[CampApplication, CampApplication] = (
        models.ForeignKey(
            CampApplication, on_delete=models.CASCADE, related_name="reminders"
        )
    )
    kind: models.CharField[str, str] = models.CharField("Повод", max_length=32)
    basis: models.CharField[str, str] = models.CharField(
        "Дата или версия повода", max_length=100
    )
    status: models.CharField[str, str] = models.CharField(
        "Доставка",
        max_length=16,
        default="pending",
        choices=[
            ("pending", "В очереди"),
            ("processing", "Отправляется"),
            ("sent", "Подготовлено / отправлено"),
            ("skipped", "Неактуально / отключено"),
            ("failed", "Ошибка"),
            ("uncertain", "Результат отправки неизвестен"),
        ],
    )
    attempts: models.PositiveSmallIntegerField[int, int] = (
        models.PositiveSmallIntegerField(default=0)
    )
    updated_at: models.DateTimeField[datetime, datetime] = models.DateTimeField(
        default=timezone.now
    )

    class Meta:
        verbose_name = "Напоминание"
        verbose_name_plural = "Напоминания"
        constraints = [
            models.UniqueConstraint(
                fields=["application", "kind", "basis"], name="camp_unique_reminder"
            )
        ]

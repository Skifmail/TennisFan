"""Детские участники, представители и история управления."""

import uuid
from datetime import date

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from apps.notifications.models import EventPolicy

from .rules import AgeRule, Ball, ScoreFormat, age_on


class ChildProfile(models.Model):
    """Спортивный профиль ребёнка без учётной записи для входа."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        ACTIVE = "active", "Активен"
        SUSPENDED = "suspended", "Приостановлен"
        ARCHIVED = "archived", "Архив"

    first_name = models.CharField("Имя", max_length=100)
    last_name = models.CharField("Фамилия", max_length=100)
    birth_date = models.DateField("Дата рождения")
    city = models.CharField("Город", max_length=100)
    gender = models.CharField(
        "Пол", max_length=10, choices=[("male", "Мальчик"), ("female", "Девочка")]
    )
    ball = models.CharField(
        "Ступень подготовки", max_length=10, choices=Ball.choices, default=Ball.RED
    )
    status = models.CharField(
        "Статус", max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    birth_verified_at = models.DateTimeField(
        "Возраст проверен", null=True, blank=True, editable=False
    )
    created_at = models.DateTimeField("Создан", default=timezone.now, editable=False)
    updated_at = models.DateTimeField("Обновлён", auto_now=True)
    adult_user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="junior_history",
        verbose_name="Владелец после совершеннолетия",
        editable=False,
    )

    class Meta:
        verbose_name = "Детский профиль"
        verbose_name_plural = "01. Детские профили"
        ordering = ["last_name", "first_name", "pk"]
        permissions = [
            ("verify_representative", "Может проверять законных представителей")
        ]

    def __str__(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def clean(self) -> None:
        super().clean()
        if self.birth_date:
            today = timezone.localdate()
            if self.birth_date > today or age_on(self.birth_date, today) >= 18:
                raise ValidationError(
                    {"birth_date": "Укажите дату рождения ребёнка младше 18 лет."}
                )

    def age_at(self, reference: date) -> int:
        """Возвращает возраст для проверки допуска к соревнованиям."""
        return age_on(self.birth_date, reference)

    @property
    def public_label(self) -> str:
        """Возвращает нейтральную подпись без раскрытия имени ребёнка."""
        return f"Участник {self.pk}"


class GuardianLink(models.Model):
    """Полномочия взрослого на управление конкретным детским профилем."""

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает проверки"
        VERIFIED = "verified", "Подтверждены"
        REJECTED = "rejected", "Отклонены"
        REVOKED = "revoked", "Прекращены"

    class Relation(models.TextChoices):
        PARENT = "parent", "Родитель / усыновитель"
        GUARDIAN = "guardian", "Опекун / попечитель"

    child = models.ForeignKey(
        ChildProfile,
        on_delete=models.PROTECT,
        related_name="guardians",
        verbose_name="Ребёнок",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="junior_guardianships",
        verbose_name="Взрослый",
    )
    relation = models.CharField(
        "Основание", max_length=12, choices=Relation.choices, default=Relation.PARENT
    )
    status = models.CharField(
        "Полномочия", max_length=12, choices=Status.choices, default=Status.PENDING
    )
    receive_notifications = models.BooleanField("Получать уведомления", default=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="junior_verifications",
        verbose_name="Проверяющий",
    )
    verified_at = models.DateTimeField("Проверено", null=True, blank=True)
    evidence = models.CharField(
        "Основание проверки",
        max_length=255,
        blank=True,
        help_text="Способ предъявления и вид документа. Не вводите номера документов и копии.",
    )
    reason = models.CharField("Причина решения", max_length=255, blank=True)
    created_at = models.DateTimeField("Создана", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Представитель"
        verbose_name_plural = "02. Представители и проверки"
        constraints = [
            models.UniqueConstraint(
                fields=["child", "user"], name="unique_junior_guardian"
            )
        ]

    def __str__(self) -> str:
        return f"{self.child} — представитель #{self.user_id}"


class AuditEvent(models.Model):
    """Неизменяемый журнал действий с указанием взрослого и участника."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="junior_actions",
        verbose_name="Кто выполнил",
    )
    child = models.ForeignKey(
        ChildProfile,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_events",
        verbose_name="Ребёнок",
    )
    action = models.CharField("Действие", max_length=80)
    details = models.JSONField("Подробности", default=dict, blank=True)
    created_at = models.DateTimeField("Когда", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Событие"
        verbose_name_plural = "09. Журнал действий"
        ordering = ["-created_at", "-pk"]

    def __str__(self) -> str:
        return f"{self.created_at:%d.%m.%Y %H:%M} — {self.action}"


class LegalRelease(models.Model):
    """Версия отдельно утверждённых юридических текстов детской лиги."""

    version = models.CharField("Версия", max_length=40, unique=True)
    processing_text = models.TextField("Согласие на обработку данных")
    publication_text = models.TextField(
        "Отдельное согласие на публикацию имени и результатов"
    )
    reviewed = models.BooleanField(
        "Проверено юристом, разрешено оформление согласий", default=False
    )
    is_current = models.BooleanField("Текущая версия", default=False)
    created_at = models.DateTimeField("Создана", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Юридическая версия"
        verbose_name_plural = "03. Юридические тексты"
        constraints = [
            models.UniqueConstraint(
                fields=["is_current"],
                condition=models.Q(is_current=True),
                name="one_current_junior_release",
            )
        ]

    def clean(self) -> None:
        super().clean()
        if self.is_current and not self.reviewed:
            raise ValidationError("Текущая версия должна быть проверена юристом.")
        if self.pk and self.consents.exists():
            previous = type(self).objects.get(pk=self.pk)
            if any(
                getattr(previous, field) != getattr(self, field)
                for field in (
                    "version",
                    "processing_text",
                    "publication_text",
                    "reviewed",
                )
            ):
                raise ValidationError(
                    "По этой версии уже получены согласия. Создайте новую версию."
                )

    def __str__(self) -> str:
        return str(self.version)


class ChildConsent(models.Model):
    """Отдельное согласие проверенного представителя с текстовым снимком."""

    class Kind(models.TextChoices):
        PROCESSING = "processing", "Обработка данных"
        PUBLICATION = "publication", "Публикация имени и результатов"

    guardian = models.ForeignKey(
        GuardianLink,
        on_delete=models.PROTECT,
        related_name="consents",
        verbose_name="Представитель",
    )
    release = models.ForeignKey(
        LegalRelease,
        on_delete=models.PROTECT,
        related_name="consents",
        verbose_name="Версия",
    )
    kind = models.CharField("Вид согласия", max_length=12, choices=Kind.choices)
    text_snapshot = models.TextField("Подтверждённый текст", editable=False)
    accepted_at = models.DateTimeField("Получено", default=timezone.now, editable=False)
    withdrawn_at = models.DateTimeField(
        "Отозвано", null=True, blank=True, editable=False
    )

    class Meta:
        verbose_name = "Согласие"
        verbose_name_plural = "04. Согласия представителей"
        constraints = [
            models.UniqueConstraint(
                fields=["guardian", "release", "kind"],
                condition=models.Q(withdrawn_at__isnull=True),
                name="one_live_junior_consent",
            )
        ]

    def __str__(self) -> str:
        return f"{self.guardian.child} — {self.get_kind_display()} ({self.release})"


class JuniorSeason(models.Model):
    """Сезон и опубликованные правила собственного рейтинга TennisFan."""

    name = models.CharField("Сезон", max_length=100)
    starts_on = models.DateField("Начало")
    ends_on = models.DateField("Окончание")
    best_results = models.PositiveSmallIntegerField(
        "Учитывать лучших результатов", default=6
    )
    points_winner = models.PositiveIntegerField("Очки победителю", default=100)
    points_finalist = models.PositiveIntegerField("Очки финалисту", default=70)
    points_semifinalist = models.PositiveIntegerField("Очки за 3–4 место", default=45)
    points_participation = models.PositiveIntegerField("Очки за участие", default=10)
    rules_version = models.CharField("Версия правил", max_length=40, default="1")

    class Meta:
        verbose_name = "Сезон и рейтинг"
        verbose_name_plural = "05. Сезоны и правила рейтинга"
        ordering = ["-starts_on"]

    def clean(self) -> None:
        super().clean()
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValidationError("Окончание сезона раньше начала.")
        if not 1 <= self.best_results <= 100:
            raise ValidationError("Число результатов должно быть от 1 до 100.")
        if (
            not self.points_winner
            >= self.points_finalist
            >= self.points_semifinalist
            >= self.points_participation
        ):
            raise ValidationError("Очки должны убывать от победителя к участникам.")
        if self.pk and self.tournaments.exists():
            old = type(self).objects.get(pk=self.pk)
            fields = (
                "starts_on",
                "ends_on",
                "best_results",
                "points_winner",
                "points_finalist",
                "points_semifinalist",
                "points_participation",
                "rules_version",
            )
            if any(getattr(old, key) != getattr(self, key) for key in fields):
                raise ValidationError(
                    "Правила используемого сезона неизменны. Создайте новый сезон."
                )

    def __str__(self) -> str:
        return str(self.name)


class JuniorTournament(models.Model):
    """Самостоятельная категория детского соревнования с фиксируемым регламентом."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        OPEN = "open", "Регистрация открыта"
        CLOSED = "closed", "Регистрация закрыта"
        ACTIVE = "active", "Идёт"
        COMPLETED = "completed", "Завершён"
        CANCELLED = "cancelled", "Отменён"

    class Format(models.TextChoices):
        ROUND_ROBIN = "round_robin", "Круговой"
        ELIMINATION = "elimination", "На выбывание"

    name = models.CharField("Название категории / турнира", max_length=200)
    event_name = models.CharField(
        "Общее мероприятие (необязательно)", max_length=200, blank=True
    )
    city = models.CharField("Город", max_length=100)
    venue = models.CharField("Место проведения", max_length=255)
    club = models.ForeignKey(
        "clubs.Club",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        verbose_name="Клуб",
    )
    organizer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="junior_tournaments",
        verbose_name="Организатор",
    )
    season = models.ForeignKey(
        JuniorSeason,
        on_delete=models.PROTECT,
        related_name="tournaments",
        verbose_name="Сезон",
    )
    ball = models.CharField("Мяч", max_length=10, choices=Ball.choices)
    min_age = models.PositiveSmallIntegerField("Минимальный возраст", default=5)
    max_age = models.PositiveSmallIntegerField("Максимальный возраст", default=10)
    age_rule = models.CharField(
        "Расчёт возраста",
        max_length=12,
        choices=AgeRule.choices,
        default=AgeRule.START_DATE,
    )
    gender = models.CharField(
        "Участники",
        max_length=10,
        choices=[("open", "Все"), ("male", "Мальчики"), ("female", "Девочки")],
        default="open",
    )
    format = models.CharField(
        "Сетка", max_length=12, choices=Format.choices, default=Format.ROUND_ROBIN
    )
    score_format = models.CharField(
        "Счёт",
        max_length=12,
        choices=ScoreFormat.choices,
        default=ScoreFormat.SHORT_SET,
    )
    starts_at = models.DateTimeField("Начало")
    registration_ends_at = models.DateTimeField("Окончание регистрации")
    capacity = models.PositiveSmallIntegerField("Мест", default=8)
    entry_fee = models.DecimalField(
        "Взнос, ₽", max_digits=9, decimal_places=2, default=0
    )
    regulation = models.TextField("Регламент: программа, допуск, снятие и возврат")
    regulation_version = models.PositiveIntegerField("Версия регламента", default=1)
    ranked = models.BooleanField("Учитывать в рейтинге", default=True)
    status = models.CharField(
        "Статус", max_length=12, choices=Status.choices, default=Status.DRAFT
    )
    created_at = models.DateTimeField("Создан", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Детский турнир"
        verbose_name_plural = "06. Детские турниры"
        ordering = ["-starts_at", "pk"]
        permissions = [("manage_competition", "Может проводить детские турниры")]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(min_age__lte=models.F("max_age"))
                & models.Q(max_age__lte=17),
                name="junior_age_range",
            ),
            models.CheckConstraint(
                condition=models.Q(entry_fee__gte=0), name="junior_nonnegative_fee"
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if not 0 <= self.min_age <= self.max_age <= 17:
            raise ValidationError(
                "Возрастная категория должна находиться в пределах 0–17 лет."
            )
        if self.ball == Ball.YELLOW and self.max_age <= 10:
            raise ValidationError(
                "Категория «10 лет и младше» использует замедленные мячи. Для жёлтого мяча задайте старшую категорию и её условия допуска."
            )
        if not 2 <= self.capacity <= 64:
            raise ValidationError("Вместимость должна быть от 2 до 64 участников.")
        if self.format == self.Format.ROUND_ROBIN and self.capacity > 16:
            raise ValidationError("Для кругового формата максимум 16 участников.")
        if (
            self.starts_at
            and self.registration_ends_at
            and self.registration_ends_at > self.starts_at
        ):
            raise ValidationError("Регистрация должна завершаться до начала турнира.")
        if self.season_id and self.starts_at:
            start: date = timezone.localdate(self.starts_at)
            if not self.season.starts_on <= start <= self.season.ends_on:
                raise ValidationError("Дата турнира вне сезона.")
        if self.pk and self.entries.exists():
            old = type(self).objects.get(pk=self.pk)
            frozen = (
                "ball",
                "min_age",
                "max_age",
                "age_rule",
                "gender",
                "format",
                "score_format",
                "entry_fee",
                "season_id",
                "regulation",
                "regulation_version",
                "ranked",
                "starts_at",
                "capacity",
            )
            if any(getattr(old, key) != getattr(self, key) for key in frozen):
                raise ValidationError(
                    "У турнира уже есть заявки. Для другого регламента создайте новую категорию; перенос оформляется отдельной операцией."
                )

    @property
    def age_reference(self) -> date:
        start: date = timezone.localdate(self.starts_at)
        return date(start.year, 12, 31) if self.age_rule == AgeRule.YEAR_END else start

    @property
    def court_label(self) -> str:
        labels: dict[str, str] = {
            str(Ball.RED): "Корт 11–13 м",
            str(Ball.ORANGE): "Корт около 18 м",
            str(Ball.GREEN): "Полноразмерный корт",
            str(Ball.YELLOW): "Полноразмерный корт",
        }
        return labels[self.ball]

    def __str__(self) -> str:
        return str(self.name)


class JuniorEntry(models.Model):
    """Одна заявка ребёнка, оформленная конкретным представителем."""

    class Status(models.TextChoices):
        CONFIRMED = "confirmed", "Подтверждена"
        PAYMENT = "payment", "Ожидает оплаты"
        WAITLIST = "waitlist", "Лист ожидания"
        WITHDRAWN = "withdrawn", "Снята"

    tournament = models.ForeignKey(
        JuniorTournament,
        on_delete=models.PROTECT,
        related_name="entries",
        verbose_name="Турнир",
    )
    child = models.ForeignKey(
        ChildProfile,
        on_delete=models.PROTECT,
        related_name="entries",
        verbose_name="Ребёнок",
    )
    guardian = models.ForeignKey(
        GuardianLink,
        on_delete=models.PROTECT,
        related_name="entries",
        verbose_name="Кто записал",
    )
    status = models.CharField("Статус", max_length=12, choices=Status.choices)
    amount = models.DecimalField(
        "Зафиксированный взнос", max_digits=9, decimal_places=2
    )
    regulation_snapshot = models.JSONField("Принятые условия", default=dict)
    reserved_until = models.DateTimeField("Резерв до", null=True, blank=True)
    created_at = models.DateTimeField("Подана", default=timezone.now, editable=False)
    reminder_sent_at = models.DateTimeField(
        "Напоминание перед стартом", null=True, blank=True, editable=False
    )

    class Meta:
        verbose_name = "Детская заявка"
        verbose_name_plural = "07. Заявки на турниры"
        ordering = ["created_at", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "child"], name="unique_junior_entry"
            )
        ]

    def __str__(self) -> str:
        return f"{self.child} — {self.tournament}"


class JuniorPayment(models.Model):
    """Отдельная попытка оплаты конкретного резерва, без детских данных в шлюзе."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    entry = models.ForeignKey(
        JuniorEntry,
        on_delete=models.PROTECT,
        related_name="payments",
        verbose_name="Заявка",
    )
    payer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="junior_payments",
        verbose_name="Плательщик",
    )
    reservation = models.DateTimeField("Оплачиваемый резерв")
    amount = models.DecimalField("Сумма, ₽", max_digits=9, decimal_places=2)
    provider_id = models.CharField(
        "ID ЮKassa", max_length=64, unique=True, null=True, blank=True
    )
    confirmation_url = models.URLField("Страница оплаты", max_length=1000, blank=True)
    return_url = models.URLField("Адрес возврата", max_length=1000, blank=True)
    receipt_email = models.EmailField("Email чека", blank=True)
    status = models.CharField(
        "Статус",
        max_length=16,
        choices=[
            ("new", "Создаётся"),
            ("pending", "Ожидает оплаты"),
            ("paid", "Оплачен"),
            ("cancelled", "Отменён"),
            ("refund_pending", "Нужен возврат"),
            ("refunded", "Возвращён"),
        ],
        default="new",
    )
    created_at = models.DateTimeField("Создан", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Детская оплата"
        verbose_name_plural = "08. Оплаты"
        constraints = [
            models.UniqueConstraint(
                fields=["entry", "reservation"],
                name="unique_junior_reservation_payment",
            )
        ]

    def __str__(self) -> str:
        return f"{self.amount} ₽ · {self.get_status_display()} · {self.pk}"


class JuniorRefund(models.Model):
    """Заявка на полный возврат исходному плательщику."""

    payment = models.OneToOneField(
        JuniorPayment,
        on_delete=models.PROTECT,
        related_name="refund",
        verbose_name="Оплата",
    )
    reason = models.CharField("Причина", max_length=255)
    status = models.CharField(
        "Статус",
        max_length=12,
        choices=[
            ("requested", "Запрошен"),
            ("processing", "Выполняется"),
            ("refunded", "Возвращён"),
        ],
        default="requested",
    )
    key = models.UUIDField("Ключ операции", default=uuid.uuid4, editable=False)
    provider_id = models.CharField("ID возврата ЮKassa", max_length=64, blank=True)
    started_at = models.DateTimeField(
        "Первая попытка возврата", null=True, blank=True, editable=False
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Одобрил",
    )
    created_at = models.DateTimeField("Создан", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Детский возврат"
        verbose_name_plural = "08. Запросы возврата"


class JuniorMatch(models.Model):
    """Детский матч; счёт не попадает во взрослый FAN и оценки соперников."""

    tournament = models.ForeignKey(
        JuniorTournament,
        on_delete=models.PROTECT,
        related_name="matches",
        verbose_name="Турнир",
    )
    round_number = models.PositiveSmallIntegerField("Круг", default=1)
    position = models.PositiveSmallIntegerField("Позиция", default=0)
    entry1 = models.ForeignKey(
        JuniorEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="matches_first",
        verbose_name="Участник 1",
    )
    entry2 = models.ForeignKey(
        JuniorEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="matches_second",
        verbose_name="Участник 2",
    )
    winner = models.ForeignKey(
        JuniorEntry,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="won_matches",
        verbose_name="Победитель",
    )
    status = models.CharField(
        "Статус",
        max_length=12,
        choices=[
            ("pending", "Ожидает"),
            ("completed", "Сыгран"),
            ("walkover", "Технический результат"),
            ("bye", "Свободный круг"),
        ],
        default="pending",
    )
    scores = models.JSONField("Счёт", default=list, blank=True)
    reason = models.CharField(
        "Основание технического результата / исправления", max_length=255, blank=True
    )
    scheduled_at = models.DateTimeField("Расписание", null=True, blank=True)
    completed_at = models.DateTimeField("Результат зафиксирован", null=True, blank=True)

    class Meta:
        verbose_name = "Детский матч"
        verbose_name_plural = "07. Матчи и результаты"
        ordering = ["round_number", "position"]
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "round_number", "position"],
                name="unique_junior_match_position",
            )
        ]

    def __str__(self) -> str:
        return f"{self.tournament} · круг {self.round_number}, матч {self.position + 1}"


class JuniorAward(models.Model):
    """Начисление очков за один завершённый турнир с исторической категорией."""

    tournament = models.ForeignKey(
        JuniorTournament,
        on_delete=models.PROTECT,
        related_name="awards",
        verbose_name="Турнир",
    )
    child = models.ForeignKey(
        ChildProfile,
        on_delete=models.PROTECT,
        related_name="awards",
        verbose_name="Ребёнок",
    )
    season = models.ForeignKey(
        JuniorSeason,
        on_delete=models.PROTECT,
        related_name="awards",
        verbose_name="Сезон",
    )
    place = models.PositiveSmallIntegerField("Место")
    points = models.PositiveIntegerField("Очки")
    ball = models.CharField(
        "Мяч на момент турнира", max_length=10, choices=Ball.choices
    )
    category = models.CharField("Историческая категория", max_length=80)
    city = models.CharField("Город турнира", max_length=100)
    rules_version = models.CharField("Версия правил", max_length=40)
    created_at = models.DateTimeField("Начислено", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Начисление рейтинга"
        verbose_name_plural = "08. Начисления рейтинга"
        constraints = [
            models.UniqueConstraint(
                fields=["tournament", "child"], name="unique_junior_award"
            )
        ]


class JuniorNotificationPolicy(EventPolicy):
    """Настройка детских уведомлений в едином разделе админки."""

    class Meta:
        proxy = True
        verbose_name = "Каналы детских уведомлений"
        verbose_name_plural = "10. Каналы уведомлений"


class JuniorAppeal(models.Model):
    """Возражение законного представителя по результату детского матча."""

    match = models.ForeignKey(
        JuniorMatch,
        on_delete=models.PROTECT,
        related_name="appeals",
        verbose_name="Матч",
    )
    tournament = models.ForeignKey(
        JuniorTournament,
        on_delete=models.PROTECT,
        related_name="appeals",
        verbose_name="Турнир",
    )
    guardian = models.ForeignKey(
        GuardianLink,
        on_delete=models.PROTECT,
        related_name="appeals",
        verbose_name="Представитель",
    )
    message = models.TextField("Возражение", max_length=2000)
    status = models.CharField(
        "Статус",
        max_length=12,
        choices=[("open", "Открыто"), ("resolved", "Рассмотрено")],
        default="open",
    )
    response = models.TextField("Решение", max_length=2000, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Рассмотрел",
    )
    created_at = models.DateTimeField("Подано", default=timezone.now, editable=False)

    class Meta:
        verbose_name = "Возражение"
        verbose_name_plural = "07. Возражения по результатам"
        constraints = [
            models.UniqueConstraint(
                fields=["match", "guardian"],
                condition=models.Q(status="open"),
                name="unique_open_junior_appeal",
            )
        ]


class JuniorRankingSnapshot(models.Model):
    """История опубликованных позиций без копирования персональных данных."""

    child = models.ForeignKey(
        ChildProfile,
        on_delete=models.PROTECT,
        related_name="ranking_history",
        verbose_name="Ребёнок",
    )
    tournament = models.ForeignKey(
        JuniorTournament, on_delete=models.PROTECT, verbose_name="Повод пересчёта"
    )
    season = models.ForeignKey(
        JuniorSeason, on_delete=models.PROTECT, verbose_name="Сезон"
    )
    ball = models.CharField("Мяч", max_length=10, choices=Ball.choices)
    category = models.CharField("Категория", max_length=80)
    position = models.PositiveIntegerField("Позиция")
    points = models.PositiveIntegerField("Очки")
    valid = models.BooleanField("Расчёт актуален", default=True)
    created_at = models.DateTimeField(
        "Опубликовано", default=timezone.now, editable=False
    )

    class Meta:
        verbose_name = "История позиции"
        verbose_name_plural = "08. История рейтинга"
        ordering = ["-created_at", "-pk"]

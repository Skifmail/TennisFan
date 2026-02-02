from django.db import models
from django.conf import settings
from django.utils import timezone
from dateutil.relativedelta import relativedelta


class SubscriptionTier(models.Model):
    """Subscription tier levels."""
    
    class Level(models.TextChoices):
        FREE = 'free', 'Free'
        SILVER = 'silver', 'Silver'
        GOLD = 'gold', 'Gold'
        DIAMOND = 'diamond', 'Diamond'

    name = models.CharField("Название тарифа", max_length=50, choices=Level.choices, unique=True)
    price = models.DecimalField("Стоимость (руб)", max_digits=10, decimal_places=2, default=0)
    
    # Registration limits
    max_tournaments = models.PositiveIntegerField(
        "Максимум турниров в месяц", 
        help_text="0 = без ограничений, или конкретное число",
        default=0
    )
    is_unlimited = models.BooleanField("Неограниченные регистрации", default=False)
    
    # Discounts
    one_day_tournament_discount = models.PositiveIntegerField(
        "Скидка на однодневные турниры (%)", 
        default=0,
        help_text="Процент скидки (0-100)"
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

    class Meta:
        verbose_name = "Тариф"
        verbose_name_plural = "Тарифы"
        ordering = ['price']

    def __str__(self):
        return self.get_name_display()


class UserSubscription(models.Model):
    """User's active subscription."""
    
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='subscription',
        verbose_name="Пользователь"
    )
    tier = models.ForeignKey(
        SubscriptionTier, 
        on_delete=models.PROTECT, 
        verbose_name="Тариф"
    )
    start_date = models.DateTimeField("Дата начала", default=timezone.now)
    end_date = models.DateTimeField("Дата окончания")
    is_active = models.BooleanField("Активна", default=True)
    
    # Registration tracking for the current period
    tournaments_registered_count = models.PositiveIntegerField(
        "Использовано регистраций в этом месяце", 
        default=0
    )

    class Meta:
        verbose_name = "Подписка пользователя"
        verbose_name_plural = "Подписки пользователей"

    def __str__(self):
        return f"{self.user} - {self.tier} ({self.status_display})"

    @property
    def status_display(self):
        if not self.is_active:
            return "Отменена"
        if self.end_date < timezone.now():
            return "Истекла"
        return "Активна"

    def save(self, *args, **kwargs):
        if not self.end_date:
            # Default to 1 month from start
            self.end_date = self.start_date + relativedelta(months=1)
        super().save(*args, **kwargs)

    def is_valid(self):
        return self.is_active and self.end_date > timezone.now()

    def can_register_for_tournament(self):
        """Check if user has registration slots left."""
        if self.tier.is_unlimited:
            return True
        return self.tournaments_registered_count < self.tier.max_tournaments
    
    def increment_usage(self):
        self.tournaments_registered_count += 1
        self.save(update_fields=['tournaments_registered_count'])

    def decrement_usage(self):
        """Восстановить одну регистрацию (например, при удалении из турнира)."""
        if self.tournaments_registered_count > 0:
            self.tournaments_registered_count -= 1
            self.save(update_fields=['tournaments_registered_count'])

    def get_remaining_slots(self):
        if self.tier.is_unlimited:
            return 999
        return max(0, self.tier.max_tournaments - self.tournaments_registered_count)

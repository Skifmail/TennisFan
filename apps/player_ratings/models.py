"""
Модели оценки соперников после матча.
"""

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.tournaments.models import Match
from apps.users.models import Player

from .constants import RATING_MAX, RATING_MIN
from .enums import SkillMetric


def _rating_field(**kwargs):
    return models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(RATING_MIN), MaxValueValidator(RATING_MAX)],
        **kwargs,
    )


class PlayerSkillRating(models.Model):
    """
    Одна сырая оценка соперника за матч (анонимная, от from_player к to_player).
    Один раз на матч на оценивающего (unique match, from_player).
    """

    match = models.ForeignKey(
        Match,
        on_delete=models.CASCADE,
        related_name="skill_ratings",
    )
    from_player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="skill_ratings_given",
    )
    to_player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="skill_ratings_received",
    )

    serve = _rating_field()
    accuracy = _rating_field()
    tactics = _rating_field()
    speed = _rating_field()
    endurance = _rating_field()
    consistency = _rating_field()
    net_play = _rating_field()
    pressure_play = _rating_field()
    fighting_spirit = _rating_field()
    communication = _rating_field()
    punctuality = _rating_field()
    fairness = _rating_field()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "player_ratings_skillrating"
        verbose_name = "Оценка навыков соперника"
        verbose_name_plural = "Оценки навыков соперников"
        constraints = [
            models.UniqueConstraint(
                fields=["match", "from_player"],
                name="player_ratings_unique_match_from",
            )
        ]
        indexes = [
            models.Index(fields=["to_player"]),
        ]

    def __str__(self):
        return f"{self.match_id} — {self.from_player_id} → {self.to_player_id}"

    def get_metric_value(self, metric_name: str) -> int | None:
        return getattr(self, metric_name, None)

    def set_metric_value(self, metric_name: str, value: int | None) -> None:
        if metric_name in SkillMetric.all_metric_names() and (
            value is None or (RATING_MIN <= value <= RATING_MAX)
        ):
            setattr(self, metric_name, value)


class SkillMetricConfig(models.Model):
    """Настраиваемое отображаемое название метрики навыков игрока."""

    metric_name = models.CharField(
        max_length=30,
        choices=SkillMetric.choices,
        unique=True,
        db_index=True,
        verbose_name="Системное имя метрики",
    )
    label = models.CharField(
        max_length=100,
        verbose_name="Название для отображения",
        help_text="Будет показано в профиле игрока и в форме оценки соперника.",
    )
    display_on_page = models.BooleanField(
        default=True,
        verbose_name="Отображать на странице",
        help_text=(
            "Если включено, метрика будет показана в профиле игрока и в форме оценки соперника."
        ),
    )

    class Meta:
        verbose_name = "Название метрики навыков"
        verbose_name_plural = "Названия метрик навыков"
        db_table = "player_ratings_skillmetricconfig"

    def __str__(self) -> str:
        return f"{self.metric_name} → {self.label}"


class PlayerSkillAggregate(models.Model):
    """
    Агрегат по игроку и метрике: среднее, байесовское среднее, число оценок.
    Пересчитывается при сохранении PlayerSkillRating.
    """

    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="skill_aggregates",
    )
    metric_name = models.CharField(
        max_length=30,
        choices=SkillMetric.choices,
        db_index=True,
    )
    average_raw = models.FloatField(default=0.0)
    average_weighted = models.FloatField(default=0.0)
    votes_count = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "player_ratings_skillaggregate"
        verbose_name = "Агрегат навыка игрока"
        verbose_name_plural = "Агрегаты навыков игроков"
        constraints = [
            models.UniqueConstraint(
                fields=["player", "metric_name"],
                name="player_ratings_unique_player_metric",
            )
        ]
        indexes = [
            models.Index(fields=["player", "metric_name"]),
        ]

    def __str__(self):
        return f"{self.player_id} {self.metric_name} (n={self.votes_count})"

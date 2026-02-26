"""
Перечисление метрик оценки соперника.
"""

from django.db import models


class SkillMetric(models.TextChoices):
    """12 фиксированных метрик оценки (1–10, nullable)."""

    # Игровые
    SERVE = "serve", "Подача"
    ACCURACY = "accuracy", "Точность"
    TACTICS = "tactics", "Тактика"
    SPEED = "speed", "Скорость"
    ENDURANCE = "endurance", "Выносливость"
    CONSISTENCY = "consistency", "Стабильность"
    NET_PLAY = "net_play", "Игра у сетки"

    # Психология
    PRESSURE_PLAY = "pressure_play", "Игра под давлением"
    FIGHTING_SPIRIT = "fighting_spirit", "Боевой дух"

    # Поведение
    COMMUNICATION = "communication", "Коммуникация"
    PUNCTUALITY = "punctuality", "Пунктуальность"
    FAIRNESS = "fairness", "Честная игра"

    @classmethod
    def all_metric_names(cls) -> list[str]:
        return [choice[0] for choice in cls.choices]

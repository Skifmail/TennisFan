"""Версионируемые проекты документов детского раздела."""

import hashlib
from pathlib import Path

from django.conf import settings

TITLES = {
    "family": "Правила семейного кабинета детской лиги",
    "privacy": "Обработка данных детей",
    "processing": "Согласие на обработку данных ребёнка",
    "publication": "Согласие на публикацию имени и спортивных результатов ребёнка",
    "payments": "Условия подачи заявок, оплаты взносов и возвратов",
}


def document_text(slug: str) -> str:
    if slug not in TITLES:
        raise KeyError(slug)
    path = Path(settings.BASE_DIR) / "static" / "documents" / "juniors" / f"{slug}.txt"
    return path.read_text(encoding="utf-8")


def release_version() -> str:
    contents = document_text("processing") + "\n" + document_text("publication")
    return "draft-" + hashlib.sha256(contents.encode()).hexdigest()[:12]


REPRESENTATIVE_CONFIRMATION = "Мне исполнилось 18 лет. Я родитель или законный представитель этого ребёнка и вправе действовать от его имени."
ACCURACY_CONFIRMATION = "Я указал достоверные сведения о ребёнке и обязуюсь исправить их, если они изменятся."
FAMILY_RESPONSIBILITY = "Я знакомлюсь с условиями выбранного турнира и выполняю относящиеся ко мне обязанности по участию ребёнка. Это подтверждение не освобождает платформу и организатора от их обязанностей."

"""
Удалить все привязки Telegram (UserTelegramLink) для бота поддержки.

Используйте после смены бота или чтобы убрать пробные/неудачные привязки.
Пользователям снова будет показана ссылка привязки при следующей отправке формы.

Запуск: python manage.py clear_telegram_support_bindings
Опционально: --dry-run — только показать количество, не удалять.
"""

from django.core.management.base import BaseCommand

from apps.core.models import UserTelegramLink


class Command(BaseCommand):
    help = "Удалить все привязки Telegram (UserTelegramLink). После смены бота или для очистки пробных привязок."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, сколько записей будет удалено.",
        )

    def handle(self, *args, **options):
        qs = UserTelegramLink.objects.all()
        count = qs.count()

        if count == 0:
            self.stdout.write("Привязок Telegram нет.")
            return

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING(f"Будет удалено привязок: {count}. Запустите без --dry-run для удаления."))
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(f"Удалено привязок: {count}. Пользователи смогут привязать аккаунт заново при следующей отправке формы."))

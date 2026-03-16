"""
Management-команда для напоминаний о турнирах клуба (за 24 часа до начала).
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.clubs.models import ClubMember, ClubMemberStatus
from apps.clubs.notifications import send_tournament_reminder
from apps.tournaments.models import Tournament

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Отправка напоминаний о клубных турнирах участникам клуба."""

    help = "Отправляет напоминания участникам клуба о турнирах (за 24 часа до начала)."

    def handle(self, *args, **options):
        """Основная логика команды."""
        now = timezone.now()
        tomorrow_start = (now + timedelta(hours=23)).date()
        tomorrow_end = (now + timedelta(hours=25)).date()

        club_tournaments = Tournament.objects.filter(
            club__isnull=False,
            start_date__gte=tomorrow_start,
            start_date__lte=tomorrow_end,
            status="upcoming",
        ).select_related("club")

        sent = 0
        for tournament in club_tournaments:
            club = tournament.club
            members = ClubMember.objects.filter(
                club=club, status=ClubMemberStatus.ACTIVE
            ).select_related("user")

            for member in members:
                send_tournament_reminder(
                    club=club,
                    member=member,
                    tournament_name=tournament.name,
                    start_info="через 24 часа",
                    city=tournament.city or club.city,
                )
                sent += 1

        self.stdout.write(
            self.style.SUCCESS(f"Напоминания о турнирах отправлены: {sent}.")
        )
        logger.info("send_club_tournament_reminders: sent=%d", sent)

"""
Автоматически подтвердить заявки на результат матча, ожидающие подтверждения более 6 часов.

Если один игрок отправил результат на подтверждение второму, у второго есть 6 часов на ответ.
По истечении 6 часов заявка считается подтверждённой системой (результат применяется к матчу).

Запуск: python manage.py auto_accept_stale_proposals

Рекомендуется добавить в cron (например, каждые 15–30 минут):
  */15 * * * * cd /path/to/project && venv/bin/python manage.py auto_accept_stale_proposals
"""

import logging
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils import timezone

from apps.users.models import Notification
from apps.tournaments.models import Match, MatchResultProposal
from apps.tournaments.proposal_service import apply_proposal

logger = logging.getLogger(__name__)

PROPOSAL_AUTO_ACCEPT_HOURS = 6


class Command(BaseCommand):
    help = (
        "Найти заявки на результат матча (status=PENDING) старше 6 часов и применить их, "
        "как если бы соперник подтвердил результат."
    )

    def handle(self, *args, **options):
        now = timezone.now()
        deadline = now - timedelta(hours=PROPOSAL_AUTO_ACCEPT_HOURS)
        proposals = list(
            MatchResultProposal.objects.filter(
                status=Match.ProposalStatus.PENDING,
                created_at__lte=deadline,
            )
            .exclude(
                match__status__in=(Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER),
            )
            .select_related(
                "match",
                "match__tournament",
                "match__player1",
                "match__player2",
                "match__team1",
                "match__team2",
                "match__team1__player1",
                "match__team1__player2",
                "match__team2__player1",
                "match__team2__player2",
                "proposer",
            )
        )
        if not proposals:
            self.stdout.write("Нет заявок на авто-подтверждение (старше 6 ч).")
            return
        total = 0
        for proposal in proposals:
            try:
                match = proposal.match
                apply_proposal(proposal)
                url = reverse("match_detail", args=[match.pk])
                msg = (
                    "Результат матча автоматически подтверждён по истечении 6 часов "
                    "без ответа с вашей стороны."
                )
                if match.team1_id and match.team2_id:
                    proposer_team = (
                        match.team1
                        if proposal.proposer in (match.team1.player1, match.team1.player2)
                        else match.team2
                    )
                    opponent_team = match.team2 if proposer_team == match.team1 else match.team1
                    for p in (opponent_team.player1, opponent_team.player2):
                        if p and not getattr(p, "is_bye", False):
                            Notification.objects.create(user=p.user, message=msg, url=url)
                else:
                    opponent = (
                        match.player2 if proposal.proposer == match.player1 else match.player1
                    )
                    if opponent and not getattr(opponent, "is_bye", False):
                        Notification.objects.create(user=opponent.user, message=msg, url=url)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"Заявка {proposal.pk} (матч {proposal.match_id}) автоматически подтверждена."
                    )
                )
                total += 1
            except Exception as e:
                logger.exception("Auto-accept proposal %s failed: %s", proposal.pk, e)
                self.stdout.write(
                    self.style.ERROR(f"Заявка {proposal.pk}: ошибка — {e}")
                )
        self.stdout.write(f"Подтверждено заявок: {total} из {len(proposals)}.")

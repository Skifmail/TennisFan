"""Management-команда наполнения ленты активности историческими событиями.

Команда проходит по существующим объектам платформы (игроки, оплаты, заявки на
результат, регистрации на турниры, спарринги) и создаёт для них записи в журнале
:class:`apps.core.models.PlatformActivityEvent`. Благодаря стабильным ключам
дедупликации команда идемпотентна — повторный запуск не создаёт дубликатов.
"""

from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import QuerySet
from django.urls import NoReverseMatch, reverse

from apps.core.activity import log_activity, normalize_event_type, player_user
from apps.core.models import PlatformActivityEvent

EventType = PlatformActivityEvent.EventType

_PAYMENT_TYPE_TO_EVENT: dict[str, str] = {
    "subscription": normalize_event_type(EventType.PAYMENT_SUBSCRIPTION),
    "club_plan": normalize_event_type(EventType.PAYMENT_CLUB_PLAN),
    "club_fee": normalize_event_type(EventType.PAYMENT_CLUB_FEE),
    "tournament": normalize_event_type(EventType.PAYMENT_TOURNAMENT),
    "donation": normalize_event_type(EventType.PAYMENT_DONATION),
}


def _safe_reverse(name: str, **kwargs: Any) -> str:
    """Построить URL по имени маршрута, подавляя ошибки разрешения."""
    try:
        return str(reverse(name, kwargs=kwargs) if kwargs else reverse(name))
    except NoReverseMatch:
        return ""


class Command(BaseCommand):
    """Идемпотентно восстановить ленту активности из существующих данных."""

    help = "Восстанавливает ленту активности платформы из исторических данных."

    def handle(self, *args: Any, **options: Any) -> None:
        """Выполнить бэкфилл по всем источникам событий.

        Args:
            *args: Позиционные аргументы команды (не используются).
            **options: Опции команды (не используются).

        Returns:
            None: Прогресс выводится в stdout.
        """
        created_before = PlatformActivityEvent.objects.count()
        self._backfill_registrations()
        self._backfill_payments()
        self._backfill_match_results()
        self._backfill_tournament_registrations()
        self._backfill_sparring()
        self._backfill_clubs()
        self._backfill_coaches_and_trainings()
        self._backfill_comments()
        self._backfill_photos()
        self._backfill_fancoin()
        self._backfill_subscription_cancellations()
        created_after = PlatformActivityEvent.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Событий в журнале: {created_after} "
                f"(+{created_after - created_before})."
            )
        )

    def _backfill_registrations(self) -> None:
        """Создать события регистрации по профилям игроков."""
        from apps.users.models import Player

        players: QuerySet = Player.objects.filter(is_bye=False).select_related("user")
        count = 0
        for player in players.iterator():
            user = player_user(player)
            if user is None:
                continue
            log_activity(
                event_type=EventType.REGISTRATION,
                actor=user,
                description="Зарегистрировался на платформе",
                target_url=_safe_reverse("admin:users_user_change", object_id=user.pk),
                dedupe_key=f"player:{player.pk}",
                created_at=getattr(player, "created_at", None),
            )
            count += 1
        self.stdout.write(f"  Регистрации: обработано {count}")

    def _backfill_payments(self) -> None:
        """Создать события оплат по успешным записям PaymentRecord."""
        from apps.payments.models import PaymentRecord

        payments: QuerySet = PaymentRecord.objects.filter(
            status="succeeded"
        ).select_related("user")
        count = 0
        for payment in payments.iterator():
            event_type = _PAYMENT_TYPE_TO_EVENT.get(payment.payment_type)
            if event_type is None:
                continue
            log_activity(
                event_type=event_type,
                actor=payment.user,
                description=payment.item_label or payment.get_payment_type_display(),
                amount=payment.amount,
                currency=payment.currency,
                target_url=_safe_reverse(
                    "admin:payments_paymentrecord_change", object_id=payment.pk
                ),
                metadata={"payment_type": payment.payment_type},
                dedupe_key=f"payment:{payment.pk}",
                created_at=payment.paid_at,
            )
            count += 1
        self.stdout.write(f"  Оплаты: обработано {count}")

    def _backfill_match_results(self) -> None:
        """Создать события внесения и подтверждения результатов матчей."""
        from apps.tournaments.models import Match, MatchResultProposal
        from apps.tournaments.utils import get_match_participants

        proposals: QuerySet = MatchResultProposal.objects.select_related(
            "match", "proposer__user"
        )
        made = 0
        confirmed = 0
        for proposal in proposals.iterator():
            match = proposal.match
            match_url = _safe_reverse("match_detail", pk=match.pk) if match else ""
            log_activity(
                event_type=EventType.MATCH_RESULT_PROPOSED,
                actor=player_user(proposal.proposer),
                description=(
                    f"Внёс результат матча: {match}"
                    if match
                    else "Внёс результат матча"
                ),
                target_url=match_url,
                dedupe_key=f"proposal_made:{proposal.pk}",
                created_at=getattr(proposal, "created_at", None),
            )
            made += 1
            if proposal.status == Match.ProposalStatus.ACCEPTED:
                confirmer = None
                try:
                    proposer_user_id = getattr(proposal.proposer, "user_id", None)
                    others = [
                        p.user
                        for p in get_match_participants(match)
                        if p
                        and not getattr(p, "is_bye", False)
                        and getattr(p, "user_id", None)
                        and p.user_id != proposer_user_id
                    ]
                    if len(others) == 1:
                        confirmer = others[0]
                except Exception:  # noqa: BLE001
                    confirmer = None
                log_activity(
                    event_type=EventType.MATCH_RESULT_CONFIRMED,
                    actor=confirmer,
                    description=(
                        f"Подтвердил результат матча: {match}"
                        if match
                        else "Подтвердил результат матча"
                    ),
                    target_url=match_url,
                    dedupe_key=f"proposal_confirmed:{proposal.pk}",
                    created_at=getattr(proposal, "created_at", None),
                )
                confirmed += 1
        self.stdout.write(
            f"  Результаты матчей: внесено {made}, подтверждено {confirmed}"
        )

    def _backfill_tournament_registrations(self) -> None:
        """Создать события регистраций на турниры (одиночные и парные)."""
        from apps.tournaments.models import Tournament, TournamentTeam

        singles = 0
        for tournament in Tournament.objects.prefetch_related(
            "participants__user"
        ).iterator(chunk_size=500):
            tournament_url = _safe_reverse("tournament_detail", slug=tournament.slug)
            for player in tournament.participants.all():
                user = player_user(player)
                if user is None:
                    continue
                log_activity(
                    event_type=EventType.TOURNAMENT_REGISTERED,
                    actor=user,
                    description=f"Регистрация на турнир «{tournament.name}»",
                    target_url=tournament_url,
                    metadata={"tournament_id": tournament.pk},
                    dedupe_key=f"tournament_reg:{tournament.pk}:{player.pk}",
                    created_at=getattr(tournament, "created_at", None),
                )
                singles += 1

        doubles = 0
        teams: QuerySet = TournamentTeam.objects.select_related(
            "tournament", "player1__user", "player2__user"
        )
        for team in teams.iterator():
            captain = player_user(team.player1)
            if captain is None:
                continue
            tournament = team.tournament
            log_activity(
                event_type=EventType.TOURNAMENT_REGISTERED,
                actor=captain,
                description=(
                    f"Регистрация пары на турнир «{tournament.name}» "
                    f"({team.get_display_name()})"
                ),
                target_url=_safe_reverse("tournament_detail", slug=tournament.slug),
                metadata={"tournament_id": tournament.pk, "team_id": team.pk},
                dedupe_key=f"tournament_team:{team.pk}",
                created_at=getattr(team, "created_at", None),
            )
            doubles += 1
        self.stdout.write(
            f"  Регистрации на турниры: одиночные {singles}, парные {doubles}"
        )

    def _backfill_sparring(self) -> None:
        """Создать события по спаррингам (1×1 и 2×2)."""
        from apps.sparring.models import (
            DoublesJoinRequest,
            DoublesJoinRequestStatus,
            DoublesMatchRequest,
            SparringInvitation,
            SparringRequest,
            SparringResponse,
        )

        sparring_url = _safe_reverse("sparring_list")
        counters = {
            "requests": 0,
            "responses": 0,
            "approvals": 0,
            "invitations": 0,
            "doubles": 0,
            "joins": 0,
        }

        for req in SparringRequest.objects.select_related("player__user").iterator():
            user = player_user(req.player)
            if user is None:
                continue
            log_activity(
                event_type=EventType.SPARRING_CREATED,
                actor=user,
                description=f"Создал заявку на спарринг ({req.city})",
                target_url=sparring_url,
                dedupe_key=f"sparring_request:{req.pk}",
                created_at=getattr(req, "created_at", None),
            )
            counters["requests"] += 1

        for resp in SparringResponse.objects.select_related(
            "respondent__user", "sparring_request__player__user"
        ).iterator():
            user = player_user(resp.respondent)
            owner_name = ""
            try:
                owner_name = resp.sparring_request.player.get_display_name()
            except Exception:  # noqa: BLE001
                owner_name = ""
            if user is not None:
                log_activity(
                    event_type=EventType.SPARRING_APPLIED,
                    actor=user,
                    description=(
                        f"Откликнулся на спарринг игрока {owner_name}".strip()
                        if owner_name
                        else "Откликнулся на спарринг"
                    ),
                    target_url=sparring_url,
                    dedupe_key=f"sparring_response:{resp.pk}",
                    created_at=getattr(resp, "created_at", None),
                )
                counters["responses"] += 1
            if resp.status == SparringResponse.ResponseStatus.ACCEPTED:
                owner_user = player_user(getattr(resp.sparring_request, "player", None))
                respondent_name = ""
                try:
                    respondent_name = resp.respondent.get_display_name()
                except Exception:  # noqa: BLE001
                    respondent_name = ""
                log_activity(
                    event_type=EventType.SPARRING_APPROVED,
                    actor=owner_user,
                    description=(
                        f"Одобрил отклик игрока {respondent_name} на свой спарринг".strip()
                        if respondent_name
                        else "Одобрил отклик на свой спарринг"
                    ),
                    target_url=sparring_url,
                    dedupe_key=f"sparring_response_approved:{resp.pk}",
                    created_at=getattr(resp, "updated_at", None),
                )
                counters["approvals"] += 1

        for inv in SparringInvitation.objects.select_related(
            "inviter__user", "invitee__user"
        ).iterator():
            user = player_user(inv.inviter)
            if user is None:
                continue
            invitee_name = ""
            try:
                invitee_name = inv.invitee.get_display_name()
            except Exception:  # noqa: BLE001
                invitee_name = ""
            log_activity(
                event_type=EventType.SPARRING_INVITED,
                actor=user,
                description=(
                    f"Пригласил игрока {invitee_name} на спарринг".strip()
                    if invitee_name
                    else "Пригласил игрока на спарринг"
                ),
                target_url=sparring_url,
                dedupe_key=f"sparring_invitation:{inv.pk}",
                created_at=getattr(inv, "created_at", None),
            )
            counters["invitations"] += 1

        for dreq in DoublesMatchRequest.objects.select_related(
            "created_by__user"
        ).iterator():
            user = player_user(dreq.created_by)
            if user is None:
                continue
            log_activity(
                event_type=EventType.DOUBLES_CREATED,
                actor=user,
                description=f"Создал парный спарринг ({dreq.get_kind_display()})",
                target_url=sparring_url,
                dedupe_key=f"doubles_request:{dreq.pk}",
                created_at=getattr(dreq, "created_at", None),
            )
            counters["doubles"] += 1

        for join in DoublesJoinRequest.objects.select_related(
            "created_by__user", "match_request__created_by__user"
        ).iterator():
            user = player_user(join.created_by)
            if user is not None:
                log_activity(
                    event_type=EventType.DOUBLES_JOIN_REQUESTED,
                    actor=user,
                    description="Подал заявку в парный спарринг",
                    target_url=sparring_url,
                    dedupe_key=f"doubles_join:{join.pk}",
                    created_at=getattr(join, "created_at", None),
                )
                counters["joins"] += 1
            if join.status == DoublesJoinRequestStatus.ACCEPTED:
                owner_user = None
                applicant_name = ""
                try:
                    owner_user = player_user(join.match_request.created_by)
                    applicant_name = join.created_by.get_display_name()
                except Exception:  # noqa: BLE001
                    pass
                log_activity(
                    event_type=EventType.DOUBLES_JOIN_APPROVED,
                    actor=owner_user,
                    description=(
                        f"Одобрил заявку игрока {applicant_name} в парный спарринг".strip()
                        if applicant_name
                        else "Одобрил заявку в парный спарринг"
                    ),
                    target_url=sparring_url,
                    dedupe_key=f"doubles_join_approved:{join.pk}",
                    created_at=getattr(join, "processed_at", None)
                    or getattr(join, "updated_at", None),
                )

        self.stdout.write(
            "  Спарринги: "
            f"заявок {counters['requests']}, откликов {counters['responses']}, "
            f"одобрений {counters['approvals']}, приглашений {counters['invitations']}, "
            f"парных {counters['doubles']}, присоединений {counters['joins']}"
        )

    def _backfill_clubs(self) -> None:
        """Создать события по заявкам в клуб и вступлениям."""
        from apps.clubs.models import (
            ClubJoinRequest,
            ClubJoinRequestStatus,
            ClubMember,
            ClubMemberStatus,
        )

        requested = 0
        rejected = 0
        for req in ClubJoinRequest.objects.select_related("club", "user").iterator():
            club = req.club
            club_url = _safe_reverse("club_public_detail", slug=club.slug)
            log_activity(
                event_type=EventType.CLUB_JOIN_REQUESTED,
                actor=req.user,
                description=f"Подал заявку в клуб «{club.name}»",
                target_url=club_url,
                metadata={"club_id": club.pk},
                dedupe_key=f"club_join_req:{req.pk}",
                created_at=getattr(req, "created_at", None),
            )
            requested += 1
            if req.status == ClubJoinRequestStatus.REJECTED:
                log_activity(
                    event_type=EventType.CLUB_JOIN_REJECTED,
                    actor=req.user,
                    description=f"Заявка в клуб «{club.name}» отклонена",
                    target_url=club_url,
                    metadata={"club_id": club.pk},
                    dedupe_key=f"club_join_rejected:{req.pk}",
                    created_at=getattr(req, "reviewed_at", None)
                    or getattr(req, "updated_at", None),
                )
                rejected += 1

        joined = 0
        for member in (
            ClubMember.objects.filter(status=ClubMemberStatus.ACTIVE)
            .select_related("club", "user")
            .iterator()
        ):
            club = member.club
            log_activity(
                event_type=EventType.CLUB_JOINED,
                actor=member.user,
                description=f"Вступил в клуб «{club.name}»",
                target_url=_safe_reverse("club_public_detail", slug=club.slug),
                metadata={"club_id": club.pk, "role": member.role},
                dedupe_key=f"club_member:{member.pk}",
                created_at=getattr(member, "joined_at", None)
                or getattr(member, "created_at", None),
            )
            joined += 1
        self.stdout.write(
            f"  Клубы: заявок {requested}, отклонено {rejected}, вступлений {joined}"
        )

    def _backfill_coaches_and_trainings(self) -> None:
        """Создать события по заявкам на тренера, тренировкам и записям."""
        from apps.training.models import (
            CoachApplication,
            Training,
            TrainingEnrollment,
        )

        applications = 0
        for app in CoachApplication.objects.select_related("applicant_user").iterator():
            log_activity(
                event_type=EventType.COACH_APPLICATION,
                actor=app.applicant_user,
                actor_name=(
                    "" if app.applicant_user_id else (app.applicant_name or "")
                ),
                description=f"Подал заявку на тренера ({app.city})",
                target_url=_safe_reverse("training_list"),
                dedupe_key=f"coach_application:{app.pk}",
                created_at=getattr(app, "created_at", None),
            )
            applications += 1

        trainings = 0
        for training in Training.objects.select_related("coach__user").iterator():
            coach = training.coach
            coach_user = getattr(coach, "user", None) if coach else None
            log_activity(
                event_type=EventType.TRAINING_PUBLISHED,
                actor=coach_user,
                actor_name="" if coach_user else (getattr(coach, "name", "") or ""),
                description=f"Опубликовал тренировку «{training.title}»",
                target_url=_safe_reverse("training_detail", slug=training.slug),
                dedupe_key=f"training:{training.pk}",
                created_at=getattr(training, "created_at", None),
            )
            trainings += 1

        enrollments = 0
        for enroll in TrainingEnrollment.objects.select_related(
            "player__user", "training"
        ).iterator():
            user = player_user(enroll.player)
            title = getattr(enroll.training, "title", "")
            log_activity(
                event_type=EventType.TRAINING_ENROLLED,
                actor=user,
                actor_name="" if user else (enroll.full_name or ""),
                description=(
                    f"Записался на тренировку «{title}»"
                    if title
                    else "Записался на тренировку"
                ),
                target_url=_safe_reverse("training_list"),
                dedupe_key=f"training_enrollment:{enroll.pk}",
                created_at=getattr(enroll, "created_at", None),
            )
            enrollments += 1
        self.stdout.write(
            f"  Тренеры/тренировки: заявок {applications}, тренировок {trainings}, "
            f"записей {enrollments}"
        )

    def _backfill_comments(self) -> None:
        """Создать события по комментариям."""
        from apps.comments.models import Comment

        labels = {
            "court": "корту",
            "player": "игроку",
            "news": "новости",
            "match": "матчу",
            "coach": "тренеру",
            "training": "тренировке",
            "tournament": "турниру",
        }
        count = 0
        for comment in Comment.objects.select_related(
            "author__user", "content_type"
        ).iterator():
            user = player_user(comment.author)
            try:
                model_name = comment.content_type.model
            except Exception:  # noqa: BLE001
                model_name = ""
            target_label = labels.get(model_name, "объекту")
            log_activity(
                event_type=EventType.COMMENT_ADDED,
                actor=user,
                description=f"Оставил комментарий к {target_label}",
                metadata={"target_model": model_name, "object_id": comment.object_id},
                dedupe_key=f"comment:{comment.pk}",
                created_at=getattr(comment, "created_at", None),
            )
            count += 1
        self.stdout.write(f"  Комментарии: обработано {count}")

    def _backfill_photos(self) -> None:
        """Создать события по фотографиям, загруженным в турниры."""
        from apps.tournaments.models import TournamentPhoto

        count = 0
        photos: QuerySet = TournamentPhoto.objects.select_related(
            "tournament", "uploaded_by__user"
        )
        for photo in photos.iterator():
            tournament = photo.tournament
            if tournament is None:
                continue
            gallery_url = _safe_reverse(
                "tournament_gallery_detail", slug=tournament.slug
            ) or _safe_reverse("tournament_detail", slug=tournament.slug)
            log_activity(
                event_type=EventType.PHOTO_ADDED,
                actor=player_user(photo.uploaded_by),
                description=f"Добавил фото в турнир «{tournament.name}»",
                target_url=gallery_url,
                metadata={"tournament_id": tournament.pk, "photo_id": photo.pk},
                dedupe_key=f"tournament_photo:{photo.pk}",
                created_at=getattr(photo, "created_at", None),
            )
            count += 1
        self.stdout.write(f"  Фото турниров: обработано {count}")

    def _backfill_fancoin(self) -> None:
        """Создать события по списаниям и возвратам FAN-коинов."""
        from apps.subscriptions.models import FancoinTransaction

        count = 0
        for tx in FancoinTransaction.objects.select_related("user").iterator():
            is_charge = tx.direction == FancoinTransaction.Direction.CHARGE
            event_type = (
                EventType.FANCOIN_CHARGE if is_charge else EventType.FANCOIN_REFUND
            )
            verb = "Списано" if is_charge else "Возвращено"
            log_activity(
                event_type=event_type,
                actor=tx.user,
                description=f"{verb}: {tx.get_reason_display()}",
                amount=tx.amount,
                currency="FT",
                metadata={"reason": tx.reason, "direction": tx.direction},
                dedupe_key=f"fancoin:{tx.pk}",
                created_at=getattr(tx, "created_at", None),
            )
            count += 1
        self.stdout.write(f"  FAN-коины: обработано {count}")

    def _backfill_subscription_cancellations(self) -> None:
        """Создать события по отменённым пользовательским подпискам."""
        from apps.subscriptions.models import UserSubscription

        count = 0
        for sub in (
            UserSubscription.objects.filter(cancelled_at__isnull=False)
            .select_related("user")
            .iterator()
        ):
            stamp = int(sub.cancelled_at.timestamp())
            log_activity(
                event_type=EventType.SUBSCRIPTION_CANCELLED,
                actor=sub.user,
                description="Отменил подписку (действует до конца оплаченного периода)",
                target_url=_safe_reverse(
                    "admin:subscriptions_usersubscription_change", object_id=sub.pk
                ),
                dedupe_key=f"subscription_cancelled:{sub.pk}:{stamp}",
                created_at=sub.cancelled_at,
            )
            count += 1
        self.stdout.write(f"  Отмены подписок: обработано {count}")

"""Сигналы, наполняющие ленту активности платформы.

Модуль подписывается на ключевые модели и записывает события в журнал
:class:`apps.core.models.PlatformActivityEvent` через :func:`apps.core.activity.log_activity`.
Каждое событие снабжается стабильным ``dedupe_key``, поэтому повторные сохранения
моделей не создают дубликатов, а сигналы можно безопасно вызывать многократно.

Регистрация выполняется из :meth:`apps.core.apps.CoreConfig.ready`.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models.signals import m2m_changed, post_save, pre_save
from django.urls import NoReverseMatch, reverse

from apps.core.activity import log_activity, normalize_event_type, player_user
from apps.core.models import PlatformActivityEvent

logger = logging.getLogger(__name__)

EventType = PlatformActivityEvent.EventType

#: Соответствие типа оплаты PaymentRecord типу события активности.
_PAYMENT_TYPE_TO_EVENT: dict[str, str] = {
    "subscription": normalize_event_type(EventType.PAYMENT_SUBSCRIPTION),
    "club_plan": normalize_event_type(EventType.PAYMENT_CLUB_PLAN),
    "club_fee": normalize_event_type(EventType.PAYMENT_CLUB_FEE),
    "tournament": normalize_event_type(EventType.PAYMENT_TOURNAMENT),
    "donation": normalize_event_type(EventType.PAYMENT_DONATION),
}


def _safe_reverse(name: str, **kwargs: Any) -> str:
    """Построить URL по имени маршрута, не пробрасывая ошибки.

    Args:
        name: Имя URL-маршрута.
        **kwargs: Аргументы маршрута.

    Returns:
        str: Готовый URL или пустая строка, если маршрут не разрешился.
    """
    try:
        return str(reverse(name, kwargs=kwargs) if kwargs else reverse(name))
    except NoReverseMatch:
        return ""


def register() -> None:
    """Подключить обработчики сигналов ленты активности.

    Импорт моделей и подключение приёмников выполняются здесь, чтобы избежать
    обращения к ещё не загруженным приложениям на этапе импорта модуля.

    Returns:
        None: Регистрирует сигналы как побочный эффект.
    """
    from apps.clubs.models import (
        ClubJoinRequest,
        ClubJoinRequestStatus,
        ClubMember,
        ClubMemberStatus,
    )
    from apps.comments.models import Comment
    from apps.payments.models import PaymentRecord
    from apps.sparring.models import (
        DoublesJoinRequest,
        DoublesJoinRequestStatus,
        DoublesMatchRequest,
        SparringInvitation,
        SparringRequest,
        SparringResponse,
    )
    from apps.subscriptions.models import FancoinTransaction, UserSubscription
    from apps.tournaments.models import (
        Match,
        MatchResultProposal,
        Tournament,
        TournamentTeam,
    )
    from apps.training.models import CoachApplication, Training, TrainingEnrollment
    from apps.users.models import Player

    # ------------------------------------------------------------------
    # Регистрация пользователя (создание профиля игрока)
    # ------------------------------------------------------------------
    def on_player_created(sender, instance, created, **kwargs):
        if not created or getattr(instance, "is_bye", False):
            return
        user = player_user(instance)
        if user is None:
            return
        log_activity(
            event_type=EventType.REGISTRATION,
            actor=user,
            description="Зарегистрировался на платформе",
            target_url=_safe_reverse("admin:users_user_change", object_id=user.pk),
            dedupe_key=f"player:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_player_created,
        sender=Player,
        dispatch_uid="activity_player_created",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Оплаты: подписки, тарифы, взносы, турниры, донаты
    # ------------------------------------------------------------------
    def on_payment_saved(sender, instance, created, **kwargs):
        if instance.status != "succeeded":
            return
        event_type = _PAYMENT_TYPE_TO_EVENT.get(instance.payment_type)
        if event_type is None:
            return
        type_label = instance.get_payment_type_display()
        description = instance.item_label or type_label
        log_activity(
            event_type=event_type,
            actor=instance.user,
            description=description,
            amount=instance.amount,
            currency=instance.currency,
            target_url=_safe_reverse(
                "admin:payments_paymentrecord_change", object_id=instance.pk
            ),
            metadata={"payment_type": instance.payment_type},
            dedupe_key=f"payment:{instance.pk}",
            created_at=getattr(instance, "paid_at", None),
        )

    post_save.connect(
        on_payment_saved,
        sender=PaymentRecord,
        dispatch_uid="activity_payment_saved",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Результаты матчей: внесение и подтверждение
    # ------------------------------------------------------------------
    def _infer_confirmer_user(match, proposer):
        """Определить подтвердившего соперника для одиночного матча."""
        try:
            from apps.tournaments.utils import get_match_participants

            proposer_user_id = getattr(proposer, "user_id", None)
            others = [
                p.user
                for p in get_match_participants(match)
                if p
                and not getattr(p, "is_bye", False)
                and getattr(p, "user_id", None)
                and p.user_id != proposer_user_id
            ]
            if len(others) == 1:
                return others[0]
        except Exception as exc:  # noqa: BLE001
            logger.debug("Не удалось определить подтвердившего матч: %s", exc)
        return None

    def on_proposal_saved(sender, instance, created, **kwargs):
        from apps.tournaments.proposal_service import format_proposal_score

        match = instance.match
        match_url = _safe_reverse("match_detail", pk=match.pk) if match else ""
        if created:
            score_text = format_proposal_score(instance)
            result_text = instance.get_result_display()
            log_activity(
                event_type=EventType.MATCH_RESULT_PROPOSED,
                actor=player_user(instance.proposer),
                description=(
                    f"Внёс результат матча: {match}. "
                    f"Выбор: {result_text}. Счёт: {score_text}"
                    if match
                    else "Внёс результат матча"
                ),
                target_url=match_url,
                dedupe_key=f"proposal_made:{instance.pk}",
                created_at=getattr(instance, "created_at", None),
            )
        if (
            instance.status == Match.ProposalStatus.ACCEPTED
            and getattr(instance, "_confirmed_by_user", None) is not None
        ):
            confirmer = instance._confirmed_by_user
            log_activity(
                event_type=EventType.MATCH_RESULT_CONFIRMED,
                actor=confirmer,
                description=(
                    f"Подтвердил результат матча: {match}"
                    if match
                    else "Подтвердил результат матча"
                ),
                target_url=match_url,
                dedupe_key=f"proposal_confirmed:{instance.pk}",
            )

    post_save.connect(
        on_proposal_saved,
        sender=MatchResultProposal,
        dispatch_uid="activity_proposal_saved",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Регистрация на турнир (одиночный — через M2M participants)
    # ------------------------------------------------------------------
    def on_participants_changed(sender, instance, action, pk_set, **kwargs):
        if action != "post_add" or not pk_set:
            return
        if not isinstance(instance, Tournament):
            return
        tournament = instance
        tournament_url = _safe_reverse("tournament_detail", slug=tournament.slug)
        for player in Player.objects.filter(pk__in=pk_set).select_related("user"):
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
            )

    m2m_changed.connect(
        on_participants_changed,
        sender=Tournament.participants.through,
        dispatch_uid="activity_tournament_participants",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Регистрация на турнир (парный — создание команды)
    # ------------------------------------------------------------------
    def on_team_created(sender, instance, created, **kwargs):
        if not created:
            return
        captain = player_user(instance.player1)
        if captain is None:
            return
        tournament = instance.tournament
        tournament_url = _safe_reverse("tournament_detail", slug=tournament.slug)
        team_label = instance.get_display_name()
        log_activity(
            event_type=EventType.TOURNAMENT_REGISTERED,
            actor=captain,
            description=f"Регистрация пары на турнир «{tournament.name}» ({team_label})",
            target_url=tournament_url,
            metadata={"tournament_id": tournament.pk, "team_id": instance.pk},
            dedupe_key=f"tournament_team:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_team_created,
        sender=TournamentTeam,
        dispatch_uid="activity_team_created",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Спарринг 1×1: создание заявки, отклик, одобрение, приглашение
    # ------------------------------------------------------------------
    def on_sparring_request_created(sender, instance, created, **kwargs):
        if not created:
            return
        user = player_user(instance.player)
        if user is None:
            return
        log_activity(
            event_type=EventType.SPARRING_CREATED,
            actor=user,
            description=f"Создал заявку на спарринг ({instance.city})",
            target_url=_safe_reverse("sparring_list"),
            dedupe_key=f"sparring_request:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_sparring_request_created,
        sender=SparringRequest,
        dispatch_uid="activity_sparring_request",
        weak=False,
    )

    def on_sparring_response_saved(sender, instance, created, **kwargs):
        request = instance.sparring_request
        owner_name = ""
        try:
            owner_name = instance.sparring_request.player.get_display_name()
        except Exception:  # noqa: BLE001
            owner_name = ""
        if created:
            user = player_user(instance.respondent)
            if user is not None:
                log_activity(
                    event_type=EventType.SPARRING_APPLIED,
                    actor=user,
                    description=(
                        f"Откликнулся на спарринг игрока {owner_name}".strip()
                        if owner_name
                        else "Откликнулся на спарринг"
                    ),
                    target_url=_safe_reverse("sparring_list"),
                    dedupe_key=f"sparring_response:{instance.pk}",
                    created_at=getattr(instance, "created_at", None),
                )
        if instance.status == SparringResponse.ResponseStatus.ACCEPTED:
            owner_user = player_user(getattr(request, "player", None))
            respondent_name = ""
            try:
                respondent_name = instance.respondent.get_display_name()
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
                target_url=_safe_reverse("sparring_list"),
                dedupe_key=f"sparring_response_approved:{instance.pk}",
            )
        if instance.status == SparringResponse.ResponseStatus.REJECTED:
            owner_user = player_user(getattr(request, "player", None))
            respondent_name = ""
            try:
                respondent_name = instance.respondent.get_display_name()
            except Exception:  # noqa: BLE001
                respondent_name = ""
            log_activity(
                event_type=EventType.SPARRING_REJECTED,
                actor=owner_user,
                description=(
                    f"Отклонил отклик игрока {respondent_name} на свой спарринг".strip()
                    if respondent_name
                    else "Отклонил отклик на свой спарринг"
                ),
                target_url=_safe_reverse("sparring_list"),
                dedupe_key=f"sparring_response_rejected:{instance.pk}",
            )

    post_save.connect(
        on_sparring_response_saved,
        sender=SparringResponse,
        dispatch_uid="activity_sparring_response",
        weak=False,
    )

    def on_sparring_invitation_created(sender, instance, created, **kwargs):
        if not created:
            return
        user = player_user(instance.inviter)
        if user is None:
            return
        invitee_name = ""
        try:
            invitee_name = instance.invitee.get_display_name()
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
            target_url=_safe_reverse("sparring_list"),
            dedupe_key=f"sparring_invitation:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_sparring_invitation_created,
        sender=SparringInvitation,
        dispatch_uid="activity_sparring_invitation",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Парный/командный спарринг 2×2: создание заявки и присоединение
    # ------------------------------------------------------------------
    def on_doubles_request_created(sender, instance, created, **kwargs):
        if not created:
            return
        user = player_user(instance.created_by)
        if user is None:
            return
        log_activity(
            event_type=EventType.DOUBLES_CREATED,
            actor=user,
            description=f"Создал парный спарринг ({instance.get_kind_display()})",
            target_url=_safe_reverse("sparring_list"),
            dedupe_key=f"doubles_request:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_doubles_request_created,
        sender=DoublesMatchRequest,
        dispatch_uid="activity_doubles_request",
        weak=False,
    )

    def on_doubles_join_saved(sender, instance, created, **kwargs):
        if created:
            user = player_user(instance.created_by)
            if user is not None:
                log_activity(
                    event_type=EventType.DOUBLES_JOIN_REQUESTED,
                    actor=user,
                    description="Подал заявку в парный спарринг",
                    target_url=_safe_reverse("sparring_list"),
                    dedupe_key=f"doubles_join:{instance.pk}",
                    created_at=getattr(instance, "created_at", None),
                )
        if instance.status == DoublesJoinRequestStatus.ACCEPTED:
            owner_user = None
            applicant_name = ""
            try:
                owner_user = player_user(instance.match_request.created_by)
                applicant_name = instance.created_by.get_display_name()
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
                target_url=_safe_reverse("sparring_list"),
                dedupe_key=f"doubles_join_approved:{instance.pk}",
            )
        if instance.status == DoublesJoinRequestStatus.REJECTED:
            owner_user = None
            applicant_name = ""
            try:
                owner_user = player_user(instance.match_request.created_by)
                applicant_name = instance.created_by.get_display_name()
            except Exception:  # noqa: BLE001
                pass
            log_activity(
                event_type=EventType.DOUBLES_JOIN_REJECTED,
                actor=owner_user,
                description=(
                    f"Отклонил заявку игрока {applicant_name} в парный спарринг".strip()
                    if applicant_name
                    else "Отклонил заявку в парный спарринг"
                ),
                target_url=_safe_reverse("sparring_list"),
                dedupe_key=f"doubles_join_rejected:{instance.pk}",
            )

    post_save.connect(
        on_doubles_join_saved,
        sender=DoublesJoinRequest,
        dispatch_uid="activity_doubles_join",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Клубы: заявка на вступление, вступление, отклонение заявки
    # ------------------------------------------------------------------
    def on_club_join_request_saved(sender, instance, created, **kwargs):
        club = instance.club
        club_url = _safe_reverse("club_public_detail", slug=club.slug) if club else ""
        if created:
            log_activity(
                event_type=EventType.CLUB_JOIN_REQUESTED,
                actor=instance.user,
                description=f"Подал заявку в клуб «{club.name}»",
                target_url=club_url,
                metadata={"club_id": club.pk},
                dedupe_key=f"club_join_req:{instance.pk}",
                created_at=getattr(instance, "created_at", None),
            )
        if instance.status == ClubJoinRequestStatus.REJECTED:
            log_activity(
                event_type=EventType.CLUB_JOIN_REJECTED,
                actor=instance.user,
                description=f"Заявка в клуб «{club.name}» отклонена",
                target_url=club_url,
                metadata={"club_id": club.pk},
                dedupe_key=f"club_join_rejected:{instance.pk}",
            )

    post_save.connect(
        on_club_join_request_saved,
        sender=ClubJoinRequest,
        dispatch_uid="activity_club_join_request",
        weak=False,
    )

    def on_club_member_saved(sender, instance, created, **kwargs):
        if instance.status != ClubMemberStatus.ACTIVE:
            return
        club = instance.club
        log_activity(
            event_type=EventType.CLUB_JOINED,
            actor=instance.user,
            description=f"Вступил в клуб «{club.name}»",
            target_url=(
                _safe_reverse("club_public_detail", slug=club.slug) if club else ""
            ),
            metadata={"club_id": club.pk, "role": instance.role},
            dedupe_key=f"club_member:{instance.pk}",
            created_at=getattr(instance, "joined_at", None)
            or getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_club_member_saved,
        sender=ClubMember,
        dispatch_uid="activity_club_member",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Тренеры и тренировки
    # ------------------------------------------------------------------
    def on_coach_application_created(sender, instance, created, **kwargs):
        if not created:
            return
        log_activity(
            event_type=EventType.COACH_APPLICATION,
            actor=instance.applicant_user,
            actor_name=(
                "" if instance.applicant_user_id else (instance.applicant_name or "")
            ),
            description=f"Подал заявку на тренера ({instance.city})",
            target_url=_safe_reverse("training_list"),
            dedupe_key=f"coach_application:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_coach_application_created,
        sender=CoachApplication,
        dispatch_uid="activity_coach_application",
        weak=False,
    )

    def on_training_created(sender, instance, created, **kwargs):
        if not created:
            return
        coach = instance.coach
        coach_user = getattr(coach, "user", None) if coach else None
        log_activity(
            event_type=EventType.TRAINING_PUBLISHED,
            actor=coach_user,
            actor_name="" if coach_user else (getattr(coach, "name", "") or ""),
            description=f"Опубликовал тренировку «{instance.title}»",
            target_url=_safe_reverse("training_detail", slug=instance.slug),
            dedupe_key=f"training:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_training_created,
        sender=Training,
        dispatch_uid="activity_training_created",
        weak=False,
    )

    def on_training_enrollment_created(sender, instance, created, **kwargs):
        if not created:
            return
        user = player_user(instance.player)
        title = ""
        try:
            title = instance.training.title
        except Exception:  # noqa: BLE001
            title = ""
        log_activity(
            event_type=EventType.TRAINING_ENROLLED,
            actor=user,
            actor_name="" if user else (instance.full_name or ""),
            description=(
                f"Записался на тренировку «{title}»"
                if title
                else "Записался на тренировку"
            ),
            target_url=_safe_reverse("training_list"),
            dedupe_key=f"training_enrollment:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_training_enrollment_created,
        sender=TrainingEnrollment,
        dispatch_uid="activity_training_enrollment",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Комментарии (к кортам, игрокам, новостям, матчам и т.д.)
    # ------------------------------------------------------------------
    content_type_labels = {
        "court": "корту",
        "player": "игроку",
        "news": "новости",
        "match": "матчу",
        "coach": "тренеру",
        "training": "тренировке",
        "tournament": "турниру",
    }

    def on_comment_created(sender, instance, created, **kwargs):
        if not created:
            return
        user = player_user(instance.author)
        model_name = ""
        try:
            model_name = instance.content_type.model
        except Exception:  # noqa: BLE001
            model_name = ""
        target_label = content_type_labels.get(model_name, "объекту")
        log_activity(
            event_type=EventType.COMMENT_ADDED,
            actor=user,
            description=f"Оставил комментарий к {target_label}",
            metadata={"target_model": model_name, "object_id": instance.object_id},
            dedupe_key=f"comment:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_comment_created,
        sender=Comment,
        dispatch_uid="activity_comment",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Списания и возвраты FAN-коинов
    # ------------------------------------------------------------------
    def on_fancoin_transaction_created(sender, instance, created, **kwargs):
        if not created:
            return
        is_charge = instance.direction == FancoinTransaction.Direction.CHARGE
        event_type = EventType.FANCOIN_CHARGE if is_charge else EventType.FANCOIN_REFUND
        verb = "Списано" if is_charge else "Возвращено"
        log_activity(
            event_type=event_type,
            actor=instance.user,
            description=f"{verb}: {instance.get_reason_display()}",
            amount=instance.amount,
            currency="FT",
            metadata={"reason": instance.reason, "direction": instance.direction},
            dedupe_key=f"fancoin:{instance.pk}",
            created_at=getattr(instance, "created_at", None),
        )

    post_save.connect(
        on_fancoin_transaction_created,
        sender=FancoinTransaction,
        dispatch_uid="activity_fancoin",
        weak=False,
    )

    # ------------------------------------------------------------------
    # Отмена пользовательской подписки
    # ------------------------------------------------------------------
    def on_subscription_pre_save(sender, instance, **kwargs):
        if instance.pk:
            try:
                instance._old_cancelled_at = (
                    UserSubscription.objects.filter(pk=instance.pk)
                    .values_list("cancelled_at", flat=True)
                    .first()
                )
            except Exception:  # noqa: BLE001
                instance._old_cancelled_at = None
        else:
            instance._old_cancelled_at = None

    def on_subscription_saved(sender, instance, created, **kwargs):
        old_cancelled = getattr(instance, "_old_cancelled_at", None)
        if instance.cancelled_at and not old_cancelled:
            stamp = int(instance.cancelled_at.timestamp())
            log_activity(
                event_type=EventType.SUBSCRIPTION_CANCELLED,
                actor=instance.user,
                description="Отменил подписку (действует до конца оплаченного периода)",
                target_url=_safe_reverse(
                    "admin:subscriptions_usersubscription_change",
                    object_id=instance.pk,
                ),
                dedupe_key=f"subscription_cancelled:{instance.pk}:{stamp}",
                created_at=instance.cancelled_at,
            )

    pre_save.connect(
        on_subscription_pre_save,
        sender=UserSubscription,
        dispatch_uid="activity_subscription_presave",
        weak=False,
    )
    post_save.connect(
        on_subscription_saved,
        sender=UserSubscription,
        dispatch_uid="activity_subscription_saved",
        weak=False,
    )

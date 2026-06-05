import logging

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .fan import (
    _is_fan,
    advance_winner_and_award_loser,
    ensure_consolation_created,
    finalize_tournament,
)
from .models import Match, MatchResultProposal
from .olympic_consolation import (
    _is_olympic,
    advance_winner_olympic,
    ensure_consolation_created_for_round,
)
from .proposal_service import apply_proposal
from .round_robin import _is_round_robin, check_and_finalize_if_complete
from .tvd import (
    TVD_STAGE_GROUP,
    _is_tvd,
)
from .tvd import (
    advance_winner as tvd_advance_winner,
)
from .tvd import (
    check_and_finalize as tvd_check_and_finalize,
)
from .tvd import (
    recalculate_group_standings as tvd_recalculate_group_standings,
)

logger = logging.getLogger(__name__)


@receiver(post_save, sender=Match)
def notify_telegram_match_created(sender, instance, created, **kwargs):
    """После создания матча с участниками — уведомление в Telegram и в ЛК."""
    if not created:
        return
    if not instance.player1_id and not instance.team1_id:
        return
    try:
        from apps.telegram_bot.notifications import notify_match_created

        notify_match_created(instance)
    except Exception as e:
        logger.exception("Telegram notify_match_created failed: %s", e)

    try:
        from django.urls import reverse

        from apps.tournaments.utils import (
            get_match_opponents_for_player,
            get_match_participants,
            get_opponents_contacts_short,
        )
        from apps.users.models import Notification

        deadline_str = (
            instance.deadline.strftime("%d.%m.%Y %H:%M")
            if instance.deadline
            else "не указан"
        )
        url = reverse("match_detail", args=[instance.pk])
        participants = [
            p
            for p in get_match_participants(instance)
            if p and not getattr(p, "is_bye", False) and getattr(p, "user_id", None)
        ]
        for player in participants:
            opponents = get_match_opponents_for_player(instance, player)
            contacts = get_opponents_contacts_short(opponents)
            # Сообщение персональное: контакты — соперника конкретного получателя.
            msg = (
                f"Новый матч: {instance.get_player1_display()} — "
                f"{instance.get_player2_display()}. "
                f"Дедлайн: {deadline_str}. Контакты соперника: {contacts}. "
                "Подробнее в «Мои матчи»."
            )
            if len(msg) > 255:
                msg = msg[:252] + "..."
            try:
                Notification.objects.create(user=player.user, message=msg, url=url)
            except Exception as e:
                logger.warning(
                    "notify_match_created LK for user %s: %s",
                    getattr(player, "user_id", None),
                    e,
                )
    except Exception as e:
        logger.exception("LK notify_match_created failed: %s", e)


@receiver(pre_save, sender=Match)
def prepare_match_completion(sender, instance, **kwargs):
    """
    Handle pre-save logic:
    1. Store old status to detect transitions.
    2. Mark completed matches as pending_calc for FAN rating.
    """
    if instance.pk:
        try:
            old_instance = Match.objects.get(pk=instance.pk)
            instance._old_status = old_instance.status
        except Match.DoesNotExist:
            instance._old_status = None
    else:
        instance._old_status = None

    # If match is transitioning to completed, mark for FAN calculation
    if instance.status in [Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER]:
        old_status = getattr(instance, "_old_status", None)
        was_completed = old_status in [
            Match.MatchStatus.COMPLETED,
            Match.MatchStatus.WALKOVER,
        ]
        if (
            not was_completed
            and instance.rating_status == Match.RatingCalcStatus.NOT_APPLICABLE
        ):
            instance.rating_status = Match.RatingCalcStatus.PENDING


@receiver(post_save, sender=Match)
def update_player_stats(sender, instance, created, **kwargs):
    """
    Update matches_played / matches_won when match is completed.
    Apply shadow FAN rating calculation to hidden_rating.
    """
    old_status = getattr(instance, "_old_status", None)
    is_completed = instance.status in [
        Match.MatchStatus.COMPLETED,
        Match.MatchStatus.WALKOVER,
    ]
    was_completed = old_status in [
        Match.MatchStatus.COMPLETED,
        Match.MatchStatus.WALKOVER,
    ]

    logger.debug(
        "Match %s: status=%s, old_status=%s, is_completed=%s, was_completed=%s",
        instance.pk,
        instance.status,
        old_status,
        is_completed,
        was_completed,
    )

    already_processed = instance.rating_status == Match.RatingCalcStatus.CALCULATED

    if not is_completed:
        logger.debug("Match %s: skipping - not completed", instance.pk)
        return

    # Повторная обработка: админ мог сначала сохранить статус «Завершён» без победителя,
    # а на следующем сохранении winner уже есть, но was_completed=True блокировал расчёт.
    if was_completed and already_processed:
        logger.debug(
            "Match %s: skipping - already completed and rating calculated",
            instance.pk,
        )
        return

    if was_completed and not already_processed:
        logger.info(
            "Match %s: re-processing completed match with pending rating",
            instance.pk,
        )

    # Перезагружаем матч с связанными объектами для правильной работы
    match = Match.objects.select_related(
        "player1",
        "player2",
        "partner1",
        "partner2",
        "winner",
        "tournament",
        "tvd_group",
        "team1__player1",
        "team1__player2",
        "team2__player1",
        "team2__player2",
        "winner_team__player1",
        "winner_team__player2",
        "sparring_response__sparring_request",
    ).get(pk=instance.pk)

    winner = match.winner
    winner_team = match.winner_team
    t = match.tournament
    # Парный матч: по командам (team1/team2) или по парному спаррингу (player1/partner1 vs player2/partner2)
    is_doubles = bool(match.team1_id and match.team2_id) or match.is_doubles_sparring()

    logger.debug(
        "Match %s: winner=%s, winner_team=%s, is_doubles=%s, is_sparring=%s",
        match.pk,
        winner.pk if winner else None,
        winner_team.pk if winner_team else None,
        is_doubles,
        match.is_sparring(),
    )

    # Для парного спарринга winner есть, winner_team нет
    if not winner and not winner_team:
        logger.warning(
            "Match %s: no winner or winner_team, skipping rating update", match.pk
        )
        return

    # Дружеский спарринг (одиночный и парный): не влияет на рейтинг и статистику
    if match.is_friendly_sparring():
        logger.info(
            "Match %s: friendly sparring, skipping stats and rating update",
            match.pk,
        )
        return

    _walkover_loss = instance.is_walkover_loss()

    # ------------------------------------------------------------------
    # 1) Update matches_played / matches_won for all formats
    # ------------------------------------------------------------------
    def _update_stats_singles(match):
        w = match.winner
        los = match.player2 if w == match.player1 else match.player1
        if w and not getattr(w, "is_bye", False):
            w.matches_played += 1
            w.matches_won += 1
            w.save(update_fields=["matches_played", "matches_won"])
        if los and not getattr(los, "is_bye", False):
            los.matches_played += 1
            los.save(update_fields=["matches_played"])

    def _update_stats_doubles(match):
        wt = match.winner_team
        lt = match.team2 if wt == match.team1 else match.team1
        for p in (wt.player1, wt.player2):
            if p and not getattr(p, "is_bye", False):
                p.matches_played += 1
                p.matches_won += 1
                p.save(update_fields=["matches_played", "matches_won"])
        for p in (lt.player1, lt.player2):
            if p and not getattr(p, "is_bye", False):
                p.matches_played += 1
                p.save(update_fields=["matches_played"])

    def _update_stats_doubles_sparring(match):
        win_side = (
            (match.player1, match.partner1)
            if winner in (match.player1, match.partner1)
            else (match.player2, match.partner2)
        )
        lose_side = (
            (match.player2, match.partner2)
            if winner in (match.player1, match.partner1)
            else (match.player1, match.partner1)
        )
        for p in win_side:
            if p and not getattr(p, "is_bye", False):
                p.matches_played += 1
                p.matches_won += 1
                p.save(update_fields=["matches_played", "matches_won"])
        for p in lose_side:
            if p and not getattr(p, "is_bye", False):
                p.matches_played += 1
                p.save(update_fields=["matches_played"])

    if match.is_doubles_sparring():
        _update_stats_doubles_sparring(match)
    elif is_doubles:
        _update_stats_doubles(match)
    else:
        _update_stats_singles(match)

    # ------------------------------------------------------------------
    # 2) Shadow FAN calculation (updates hidden_rating immediately)
    # ------------------------------------------------------------------
    logger.debug("Match %s: applying FAN shadow calculation", match.pk)
    _apply_fan_shadow(match)

    # ------------------------------------------------------------------
    # 3) Format-specific bracket advancement (FAN / Olympic / Round Robin)
    # Только для турнирных матчей, не для спаррингов
    # ------------------------------------------------------------------
    if match.is_sparring():
        # Спарринговые матчи не участвуют в турнирной логике
        logger.debug("Match %s: sparring match, skipping tournament logic", match.pk)
        return

    if t and _is_olympic(t):
        advance_winner_olympic(match, skip_points=_walkover_loss)
        if match.round_index >= 1 and not match.is_consolation:
            ensure_consolation_created_for_round(t, match.round_index)
    elif t and _is_fan(t):
        advance_winner_and_award_loser(match, skip_points=_walkover_loss)
        if match.round_index == 1 and not match.is_consolation:
            ensure_consolation_created(t)
        finalize_tournament(t)
    elif t and _is_round_robin(t):
        check_and_finalize_if_complete(t)
    elif t and _is_tvd(t):
        if getattr(match, "tvd_stage", None) == TVD_STAGE_GROUP and match.tvd_group_id:
            tvd_recalculate_group_standings(match.tvd_group)
        else:
            tvd_advance_winner(match)
        tvd_check_and_finalize(t)


def _apply_fan_shadow(match: Match) -> None:
    """Apply shadow FAN rating calculation to both players' hidden_rating.

    For doubles: delta is applied to both members of each team.
    Bye players are skipped.
    """
    from apps.users.rating_utils import rating_to_ntrp_level, rating_to_skill_level

    from .rating import (
        MatchScore,
        PlayerRatingSnapshot,
        calculate_new_ratings,
    )

    is_doubles_sparring = bool(match.partner1_id and match.partner2_id)
    is_doubles = bool(match.team1_id and match.team2_id) or is_doubles_sparring

    logger.debug("_apply_fan_shadow: match %s, is_doubles=%s", match.pk, is_doubles)

    # Для парных матчей используем первого игрока команды (или стороны) для расчёта рейтинга
    if is_doubles_sparring:
        p1 = match.player1
        p2 = match.player2
        if not p1 or not p2 or not match.partner1 or not match.partner2:
            logger.warning(
                "_apply_fan_shadow: match %s doubles sparring has missing players",
                match.pk,
            )
            return
        logger.debug("_apply_fan_shadow: doubles sparring, p1=%s, p2=%s", p1.pk, p2.pk)
    elif is_doubles:
        # Парные матчи турнира (team1/team2)
        team1 = match.team1
        team2 = match.team2
        if not team1 or not team2:
            logger.warning(
                "_apply_fan_shadow: match %s has teams but team1=%s, team2=%s",
                match.pk,
                team1,
                team2,
            )
            return
        p1 = team1.player1
        p2 = team2.player1
        if not p1 or not p2:
            logger.warning(
                "_apply_fan_shadow: match %s has teams but p1=%s, p2=%s",
                match.pk,
                p1,
                p2,
            )
            return
        logger.debug("_apply_fan_shadow: doubles match, p1=%s, p2=%s", p1.pk, p2.pk)
    else:
        # Для одиночных матчей используем player1 и player2
        p1 = match.player1
        p2 = match.player2
        if not p1 or not p2:
            logger.warning(
                "_apply_fan_shadow: match %s has no players: p1=%s, p2=%s",
                match.pk,
                p1,
                p2,
            )
            return
        logger.debug("_apply_fan_shadow: singles match, p1=%s, p2=%s", p1.pk, p2.pk)

    if getattr(p1, "is_bye", False) or getattr(p2, "is_bye", False):
        logger.debug("_apply_fan_shadow: match %s has bye players, skipping", match.pk)
        return

    # K-factor определяется по количеству матчей ДО этого матча
    # (matches_played уже обновлён выше, поэтому вычитаем 1)
    matches_before_a = max(0, p1.matches_played - 1)
    matches_before_b = max(0, p2.matches_played - 1)

    # Для первого матча используем total_points как начальный рейтинг,
    # если hidden_rating не был правильно инициализирован
    rating_a = p1.hidden_rating
    rating_b = p2.hidden_rating

    if matches_before_a == 0:
        # Первый матч: если hidden_rating сильно отличается от total_points,
        # используем total_points как начальный рейтинг
        if abs(rating_a - float(p1.total_points)) > 200:
            rating_a = float(p1.total_points)
            logger.warning(
                "Player %s: hidden_rating (%.1f) не соответствует total_points (%.1f) для первого матча. "
                "Используем total_points как начальный рейтинг.",
                p1.pk,
                p1.hidden_rating,
                p1.total_points,
            )

    if matches_before_b == 0:
        # Первый матч: если hidden_rating сильно отличается от total_points,
        # используем total_points как начальный рейтинг
        if abs(rating_b - float(p2.total_points)) > 200:
            rating_b = float(p2.total_points)
            logger.warning(
                "Player %s: hidden_rating (%.1f) не соответствует total_points (%.1f) для первого матча. "
                "Используем total_points как начальный рейтинг.",
                p2.pk,
                p2.hidden_rating,
                p2.total_points,
            )

    snap_a = PlayerRatingSnapshot(rating=rating_a, total_matches=matches_before_a)
    snap_b = PlayerRatingSnapshot(rating=rating_b, total_matches=matches_before_b)

    score_a = MatchScore(
        set1=match.player1_set1 or 0,
        set2=match.player1_set2 or 0,
        set3=match.player1_set3 or 0,
    )
    score_b = MatchScore(
        set1=match.player2_set1 or 0,
        set2=match.player2_set2 or 0,
        set3=match.player2_set3 or 0,
    )

    if is_doubles_sparring:
        a_won = match.winner_id in (match.player1_id, match.partner1_id)
    else:
        a_won = (
            match.winner_team_id == match.team1_id
            if match.team1_id and match.team2_id and match.winner_team_id
            else match.winner_id == p1.pk
        )

    result = calculate_new_ratings(snap_a, snap_b, score_a, score_b, a_won)
    logger.debug(
        "_apply_fan_shadow: calculated ratings p1 %.1f (delta %.1f), p2 %.1f (delta %.1f)",
        result.new_rating_a,
        result.delta_a,
        result.new_rating_b,
        result.delta_b,
    )

    # is_doubles уже определен выше

    # Проверяем, является ли это техническим поражением (Retired)
    is_walkover_loss = match.is_walkover_loss()

    # Определяем проигравшего для применения штрафа
    if is_doubles_sparring:
        loser_team = None
        loser = None
        side_a_players = (match.player1, match.partner1)
        side_b_players = (match.player2, match.partner2)
        loser_side_players = side_b_players if a_won else side_a_players
    elif is_doubles:
        winner_team = match.winner_team
        if winner_team:
            loser_team = match.team2 if winner_team == match.team1 else match.team1
        else:
            loser_team = match.team2 if a_won else match.team1
        loser = None
        side_a_players = (
            (match.team1.player1 if match.team1 else None),
            (match.team1.player2 if match.team1 else None),
        )
        side_b_players = (
            (match.team2.player1 if match.team2 else None),
            (match.team2.player2 if match.team2 else None),
        )
        loser_side_players = None
    else:
        loser = p2 if a_won else p1
        loser_team = None
        side_a_players = None
        side_b_players = None
        loser_side_players = None

    # Update hidden_rating and total_points for player1 side
    if is_doubles:
        for p in (
            (side_a_players or ())
            if is_doubles_sparring
            else (match.team1.player1, match.team1.player2)
        ):
            if p and not getattr(p, "is_bye", False):
                new_rating = result.new_rating_a
                # Применяем штраф -40 очков для проигравшего при тех. поражении
                if is_walkover_loss and (
                    (loser_team and p in (loser_team.player1, loser_team.player2))
                    or (loser_side_players and p in loser_side_players)
                ):
                    new_rating = max(0, new_rating - 40.0)
                    logger.info(
                        "Player %s: штраф -40 очков за тех. поражение (Retired)", p.pk
                    )
                old_rating = p.total_points
                old_ntrp = rating_to_ntrp_level(
                    old_rating
                )  # Вычисляем из рейтинга, а не берем из БД
                p.hidden_rating = new_rating
                p.total_points = float(new_rating)
                # Обновляем skill_level и ntrp_level на основе нового рейтинга
                p.skill_level = rating_to_skill_level(new_rating)
                new_ntrp = rating_to_ntrp_level(new_rating)
                p.ntrp_level = new_ntrp
                p.save(
                    update_fields=[
                        "hidden_rating",
                        "total_points",
                        "skill_level",
                        "ntrp_level",
                    ]
                )
                logger.debug(
                    "Player %s (doubles team1): rating updated %.1f -> %.1f (delta: %.1f), Сила %s -> %s",
                    p.pk,
                    old_rating,
                    new_rating,
                    new_rating - old_rating,
                    old_ntrp,
                    new_ntrp,
                )
                # Критическое событие: изменение рейтинга игрока
                if abs(new_rating - old_rating) > 50:
                    logger.warning(
                        "CRITICAL: Large rating change for player %s: %.1f -> %.1f (delta: %.1f)",
                        p.pk,
                        old_rating,
                        new_rating,
                        new_rating - old_rating,
                    )
    else:
        new_rating_a = result.new_rating_a
        # Применяем штраф -40 очков для проигравшего при тех. поражении
        if is_walkover_loss and loser == p1:
            new_rating_a = max(0, new_rating_a - 40.0)
            logger.info("Player %s: штраф -40 очков за тех. поражение (Retired)", p1.pk)
        old_rating_p1 = p1.total_points
        old_ntrp_p1 = rating_to_ntrp_level(
            old_rating_p1
        )  # Вычисляем из рейтинга, а не берем из БД
        p1.hidden_rating = new_rating_a
        p1.total_points = float(new_rating_a)
        # Обновляем skill_level и ntrp_level на основе нового рейтинга
        p1.skill_level = rating_to_skill_level(new_rating_a)
        new_ntrp_p1 = rating_to_ntrp_level(new_rating_a)
        p1.ntrp_level = new_ntrp_p1
        p1.save(
            update_fields=["hidden_rating", "total_points", "skill_level", "ntrp_level"]
        )
        logger.debug(
            "Player %s (singles): rating updated %.1f -> %.1f (delta: %.1f), Сила %s -> %s",
            p1.pk,
            old_rating_p1,
            new_rating_a,
            new_rating_a - old_rating_p1,
            old_ntrp_p1,
            new_ntrp_p1,
        )
        # Критическое событие: изменение рейтинга игрока
        if abs(new_rating_a - old_rating_p1) > 50:
            logger.warning(
                "CRITICAL: Large rating change for player %s: %.1f -> %.1f (delta: %.1f)",
                p1.pk,
                old_rating_p1,
                new_rating_a,
                new_rating_a - old_rating_p1,
            )

    # Update hidden_rating and total_points for player2 side
    if is_doubles:
        for p in (
            (side_b_players or ())
            if is_doubles_sparring
            else (match.team2.player1, match.team2.player2)
        ):
            if p and not getattr(p, "is_bye", False):
                new_rating = result.new_rating_b
                # Применяем штраф -40 очков для проигравшего при тех. поражении
                if is_walkover_loss and (
                    (loser_team and p in (loser_team.player1, loser_team.player2))
                    or (loser_side_players and p in loser_side_players)
                ):
                    new_rating = max(0, new_rating - 40.0)
                    logger.info(
                        "Player %s: штраф -40 очков за тех. поражение (Retired)", p.pk
                    )
                old_rating = p.total_points
                old_ntrp = rating_to_ntrp_level(
                    old_rating
                )  # Вычисляем из рейтинга, а не берем из БД
                p.hidden_rating = new_rating
                p.total_points = float(new_rating)
                # Обновляем skill_level и ntrp_level на основе нового рейтинга
                p.skill_level = rating_to_skill_level(new_rating)
                new_ntrp = rating_to_ntrp_level(new_rating)
                p.ntrp_level = new_ntrp
                p.save(
                    update_fields=[
                        "hidden_rating",
                        "total_points",
                        "skill_level",
                        "ntrp_level",
                    ]
                )
                logger.debug(
                    "Player %s (doubles team2): rating updated %.1f -> %.1f (delta: %.1f), Сила %s -> %s",
                    p.pk,
                    old_rating,
                    new_rating,
                    new_rating - old_rating,
                    old_ntrp,
                    new_ntrp,
                )
                # Критическое событие: изменение рейтинга игрока
                if abs(new_rating - old_rating) > 50:
                    logger.warning(
                        "CRITICAL: Large rating change for player %s: %.1f -> %.1f (delta: %.1f)",
                        p.pk,
                        old_rating,
                        new_rating,
                        new_rating - old_rating,
                    )
    else:
        new_rating_b = result.new_rating_b
        # Применяем штраф -40 очков для проигравшего при тех. поражении
        if is_walkover_loss and loser == p2:
            new_rating_b = max(0, new_rating_b - 40.0)
            logger.info("Player %s: штраф -40 очков за тех. поражение (Retired)", p2.pk)
        old_rating_p2 = p2.total_points
        old_ntrp_p2 = rating_to_ntrp_level(
            old_rating_p2
        )  # Вычисляем из рейтинга, а не берем из БД
        p2.hidden_rating = new_rating_b
        p2.total_points = float(new_rating_b)
        # Обновляем skill_level и ntrp_level на основе нового рейтинга
        p2.skill_level = rating_to_skill_level(new_rating_b)
        new_ntrp_p2 = rating_to_ntrp_level(new_rating_b)
        p2.ntrp_level = new_ntrp_p2
        p2.save(
            update_fields=["hidden_rating", "total_points", "skill_level", "ntrp_level"]
        )
        logger.debug(
            "Player %s (singles): rating updated %.1f -> %.1f (delta: %.1f), Сила %s -> %s",
            p2.pk,
            old_rating_p2,
            new_rating_b,
            new_rating_b - old_rating_p2,
            old_ntrp_p2,
            new_ntrp_p2,
        )
        # Критическое событие: изменение рейтинга игрока
        if abs(new_rating_b - old_rating_p2) > 50:
            logger.warning(
                "CRITICAL: Large rating change for player %s: %.1f -> %.1f (delta: %.1f)",
                p2.pk,
                old_rating_p2,
                new_rating_b,
                new_rating_b - old_rating_p2,
            )

    # Store deltas and mark as calculated
    Match.objects.filter(pk=match.pk).update(
        rating_delta_player1=result.delta_a,
        rating_delta_player2=result.delta_b,
        rating_status=Match.RatingCalcStatus.CALCULATED,
    )


@receiver(post_save, sender=MatchResultProposal)
def apply_proposal_on_admin_accept(sender, instance, created, **kwargs):
    """
    Когда админ вручную меняет статус заявки на «Подтверждено» — применить результат к матчу.
    При подтверждении через ЛК (confirm_proposal) apply_proposal вызывается из view,
    матч уже обновлён, поэтому пропускаем (match.status in COMPLETED/WALKOVER).
    """
    if instance.status != Match.ProposalStatus.ACCEPTED:
        return
    match = instance.match
    if match.status in (Match.MatchStatus.COMPLETED, Match.MatchStatus.WALKOVER):
        return  # уже применено (например, из confirm_proposal)
    apply_proposal(instance)

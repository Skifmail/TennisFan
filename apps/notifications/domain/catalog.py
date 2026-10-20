"""Явный реестр событий и единственного источника внешнего push.

Ключи не выводятся из текста сообщения. Связи с производителями проверяются AST-тестом.
"""

from dataclasses import dataclass
from typing import Final, Literal


@dataclass(frozen=True, slots=True)
class EventDefinition:
    """Описание события без зависимости от транспорта.

    Attributes:
        title: Понятное название.
        group: Раздел платформы.
        source: Единственный источник push для исключения email/ЛК-дублей.
        required_email: Письмо восстановления доступа не зависит от общей подписки.
    """

    title: str
    group: str
    source: Literal["inbox", "email"]
    required_email: bool = False


EVENTS: Final[dict[str, EventDefinition]] = {
    "stringer.application_submitted": EventDefinition(
        "Новая заявка стрингера", "Стрингеры", "inbox"
    ),
    "platform.user_message": EventDefinition(
        "Служебное сообщение пользователю", "Платформа", "email"
    ),
    "account.welcome": EventDefinition(
        "Добро пожаловать", "Аккаунт и безопасность", "email", False
    ),
    "account.email_verification": EventDefinition(
        "Подтверждение email", "Аккаунт и безопасность", "email", True
    ),
    "account.password_reset": EventDefinition(
        "Восстановление доступа", "Аккаунт и безопасность", "email", True
    ),
    "account.password_changed": EventDefinition(
        "Пароль изменён", "Аккаунт и безопасность", "email", False
    ),
    "account.phone_changed": EventDefinition(
        "Телефон изменён", "Аккаунт и безопасность", "email", False
    ),
    "subscription.pre_debit": EventDefinition(
        "Предстоящее списание подписки", "Подписки", "email", False
    ),
    "subscription.purchased": EventDefinition(
        "Подписка оплачена", "Подписки", "email", False
    ),
    "subscription.renewal_failed": EventDefinition(
        "Ошибка продления подписки", "Подписки", "email", False
    ),
    "subscription.expiring": EventDefinition(
        "Подписка заканчивается", "Подписки", "email", False
    ),
    "match.created": EventDefinition("Назначен матч", "Матчи", "inbox", False),
    "match.result_proposed": EventDefinition(
        "Предложен результат матча", "Матчи", "inbox", False
    ),
    "match.result_confirmed": EventDefinition(
        "Подтверждён результат матча", "Матчи", "inbox", False
    ),
    "match.proposal_updated": EventDefinition(
        "Ответ на предложение результата", "Матчи", "inbox", False
    ),
    "match.deadline_changed": EventDefinition(
        "Изменён срок матча", "Матчи", "inbox", False
    ),
    "match.deadline_reminder": EventDefinition(
        "Напоминание о сроке матча", "Матчи", "inbox", False
    ),
    "match.overdue_admin": EventDefinition(
        "Просроченный матч: организатору", "Матчи", "inbox", False
    ),
    "match.overdue_result": EventDefinition(
        "Автоматический результат по дедлайну", "Матчи", "inbox", False
    ),
    "match.deadline_extended": EventDefinition(
        "Продлён срок матча", "Матчи", "email", False
    ),
    "match.opponent_withdrawn": EventDefinition(
        "Соперник снялся с турнира", "Матчи", "inbox", False
    ),
    "match.rate_players": EventDefinition(
        "Оцените участников матча", "Матчи", "inbox", False
    ),
    "match.auto_accepted": EventDefinition(
        "Результат принят автоматически", "Матчи", "inbox", False
    ),
    "tournament.created": EventDefinition("Новый турнир", "Турниры", "inbox", False),
    "tournament.started": EventDefinition("Турнир начался", "Турниры", "inbox", False),
    "tournament.completed": EventDefinition(
        "Турнир завершён", "Турниры", "inbox", False
    ),
    "tournament.cancelled": EventDefinition(
        "Турнир отменён", "Турниры", "inbox", False
    ),
    "tournament.reopened": EventDefinition(
        "Возобновлена регистрация в турнир", "Турниры", "inbox", False
    ),
    "tournament.registered": EventDefinition(
        "Регистрация в турнир", "Турниры", "email", False
    ),
    "tournament.withdrawn": EventDefinition(
        "Снятие с турнира", "Турниры", "inbox", False
    ),
    "tournament.removed_refund": EventDefinition(
        "Исключение с возвратом оплаты", "Турниры", "inbox", False
    ),
    "tournament.removed": EventDefinition(
        "Исключение из турнира", "Турниры", "inbox", False
    ),
    "tournament.unpaid_removed": EventDefinition(
        "Исключение за неоплату", "Турниры", "email", False
    ),
    "tournament.payment_opened": EventDefinition(
        "Открыта оплата турнира", "Турниры", "inbox", False
    ),
    "tournament.payment_reminder": EventDefinition(
        "Напоминание об оплате турнира", "Турниры", "inbox", False
    ),
    "tournament.payment_link": EventDefinition(
        "Повторная ссылка оплаты турнира", "Турниры", "inbox", False
    ),
    "tournament.payment_requested": EventDefinition(
        "Организатор запросил оплату", "Турниры", "inbox", False
    ),
    "tournament.fancoin_paid": EventDefinition(
        "Участие оплачено FanCoin", "Турниры", "inbox", False
    ),
    "tournament.receipt": EventDefinition(
        "Подтверждение оплаты турнира", "Турниры", "email", False
    ),
    "tournament.bracket": EventDefinition(
        "Сформирована сетка турнира", "Турниры", "inbox", False
    ),
    "tournament.team_joined": EventDefinition(
        "Присоединение к команде", "Турниры", "inbox", False
    ),
    "tournament.partner_added": EventDefinition(
        "Добавлен партнёр в команду", "Турниры", "inbox", False
    ),
    "tournament.solo_removed": EventDefinition(
        "Исключена неполная команда", "Турниры", "inbox", False
    ),
    "sparring.published": EventDefinition(
        "Новый спарринг в городе", "Спарринги", "inbox", False
    ),
    "sparring.response": EventDefinition(
        "Отклик на спарринг", "Спарринги", "inbox", False
    ),
    "sparring.accepted": EventDefinition(
        "Отклик на спарринг принят", "Спарринги", "inbox", False
    ),
    "sparring.doubles_joined": EventDefinition(
        "Участие в парном спарринге", "Спарринги", "inbox", False
    ),
    "sparring.team_ready": EventDefinition(
        "Команды спарринга готовы", "Спарринги", "inbox", False
    ),
    "sparring.invited": EventDefinition(
        "Приглашение на спарринг", "Спарринги", "inbox", False
    ),
    "sparring.invitation_accepted": EventDefinition(
        "Приглашение на спарринг принято", "Спарринги", "inbox", False
    ),
    "club.invited": EventDefinition("Приглашение в клуб", "Клубы", "email", False),
    "club.join_requested": EventDefinition(
        "Заявка на вступление в клуб", "Клубы", "inbox", False
    ),
    "club.join_approved": EventDefinition(
        "Вступление в клуб одобрено", "Клубы", "inbox", False
    ),
    "club.join_rejected": EventDefinition(
        "Заявка в клуб отклонена", "Клубы", "inbox", False
    ),
    "club.fee_reminder": EventDefinition(
        "Напоминание о клубном взносе", "Клубы", "email", False
    ),
    "club.fee_overdue": EventDefinition(
        "Клубный взнос просрочен", "Клубы", "email", False
    ),
    "club.fee_paid": EventDefinition("Клубный взнос оплачен", "Клубы", "email", False),
    "club.plan_receipt": EventDefinition(
        "Тариф клуба оплачен", "Клубы", "email", False
    ),
    "club.subscription_expiring": EventDefinition(
        "Подписка клуба заканчивается", "Клубы", "email", False
    ),
    "club.new_member": EventDefinition("Новый участник клуба", "Клубы", "email", False),
    "club.debtors": EventDefinition(
        "Сводка задолженностей клуба", "Клубы", "email", False
    ),
    "club.tournament_reminder": EventDefinition(
        "Скоро клубный турнир", "Клубы", "email", False
    ),
    "club.tournament_min_fill": EventDefinition(
        "Минимальный состав турнира набран", "Клубы", "email", False
    ),
    "club.tournament_insufficient": EventDefinition(
        "Недостаточно участников турнира", "Клубы", "email", False
    ),
    "club.tournament_cancelled": EventDefinition(
        "Клубный турнир отменён", "Клубы", "email", False
    ),
    "chat.direct_message": EventDefinition(
        "Новое личное сообщение", "Личные чаты", "inbox"
    ),
    "chat.reply": EventDefinition("Ответ в чате", "Чаты", "inbox", False),
    "chat.invited": EventDefinition("Приглашение в чат", "Чаты", "inbox", False),
    "support.reply": EventDefinition("Ответ поддержки", "Поддержка", "email", False),
    "support.admin_message": EventDefinition(
        "Новое обращение в поддержку", "Поддержка", "email", False
    ),
    "training.enrollment": EventDefinition(
        "Заявка на тренировку", "Тренировки", "email", False
    ),
    "training.coach_decision": EventDefinition(
        "Решение по заявке тренера", "Тренировки", "email", False
    ),
    "court.application_decision": EventDefinition(
        "Решение по заявке корта", "Корты", "email", False
    ),
    "payment.donation": EventDefinition(
        "Спасибо за поддержку", "Платежи", "email", False
    ),
    "platform.admin_message": EventDefinition(
        "Служебное сообщение администратору", "Администрация", "email", False
    ),
    "manual.broadcast": EventDefinition(
        "Сообщение администрации", "Ручные рассылки", "inbox", False
    ),
}

PRODUCER_EVENTS: Final[dict[str, str]] = {
    "apps.chat.direct:notify_direct_message": "chat.direct_message",
    "apps.content.stringer_services:notify_stringer_application": "stringer.application_submitted",
    "apps.notifications.auth_forms:send_mail": "account.password_reset",
    "apps.chat.invitation_email:send_invitation_email": "chat.invited",
    "apps.chat.invitations:_expand_campaign": "chat.invited",
    "apps.chat.services:send_message": "chat.reply",
    "apps.clubs.notifications:send_club_invite_email": "club.invited",
    "apps.clubs.notifications:send_debtors_summary": "club.debtors",
    "apps.clubs.notifications:send_fee_overdue_notifications": "club.fee_overdue",
    "apps.clubs.notifications:send_fee_paid_notification": "club.fee_paid",
    "apps.clubs.notifications:send_fee_reminder_notifications": "club.fee_reminder",
    "apps.clubs.notifications:send_join_request_notification": "club.join_requested",
    "apps.clubs.notifications:send_new_member_notification": "club.new_member",
    "apps.clubs.notifications:send_subscription_expiring_notifications": "club.subscription_expiring",
    "apps.clubs.notifications:send_tournament_auto_cancelled_to_club": "club.tournament_cancelled",
    "apps.clubs.notifications:send_tournament_insufficient_to_club": "club.tournament_insufficient",
    "apps.clubs.notifications:send_tournament_min_fill_to_club": "club.tournament_min_fill",
    "apps.clubs.notifications:send_tournament_reminder": "club.tournament_reminder",
    "apps.clubs.services:approve_club_join_request": "club.join_approved",
    "apps.clubs.services:reject_club_join_request": "club.join_rejected",
    "apps.clubs.views.public:join_request_create": "club.join_requested",
    "apps.core.email_service:send_city_sparring_email": "sparring.published",
    "apps.core.email_service:send_club_plan_receipt_email": "club.plan_receipt",
    "apps.core.email_service:send_coach_application_decision_email": "training.coach_decision",
    "apps.core.email_service:send_court_application_decision_email": "court.application_decision",
    "apps.core.email_service:send_donation_thanks_email": "payment.donation",
    "apps.core.email_service:send_email_verification": "account.email_verification",
    "apps.core.email_service:send_match_deadline_changed_email": "match.deadline_changed",
    "apps.core.email_service:send_match_deadline_overdue_admin_email": "match.overdue_admin",
    "apps.core.email_service:send_match_deadline_reminder_email": "match.deadline_reminder",
    "apps.core.email_service:send_new_tournament_email": "tournament.created",
    "apps.core.email_service:send_password_changed_email": "account.password_changed",
    "apps.core.email_service:send_phone_changed_email": "account.phone_changed",
    "apps.core.email_service:send_subscription_autorenew_failed_email": "subscription.renewal_failed",
    "apps.core.email_service:send_subscription_pre_debit_notification": "subscription.pre_debit",
    "apps.core.email_service:send_tournament_cancelled_email": "tournament.cancelled",
    "apps.core.email_service:send_tournament_completed_email": "tournament.completed",
    "apps.core.email_service:send_tournament_entry_fancoin_confirmed_email": "tournament.fancoin_paid",
    "apps.core.email_service:send_tournament_entry_receipt_email": "tournament.receipt",
    "apps.core.email_service:send_tournament_opponent_match_closed_email": "match.opponent_withdrawn",
    "apps.core.email_service:send_tournament_participant_withdrawn_email": "tournament.withdrawn",
    "apps.core.email_service:send_tournament_postpayment_1h_reminder_email": "tournament.payment_reminder",
    "apps.core.email_service:send_tournament_postpayment_opened_email": "tournament.payment_opened",
    "apps.core.email_service:send_tournament_postpayment_resend_email": "tournament.payment_link",
    "apps.core.email_service:send_training_enrollment_email": "training.enrollment",
    "apps.core.email_service:send_welcome_email": "account.welcome",
    "apps.core.support_notifications:send_admin_support_notification": "support.admin_message",
    "apps.core.support_notifications:send_user_support_reply": "support.reply",
    "apps.core.telegram_notify:_send_admin_email": "platform.admin_message",
    "apps.player_ratings.notifications:notify_players_to_rate_match": "match.rate_players",
    "apps.sparring.city_notify:notify_published_sparring": "sparring.published",
    "apps.sparring.doubles_services:_notify_team_sparring_ready": "sparring.team_ready",
    "apps.sparring.views:doubles_accept_join": "sparring.doubles_joined",
    "apps.sparring.views:sparring_confirm_response": "sparring.accepted",
    "apps.sparring.views:sparring_respond": "sparring.response",
    "apps.subscriptions.utils:send_subscription_purchase_email": "subscription.purchased",
    "apps.telegram_bot.notifications:_notify_new_tournament_lk_and_email": "tournament.created",
    "apps.telegram_bot.notifications:notify_bracket_formed": "tournament.bracket",
    "apps.telegram_bot.notifications:notify_doubles_join_accepted": "sparring.doubles_joined",
    "apps.telegram_bot.notifications:notify_extension_approved": "match.deadline_extended",
    "apps.telegram_bot.notifications:notify_match_created": "match.created",
    "apps.telegram_bot.notifications:notify_match_deadline_reminder": "match.deadline_reminder",
    "apps.telegram_bot.notifications:notify_postpayment_1h_reminder": "tournament.payment_reminder",
    "apps.telegram_bot.notifications:notify_postpayment_opened": "tournament.payment_opened",
    "apps.telegram_bot.notifications:notify_postpayment_removed": "tournament.unpaid_removed",
    "apps.telegram_bot.notifications:notify_proposal_confirmed": "match.result_confirmed",
    "apps.telegram_bot.notifications:notify_proposal_rejected": "match.proposal_updated",
    "apps.telegram_bot.notifications:notify_result_confirmed_to_participants": "match.result_confirmed",
    "apps.telegram_bot.notifications:notify_result_proposal": "match.result_proposed",
    "apps.telegram_bot.notifications:notify_sparring_invitation_accepted_inviter": "sparring.invitation_accepted",
    "apps.telegram_bot.notifications:notify_sparring_invitation_created": "sparring.invited",
    "apps.telegram_bot.notifications:notify_sparring_response": "sparring.response",
    "apps.telegram_bot.notifications:notify_sparring_response_accepted": "sparring.accepted",
    "apps.telegram_bot.notifications:notify_subscription_expiring": "subscription.expiring",
    "apps.telegram_bot.notifications:notify_team_sparring_ready": "sparring.team_ready",
    "apps.telegram_bot.notifications:notify_tournament_registered": "tournament.registered",
    "apps.telegram_bot.notifications:notify_tournament_removed_refund": "tournament.removed_refund",
    "apps.telegram_bot.notifications:notify_tournament_start": "tournament.started",
    "apps.telegram_bot.views:_handle_proposal_callback": "match.proposal_updated",
    "apps.telegram_bot.views:_handle_result_type_callback": "match.result_proposed",
    "apps.telegram_bot.views:_handle_sparring_callback": "sparring.accepted",
    "apps.telegram_bot.views:user_bot_webhook": "match.result_proposed",
    "apps.tournaments.cancel:_notify_cancelled": "tournament.cancelled",
    "apps.tournaments.cancel:_notify_tournament_reopened": "tournament.reopened",
    "apps.tournaments.completion_notify:_deliver_completion_notifications": "tournament.completed",
    "apps.tournaments.fan:apply_overdue_walkover": "match.overdue_result",
    "apps.tournaments.management.commands.auto_accept_stale_proposals:handle": "match.auto_accepted",
    "apps.tournaments.management.commands.send_deadline_reminders:_notify_match_participants": "match.deadline_reminder",
    "apps.tournaments.overdue:_notify_players_deadline_auto_rt": "match.overdue_result",
    "apps.tournaments.overdue:notify_admins_match_deadline_overdue": "match.overdue_admin",
    "apps.tournaments.postpayment:_remove_unpaid_players": "tournament.unpaid_removed",
    "apps.tournaments.postpayment:_send_fancoin_settled_notification": "tournament.fancoin_paid",
    "apps.tournaments.postpayment:_send_postpayment_opened_notification": "tournament.payment_opened",
    "apps.tournaments.postpayment:resend_postpayment_payment_link": "tournament.payment_link",
    "apps.tournaments.postpayment:send_1h_reminders": "tournament.payment_reminder",
    "apps.tournaments.proposal_service:notify_participants_match_result_confirmed": "match.result_confirmed",
    "apps.tournaments.round_robin:apply_overdue_walkover_round_robin": "match.overdue_result",
    "apps.tournaments.signals:notify_telegram_match_created": "match.created",
    "apps.tournaments.solo_teams:remove_solo_teams_from_doubles_tournament": "tournament.solo_removed",
    "apps.tournaments.utils:notify_participants_match_deadline_changed": "match.deadline_changed",
    "apps.tournaments.views:_do_add_partner": "tournament.partner_added",
    "apps.tournaments.views:_do_join_team": "tournament.team_joined",
    "apps.tournaments.views:_notify_removed_participant_no_refund": "tournament.removed",
    "apps.tournaments.views:_notify_removed_participant_refund": "tournament.removed_refund",
    "apps.tournaments.views:confirm_proposal": "match.proposal_updated",
    "apps.tournaments.views:propose_result": "match.result_proposed",
    "apps.tournaments.views:tournament_manage_send_payment_notification": "tournament.payment_requested",
    "apps.tournaments.withdraw:_notify_opponents": "match.opponent_withdrawn",
    "apps.tournaments.withdraw:_notify_withdrawn_user": "tournament.withdrawn",
}

# В этих событиях до центра не существовало почтового производителя.
INBOX_ONLY_EVENTS: Final = frozenset(
    [
        "stringer.application_submitted",
        "chat.reply",
        "club.join_approved",
        "club.join_rejected",
        "match.auto_accepted",
        "match.overdue_result",
        "tournament.partner_added",
        "tournament.payment_requested",
        "tournament.removed",
        "tournament.solo_removed",
        "tournament.team_joined",
    ]
)

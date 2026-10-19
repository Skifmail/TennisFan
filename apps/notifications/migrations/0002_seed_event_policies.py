"""Заполняет полный реестр, не меняя существующие пользовательские согласия."""

from typing import Any

from django.db import migrations

ROWS = [
    {
        "key": "platform.user_message",
        "title": "Служебное сообщение пользователю",
        "group": "Платформа",
        "description": "Служебное сообщение пользователю. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: ",
    },
    {
        "key": "account.welcome",
        "title": "Добро пожаловать",
        "group": "Аккаунт и безопасность",
        "description": "Добро пожаловать. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_welcome_email",
    },
    {
        "key": "account.email_verification",
        "title": "Подтверждение email",
        "group": "Аккаунт и безопасность",
        "description": "Подтверждение email. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Письмо доступа: пользовательский email-переключатель не применяется. Производители: apps.core.email_service:send_email_verification",
    },
    {
        "key": "account.password_reset",
        "title": "Восстановление доступа",
        "group": "Аккаунт и безопасность",
        "description": "Восстановление доступа. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Письмо доступа: пользовательский email-переключатель не применяется. Производители: ",
    },
    {
        "key": "account.password_changed",
        "title": "Пароль изменён",
        "group": "Аккаунт и безопасность",
        "description": "Пароль изменён. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_password_changed_email",
    },
    {
        "key": "account.phone_changed",
        "title": "Телефон изменён",
        "group": "Аккаунт и безопасность",
        "description": "Телефон изменён. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_phone_changed_email",
    },
    {
        "key": "subscription.pre_debit",
        "title": "Предстоящее списание подписки",
        "group": "Подписки",
        "description": "Предстоящее списание подписки. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_subscription_pre_debit_notification",
    },
    {
        "key": "subscription.purchased",
        "title": "Подписка оплачена",
        "group": "Подписки",
        "description": "Подписка оплачена. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.subscriptions.utils:send_subscription_purchase_email",
    },
    {
        "key": "subscription.renewal_failed",
        "title": "Ошибка продления подписки",
        "group": "Подписки",
        "description": "Ошибка продления подписки. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_subscription_autorenew_failed_email",
    },
    {
        "key": "subscription.expiring",
        "title": "Подписка заканчивается",
        "group": "Подписки",
        "description": "Подписка заканчивается. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.telegram_bot.notifications:notify_subscription_expiring",
    },
    {
        "key": "match.created",
        "title": "Назначен матч",
        "group": "Матчи",
        "description": "Назначен матч. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_match_created, apps.tournaments.signals:notify_telegram_match_created",
    },
    {
        "key": "match.result_proposed",
        "title": "Предложен результат матча",
        "group": "Матчи",
        "description": "Предложен результат матча. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_result_proposal, apps.telegram_bot.views:_handle_result_type_callback, apps.telegram_bot.views:user_bot_webhook, apps.tournaments.views:propose_result",
    },
    {
        "key": "match.result_confirmed",
        "title": "Подтверждён результат матча",
        "group": "Матчи",
        "description": "Подтверждён результат матча. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_proposal_confirmed, apps.telegram_bot.notifications:notify_result_confirmed_to_participants, apps.tournaments.proposal_service:notify_participants_match_result_confirmed",
    },
    {
        "key": "match.proposal_updated",
        "title": "Ответ на предложение результата",
        "group": "Матчи",
        "description": "Ответ на предложение результата. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_proposal_rejected, apps.telegram_bot.views:_handle_proposal_callback, apps.tournaments.views:confirm_proposal",
    },
    {
        "key": "match.deadline_changed",
        "title": "Изменён срок матча",
        "group": "Матчи",
        "description": "Изменён срок матча. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_match_deadline_changed_email, apps.tournaments.utils:notify_participants_match_deadline_changed",
    },
    {
        "key": "match.deadline_reminder",
        "title": "Напоминание о сроке матча",
        "group": "Матчи",
        "description": "Напоминание о сроке матча. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_match_deadline_reminder_email, apps.telegram_bot.notifications:notify_match_deadline_reminder, apps.tournaments.management.commands.send_deadline_reminders:_notify_match_participants",
    },
    {
        "key": "match.overdue_admin",
        "title": "Просроченный матч: организатору",
        "group": "Матчи",
        "description": "Просроченный матч: организатору. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_match_deadline_overdue_admin_email, apps.tournaments.overdue:notify_admins_match_deadline_overdue",
    },
    {
        "key": "match.overdue_result",
        "title": "Автоматический результат по дедлайну",
        "group": "Матчи",
        "description": "Автоматический результат по дедлайну. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.fan:apply_overdue_walkover, apps.tournaments.overdue:_notify_players_deadline_auto_rt, apps.tournaments.round_robin:apply_overdue_walkover_round_robin",
    },
    {
        "key": "match.deadline_extended",
        "title": "Продлён срок матча",
        "group": "Матчи",
        "description": "Продлён срок матча. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.telegram_bot.notifications:notify_extension_approved",
    },
    {
        "key": "match.opponent_withdrawn",
        "title": "Соперник снялся с турнира",
        "group": "Матчи",
        "description": "Соперник снялся с турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_opponent_match_closed_email, apps.tournaments.withdraw:_notify_opponents",
    },
    {
        "key": "match.rate_players",
        "title": "Оцените участников матча",
        "group": "Матчи",
        "description": "Оцените участников матча. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.player_ratings.notifications:notify_players_to_rate_match",
    },
    {
        "key": "match.auto_accepted",
        "title": "Результат принят автоматически",
        "group": "Матчи",
        "description": "Результат принят автоматически. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.management.commands.auto_accept_stale_proposals:handle",
    },
    {
        "key": "tournament.created",
        "title": "Новый турнир",
        "group": "Турниры",
        "description": "Новый турнир. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_new_tournament_email, apps.telegram_bot.notifications:_notify_new_tournament_lk_and_email",
    },
    {
        "key": "tournament.started",
        "title": "Турнир начался",
        "group": "Турниры",
        "description": "Турнир начался. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_tournament_start",
    },
    {
        "key": "tournament.completed",
        "title": "Турнир завершён",
        "group": "Турниры",
        "description": "Турнир завершён. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_completed_email, apps.tournaments.completion_notify:_deliver_completion_notifications",
    },
    {
        "key": "tournament.cancelled",
        "title": "Турнир отменён",
        "group": "Турниры",
        "description": "Турнир отменён. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_cancelled_email, apps.tournaments.cancel:_notify_cancelled",
    },
    {
        "key": "tournament.reopened",
        "title": "Возобновлена регистрация в турнир",
        "group": "Турниры",
        "description": "Возобновлена регистрация в турнир. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.cancel:_notify_tournament_reopened",
    },
    {
        "key": "tournament.registered",
        "title": "Регистрация в турнир",
        "group": "Турниры",
        "description": "Регистрация в турнир. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.telegram_bot.notifications:notify_tournament_registered",
    },
    {
        "key": "tournament.withdrawn",
        "title": "Снятие с турнира",
        "group": "Турниры",
        "description": "Снятие с турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_participant_withdrawn_email, apps.tournaments.withdraw:_notify_withdrawn_user",
    },
    {
        "key": "tournament.removed_refund",
        "title": "Исключение с возвратом оплаты",
        "group": "Турниры",
        "description": "Исключение с возвратом оплаты. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_tournament_removed_refund, apps.tournaments.views:_notify_removed_participant_refund",
    },
    {
        "key": "tournament.removed",
        "title": "Исключение из турнира",
        "group": "Турниры",
        "description": "Исключение из турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.views:_notify_removed_participant_no_refund",
    },
    {
        "key": "tournament.unpaid_removed",
        "title": "Исключение за неоплату",
        "group": "Турниры",
        "description": "Исключение за неоплату. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.telegram_bot.notifications:notify_postpayment_removed, apps.tournaments.postpayment:_remove_unpaid_players",
    },
    {
        "key": "tournament.payment_opened",
        "title": "Открыта оплата турнира",
        "group": "Турниры",
        "description": "Открыта оплата турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_postpayment_opened_email, apps.telegram_bot.notifications:notify_postpayment_opened, apps.tournaments.postpayment:_send_postpayment_opened_notification",
    },
    {
        "key": "tournament.payment_reminder",
        "title": "Напоминание об оплате турнира",
        "group": "Турниры",
        "description": "Напоминание об оплате турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_postpayment_1h_reminder_email, apps.telegram_bot.notifications:notify_postpayment_1h_reminder, apps.tournaments.postpayment:send_1h_reminders",
    },
    {
        "key": "tournament.payment_link",
        "title": "Повторная ссылка оплаты турнира",
        "group": "Турниры",
        "description": "Повторная ссылка оплаты турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_postpayment_resend_email, apps.tournaments.postpayment:resend_postpayment_payment_link",
    },
    {
        "key": "tournament.payment_requested",
        "title": "Организатор запросил оплату",
        "group": "Турниры",
        "description": "Организатор запросил оплату. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.views:tournament_manage_send_payment_notification",
    },
    {
        "key": "tournament.fancoin_paid",
        "title": "Участие оплачено FanCoin",
        "group": "Турниры",
        "description": "Участие оплачено FanCoin. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_tournament_entry_fancoin_confirmed_email, apps.tournaments.postpayment:_send_fancoin_settled_notification",
    },
    {
        "key": "tournament.receipt",
        "title": "Подтверждение оплаты турнира",
        "group": "Турниры",
        "description": "Подтверждение оплаты турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_tournament_entry_receipt_email",
    },
    {
        "key": "tournament.bracket",
        "title": "Сформирована сетка турнира",
        "group": "Турниры",
        "description": "Сформирована сетка турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_bracket_formed",
    },
    {
        "key": "tournament.team_joined",
        "title": "Присоединение к команде",
        "group": "Турниры",
        "description": "Присоединение к команде. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.views:_do_join_team",
    },
    {
        "key": "tournament.partner_added",
        "title": "Добавлен партнёр в команду",
        "group": "Турниры",
        "description": "Добавлен партнёр в команду. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.views:_do_add_partner",
    },
    {
        "key": "tournament.solo_removed",
        "title": "Исключена неполная команда",
        "group": "Турниры",
        "description": "Исключена неполная команда. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.tournaments.solo_teams:remove_solo_teams_from_doubles_tournament",
    },
    {
        "key": "sparring.published",
        "title": "Новый спарринг в городе",
        "group": "Спарринги",
        "description": "Новый спарринг в городе. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.core.email_service:send_city_sparring_email, apps.sparring.city_notify:notify_published_sparring",
    },
    {
        "key": "sparring.response",
        "title": "Отклик на спарринг",
        "group": "Спарринги",
        "description": "Отклик на спарринг. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.sparring.views:sparring_respond, apps.telegram_bot.notifications:notify_sparring_response",
    },
    {
        "key": "sparring.accepted",
        "title": "Отклик на спарринг принят",
        "group": "Спарринги",
        "description": "Отклик на спарринг принят. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.sparring.views:sparring_confirm_response, apps.telegram_bot.notifications:notify_sparring_response_accepted, apps.telegram_bot.views:_handle_sparring_callback",
    },
    {
        "key": "sparring.doubles_joined",
        "title": "Участие в парном спарринге",
        "group": "Спарринги",
        "description": "Участие в парном спарринге. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.sparring.views:doubles_accept_join, apps.telegram_bot.notifications:notify_doubles_join_accepted",
    },
    {
        "key": "sparring.team_ready",
        "title": "Команды спарринга готовы",
        "group": "Спарринги",
        "description": "Команды спарринга готовы. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.sparring.doubles_services:_notify_team_sparring_ready, apps.telegram_bot.notifications:notify_team_sparring_ready",
    },
    {
        "key": "sparring.invited",
        "title": "Приглашение на спарринг",
        "group": "Спарринги",
        "description": "Приглашение на спарринг. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_sparring_invitation_created",
    },
    {
        "key": "sparring.invitation_accepted",
        "title": "Приглашение на спарринг принято",
        "group": "Спарринги",
        "description": "Приглашение на спарринг принято. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.telegram_bot.notifications:notify_sparring_invitation_accepted_inviter",
    },
    {
        "key": "club.invited",
        "title": "Приглашение в клуб",
        "group": "Клубы",
        "description": "Приглашение в клуб. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_club_invite_email",
    },
    {
        "key": "club.join_requested",
        "title": "Заявка на вступление в клуб",
        "group": "Клубы",
        "description": "Заявка на вступление в клуб. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.clubs.notifications:send_join_request_notification, apps.clubs.views.public:join_request_create",
    },
    {
        "key": "club.join_approved",
        "title": "Вступление в клуб одобрено",
        "group": "Клубы",
        "description": "Вступление в клуб одобрено. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.clubs.services:approve_club_join_request",
    },
    {
        "key": "club.join_rejected",
        "title": "Заявка в клуб отклонена",
        "group": "Клубы",
        "description": "Заявка в клуб отклонена. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.clubs.services:reject_club_join_request",
    },
    {
        "key": "club.fee_reminder",
        "title": "Напоминание о клубном взносе",
        "group": "Клубы",
        "description": "Напоминание о клубном взносе. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_fee_reminder_notifications",
    },
    {
        "key": "club.fee_overdue",
        "title": "Клубный взнос просрочен",
        "group": "Клубы",
        "description": "Клубный взнос просрочен. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_fee_overdue_notifications",
    },
    {
        "key": "club.fee_paid",
        "title": "Клубный взнос оплачен",
        "group": "Клубы",
        "description": "Клубный взнос оплачен. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_fee_paid_notification",
    },
    {
        "key": "club.plan_receipt",
        "title": "Тариф клуба оплачен",
        "group": "Клубы",
        "description": "Тариф клуба оплачен. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_club_plan_receipt_email",
    },
    {
        "key": "club.subscription_expiring",
        "title": "Подписка клуба заканчивается",
        "group": "Клубы",
        "description": "Подписка клуба заканчивается. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_subscription_expiring_notifications",
    },
    {
        "key": "club.new_member",
        "title": "Новый участник клуба",
        "group": "Клубы",
        "description": "Новый участник клуба. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_new_member_notification",
    },
    {
        "key": "club.debtors",
        "title": "Сводка задолженностей клуба",
        "group": "Клубы",
        "description": "Сводка задолженностей клуба. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_debtors_summary",
    },
    {
        "key": "club.tournament_reminder",
        "title": "Скоро клубный турнир",
        "group": "Клубы",
        "description": "Скоро клубный турнир. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_tournament_reminder",
    },
    {
        "key": "club.tournament_min_fill",
        "title": "Минимальный состав турнира набран",
        "group": "Клубы",
        "description": "Минимальный состав турнира набран. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_tournament_min_fill_to_club",
    },
    {
        "key": "club.tournament_insufficient",
        "title": "Недостаточно участников турнира",
        "group": "Клубы",
        "description": "Недостаточно участников турнира. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_tournament_insufficient_to_club",
    },
    {
        "key": "club.tournament_cancelled",
        "title": "Клубный турнир отменён",
        "group": "Клубы",
        "description": "Клубный турнир отменён. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.clubs.notifications:send_tournament_auto_cancelled_to_club",
    },
    {
        "key": "chat.reply",
        "title": "Ответ в чате",
        "group": "Чаты",
        "description": "Ответ в чате. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.chat.services:send_message",
    },
    {
        "key": "chat.invited",
        "title": "Приглашение в чат",
        "group": "Чаты",
        "description": "Приглашение в чат. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: apps.chat.invitation_email:send_invitation_email, apps.chat.invitations:_expand_campaign",
    },
    {
        "key": "support.reply",
        "title": "Ответ поддержки",
        "group": "Поддержка",
        "description": "Ответ поддержки. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.support_notifications:send_user_support_reply",
    },
    {
        "key": "support.admin_message",
        "title": "Новое обращение в поддержку",
        "group": "Поддержка",
        "description": "Новое обращение в поддержку. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.support_notifications:send_admin_support_notification",
    },
    {
        "key": "training.enrollment",
        "title": "Заявка на тренировку",
        "group": "Тренировки",
        "description": "Заявка на тренировку. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_training_enrollment_email",
    },
    {
        "key": "training.coach_decision",
        "title": "Решение по заявке тренера",
        "group": "Тренировки",
        "description": "Решение по заявке тренера. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_coach_application_decision_email",
    },
    {
        "key": "court.application_decision",
        "title": "Решение по заявке корта",
        "group": "Корты",
        "description": "Решение по заявке корта. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_court_application_decision_email",
    },
    {
        "key": "payment.donation",
        "title": "Спасибо за поддержку",
        "group": "Платежи",
        "description": "Спасибо за поддержку. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.email_service:send_donation_thanks_email",
    },
    {
        "key": "platform.admin_message",
        "title": "Служебное сообщение администратору",
        "group": "Администрация",
        "description": "Служебное сообщение администратору. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: email. Производители: apps.core.telegram_notify:_send_admin_email",
    },
    {
        "key": "manual.broadcast",
        "title": "Сообщение администрации",
        "group": "Ручные рассылки",
        "description": "Сообщение администрации. Получатели определяются существующим бизнес-событием, не общей аудиторией. Источник push: inbox. Производители: ",
    },
]


def seed(apps: Any, schema_editor: Any) -> None:
    """Создаёт отсутствующие политики; существующие чекбоксы не перезаписывает.

    Args:
        apps: Исторические модели миграции.
        schema_editor: Текущее подключение схемы.
    """
    model = apps.get_model("notifications", "EventPolicy")
    for row in ROWS:
        model.objects.using(schema_editor.connection.alias).get_or_create(
            key=row["key"], defaults={k: v for k, v in row.items() if k != "key"}
        )


class Migration(migrations.Migration):
    """Начальные политики для явного каталога событий.

    Attributes:
        dependencies: Созданная схема центра.
        operations: Добавление только отсутствующих настроек.
    """

    dependencies = [("notifications", "0001_initial")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]

# Полный реестр событий центра уведомлений

Источники перечислены явно; новые неразмеченные отправители обнаруживаются AST-тестом.

| Код | Событие | Раздел | Канонический источник push |
|---|---|---|---|
| `account.email_verification` | Подтверждение email | Аккаунт и безопасность | email |
| `account.password_changed` | Пароль изменён | Аккаунт и безопасность | email |
| `account.password_reset` | Восстановление доступа | Аккаунт и безопасность | email |
| `account.phone_changed` | Телефон изменён | Аккаунт и безопасность | email |
| `account.welcome` | Добро пожаловать | Аккаунт и безопасность | email |
| `chat.invited` | Приглашение в чат | Чаты | inbox |
| `chat.reply` | Ответ в чате | Чаты | inbox |
| `club.debtors` | Сводка задолженностей клуба | Клубы | email |
| `club.fee_overdue` | Клубный взнос просрочен | Клубы | email |
| `club.fee_paid` | Клубный взнос оплачен | Клубы | email |
| `club.fee_reminder` | Напоминание о клубном взносе | Клубы | email |
| `club.invited` | Приглашение в клуб | Клубы | email |
| `club.join_approved` | Вступление в клуб одобрено | Клубы | inbox |
| `club.join_rejected` | Заявка в клуб отклонена | Клубы | inbox |
| `club.join_requested` | Заявка на вступление в клуб | Клубы | inbox |
| `club.new_member` | Новый участник клуба | Клубы | email |
| `club.plan_receipt` | Тариф клуба оплачен | Клубы | email |
| `club.subscription_expiring` | Подписка клуба заканчивается | Клубы | email |
| `club.tournament_cancelled` | Клубный турнир отменён | Клубы | email |
| `club.tournament_insufficient` | Недостаточно участников турнира | Клубы | email |
| `club.tournament_min_fill` | Минимальный состав турнира набран | Клубы | email |
| `club.tournament_reminder` | Скоро клубный турнир | Клубы | email |
| `court.application_decision` | Решение по заявке корта | Корты | email |
| `manual.broadcast` | Сообщение администрации | Ручные рассылки | inbox |
| `match.auto_accepted` | Результат принят автоматически | Матчи | inbox |
| `match.created` | Назначен матч | Матчи | inbox |
| `match.deadline_changed` | Изменён срок матча | Матчи | inbox |
| `match.deadline_extended` | Продлён срок матча | Матчи | email |
| `match.deadline_reminder` | Напоминание о сроке матча | Матчи | inbox |
| `match.opponent_withdrawn` | Соперник снялся с турнира | Матчи | inbox |
| `match.overdue_admin` | Просроченный матч: организатору | Матчи | inbox |
| `match.overdue_result` | Автоматический результат по дедлайну | Матчи | inbox |
| `match.proposal_updated` | Ответ на предложение результата | Матчи | inbox |
| `match.rate_players` | Оцените участников матча | Матчи | inbox |
| `match.result_confirmed` | Подтверждён результат матча | Матчи | inbox |
| `match.result_proposed` | Предложен результат матча | Матчи | inbox |
| `payment.donation` | Спасибо за поддержку | Платежи | email |
| `platform.admin_message` | Служебное сообщение администратору | Администрация | email |
| `platform.user_message` | Служебное сообщение пользователю | Платформа | email |
| `sparring.accepted` | Отклик на спарринг принят | Спарринги | inbox |
| `sparring.doubles_joined` | Участие в парном спарринге | Спарринги | inbox |
| `sparring.invitation_accepted` | Приглашение на спарринг принято | Спарринги | inbox |
| `sparring.invited` | Приглашение на спарринг | Спарринги | inbox |
| `sparring.published` | Новый спарринг в городе | Спарринги | inbox |
| `sparring.response` | Отклик на спарринг | Спарринги | inbox |
| `sparring.team_ready` | Команды спарринга готовы | Спарринги | inbox |
| `subscription.expiring` | Подписка заканчивается | Подписки | email |
| `subscription.pre_debit` | Предстоящее списание подписки | Подписки | email |
| `subscription.purchased` | Подписка оплачена | Подписки | email |
| `subscription.renewal_failed` | Ошибка продления подписки | Подписки | email |
| `support.admin_message` | Новое обращение в поддержку | Поддержка | email |
| `support.reply` | Ответ поддержки | Поддержка | email |
| `tournament.bracket` | Сформирована сетка турнира | Турниры | inbox |
| `tournament.cancelled` | Турнир отменён | Турниры | inbox |
| `tournament.completed` | Турнир завершён | Турниры | inbox |
| `tournament.created` | Новый турнир | Турниры | inbox |
| `tournament.fancoin_paid` | Участие оплачено FanCoin | Турниры | inbox |
| `tournament.partner_added` | Добавлен партнёр в команду | Турниры | inbox |
| `tournament.payment_link` | Повторная ссылка оплаты турнира | Турниры | inbox |
| `tournament.payment_opened` | Открыта оплата турнира | Турниры | inbox |
| `tournament.payment_reminder` | Напоминание об оплате турнира | Турниры | inbox |
| `tournament.payment_requested` | Организатор запросил оплату | Турниры | inbox |
| `tournament.receipt` | Подтверждение оплаты турнира | Турниры | email |
| `tournament.registered` | Регистрация в турнир | Турниры | email |
| `tournament.removed` | Исключение из турнира | Турниры | inbox |
| `tournament.removed_refund` | Исключение с возвратом оплаты | Турниры | inbox |
| `tournament.reopened` | Возобновлена регистрация в турнир | Турниры | inbox |
| `tournament.solo_removed` | Исключена неполная команда | Турниры | inbox |
| `tournament.started` | Турнир начался | Турниры | inbox |
| `tournament.team_joined` | Присоединение к команде | Турниры | inbox |
| `tournament.unpaid_removed` | Исключение за неоплату | Турниры | email |
| `tournament.withdrawn` | Снятие с турнира | Турниры | inbox |
| `training.coach_decision` | Решение по заявке тренера | Тренировки | email |
| `training.enrollment` | Заявка на тренировку | Тренировки | email |

## Связи с существующими производителями

- `apps.chat.invitation_email:send_invitation_email` → `chat.invited`
- `apps.chat.invitations:_expand_campaign` → `chat.invited`
- `apps.chat.services:send_message` → `chat.reply`
- `apps.clubs.notifications:send_club_invite_email` → `club.invited`
- `apps.clubs.notifications:send_debtors_summary` → `club.debtors`
- `apps.clubs.notifications:send_fee_overdue_notifications` → `club.fee_overdue`
- `apps.clubs.notifications:send_fee_paid_notification` → `club.fee_paid`
- `apps.clubs.notifications:send_fee_reminder_notifications` → `club.fee_reminder`
- `apps.clubs.notifications:send_join_request_notification` → `club.join_requested`
- `apps.clubs.notifications:send_new_member_notification` → `club.new_member`
- `apps.clubs.notifications:send_subscription_expiring_notifications` → `club.subscription_expiring`
- `apps.clubs.notifications:send_tournament_auto_cancelled_to_club` → `club.tournament_cancelled`
- `apps.clubs.notifications:send_tournament_insufficient_to_club` → `club.tournament_insufficient`
- `apps.clubs.notifications:send_tournament_min_fill_to_club` → `club.tournament_min_fill`
- `apps.clubs.notifications:send_tournament_reminder` → `club.tournament_reminder`
- `apps.clubs.services:approve_club_join_request` → `club.join_approved`
- `apps.clubs.services:reject_club_join_request` → `club.join_rejected`
- `apps.clubs.views.public:join_request_create` → `club.join_requested`
- `apps.core.email_service:send_city_sparring_email` → `sparring.published`
- `apps.core.email_service:send_club_plan_receipt_email` → `club.plan_receipt`
- `apps.core.email_service:send_coach_application_decision_email` → `training.coach_decision`
- `apps.core.email_service:send_court_application_decision_email` → `court.application_decision`
- `apps.core.email_service:send_donation_thanks_email` → `payment.donation`
- `apps.core.email_service:send_email_verification` → `account.email_verification`
- `apps.core.email_service:send_match_deadline_changed_email` → `match.deadline_changed`
- `apps.core.email_service:send_match_deadline_overdue_admin_email` → `match.overdue_admin`
- `apps.core.email_service:send_match_deadline_reminder_email` → `match.deadline_reminder`
- `apps.core.email_service:send_new_tournament_email` → `tournament.created`
- `apps.core.email_service:send_password_changed_email` → `account.password_changed`
- `apps.core.email_service:send_phone_changed_email` → `account.phone_changed`
- `apps.core.email_service:send_subscription_autorenew_failed_email` → `subscription.renewal_failed`
- `apps.core.email_service:send_subscription_pre_debit_notification` → `subscription.pre_debit`
- `apps.core.email_service:send_tournament_cancelled_email` → `tournament.cancelled`
- `apps.core.email_service:send_tournament_completed_email` → `tournament.completed`
- `apps.core.email_service:send_tournament_entry_fancoin_confirmed_email` → `tournament.fancoin_paid`
- `apps.core.email_service:send_tournament_entry_receipt_email` → `tournament.receipt`
- `apps.core.email_service:send_tournament_opponent_match_closed_email` → `match.opponent_withdrawn`
- `apps.core.email_service:send_tournament_participant_withdrawn_email` → `tournament.withdrawn`
- `apps.core.email_service:send_tournament_postpayment_1h_reminder_email` → `tournament.payment_reminder`
- `apps.core.email_service:send_tournament_postpayment_opened_email` → `tournament.payment_opened`
- `apps.core.email_service:send_tournament_postpayment_resend_email` → `tournament.payment_link`
- `apps.core.email_service:send_training_enrollment_email` → `training.enrollment`
- `apps.core.email_service:send_welcome_email` → `account.welcome`
- `apps.core.support_notifications:send_admin_support_notification` → `support.admin_message`
- `apps.core.support_notifications:send_user_support_reply` → `support.reply`
- `apps.core.telegram_notify:_send_admin_email` → `platform.admin_message`
- `apps.notifications.auth_forms:send_mail` → `account.password_reset`
- `apps.player_ratings.notifications:notify_players_to_rate_match` → `match.rate_players`
- `apps.sparring.city_notify:notify_published_sparring` → `sparring.published`
- `apps.sparring.doubles_services:_notify_team_sparring_ready` → `sparring.team_ready`
- `apps.sparring.views:doubles_accept_join` → `sparring.doubles_joined`
- `apps.sparring.views:sparring_confirm_response` → `sparring.accepted`
- `apps.sparring.views:sparring_respond` → `sparring.response`
- `apps.subscriptions.utils:send_subscription_purchase_email` → `subscription.purchased`
- `apps.telegram_bot.notifications:_notify_new_tournament_lk_and_email` → `tournament.created`
- `apps.telegram_bot.notifications:notify_bracket_formed` → `tournament.bracket`
- `apps.telegram_bot.notifications:notify_doubles_join_accepted` → `sparring.doubles_joined`
- `apps.telegram_bot.notifications:notify_extension_approved` → `match.deadline_extended`
- `apps.telegram_bot.notifications:notify_match_created` → `match.created`
- `apps.telegram_bot.notifications:notify_match_deadline_reminder` → `match.deadline_reminder`
- `apps.telegram_bot.notifications:notify_postpayment_1h_reminder` → `tournament.payment_reminder`
- `apps.telegram_bot.notifications:notify_postpayment_opened` → `tournament.payment_opened`
- `apps.telegram_bot.notifications:notify_postpayment_removed` → `tournament.unpaid_removed`
- `apps.telegram_bot.notifications:notify_proposal_confirmed` → `match.result_confirmed`
- `apps.telegram_bot.notifications:notify_proposal_rejected` → `match.proposal_updated`
- `apps.telegram_bot.notifications:notify_result_confirmed_to_participants` → `match.result_confirmed`
- `apps.telegram_bot.notifications:notify_result_proposal` → `match.result_proposed`
- `apps.telegram_bot.notifications:notify_sparring_invitation_accepted_inviter` → `sparring.invitation_accepted`
- `apps.telegram_bot.notifications:notify_sparring_invitation_created` → `sparring.invited`
- `apps.telegram_bot.notifications:notify_sparring_response` → `sparring.response`
- `apps.telegram_bot.notifications:notify_sparring_response_accepted` → `sparring.accepted`
- `apps.telegram_bot.notifications:notify_subscription_expiring` → `subscription.expiring`
- `apps.telegram_bot.notifications:notify_team_sparring_ready` → `sparring.team_ready`
- `apps.telegram_bot.notifications:notify_tournament_registered` → `tournament.registered`
- `apps.telegram_bot.notifications:notify_tournament_removed_refund` → `tournament.removed_refund`
- `apps.telegram_bot.notifications:notify_tournament_start` → `tournament.started`
- `apps.telegram_bot.views:_handle_proposal_callback` → `match.proposal_updated`
- `apps.telegram_bot.views:_handle_result_type_callback` → `match.result_proposed`
- `apps.telegram_bot.views:_handle_sparring_callback` → `sparring.accepted`
- `apps.telegram_bot.views:user_bot_webhook` → `match.result_proposed`
- `apps.tournaments.cancel:_notify_cancelled` → `tournament.cancelled`
- `apps.tournaments.cancel:_notify_tournament_reopened` → `tournament.reopened`
- `apps.tournaments.completion_notify:_deliver_completion_notifications` → `tournament.completed`
- `apps.tournaments.fan:apply_overdue_walkover` → `match.overdue_result`
- `apps.tournaments.management.commands.auto_accept_stale_proposals:handle` → `match.auto_accepted`
- `apps.tournaments.management.commands.send_deadline_reminders:_notify_match_participants` → `match.deadline_reminder`
- `apps.tournaments.overdue:_notify_players_deadline_auto_rt` → `match.overdue_result`
- `apps.tournaments.overdue:notify_admins_match_deadline_overdue` → `match.overdue_admin`
- `apps.tournaments.postpayment:_remove_unpaid_players` → `tournament.unpaid_removed`
- `apps.tournaments.postpayment:_send_fancoin_settled_notification` → `tournament.fancoin_paid`
- `apps.tournaments.postpayment:_send_postpayment_opened_notification` → `tournament.payment_opened`
- `apps.tournaments.postpayment:resend_postpayment_payment_link` → `tournament.payment_link`
- `apps.tournaments.postpayment:send_1h_reminders` → `tournament.payment_reminder`
- `apps.tournaments.proposal_service:notify_participants_match_result_confirmed` → `match.result_confirmed`
- `apps.tournaments.round_robin:apply_overdue_walkover_round_robin` → `match.overdue_result`
- `apps.tournaments.signals:notify_telegram_match_created` → `match.created`
- `apps.tournaments.solo_teams:remove_solo_teams_from_doubles_tournament` → `tournament.solo_removed`
- `apps.tournaments.utils:notify_participants_match_deadline_changed` → `match.deadline_changed`
- `apps.tournaments.views:_do_add_partner` → `tournament.partner_added`
- `apps.tournaments.views:_do_join_team` → `tournament.team_joined`
- `apps.tournaments.views:_notify_removed_participant_no_refund` → `tournament.removed`
- `apps.tournaments.views:_notify_removed_participant_refund` → `tournament.removed_refund`
- `apps.tournaments.views:confirm_proposal` → `match.proposal_updated`
- `apps.tournaments.views:propose_result` → `match.result_proposed`
- `apps.tournaments.views:tournament_manage_send_payment_notification` → `tournament.payment_requested`
- `apps.tournaments.withdraw:_notify_opponents` → `match.opponent_withdrawn`
- `apps.tournaments.withdraw:_notify_withdrawn_user` → `tournament.withdrawn`

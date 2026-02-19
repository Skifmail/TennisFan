# Формирование парной игры 2×2 в спарринге

Предложение архитектуры для сценариев: автор с партнёром / без партнёра, отклик одиночкой / парой, выбор состава, подтверждение матча только при двух полных командах.

---

## 1. Модели Django (models.py)

Рекомендуется вынести в отдельное приложение `apps.doubles_sparring` или в `apps.sparring` в отдельный модуль `doubles_models.py`.

```python
from django.db import models
from apps.users.models import Player


class DoublesMatchRequestStatus(models.TextChoices):
    OPEN = "open", "Открыта"
    FORMING = "forming", "Формирование"
    READY = "ready", "Готова к подтверждению"
    CONFIRMED = "confirmed", "Подтверждена (матч создан)"
    CANCELLED = "cancelled", "Отменена"


class TeamSide(models.TextChoices):
    AUTHOR = "author", "Команда автора"
    OPPONENT = "opponent", "Команда соперников"


class JoinRequestStatus(models.TextChoices):
    PENDING = "pending", "Ожидает"
    ACCEPTED = "accepted", "Принят"
    REJECTED = "rejected", "Отклонён"
    CANCELLED = "cancelled", "Отменён заявителем"


class DoublesMatchRequest(models.Model):
    """
    Заявка на формирование парного матча 2×2.
    У автора всегда есть команда (author_team), команда соперников (opponent_team) создаётся при первом принятом отклике.
    """
    status = models.CharField(
        max_length=20,
        choices=DoublesMatchRequestStatus.choices,
        default=DoublesMatchRequestStatus.OPEN,
        db_index=True,
    )
    created_by = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_match_requests_created",
        verbose_name="Автор заявки",
    )
    # Опционально: город, предпочтения (можно вынести в отдельную модель или JSONField)
    city = models.CharField(max_length=100, blank=True)
    preferred_gender = models.CharField(max_length=20, blank=True)  # male/female/open/mixed
    is_friendly = models.BooleanField(default=False)
    description = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)

    # После подтверждения — ссылка на созданный Match (tournaments.Match)
    match = models.ForeignKey(
        "tournaments.Match",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="doubles_match_request",
    )

    class Meta:
        db_table = "doubles_sparring_match_request"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
        ]


class DoublesTeam(models.Model):
    """Команда в рамках заявки на парный матч. Не более 2 участников."""
    match_request = models.ForeignKey(
        DoublesMatchRequest,
        on_delete=models.CASCADE,
        related_name="teams",
        verbose_name="Заявка",
    )
    side = models.CharField(
        max_length=20,
        choices=TeamSide.choices,
        verbose_name="Сторона",
    )

    class Meta:
        db_table = "doubles_sparring_team"
        constraints = [
            models.UniqueConstraint(
                fields=["match_request", "side"],
                name="doubles_team_unique_side_per_request",
            ),
        ]
        indexes = [
            models.Index(fields=["match_request", "side"]),
        ]

    def __str__(self):
        return f"{self.get_side_display()} ({self.match_request_id})"

    @property
    def is_full(self):
        return self.members.count() >= 2


class DoublesTeamMember(models.Model):
    """Участник команды. В команде не более 2 человек."""
    team = models.ForeignKey(
        DoublesTeam,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name="Команда",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_team_memberships",
        verbose_name="Игрок",
    )
    is_captain = models.BooleanField(default=False, verbose_name="Капитан команды")

    class Meta:
        db_table = "doubles_sparring_team_member"
        constraints = [
            models.UniqueConstraint(
                fields=["team", "player"],
                name="doubles_team_member_unique_player_per_team",
            ),
        ]
        indexes = [
            models.Index(fields=["team"]),
            models.Index(fields=["player"]),
        ]


class DoublesJoinRequest(models.Model):
    """
    Заявка на присоединение к формируемому матчу.
    Может быть от одного игрока (solo) или от пары (pair).
    target_side — в какую команду хотят: author или opponent.
    """
    match_request = models.ForeignKey(
        DoublesMatchRequest,
        on_delete=models.CASCADE,
        related_name="join_requests",
        verbose_name="Заявка на матч",
    )
    target_side = models.CharField(
        max_length=20,
        choices=TeamSide.choices,
        verbose_name="Целевая команда",
    )
    status = models.CharField(
        max_length=20,
        choices=JoinRequestStatus.choices,
        default=JoinRequestStatus.PENDING,
        db_index=True,
    )
    created_by = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_join_requests_created",
        verbose_name="Кто подал заявку",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "doubles_sparring_join_request"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["match_request", "status"]),
        ]


class DoublesJoinRequestMember(models.Model):
    """Игроки в заявке на присоединение: 1 (solo) или 2 (pair)."""
    join_request = models.ForeignKey(
        DoublesJoinRequest,
        on_delete=models.CASCADE,
        related_name="members",
        verbose_name="Заявка на присоединение",
    )
    player = models.ForeignKey(
        Player,
        on_delete=models.CASCADE,
        related_name="doubles_join_request_memberships",
        verbose_name="Игрок",
    )
    order = models.PositiveSmallIntegerField(default=1)  # 1 или 2 для пары

    class Meta:
        db_table = "doubles_sparring_join_request_member"
        constraints = [
            models.UniqueConstraint(
                fields=["join_request", "player"],
                name="doubles_join_req_member_unique_player",
            ),
            models.UniqueConstraint(
                fields=["join_request", "order"],
                name="doubles_join_req_member_unique_order",
            ),
        ]
```

---

## 2. Enum статусов (сводка)

| Enum | Значения |
|------|----------|
| **DoublesMatchRequestStatus** | `open`, `forming`, `ready`, `confirmed`, `cancelled` |
| **TeamSide** | `author`, `opponent` |
| **JoinRequestStatus** | `pending`, `accepted`, `rejected`, `cancelled` |

---

## 3. ForeignKey и related_name

| Модель | Поле | related_name |
|--------|------|--------------|
| DoublesMatchRequest | created_by → Player | `doubles_match_requests_created` |
| DoublesMatchRequest | match → Match | `doubles_match_request` (обратная связь 1:1) |
| DoublesTeam | match_request → DoublesMatchRequest | `teams` |
| DoublesTeamMember | team → DoublesTeam | `members` |
| DoublesTeamMember | player → Player | `doubles_team_memberships` |
| DoublesJoinRequest | match_request → DoublesMatchRequest | `join_requests` |
| DoublesJoinRequest | created_by → Player | `doubles_join_requests_created` |
| DoublesJoinRequestMember | join_request → DoublesJoinRequest | `members` |
| DoublesJoinRequestMember | player → Player | `doubles_join_request_memberships` |

---

## 4. Unique constraints

- **DoublesTeam:** `(match_request, side)` — одна команда автора и одна команда соперников на заявку.
- **DoublesTeamMember:** `(team, player)` — игрок не может быть дважды в одной команде.
- **DoublesJoinRequestMember:** `(join_request, player)` и `(join_request, order)` — в одной заявке на присоединение игрок и порядок уникальны.

Ограничение «не более 2 членов в команде» — в сервисном слое (проверка перед добавлением) и при необходимости через `clean()` или сигнал.

---

## 5. Логика формирования команд

- При создании заявки (`create_doubles_request`) создаётся **DoublesMatchRequest** (status=open) и **DoublesTeam** (side=author) с одним участником — автором (is_captain=True). Команда opponent не создаётся.
- Автор может добавить партнёра в свою команду: создаётся **DoublesTeamMember** в author_team (если слотов < 2). Либо автор принимает **DoublesJoinRequest** с target_side=author и одним участником.
- Для соперников: при первом принятом отклике на сторону opponent создаётся **DoublesTeam** (side=opponent) и в неё добавляются игроки из **DoublesJoinRequest** (1 или 2 в зависимости от типа отклика). Дальнейшие принятые отклики на opponent заменяют состав (если автор «меняет состав») или добавляют по одному, пока не станет 2 человека — по бизнес-правилам (см. ниже).
- Переход **ready**: когда author_team и opponent_team обе имеют ровно по 2 участника.
- **confirm_match**: только при status=ready, атомарно создаётся Match (2×2), status → confirmed, заполняется `match_id`, `confirmed_at`.

---

## 6. Service-layer (примеры функций)

```python
from django.db import transaction
from django.utils import timezone

def create_doubles_request(
    *,
    created_by: Player,
    city: str = "",
    preferred_gender: str = "",
    is_friendly: bool = False,
    description: str = "",
    partner: Player | None = None,
) -> DoublesMatchRequest:
    with transaction.atomic():
        req = DoublesMatchRequest.objects.create(
            status=DoublesMatchRequestStatus.OPEN,
            created_by=created_by,
            city=city,
            preferred_gender=preferred_gender,
            is_friendly=is_friendly,
            description=description,
        )
        author_team = DoublesTeam.objects.create(
            match_request=req,
            side=TeamSide.AUTHOR,
        )
        DoublesTeamMember.objects.create(
            team=author_team,
            player=created_by,
            is_captain=True,
        )
        if partner and partner.id != created_by.id:
            DoublesTeamMember.objects.create(
                team=author_team,
                player=partner,
                is_captain=False,
            )
        # Статус можно оставить OPEN или перевести в FORMING
        if author_team.members.count() == 2:
            req.status = DoublesMatchRequestStatus.FORMING
        else:
            req.status = DoublesMatchRequestStatus.FORMING
        req.save(update_fields=["status"])
        return req


def join_team(
    *,
    match_request_id: int,
    join_request_id: int,
    accepted_by: Player,
) -> None:
    """Принять заявку на присоединение и добавить игроков в целевую команду. Автор — единственный, кто может принимать."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != accepted_by.id:
            raise PermissionError("Только автор заявки может принимать отклики")
        if req.status not in (DoublesMatchRequestStatus.OPEN, DoublesMatchRequestStatus.FORMING):
            raise ValueError("Заявка не в статусе приёма игроков")

        jr = DoublesJoinRequest.objects.select_for_update().get(
            pk=join_request_id,
            match_request=req,
            status=JoinRequestStatus.PENDING,
        )
        team = req.teams.filter(side=jr.target_side).first()
        if team is None:
            if jr.target_side != TeamSide.OPPONENT:
                raise ValueError("Команда автора должна существовать")
            team = DoublesTeam.objects.create(match_request=req, side=TeamSide.OPPONENT)
        members_to_add = list(jr.members.order_by("order").values_list("player_id", flat=True))
        if not members_to_add:
            raise ValueError("В заявке нет участников")
        current_count = team.members.count()
        if current_count + len(members_to_add) > 2:
            raise ValueError("В команде не может быть больше 2 человек")

        for player_id in members_to_add:
            DoublesTeamMember.objects.get_or_create(
                team=team,
                player_id=player_id,
                defaults={"is_captain": False},
            )
        jr.status = JoinRequestStatus.ACCEPTED
        jr.processed_at = timezone.now()
        jr.save(update_fields=["status", "processed_at", "updated_at"])

        _recompute_request_status(req)


def create_opponent_team(match_request: DoublesMatchRequest) -> DoublesTeam:
    """Создать команду соперников, если её ещё нет. Вызывать при первом принятии отклика на opponent."""
    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request.pk)
        opponent = req.teams.filter(side=TeamSide.OPPONENT).first()
        if opponent is None:
            opponent = DoublesTeam.objects.create(
                match_request=req,
                side=TeamSide.OPPONENT,
            )
        return opponent


def confirm_match(match_request_id: int, confirmed_by: Player) -> "Match":
    """Подтвердить состав и создать матч. Только автор, только при status=ready."""
    from apps.tournaments.models import Match
    from apps.sparring.services import _create_doubles_match_from_teams

    with transaction.atomic():
        req = DoublesMatchRequest.objects.select_for_update().get(pk=match_request_id)
        if req.created_by_id != confirmed_by.id:
            raise PermissionError("Только автор может подтвердить матч")
        if req.status != DoublesMatchRequestStatus.READY:
            raise ValueError("Матч можно подтвердить только при полных составах (ready)")

        author_team = req.teams.get(side=TeamSide.AUTHOR)
        opponent_team = req.teams.get(side=TeamSide.OPPONENT)
        if author_team.members.count() != 2 or opponent_team.members.count() != 2:
            raise ValueError("Обе команды должны быть по 2 человека")

        match = _create_doubles_match_from_teams(
            author_team=author_team,
            opponent_team=opponent_team,
            is_friendly=req.is_friendly,
            request_created_at=req.created_at,
        )
        req.status = DoublesMatchRequestStatus.CONFIRMED
        req.confirmed_at = timezone.now()
        req.match = match
        req.save(update_fields=["status", "confirmed_at", "match"])
        return match


def _recompute_request_status(req: DoublesMatchRequest) -> None:
    author_team = req.teams.filter(side=TeamSide.AUTHOR).first()
    opponent_team = req.teams.filter(side=TeamSide.OPPONENT).first()
    a_full = author_team and author_team.members.count() == 2
    o_full = opponent_team and opponent_team.members.count() == 2
    if a_full and o_full:
        req.status = DoublesMatchRequestStatus.READY
    else:
        req.status = DoublesMatchRequestStatus.FORMING
    req.save(update_fields=["status"])
```

Дополнительно нужны:

- **create_join_request(match_request_id, created_by, target_side, player_ids: list[Player])** — создаёт DoublesJoinRequest и DoublesJoinRequestMember (1 или 2 игрока). Проверки: слоты в целевой команде, нет дублирования игрока в команде, игрок не автор и не уже в другой команде этой заявки.
- **reject_join_request**, **cancel_join_request** (игрок отменяет свою заявку).
- **remove_team_member** (автор удаляет участника из своей команды до confirm; для opponent — по правилам продукта).

---

## 7. Переходы состояний (state machine)

| Из \ В | open | forming | ready | confirmed | cancelled |
|--------|------|---------|-------|-----------|-----------|
| **open** | — | автор добавил себя/партнёра или создал заявку | — | — | автор отменил |
| **forming** | — | добавление/удаление игроков | обе команды по 2 | — | автор отменил |
| **ready** | — | автор убрал кого-то из команды | — | автор подтвердил | автор отменил |
| **confirmed** | — | — | — | — | — |
| **cancelled** | — | — | — | — | — |

Правила:

- **open → forming:** при создании заявки с автором (и опционально партнёром) или при первом принятом отклике.
- **forming → ready:** когда в обеих командах ровно по 2 участника.
- **ready → forming:** если автор удалил участника из любой команды.
- **ready → confirmed:** только через `confirm_match()`.
- В **cancelled** из любого активного статуса может перевести автор (или система по таймауту, если будет такой сценарий).

---

## 8. Индексы

- **DoublesMatchRequest:** `status`, `(status, created_at)` — списки и фильтры.
- **DoublesTeam:** `(match_request, side)` — уникальность и выбор команды по заявке.
- **DoublesTeamMember:** `team`, `player` — проверки состава и «игрок уже в команде».
- **DoublesJoinRequest:** `match_request`, `(match_request, status)` — отображение откликов и фильтр по pending.
- **DoublesJoinRequestMember:** по необходимости по `join_request`, `player` (уже есть через FK и unique).

---

## 9. Проверки бизнес-логики

- Только автор заявки может: принимать/отклонять JoinRequest, удалять участников из обеих команд (или только из своей — решить продуктово), подтверждать матч.
- В одну команду не больше 2 человек; при принятии JoinRequest проверять `current_count + len(members_to_add) <= 2`.
- Один игрок не может быть в двух командах одной заявки и не может подать два активных JoinRequest в одну заявку (или разрешить только один pending на игрока — по правилам).
- При принятии отклика «пара» на opponent: оба игрока добавляются в opponent_team; слоты должны быть 0 или 2 (не принимать пару, если уже есть 1 участник, или сначала «очистить» команду — решить продуктово).
- **Race condition:** все операции, меняющие состав команд или статус заявки, выполняются внутри `transaction.atomic()` с `select_for_update()` на `DoublesMatchRequest` (и при необходимости на `DoublesJoinRequest`).
- Запрет подтверждения при status != ready и при неполных составах.
- Отмена: только автор отменяет заявку; игрок может отменить только свой JoinRequest (status → cancelled).

---

## 10. Фронтенд / API — пример структуры ответа

Удобно отдавать одну заявку с вложенными командами и откликами, чтобы на фронте рисовать «команда автора», «команда соперников», «ожидающие отклики».

Пример JSON для страницы заявки (или эндпоинта вида `/api/doubles-sparring/requests/<id>/`):

```json
{
  "id": 1,
  "status": "forming",
  "created_by": {
    "id": 10,
    "display_name": "Иван Иванов",
    "avatar_url": "/media/avatars/...",
    "ntrp_level": "4.0",
    "city": "Москва"
  },
  "city": "Москва",
  "preferred_gender": "open",
  "is_friendly": false,
  "description": "Ищем пару на субботу",
  "created_at": "2026-02-17T12:00:00Z",
  "teams": [
    {
      "side": "author",
      "is_full": true,
      "members": [
        {
          "player_id": 10,
          "display_name": "Иван Иванов",
          "avatar_url": "/media/...",
          "is_captain": true,
          "ntrp_level": "4.0"
        },
        {
          "player_id": 11,
          "display_name": "Пётр Петров",
          "avatar_url": null,
          "is_captain": false,
          "ntrp_level": "3.5"
        }
      ]
    },
    {
      "side": "opponent",
      "is_full": false,
      "members": [
        {
          "player_id": 12,
          "display_name": "Сидор Сидоров",
          "avatar_url": "/media/...",
          "is_captain": false,
          "ntrp_level": "4.0"
        }
      ]
    }
  ],
  "join_requests": [
    {
      "id": 101,
      "target_side": "opponent",
      "status": "pending",
      "created_by_id": 13,
      "created_at": "2026-02-17T13:00:00Z",
      "members": [
        {
          "player_id": 13,
          "display_name": "Алексей Алексеев",
          "avatar_url": null,
          "ntrp_level": "3.5"
        }
      ]
    },
    {
      "id": 102,
      "target_side": "opponent",
      "status": "pending",
      "created_by_id": 14,
      "created_at": "2026-02-17T14:00:00Z",
      "members": [
        { "player_id": 14, "display_name": "Борис Борисов", "avatar_url": null, "ntrp_level": "4.0" },
        { "player_id": 15, "display_name": "Виктор Викторов", "avatar_url": null, "ntrp_level": "3.5" }
      ]
    }
  ],
  "can_confirm": false,
  "can_edit_teams": true
}
```

Интеграция с текущей логикой:

- Для **match_type=doubles** на странице «Создать заявку на спарринг» вести пользователя в новый флоу «Парный матч 2×2» (отдельная страница или шаги), где создаётся **DoublesMatchRequest** и далее работа идёт через команды и JoinRequest.
- Список заявок можно объединять: одиночные — текущий SparringRequest; парные 2×2 — список DoublesMatchRequest с status in (open, forming, ready).
- После **confirm_match** создаётся `Match` (match_type=SPARRING, без tournament), с командной парой можно связать через отдельную модель (например SparringDoublesMatch с team1/team2 как связями к нашим DoublesTeam или к паре player1/player2). Текущая модель Match поддерживает team1/team2 (TournamentTeam); для спарринга потребуется либо отдельная сущность «спарринг-команда» (2 игрока), либо хранить в Match player1/player2 как пару и добавить partner1/partner2 (как обсуждалось ранее). В рамках этого документа за создание матча отвечает `_create_doubles_match_from_teams`, который нужно реализовать с учётом выбранной схемы хранения пар в Match.

---

Этот документ можно использовать как спецификацию для реализации модуля формирования парной игры 2×2 в спарринге и для интеграции с текущими «Мои матчи» и созданием Match.

"""Миграция одиночной связи не меняет данные, столбец и уникальные ограничения."""

from __future__ import annotations

from importlib import import_module
from typing import Final

import pytest
from django.db import IntegrityError, connection, migrations, models, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test.utils import CaptureQueriesContext

from apps.sparring.models import SparringRequest, SparringResponse
from apps.tournaments.models import Match
from tests.support.factories import make_player

LEGACY: Final = "0081_manual_stage_tours"
CURRENT: Final = "0082_sparring_response_one_to_one"

pytestmark = pytest.mark.django_db(transaction=True)


def test_upgrade_and_reverse_preserve_schema_unique_constraint_and_records() -> None:
    """Переход ForeignKey → OneToOne и обратно меняет только ORM-состояние."""
    author = make_player(email_suffix="relation-migration-author")
    opponent = make_player(email_suffix="relation-migration-opponent")
    request = SparringRequest.objects.create(player=author, city="Раменское")
    response = SparringResponse.objects.create(
        sparring_request=request,
        respondent=opponent,
        contact_method="telegram",
        status=SparringResponse.ResponseStatus.ACCEPTED,
    )
    match = Match.objects.create(
        match_type=Match.MatchType.SPARRING,
        player1=author,
        player2=opponent,
        sparring_response=response,
    )
    for _ in range(2):
        Match.objects.create(
            match_type=Match.MatchType.SPARRING, player1=author, player2=opponent
        )
    before_matches = list(Match.objects.order_by("pk").values())
    before_responses = list(SparringResponse.objects.order_by("pk").values())
    table = Match._meta.db_table
    with connection.cursor() as cursor:
        before_columns = connection.introspection.get_table_description(cursor, table)
        before_constraints = connection.introspection.get_constraints(cursor, table)
    assert any(
        constraint["unique"] and constraint["columns"] == ["sparring_response_id"]
        for constraint in before_constraints.values()
    )
    migration = import_module(f"apps.tournaments.migrations.{CURRENT}").Migration(
        CURRENT, "tournaments"
    )
    assert len(migration.operations) == 1
    operation = migration.operations[0]
    assert isinstance(operation, migrations.SeparateDatabaseAndState)
    assert operation.database_operations == []

    executor = MigrationExecutor(connection)
    latest_targets = executor.loader.graph.leaf_nodes()
    try:
        executor.migrate([("tournaments", LEGACY)])
        old_apps = executor.loader.project_state([("tournaments", LEGACY)]).apps
        old_field = old_apps.get_model("tournaments", "Match")._meta.get_field(
            "sparring_response"
        )
        assert isinstance(old_field, models.ForeignKey)
        assert not isinstance(old_field, models.OneToOneField)
        old_response = old_apps.get_model("sparring", "SparringResponse").objects.get(
            pk=response.pk
        )
        assert old_response.matches.get().pk == match.pk

        for target in (CURRENT, LEGACY):
            executor = MigrationExecutor(connection)
            with CaptureQueriesContext(connection) as queries:
                executor.migrate([("tournaments", target)])
            assert not any(
                query["sql"]
                .lstrip()
                .upper()
                .startswith(("ALTER ", "CREATE ", "DROP ", "UPDATE "))
                for query in queries.captured_queries
            )
            apps = executor.loader.project_state([("tournaments", target)]).apps
            state_match = apps.get_model("tournaments", "Match")
            field = state_match._meta.get_field("sparring_response")
            assert isinstance(field, models.OneToOneField) is (target == CURRENT)
            assert field.unique and field.null and field.blank
            if target == CURRENT:
                upgraded_response = apps.get_model(
                    "sparring", "SparringResponse"
                ).objects.get(pk=response.pk)
                assert upgraded_response.match.pk == match.pk
            assert list(Match.objects.order_by("pk").values()) == before_matches
            assert (
                list(SparringResponse.objects.order_by("pk").values())
                == before_responses
            )
            with connection.cursor() as cursor:
                assert (
                    connection.introspection.get_table_description(cursor, table)
                    == before_columns
                )
                assert (
                    connection.introspection.get_constraints(cursor, table)
                    == before_constraints
                )
            with pytest.raises(IntegrityError), transaction.atomic():
                state_match.objects.create(
                    match_type=Match.MatchType.SPARRING,
                    player1_id=author.pk,
                    player2_id=opponent.pk,
                    sparring_response_id=response.pk,
                )
    finally:
        MigrationExecutor(connection).migrate(latest_targets)

"""Тестовый PostgreSQL должен совпадать с production по поддержке Unicode."""

import pytest
from django.db import connection


@pytest.mark.django_db
def test_postgres_when_test_database_created_should_support_utf8_and_cyrillic_casefold() -> (
    None
):
    """SQL_ASCII/C не должен маскировать ограничения длины и ломать поиск."""
    if connection.vendor != "postgresql":
        pytest.skip("Проверяется контракт PostgreSQL, а не SQLite")
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_encoding")
        assert cursor.fetchone() == ("UTF8",)
        cursor.execute(
            "SELECT lower(%s), upper(%s), length(%s)", ["МОСКВА", "москва", "Москва"]
        )
        assert cursor.fetchone() == ("москва", "МОСКВА", 6)

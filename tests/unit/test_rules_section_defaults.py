"""Тесты дефолтного контента разделов правил и формы админки."""

from __future__ import annotations

from django.test import TestCase

from apps.content.forms import RulesSectionAdminForm
from apps.content.models import RulesSection
from apps.content.rules_defaults import (
    RULES_SECTION_TEMPLATES,
    get_default_rules_body,
)


class RulesDefaultsTests(TestCase):
    """Проверка загрузки HTML из шаблонов-фолбэков."""

    def test_all_mapped_templates_are_readable(self) -> None:
        """Для каждого slug в маппинге шаблон существует и не пустой."""
        for slug in RULES_SECTION_TEMPLATES:
            body = get_default_rules_body(slug)
            self.assertTrue(
                body,
                msg=f"Пустой или отсутствующий шаблон для slug={slug!r}",
            )
            self.assertIn("<", body)

    def test_unknown_slug_returns_empty(self) -> None:
        """Неизвестный slug даёт пустую строку."""
        self.assertEqual(get_default_rules_body("unknown_section"), "")


class RulesSectionAdminFormTests(TestCase):
    """Форма админки подставляет дефолт при пустом body."""

    def test_empty_body_gets_default_in_initial(self) -> None:
        """При пустом body в initial попадает HTML из шаблона."""
        section, _ = RulesSection.objects.update_or_create(
            slug="rules_round_robin",
            defaults={"title": "Круговой турнир", "body": ""},
        )
        form = RulesSectionAdminForm(instance=section)
        default = get_default_rules_body("rules_round_robin")
        self.assertEqual(form.initial.get("body"), default)
        self.assertEqual(form.fields["body"].initial, default)

    def test_non_empty_body_is_not_overridden(self) -> None:
        """Заполненный body не подменяется дефолтом."""
        custom = "<p>Кастомный текст правил</p>"
        section, _ = RulesSection.objects.update_or_create(
            slug="rules_fan",
            defaults={"title": "Одноэтапная сетка", "body": custom},
        )
        form = RulesSectionAdminForm(instance=section)
        self.assertEqual(form.initial.get("body"), custom)
        self.assertNotEqual(
            form.initial.get("body"),
            get_default_rules_body("rules_fan"),
        )

"""Матрица обязательных подтверждений и добровольного раскрытия профиля."""

from itertools import product

import pytest

from apps.chat.consent import ChatConsentForm


@pytest.mark.parametrize(
    ("personal_data", "rules", "disclosure", "show_name", "show_avatar"),
    list(product((False, True), repeat=5)),
)
def test_form_when_choices_submitted_should_require_two_consents_and_conditional_category(
    personal_data: bool,
    rules: bool,
    disclosure: bool,
    show_name: bool,
    show_avatar: bool,
) -> None:
    """Форма разрешает вход без допов, но не допускает пустое или скрытое согласие.

    Args:
        personal_data: Подтверждение обработки данных для работы чата.
        rules: Принятие правил.
        disclosure: Добровольное согласие на показ профиля.
        show_name: Выбор имени и фамилии.
        show_avatar: Выбор фотографии.
    """
    choices = {
        "personal_data": personal_data,
        "rules": rules,
        "disclosure": disclosure,
        "show_name": show_name,
        "show_avatar": show_avatar,
    }
    data = {name: "on" for name, selected in choices.items() if selected}
    form = ChatConsentForm(data={"token": "signed-context-tested-integration", **data})
    category_selected = show_name or show_avatar
    expected = personal_data and rules and (disclosure == category_selected)
    assert form.is_valid() is expected, form.errors


@pytest.mark.parametrize("value", ["false", "0", "true", "off"])
def test_form_when_optional_checkbox_forged_should_reject_unknown_value(
    value: str,
) -> None:
    """Неявные значения не считаются добровольным подтверждением.

    Args:
        value: Нестандартное значение чекбокса.
    """
    form = ChatConsentForm(
        data={
            "token": "signed-context-tested-integration",
            "personal_data": "on",
            "rules": "on",
            "disclosure": value,
        }
    )
    assert not form.is_valid()


def test_form_when_conditions_without_disclosure_should_reject_hidden_input() -> None:
    """Особые условия профиля не отправляются без добровольного выбора данных."""
    form = ChatConsentForm(
        data={
            "token": "signed-context-tested-integration",
            "personal_data": "on",
            "rules": "on",
            "conditions": "Запрет рекламы",
        }
    )
    assert not form.is_valid()

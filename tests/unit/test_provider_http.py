"""TLS-клиент для внешних API не предлагает пост-квантовый обмен."""

from pytest_mock import MockerFixture

from apps.core.http import ECDH_CURVE, ProviderTLSAdapter, provider_session


def test_provider_session_should_mount_tls_adapter() -> None:
    """Сессия использует адаптер для HTTPS, а не стандартный пул requests."""
    session = provider_session()
    assert isinstance(
        session.get_adapter("https://api.onesignal.com"), ProviderTLSAdapter
    )


def test_adapter_should_set_explicit_ecdh_curve(mocker: MockerFixture) -> None:
    """Явная кривая убирает пост-квантовый ClientHello, который теряется в сети."""
    context = mocker.Mock()
    mocker.patch("apps.core.http.ssl.create_default_context", return_value=context)
    ProviderTLSAdapter()
    context.set_ecdh_curve.assert_called_once_with(ECDH_CURVE)


def test_adapter_should_pass_context_to_pool(mocker: MockerFixture) -> None:
    """Контекст передаётся в пул соединений, иначе настройка не применяется."""
    context = mocker.Mock()
    mocker.patch("apps.core.http.ssl.create_default_context", return_value=context)
    adapter = ProviderTLSAdapter()
    assert adapter.poolmanager.connection_pool_kw["ssl_context"] is context

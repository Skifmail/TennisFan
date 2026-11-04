"""Единый каталог документов, ссылки футера и прежние адреса детской лиги."""

import pytest
from django.test import Client
from django.urls import reverse

from apps.juniors.legal import TITLES
from apps.juniors.models import LegalRelease
from apps.legal.views import DOCS

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def legal_settings(settings) -> None:
    settings.SECURE_SSL_REDIRECT = False
    settings.JUNIOR_LEAGUE_ENABLED = False


def test_catalog_contains_every_document_and_does_not_activate_releases(
    client: Client,
) -> None:
    response = client.get(reverse("legal_index"))
    assert response.status_code == 200
    html = response.content.decode()
    assert 'id="junior-documents"' in html
    for slug, meta in DOCS.items():
        url = reverse("legal_" + slug.replace("-", "_"))
        assert f'href="{url}"' in html
        document = client.get(url)
        assert document.status_code == 200
        assert meta["title"] in document.content.decode()
        assert "legal/document.html" in [
            template.name for template in document.templates
        ]
    assert not LegalRelease.objects.exists()


def test_footer_links_are_ordered_and_old_catalog_redirects(client: Client) -> None:
    html = client.get(reverse("legal_index")).content.decode()
    footer = html.split("<footer", 1)[1]
    assert (
        footer.index("Обработка персональных данных")
        < footer.index("Документы детской лиги")
        < footer.index("Все документы")
    )
    assert 'href="/legal/#junior-documents"' in footer
    response = client.get(reverse("juniors:documents"))
    assert response.status_code == 301
    assert response.url == "/legal/#junior-documents"
    for slug in TITLES:
        response = client.get(reverse("juniors:document", args=[slug]))
        assert response.status_code == 301
        assert response.url == reverse("legal_junior_" + slug.replace("-", "_"))
    assert client.get("/kids/documents/unknown/").status_code == 404


def test_league_has_no_document_link_between_banner_and_content(
    client: Client, settings
) -> None:
    settings.JUNIOR_LEAGUE_ENABLED = True
    html = client.get(reverse("juniors:home")).content.decode()
    before_footer = html.split("<footer", 1)[0]
    assert "Документы детской лиги" not in before_footer


def test_internal_guide_is_not_public(client: Client) -> None:
    from django.contrib.staticfiles import finders

    html = client.get(reverse("legal_index")).content.decode()
    assert "Инструкция организатору и проверяющему" not in html
    assert "junior-organizer" not in DOCS
    for url in ("/kids/documents/organizer/", "/legal/junior-organizer/"):
        assert client.get(url).status_code == 404
    assert finders.find("documents/juniors/organizer.txt") is None

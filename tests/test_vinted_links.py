"""I link API di Vinted devono aprire l'articolo sul sito, non su localhost."""

from types import SimpleNamespace

import pytest

from offerte.scrapers import vinted


def scrape_with_url(monkeypatch, url):
    item = SimpleNamespace(title="Notebook Lenovo", price=150, url=url)
    scraper = SimpleNamespace(search=lambda params: [item])
    monkeypatch.setattr(vinted, "VintedScraper", lambda base: scraper)
    return vinted.scrape_vinted("notebook", 0, 500, ["notebook"])


@pytest.mark.parametrize(
    "url, expected",
    [
        (
            "/items/10485687266-notebook-lenovo",
            "https://www.vinted.it/items/10485687266-notebook-lenovo",
        ),
        ("https://www.vinted.it/items/123-notebook", "https://www.vinted.it/items/123-notebook"),
        ("https://vinted.it/items/123-notebook", "https://vinted.it/items/123-notebook"),
    ],
)
def test_vinted_item_links_are_absolute_and_keep_valid_legacy_urls(monkeypatch, url, expected):
    result = scrape_with_url(monkeypatch, url)
    assert len(result) == 1
    assert result[0].link == expected


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://alien.example/items/123",
        "https://www.vinted.it.alien.example/items/123",
        "//alien.example/items/123",
        "//www.vinted.it/items/123",
        "http://www.vinted.it/items/123",
        "https://alien.example@www.vinted.it/items/123",
        "https://www.vinted.it:444/items/123",
        "https://www.vinted.it/\\alien.example/items/123",
        "https://www.vinted.it\n.alien.example/items/123",
        "/member/login",
    ],
)
def test_vinted_invalid_or_untrusted_item_links_are_not_surfaced(monkeypatch, url):
    assert scrape_with_url(monkeypatch, url) == []

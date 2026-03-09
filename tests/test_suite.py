import math
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Page, expect

from offerte_tech import Offerta, _deduplica, _is_spec_token, cerca_offerte, fetch_specs_ai, is_relevant, parse_price


def test_parse_price_europeo() -> None:
    assert parse_price("1.299,00") == 1299.0


def test_parse_price_semplice() -> None:
    assert parse_price("€ 89,99") == 89.99


def test_parse_price_vuoto() -> None:
    assert math.isinf(parse_price(""))


def test_parse_price_range() -> None:
    assert parse_price("100,00 - 200,00") == 100.0


def test_prezzo_min_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("offerte_tech.scrape_trovaprezzi", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "offerte_tech.scrape_amazon",
        lambda *args, **kwargs: [
            Offerta(nome="Apple iPhone 17 128GB", prezzo=150.0, negozio="A", link="https://example.com/1"),
            Offerta(nome="Apple iPhone 17 256GB", prezzo=320.0, negozio="B", link="https://example.com/2"),
        ],
    )
    monkeypatch.setattr("offerte_tech.scrape_ebay", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.scrape_vinted", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.fetch_specs_ai", lambda offerte, categoria, cerebras_client: offerte)

    risultati = cerca_offerte(
        query="iphone 17",
        prezzo_min=200,
        budget_max=500,
        top_n=10,
        fonti=["amazon"],
        categoria="tech",
        cerebras_client=None,
        app_id="",
        cert_id="",
    )

    assert len(risultati) == 1
    assert risultati[0].prezzo == 320.0


def test_deduplicazione() -> None:
    offerte = [
        Offerta(nome="Apple iPhone 17 128GB", prezzo=800.0, negozio="A", link="https://example.com/1"),
        Offerta(nome="Apple iPhone 17 128GB", prezzo=790.0, negozio="B", link="https://example.com/2"),
    ]
    deduplicate = _deduplica(offerte)
    assert len(deduplicate) == 1
    assert deduplicate[0].prezzo == 790.0


def test_is_relevant_falso_positivo_ios() -> None:
    assert is_relevant("Apple iPhone 15 iOS 17 5G", ["iphone", "17"]) is False


def test_is_relevant_corretto() -> None:
    assert is_relevant("Apple iPhone 17 256GB", ["iphone", "17"]) is True


def test_fetch_specs_ai_tech(cerebras_mock: MagicMock) -> None:
    offerte = [Offerta(nome="Apple iPhone 17 256GB", prezzo=999.0, negozio="Test", link="https://example.com")]
    risultati = fetch_specs_ai(offerte, "tech", cerebras_mock)
    assert risultati[0].specs["display"] == "6.1 OLED"
    assert risultati[0].specs["processore"] == "A19"
    assert risultati[0].specs["ram"] == "8 GB"
    assert risultati[0].specs["storage"] == "256 GB"


def test_fetch_specs_abbigliamento() -> None:
    offerte = [Offerta(nome="Nike Felpa Oversize Cotone Donna M", prezzo=59.0, negozio="Test", link="https://example.com")]
    risultati = fetch_specs_ai(offerte, "abbigliamento", None)
    assert risultati[0].specs["brand"] == "Nike"
    assert risultati[0].specs["taglia"] == "M"
    assert risultati[0].specs["genere"] == "donna"


def test_is_spec_token() -> None:
    assert _is_spec_token("16gb") is True
    assert _is_spec_token("512gb") is True
    assert _is_spec_token("1tb") is True
    assert _is_spec_token("ram") is True
    assert _is_spec_token("ssd") is True
    assert _is_spec_token("notebook") is False
    assert _is_spec_token("14") is False
    assert _is_spec_token("iphone") is False


def test_is_relevant_lenient_specs() -> None:
    """Con strict_specs=False, i token spec (16gb, ram) vengono ignorati nel matching."""
    assert is_relevant("Lenovo IdeaPad Laptop 14 pollici", ["notebook", "14", "16gb", "ram"], strict_specs=False) is True


def test_is_relevant_strict_specs() -> None:
    """Con strict_specs=True (default), tutti i token devono matchare."""
    assert is_relevant("Lenovo IdeaPad Laptop 14 pollici", ["notebook", "14", "16gb", "ram"], strict_specs=True) is False


def test_is_relevant_product_alias() -> None:
    """Notebook/laptop sono alias tra loro."""
    assert is_relevant("Lenovo IdeaPad Laptop 14 pollici 16GB RAM", ["notebook", "14", "16gb", "ram"]) is True


def test_spec_aware_sorting(monkeypatch: pytest.MonkeyPatch) -> None:
    """I prodotti con spec tokens nel titolo vengono ordinati prima."""
    monkeypatch.setattr("offerte_tech.scrape_trovaprezzi", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        "offerte_tech.scrape_amazon",
        lambda *args, **kwargs: [
            Offerta(nome="HP Laptop 14 pollici Intel i5", prezzo=600.0, negozio="Amazon", link="https://example.com/1"),
            Offerta(nome="HP Laptop 14 pollici 16GB RAM SSD", prezzo=700.0, negozio="Amazon", link="https://example.com/2"),
        ],
    )
    monkeypatch.setattr("offerte_tech.scrape_ebay", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.scrape_vinted", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.fetch_specs_ai", lambda offerte, categoria, cerebras_client: offerte)

    risultati = cerca_offerte(
        query="notebook 14 pollici 16gb ram",
        budget_max=800,
        top_n=10,
        fonti=["amazon"],
        categoria="tech",
        cerebras_client=None,
        app_id="",
        cert_id="",
    )

    assert len(risultati) == 2
    # Il prodotto con 16GB nel titolo deve essere primo nonostante costi di piu
    assert "16GB" in risultati[0].nome


def _open_home(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.wait_for_load_state("networkidle")


def _send_chat(page: Page, placeholder: str, text: str) -> None:
    chat = page.get_by_placeholder(placeholder)
    chat.fill(text)
    chat.press("Enter")
    page.wait_for_load_state("networkidle")


def _assistant_messages(page: Page):
    return page.locator("[data-testid='stChatMessage']")


def test_chat_prericerca_risponde(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    initial_count = _assistant_messages(page).count()
    _send_chat(page, "Descrivi prodotto, uso, vincoli e preferenze", "cerco uno smartphone")
    expect(_assistant_messages(page)).to_have_count(initial_count + 2)
    expect(_assistant_messages(page).last).to_contain_text("budget")


def test_chat_no_ripetizioni(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    initial_count = _assistant_messages(page).count()
    _send_chat(page, "Descrivi prodotto, uso, vincoli e preferenze", "cerco uno smartphone")
    expect(_assistant_messages(page)).to_have_count(initial_count + 2, timeout=10000)
    first_reply = _assistant_messages(page).last.text_content() or ""
    _send_chat(page, "Descrivi prodotto, uso, vincoli e preferenze", "massimo 800 euro")
    expect(_assistant_messages(page)).to_have_count(initial_count + 4, timeout=10000)
    second_reply = _assistant_messages(page).last.text_content() or ""
    assert first_reply != second_reply


def test_range_prezzo_sync(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    min_input = page.get_by_role("spinbutton", name="Prezzo minimo (€)")
    min_input.fill("200")
    min_input.press("Tab")
    expect(page.get_by_text("Range attivo: 200€ - 800€")).to_be_visible()
    expect(page.get_by_role("slider").first).to_have_attribute("aria-valuenow", "200")


def test_avvia_ricerca(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    page.get_by_label("Query prodotto").fill("iPhone 17")
    page.get_by_label("Query prodotto").press("Tab")
    expect(page.get_by_role("button", name="Cerca offerte")).to_be_enabled(timeout=8000)
    page.get_by_role("button", name="Cerca offerte").click()
    expect(page.get_by_text("offerte trovate per")).to_be_visible(timeout=30000)
    expect(page.locator("[data-testid='stDataFrame']")).to_be_visible(timeout=30000)


def test_chat_finale_risponde(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    page.get_by_label("Query prodotto").fill("iPhone 17")
    page.get_by_label("Query prodotto").press("Tab")
    expect(page.get_by_role("button", name="Cerca offerte")).to_be_enabled(timeout=8000)
    page.get_by_role("button", name="Cerca offerte").click()
    expect(page.get_by_text("offerte trovate per")).to_be_visible(timeout=30000)
    expect(page.locator("[data-testid='stDataFrame']")).to_be_visible(timeout=30000)
    _send_chat(page, "Esempio: quale mi consigli per uso quotidiano?", "quale mi consigli?")
    last_message = page.locator("[data-testid='stChatMessage']").last
    expect(last_message).to_contain_text("Ti consiglio", timeout=30000)

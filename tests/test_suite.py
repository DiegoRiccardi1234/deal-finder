import math
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import Page, expect

from offerte_tech import (
    Offerta,
    _deduplica,
    _is_spec_token,
    cerca_offerte,
    fetch_specs_ai,
    is_relevant,
    parse_comparison_query,
    parse_price,
    scrape_euronics,
    scrape_mediaworld,
    scrape_unieuro,
)


def test_parse_price_europeo() -> None:
    assert parse_price("1.299,00") == 1299.0


def test_parse_price_semplice() -> None:
    assert parse_price("€ 89,99") == 89.99


def test_parse_price_vuoto() -> None:
    assert math.isinf(parse_price(""))


def test_parse_price_range() -> None:
    assert parse_price("100,00 - 200,00") == 100.0


def _make_monkeypatch_cerca(monkeypatch: pytest.MonkeyPatch, amazon_results: list[Offerta] | None = None) -> None:
    """Helper: patcha tutte le fonti di cerca_offerte."""
    monkeypatch.setattr("offerte_tech.scrape_amazon", lambda *a, **kw: amazon_results or [])
    monkeypatch.setattr("offerte_tech.scrape_ebay", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_vinted", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_euronics", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_unieuro", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_mediaworld", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.fetch_specs_ai", lambda offerte, categoria, cerebras_client: offerte)


def test_prezzo_min_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _make_monkeypatch_cerca(
        monkeypatch,
        amazon_results=[
            Offerta(nome="Apple iPhone 17 128GB", prezzo=150.0, negozio="A", link="https://example.com/1"),
            Offerta(nome="Apple iPhone 17 256GB", prezzo=320.0, negozio="B", link="https://example.com/2"),
        ],
    )

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
    monkeypatch.setattr(
        "offerte_tech.scrape_amazon",
        lambda *args, **kwargs: [
            Offerta(nome="HP Laptop 14 pollici Intel i5", prezzo=600.0, negozio="Amazon", link="https://example.com/1"),
            Offerta(nome="HP Laptop 14 pollici 16GB RAM SSD", prezzo=700.0, negozio="Amazon", link="https://example.com/2"),
        ],
    )
    monkeypatch.setattr("offerte_tech.scrape_ebay", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.scrape_vinted", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.scrape_euronics", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.scrape_unieuro", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte_tech.scrape_mediaworld", lambda *args, **kwargs: [])
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


def test_nuove_fonti_vuote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica che le nuove fonti (euronics/unieuro/mediaworld) restituiscano lista vuota su errore o blocco."""
    from unittest.mock import patch
    import requests

    for scraper in (scrape_euronics, scrape_unieuro, scrape_mediaworld):
        with patch("offerte_tech.fetch_with_retry") as mock_fetch:
            mock_fetch.side_effect = requests.ConnectionError("test")
            result = scraper("notebook", 0, 1000, ["notebook"])
            assert result == [], f"{scraper.__name__} doveva restituire [] su ConnectionError"


def test_cerca_offerte_nuove_fonti_integrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica che cerca_offerte aggreghi i risultati da euronics/unieuro/mediaworld."""
    monkeypatch.setattr("offerte_tech.scrape_amazon", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_ebay", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_vinted", lambda *a, **kw: [])
    monkeypatch.setattr(
        "offerte_tech.scrape_euronics",
        lambda *a, **kw: [Offerta(nome="Samsung Galaxy A55 128GB", prezzo=349.0, negozio="Euronics", link="https://euronics.it/1", fonte="euronics.it")],
    )
    monkeypatch.setattr(
        "offerte_tech.scrape_unieuro",
        lambda *a, **kw: [Offerta(nome="Samsung Galaxy A55 256GB", prezzo=399.0, negozio="Unieuro", link="https://unieuro.it/1", fonte="unieuro.it")],
    )
    monkeypatch.setattr(
        "offerte_tech.scrape_mediaworld",
        lambda *a, **kw: [Offerta(nome="Samsung Galaxy A56 128GB", prezzo=429.0, negozio="MediaWorld", link="https://mw.it/1", fonte="mediaworld.it")],
    )
    monkeypatch.setattr("offerte_tech.fetch_specs_ai", lambda offerte, categoria, cerebras_client: offerte)

    risultati = cerca_offerte(
        query="samsung galaxy",
        budget_max=500,
        top_n=10,
        fonti=["euronics", "unieuro", "mediaworld"],
        categoria="tech",
        cerebras_client=None,
        app_id="",
        cert_id="",
    )

    assert len(risultati) == 3
    fonti_trovate = {o.fonte for o in risultati}
    assert "euronics.it" in fonti_trovate
    assert "unieuro.it" in fonti_trovate
    assert "mediaworld.it" in fonti_trovate
    # Ordine per prezzo crescente
    prezzi = [o.prezzo for o in risultati]
    assert prezzi == sorted(prezzi)


def test_parse_comparison_query() -> None:
    """Verifica che parse_comparison_query rilevi correttamente le query di confronto."""
    # vs standard
    assert parse_comparison_query("iphone 15 vs iphone 16") == ["iphone 15", "iphone 16"]
    # vs con tre elementi
    parts = parse_comparison_query("iphone 14 vs iphone 15 vs iphone 16")
    assert len(parts) == 3
    assert "iphone 14" in parts
    # confronta ... e ...
    parts2 = parse_comparison_query("confronta samsung galaxy s24 e iphone 16")
    assert len(parts2) == 2
    # versus
    assert parse_comparison_query("notebook dell versus notebook asus") == ["notebook dell", "notebook asus"]
    # query normale → lista vuota
    assert parse_comparison_query("notebook 14 pollici") == []
    assert parse_comparison_query("iphone 16") == []
    # Pattern "X V1 o V2 + confronto" (caso d'uso reale)
    parts3 = parse_comparison_query("vorrei prendere un iphone 16 o 17 fammi un confronto su quale scegliere")
    assert len(parts3) == 2
    assert any("16" in p for p in parts3), f"Atteso 'iphone 16' nei parts: {parts3}"
    assert any("17" in p for p in parts3), f"Atteso 'iphone 17' nei parts: {parts3}"
    # Variante senza "fammi"
    parts4 = parse_comparison_query("iphone 16 o 17 confronto")
    assert len(parts4) == 2
    # vs con testo trailing (deve essere pulito)
    parts5 = parse_comparison_query("iphone 16 vs iphone 17 da 256gb, quale mi conviene prendere dei due?")
    assert len(parts5) == 2
    assert parts5[0] == "iphone 16"
    assert "iphone 17" in parts5[1]
    assert "quale" not in parts5[1]  # il rumore deve essere rimosso


def _open_home(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    # Attendi il rendering del messaggio iniziale di benvenuto
    expect(_assistant_messages(page).first).to_be_visible(timeout=15000)


def _send_chat(page: Page, placeholder: str, text: str) -> None:
    chat = page.get_by_placeholder(placeholder)
    chat.fill(text)
    chat.press("Enter")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(2500)  # Attendi doppio rerun Streamlit


def _assistant_messages(page: Page):
    return page.locator("[data-testid='stChatMessage']")


def _complete_presearch(page: Page) -> None:
    """Completa la chat pre-ricerca per raggiungere STATE 2 (3 scambi nel mock mode)."""
    placeholder = "Descrivi prodotto, uso, vincoli e preferenze"
    msgs = ["cerco uno smartphone", "massimo 800 euro", "nuovo"]
    for msg in msgs:
        # Già in STATE 2?
        try:
            page.locator("button", has_text="Cerca offerte").wait_for(state="visible", timeout=2000)
            return
        except Exception:
            pass
        # Chat input ancora visibile?
        chat = page.get_by_placeholder(placeholder)
        if chat.count() == 0:
            break
        chat.fill(msg)
        chat.press("Enter")
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(3000)
    expect(page.locator("button", has_text="Cerca offerte")).to_be_visible(timeout=30000)


def test_chat_prericerca_risponde(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    initial_count = _assistant_messages(page).count()
    _send_chat(page, "Descrivi prodotto, uso, vincoli e preferenze", "cerco uno smartphone")
    expect(_assistant_messages(page)).to_have_count(initial_count + 2, timeout=30000)


def test_chat_no_ripetizioni(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    initial_count = _assistant_messages(page).count()
    _send_chat(page, "Descrivi prodotto, uso, vincoli e preferenze", "cerco uno smartphone")
    expect(_assistant_messages(page)).to_have_count(initial_count + 2, timeout=30000)
    first_reply = _assistant_messages(page).last.text_content() or ""
    greeting = _assistant_messages(page).first.text_content() or ""
    assert first_reply.strip() != greeting.strip(), "La risposta AI è uguale al saluto iniziale"


def test_range_prezzo_sync(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    _complete_presearch(page)
    min_input = page.get_by_role("spinbutton", name="Prezzo minimo (€)")
    expect(min_input).to_be_visible(timeout=5000)
    min_input.fill("200")
    min_input.press("Tab")
    page.wait_for_load_state("networkidle")
    expect(min_input).to_have_value("200", timeout=5000)


def test_avvia_ricerca(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    _complete_presearch(page)
    expect(page.get_by_role("button", name="Cerca offerte")).to_be_enabled(timeout=8000)
    page.get_by_role("button", name="Cerca offerte").click()
    expect(page.get_by_text("offerte trovate per")).to_be_visible(timeout=60000)
    expect(page.locator("[data-testid='stDataFrame']")).to_be_visible(timeout=60000)


def test_chat_finale_risponde(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    _complete_presearch(page)
    page.get_by_role("button", name="Cerca offerte").click()
    expect(page.get_by_text("offerte trovate per")).to_be_visible(timeout=60000)
    expect(page.locator("[data-testid='stDataFrame']")).to_be_visible(timeout=60000)
    _send_chat(page, "Esempio: quale mi consigli per uso quotidiano?", "quale mi consigli?")
    last_message = page.locator("[data-testid='stChatMessage']").last
    expect(last_message).to_contain_text("Ti consiglio", timeout=30000)

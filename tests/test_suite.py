from __future__ import annotations

import json
import math
from unittest.mock import MagicMock

import pytest

try:
    from playwright.sync_api import Page, expect
except ImportError:  # Playwright opzionale: i test E2E vengono skippati (vedi conftest)
    Page = expect = None  # type: ignore[assignment,misc]

from offerte_tech import (
    Offerta,
    _deduplica,
    _is_spec_token,
    cerca_offerte,
    filtra_risultati_con_ai,
    fetch_specs_ai,
    is_relevant,
    parse_comparison_query,
    parse_price,
    scrape_euronics,
    scrape_mediaworld,
    scrape_unieuro,
)


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeSession:
    def __init__(self) -> None:
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    def get(self, *args, **kwargs):
        return _FakeResponse("<html></html>")


def test_scrape_amazon_retry_second_attempt_with_open_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """Il secondo tentativo Amazon deve avvenire con sessione ancora aperta."""
    desktop_empty = "<html><body><div>no cards</div></body></html>"
    desktop_with_card = (
        '<html><body>'
        '<div data-component-type="s-search-result">'
        '<h2><span class="a-text-normal">Apple iPhone 16 128GB Nero</span></h2>'
        '<span class="a-price"><span class="a-offscreen">€ 879,00</span></span>'
        '<a class="a-link-normal" href="/dp/B0TEST1234/ref=abc"></a>'
        "</div>"
        "</body></html>"
    )

    calls: list[str] = []

    def fake_fetch_with_retry(url: str, headers: dict[str, str], **kwargs):
        session = kwargs.get("session")
        if session is not None and getattr(session, "closed", False):
            raise RuntimeError("session closed")
        calls.append(url)
        if len(calls) == 1:
            return _FakeResponse(desktop_empty, 200)
        if len(calls) == 2:
            return _FakeResponse(desktop_with_card, 200)
        return _FakeResponse(desktop_empty, 200)

    monkeypatch.setattr("offerte.scrapers.amazon.fetch_with_retry", fake_fetch_with_retry)
    monkeypatch.setattr("offerte.scrapers.amazon.requests.Session", _FakeSession)
    monkeypatch.setattr("offerte.scrapers.amazon._random_delay", lambda: None)
    monkeypatch.setattr("offerte.scrapers.amazon.time.sleep", lambda *_: None)

    risultati = __import__("offerte_tech").scrape_amazon(
        "iphone 16", prezzo_min=300, budget_max=1000, query_tokens=["iphone", "16"], condizione="nuovo"
    )

    assert len(calls) >= 2
    assert len(risultati) == 1
    assert risultati[0].prezzo == 879.0
    assert "iphone 16" in risultati[0].nome.lower()


def test_scrape_amazon_does_not_use_broken_rh_condition_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Per evitare false pagine 'nessun risultato', Amazon non deve usare rh condition in URL."""
    calls: list[str] = []

    def fake_fetch_with_retry(url: str, headers: dict[str, str], **kwargs):
        calls.append(url)
        return _FakeResponse("<html><body></body></html>", 200)

    monkeypatch.setattr("offerte.scrapers.amazon.fetch_with_retry", fake_fetch_with_retry)
    monkeypatch.setattr("offerte.scrapers.amazon.requests.Session", _FakeSession)
    monkeypatch.setattr("offerte.scrapers.amazon._random_delay", lambda: None)
    monkeypatch.setattr("offerte.scrapers.amazon.time.sleep", lambda *_: None)

    __import__("offerte_tech").scrape_amazon(
        "iphone 16", prezzo_min=300, budget_max=1000, query_tokens=["iphone", "16"], condizione="nuovo"
    )

    assert calls
    assert "p_n_condition-type" not in calls[0]


def test_scrape_amazon_condizione_nuovo_filtra_ricondizionato(monkeypatch: pytest.MonkeyPatch) -> None:
    """Con condizione=nuovo lo scraper Amazon esclude i titoli ricondizionati/usati."""
    html = (
        '<html><body>'
        '<div data-component-type="s-search-result">'
        '<h2><span class="a-text-normal">Apple iPhone 16 128GB Nero</span></h2>'
        '<span class="a-price"><span class="a-offscreen">€ 879,00</span></span>'
        '<a class="a-link-normal" href="/dp/B0TEST1234/ref=abc"></a>'
        "</div>"
        '<div data-component-type="s-search-result">'
        '<h2><span class="a-text-normal">Apple iPhone 16 Ricondizionato</span></h2>'
        '<span class="a-price"><span class="a-offscreen">€ 699,00</span></span>'
        '<a class="a-link-normal" href="/dp/B0TEST9999/ref=abc"></a>'
        "</div>"
        "</body></html>"
    )

    monkeypatch.setattr("offerte.scrapers.amazon.fetch_with_retry", lambda *a, **k: _FakeResponse(html, 200))
    monkeypatch.setattr("offerte.scrapers.amazon.requests.Session", _FakeSession)
    monkeypatch.setattr("offerte.scrapers.amazon._random_delay", lambda: None)
    monkeypatch.setattr("offerte.scrapers.amazon.time.sleep", lambda *_: None)

    risultati = __import__("offerte_tech").scrape_amazon(
        "iphone 16", prezzo_min=300, budget_max=1000, query_tokens=["iphone", "16"], condizione="nuovo"
    )

    assert len(risultati) == 1
    assert "ricondiz" not in risultati[0].nome.lower()


def test_scrape_amazon_fallback_mobile_on_desktop_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """Se Amazon desktop torna 503, lo scraper tenta automaticamente l'endpoint mobile."""
    mobile_html = (
        '<html><body>'
        '<div data-component-type="s-search-result">'
        '<h2><span class="a-text-normal">Apple iPhone 16 128GB Nero</span></h2>'
        '<span class="a-price"><span class="a-offscreen">€ 879,00</span></span>'
        '<a class="a-link-normal" href="/dp/B0TESTMOBILE/ref=abc"></a>'
        "</div>"
        "</body></html>"
    )

    calls: list[str] = []

    def fake_fetch_with_retry(url: str, headers: dict[str, str], **kwargs):
        calls.append(url)
        if "/gp/aw/s?" in url:
            return _FakeResponse(mobile_html, 200)
        return _FakeResponse("<html><body>503</body></html>", 503)

    monkeypatch.setattr("offerte.scrapers.amazon.fetch_with_retry", fake_fetch_with_retry)
    monkeypatch.setattr("offerte.scrapers.amazon.requests.Session", _FakeSession)
    monkeypatch.setattr("offerte.scrapers.amazon._random_delay", lambda: None)
    monkeypatch.setattr("offerte.scrapers.amazon.time.sleep", lambda *_: None)

    risultati = __import__("offerte_tech").scrape_amazon(
        "iphone 16", prezzo_min=300, budget_max=1000, query_tokens=["iphone", "16"], condizione="nuovo"
    )

    assert any("/gp/aw/s?" in c for c in calls)
    assert len(risultati) == 1


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
    monkeypatch.setattr("offerte.orchestrator.scrape_amazon", lambda *a, **kw: amazon_results or [])
    monkeypatch.setattr("offerte.orchestrator.scrape_ebay", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_vinted", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_euronics", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_unieuro", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_mediaworld", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_wallapop", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_comet", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_expert", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.fetch_specs_ai", lambda offerte, categoria, cerebras_client: offerte)


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
        "offerte.orchestrator.scrape_amazon",
        lambda *args, **kwargs: [
            Offerta(nome="HP Laptop 14 pollici Intel i5", prezzo=600.0, negozio="Amazon", link="https://example.com/1"),
            Offerta(nome="HP Laptop 14 pollici 16GB RAM SSD", prezzo=700.0, negozio="Amazon", link="https://example.com/2"),
        ],
    )
    monkeypatch.setattr("offerte.orchestrator.scrape_ebay", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_vinted", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_euronics", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_unieuro", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_mediaworld", lambda *args, **kwargs: [])
    monkeypatch.setattr("offerte.orchestrator.fetch_specs_ai", lambda offerte, categoria, cerebras_client: offerte)

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


def test_filtra_risultati_con_ai_hard_specs_notebook() -> None:
    """I filtri hard devono scartare notebook fuori dimensione/RAM/storage richiesti."""
    risultati = [
        Offerta(nome='Notebook 15,6" Intel i5 16GB RAM 512GB SSD', prezzo=599.0, negozio="A", link="https://x/1"),
        Offerta(nome='Notebook 17,3" Intel i5 16GB RAM 512GB SSD', prezzo=579.0, negozio="B", link="https://x/2"),
        Offerta(nome='Notebook 15,6" Intel i5 8GB RAM 512GB SSD', prezzo=499.0, negozio="C", link="https://x/3"),
        Offerta(nome='Notebook 14" Intel i5 16GB RAM 256GB SSD', prezzo=469.0, negozio="D", link="https://x/4"),
        Offerta(nome='Notebook 14" Intel i5 16GB RAM 1TB SSD', prezzo=649.0, negozio="E", link="https://x/5"),
    ]
    filtri = {
        "ram_gb": "16",
        "storage_gb": "512",
        "size_inches": "14-15",
    }

    filtered = filtra_risultati_con_ai(risultati, filtri)
    names = [o.nome.lower() for o in filtered]

    assert any('15,6" intel i5 16gb ram 512gb' in n for n in names)
    assert any('14" intel i5 16gb ram 1tb' in n for n in names)
    assert not any('17,3"' in n for n in names)
    assert not any('8gb ram' in n for n in names)
    assert not any('256gb ssd' in n for n in names)


def test_filtra_risultati_con_ai_logga_motivi_scarto(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Il filtro deve loggare il motivo dello scarto (hard filter / score basso)."""
    monkeypatch.setattr("offerte.ai._get_cerebras_client", lambda: None)

    risultati = [
        Offerta(nome='Notebook 17,3" 16GB RAM 512GB SSD', prezzo=579.0, negozio="A", link="https://x/1"),
        Offerta(nome='Notebook 14" 16GB RAM 512GB SSD', prezzo=629.0, negozio="B", link="https://x/2"),
        Offerta(nome='Notebook 14" 16GB RAM 512GB SSD colore silver', prezzo=649.0, negozio="C", link="https://x/3"),
    ]
    filtri_hard = {
        "ram_gb": "16",
        "storage_gb": "512",
        "size_inches": "14-15",
    }

    _ = filtra_risultati_con_ai(risultati, filtri_hard)
    out_hard = capsys.readouterr().out.lower()

    _ = filtra_risultati_con_ai(risultati, {"colore": "rosa"})
    out_score = capsys.readouterr().out.lower()

    assert "scarto hard" in out_hard
    assert "display" in out_hard
    assert "scarto score" in out_score


def test_nuove_fonti_vuote(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica che le nuove fonti restituiscano lista vuota su errore di rete."""
    import requests as _req

    def _raise_conn(*a, **kw):
        raise _req.ConnectionError("test")

    # Le fonti usano due meccanismi: fetch_with_retry (euronics/mediaworld/expert)
    # e requests.post/get diretti (unieuro/comet/wallapop). Va patchato il vero
    # call-site dentro ciascun modulo scraper, non quello di orchestrator (che le
    # fonti non usano), altrimenti il test colpisce la rete reale.
    for _mod in ("euronics", "mediaworld", "expert"):
        monkeypatch.setattr(f"offerte.scrapers.{_mod}.fetch_with_retry", _raise_conn)
    monkeypatch.setattr("requests.get", _raise_conn)
    monkeypatch.setattr("requests.post", _raise_conn)

    from offerte_tech import scrape_euronics, scrape_unieuro, scrape_mediaworld, scrape_comet, scrape_wallapop, scrape_expert
    for scraper in (scrape_euronics, scrape_unieuro, scrape_mediaworld, scrape_comet, scrape_wallapop, scrape_expert):
        result = scraper("notebook", 0, 1000, ["notebook"])
        assert result == [], f"{scraper.__name__} doveva restituire [] su ConnectionError"


def test_cerca_offerte_nuove_fonti_integrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica che cerca_offerte aggreghi i risultati da euronics/unieuro/mediaworld."""
    monkeypatch.setattr("offerte.orchestrator.scrape_amazon", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_ebay", lambda *a, **kw: [])
    monkeypatch.setattr("offerte.orchestrator.scrape_vinted", lambda *a, **kw: [])
    monkeypatch.setattr(
        "offerte.orchestrator.scrape_euronics",
        lambda *a, **kw: [Offerta(nome="Samsung Galaxy A55 128GB", prezzo=349.0, negozio="Euronics", link="https://euronics.it/1", fonte="euronics.it")],
    )
    monkeypatch.setattr(
        "offerte.orchestrator.scrape_unieuro",
        lambda *a, **kw: [Offerta(nome="Samsung Galaxy A55 256GB", prezzo=399.0, negozio="Unieuro", link="https://unieuro.it/1", fonte="unieuro.it")],
    )
    monkeypatch.setattr(
        "offerte.orchestrator.scrape_mediaworld",
        lambda *a, **kw: [Offerta(nome="Samsung Galaxy A56 128GB", prezzo=429.0, negozio="MediaWorld", link="https://mw.it/1", fonte="mediaworld.it")],
    )
    monkeypatch.setattr("offerte.orchestrator.fetch_specs_ai", lambda offerte, categoria, cerebras_client: offerte)

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


def test_parse_comparison_query_implicita_senza_parola_confronto() -> None:
    """Frasi con alternativa esplicita (es. iPhone 16 o 17) devono attivare il confronto."""
    parts = parse_comparison_query(
        "vorrei prendere un iphone 16 o 17, massimo 1000 euro. Quale mi consigli?"
    )
    assert len(parts) == 2
    assert "iphone 16" in parts
    assert "iphone 17" in parts


def _open_home(page: Page, base_url: str) -> None:
    page.goto(base_url)
    page.wait_for_load_state("networkidle")
    # Attendi il rendering del messaggio iniziale di benvenuto
    expect(_assistant_messages(page).first).to_be_visible(timeout=15000)


def _send_chat(page: Page, placeholder: str, text: str) -> None:
    chat = page.get_by_placeholder(placeholder)
    if chat.count() > 0:
        chat.first.fill(text)
        chat.first.press("Enter")
    else:
        # Fallback robusto: usa il chat input visibile anche se il placeholder cambia.
        chat_fallback = page.locator("[data-testid='stChatInput'] textarea")
        expect(chat_fallback.first).to_be_visible(timeout=30000)
        chat_fallback.first.fill(text)
        chat_fallback.first.press("Enter")
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
    expect(page.locator(".results-grid")).to_be_visible(timeout=60000)


def test_chat_finale_risponde(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    _complete_presearch(page)
    page.get_by_role("button", name="Cerca offerte").click()
    expect(page.get_by_text("offerte trovate per")).to_be_visible(timeout=60000)
    expect(page.locator(".results-grid")).to_be_visible(timeout=60000)

    main_text = page.locator("section[data-testid='stMain']").inner_text(timeout=30000)
    if "Aggiungi CEREBRAS_API_KEY" in main_text:
        assert "Consiglio AI" in main_text
        return

    _send_chat(page, "Esempio: quale mi consigli per uso quotidiano?", "quale mi consigli?")
    last_message = page.locator("[data-testid='stChatMessage']").last
    expect(last_message).to_contain_text("Ti consiglio", timeout=30000)


def test_chat_finale_confronto_include_entrambi_modelli(page: Page, base_url: str, streamlit_server: str) -> None:
    _open_home(page, base_url)
    _send_chat(page, "Descrivi prodotto, uso, vincoli e preferenze", "iphone 16 vs iphone 17 nuovo budget 1000")
    expect(page.get_by_role("button", name="Cerca offerte")).to_be_enabled(timeout=10000)
    page.get_by_role("button", name="Cerca offerte").click()

    # Il contenuto della raccomandazione è in fondo pagina: scorri prima di validare.
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(2000)

    advice_expander = page.get_by_text("💬 Consiglio AI")
    if advice_expander.count() > 0:
        advice_expander.first.click()
        page.wait_for_timeout(1000)

    all_text = page.locator("section[data-testid='stMain']").inner_text(timeout=30000).lower()
    assert "iphone 16" in all_text
    assert "iphone 17" in all_text


def test_chat_refine_state2_non_crasha(page: Page, base_url: str, streamlit_server: str) -> None:
    """In stato presearch completata, inviare un affinamento in chat non deve causare eccezioni Streamlit."""
    _open_home(page, base_url)
    _complete_presearch(page)

    _send_chat(page, "Vuoi affinare la query o il budget? Scrivi qui...", "alza budget massimo a 1000")

    expect(page.get_by_role("button", name="Cerca offerte")).to_be_visible(timeout=10000)
    body_text = page.locator("section[data-testid='stMain']").inner_text(timeout=15000).lower()
    assert "uncaught app execution" not in body_text


# ===========================================================================
# Vinted (library-based)
# ===========================================================================

def test_scrape_vinted_library_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """scrape_vinted deve usare VintedScraper e restituire Offerta objects."""
    from offerte_tech import scrape_vinted

    class _FakeItem:
        title = "Notebook Lenovo usato"
        price = 150.0
        url = "https://www.vinted.it/items/123-notebook"
        photo = {"url": "https://images1.vinted.net/photo.jpg"}

    class _FakeScraper:
        def __init__(self, base_url: str) -> None:
            pass
        def search(self, params: dict) -> list:
            return [_FakeItem()]

    monkeypatch.setattr("offerte.scrapers.vinted.VintedScraper", _FakeScraper)

    results = scrape_vinted("notebook", 0.0, 500.0, ["notebook"])
    assert len(results) == 1
    assert results[0].negozio == "Vinted"
    assert results[0].prezzo == 150.0
    assert results[0].immagine == "https://images1.vinted.net/photo.jpg"


def test_scrape_vinted_skips_nuovo_condizione(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_vinted
    results = scrape_vinted("notebook", 0.0, 500.0, ["notebook"], condizione="nuovo")
    assert results == []


# ===========================================================================
# Wallapop
# ===========================================================================

def test_scrape_wallapop_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_wallapop

    comp_resp = {
        "components": [{"type": "search_results", "type_data": {"query_params": {
            "search_id": "abc-123", "category_id": "24200",
        }}}]
    }
    section_resp = {
        "data": {"section": {"items": [{
            "title": "Notebook HP 15 usato",
            "price": {"amount": 280.0, "currency": "EUR"},
            "web_slug": "notebook-hp-15-280",
            "images": [{"urls": {"small": "https://cdn.wallapop.com/img.jpg"}}],
            "shipping": {"user_allows_shipping": True},
            "is_refurbished": False,
        }]}}
    }

    call_count: dict[str, int] = {"n": 0}

    class _FakeResp:
        status_code = 200
        def __init__(self, data: dict) -> None:
            self._data = data
        def json(self) -> dict:
            return self._data
        def raise_for_status(self) -> None:
            pass

    def fake_get(url: str, **kwargs):
        call_count["n"] += 1
        if "components" in url:
            return _FakeResp(comp_resp)
        return _FakeResp(section_resp)

    monkeypatch.setattr("offerte.scrapers.wallapop.requests.get", fake_get)

    results = scrape_wallapop("notebook", 0.0, 500.0, ["notebook"])
    assert call_count["n"] == 2
    assert len(results) == 1
    assert results[0].negozio == "Wallapop"
    assert results[0].prezzo == 280.0
    assert results[0].link == "https://it.wallapop.com/item/notebook-hp-15-280"


# ===========================================================================
# Comet
# ===========================================================================

def test_scrape_comet_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_comet

    algolia_resp = {"results": [{"hits": [{
        "name": "Lenovo IdeaPad 3 15 - 16GB/512GB",
        "pFinale": 549.0,
        "url": "https://www.comet.it/lenovo-ideapad-3-LEN001",
        "image": "https://static.comet.it/img/LEN001.jpg",
        "isAcquistabile": True,
    }]}]}

    class _FakeResp:
        status_code = 200
        def json(self) -> dict:
            return algolia_resp
        def raise_for_status(self) -> None:
            pass

    monkeypatch.setattr("offerte.scrapers.comet.requests.post", lambda *a, **k: _FakeResp())

    results = scrape_comet("notebook", 0.0, 800.0, ["notebook", "lenovo"])
    assert len(results) == 1
    assert results[0].negozio == "Comet"
    assert results[0].prezzo == 549.0
    assert "comet.it" in results[0].link


# ===========================================================================
# Expert
# ===========================================================================

def test_scrape_expert_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_expert

    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "numberOfItems": "1",
        "itemListElement": [{"@type": "ListItem", "position": 1, "item": {
            "@type": "Product",
            "name": "NOTEBOOK ACER ASPIRE 15 - Intel Ultra 5",
            "url": "https://www.expert.it/it/it/exp/shop/product/notebook-acer/exp123456",
            "image": "https://d3s2y7lmzr67yx.cloudfront.net/IMG/EXPERT/EXP123456.jpg",
            "offers": {"@type": "Offer", "price": "699", "priceCurrency": "EUR"},
        }}],
    })
    html = f'<html><head></head><body><script type="application/ld+json">{json_ld}</script></body></html>'

    monkeypatch.setattr("offerte.scrapers.expert.fetch_with_retry", lambda *a, **k: _FakeResponse(html, 200))

    results = scrape_expert("notebook", 0.0, 800.0, ["notebook", "acer"])
    assert len(results) == 1
    assert results[0].negozio == "Expert"
    assert results[0].prezzo == 699.0
    assert "expert.it" in results[0].link



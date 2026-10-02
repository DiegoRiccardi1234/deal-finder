"""Avvisi prudenti e rendering: segnali espliciti senza filtrare offerte."""

from types import SimpleNamespace

import pytest

from offerte.models import Offerta
from offerte.offer_warnings import get_offer_warnings
from ui import cards
from ui import recommendation


@pytest.mark.parametrize(
    "title, reason",
    [
        ("iPhone 15 solo scatola", "sola scatola"),
        ("Scatola vuota PS5", "sola scatola"),
        ("Notebook per ricambi", "guasto"),
        ("iPhone NON FUNZIONANTE", "guasto"),
        ("Notebook acconto 50 euro", "acconto"),
        ("Notebook acconto 0,50 euro", "acconto"),
        ("iPhone rata mensile", "costo mensile"),
        ("Notebook rata da 39,99 €", "costo mensile"),
        ("iPhone 29 euro al mese", "costo mensile"),
        ("Notebook 49 €/mese", "costo mensile"),
    ],
)
def test_explicit_title_signals(title, reason):
    warnings = get_offer_warnings(title)
    assert warnings and reason in warnings[0]


@pytest.mark.parametrize(
    "title",
    [
        "Notebook Lenovo 16 GB 512 GB 150 euro",
        "Notebook Lenovo 32 GB 1 TB 1500 euro",
        "Custodia iPhone 15",
        "Caricatore notebook",
        "iPhone con scatola originale",
        "Scatola per spedizione notebook",
        "iPhone senza acconto",
        "Notebook nessun acconto",
        "Notebook acconto zero",
        "Notebook acconto 0 €",
        "Notebook rateizzazione disponibile",
        "Notebook pagamento in 24 rate",
        "Notebook prezzo intero 499 euro, rate disponibili",
        "Notebook senza rata mensile",
        "Notebook senza scatola vuota",
    ],
)
def test_ambiguous_titles_do_not_trigger_warning(title):
    assert get_offer_warnings(title) == []


def _offer(title, price):
    return Offerta(
        title, price, "Negozio", "https://example.com/offerta", "amazon.it", specs={"ram": "16 GB"}
    )


def test_warned_offer_kept_and_not_awarded_best_price(monkeypatch):
    rendered = []
    monkeypatch.setattr(
        cards,
        "st",
        SimpleNamespace(
            markdown=lambda value, **kwargs: rendered.append(value),
        ),
    )
    cards._render_results_grid(
        [
            _offer("Notebook solo scatola", 5),
            _offer("Notebook completo", 500),
        ]
    )
    html = rendered[0]
    assert "Notebook solo scatola" in html
    assert "Verifica l'annuncio" in html
    assert html.count("Miglior Prezzo") == 1
    suspicious, normal = html.split("<div class='offerta-card offerta-best'>")
    assert "Miglior Prezzo" not in suspicious
    assert "Notebook completo" in normal
    assert "Miglior Prezzo" not in cards._render_offerta_card(
        _offer("Notebook solo scatola", 5),
        0,
        best_price=5,
    )


def test_warning_reason_is_html_escaped(monkeypatch):
    monkeypatch.setattr(
        cards, "get_offer_warnings", lambda title: ['Verifica <img src=x onerror="bad">']
    )
    html = cards._render_offerta_card(_offer("Titolo <script>bad</script>", 50), 0)
    assert "&lt;img" in html and "&lt;script&gt;" in html
    assert "<img src=x" not in html and "<script>" not in html


def test_specs_cards_reuse_warning_rendering(monkeypatch):
    rendered = []
    column = SimpleNamespace(markdown=lambda value, **kwargs: rendered.append(value))
    monkeypatch.setattr(
        cards,
        "st",
        SimpleNamespace(
            markdown=lambda value, **kwargs: None,
            columns=lambda *args, **kwargs: [column, column],
        ),
    )
    cards._render_specs_grid([_offer("Notebook non funzionante", 30)])
    assert "Verifica l'annuncio" in rendered[0] and "guasto" in rendered[0]
    assert "Miglior Prezzo" not in rendered[0]


def test_recommendation_prompt_keeps_flagged_products_and_explains_checks(monkeypatch):
    import json
    from unittest.mock import MagicMock

    monkeypatch.setattr(recommendation, "st", SimpleNamespace(session_state={}))
    chat = MagicMock(return_value="Consiglio di verificare il prezzo totale.")
    monkeypatch.setattr(recommendation, "_ai_chat_with_retry", chat)
    offers = [_offer("Notebook solo scatola", 5), _offer("Notebook completo", 500)]
    products = recommendation._build_products_payload(offers)
    assert len(products) == 2
    assert products[0]["avvisi"] == get_offer_warnings(offers[0].nome)
    assert products[1]["avvisi"] == []
    assert products[0]["link"] == offers[0].link
    assert products[0]["specs"] == offers[0].specs
    client = object()
    answer = recommendation._call_final_recommendation(
        client,
        offers,
        {"trascrizione": "uso ufficio"},
        [{"role": "user", "content": "Quale compro?"}],
    )
    assert "verificare" in answer
    messages = chat.call_args.args[1]
    prompt = messages[0]["content"]
    assert json.dumps(products, ensure_ascii=False) in prompt
    assert "non raccomandare come prodotto completo" in prompt
    assert "prezzo totale" in prompt
    assert "Un prezzo basso da solo non indica un problema" in prompt
    assert messages[-1] == {"role": "user", "content": "Quale compro?"}
    assert chat.call_args.args[0] is client


def test_comparison_recommendation_best_ignores_warnings_without_losing_products(monkeypatch):
    offers = [_offer("Notebook acconto 5 euro", 5), _offer("Notebook completo", 500)]
    monkeypatch.setattr(
        recommendation,
        "st",
        SimpleNamespace(
            session_state={
                "comparison_mode": True,
                "comparison_results": {
                    "notebook A": offers,
                    "notebook B": [_offer("Notebook per ricambi", 10)],
                },
            }
        ),
    )
    products, summary = recommendation._build_comparison_payload()
    assert len(products) == 3
    assert products[0]["query"] == "notebook A"
    assert products[0]["avvisi"] == get_offer_warnings(offers[0].nome)
    assert products[0]["link"] == offers[0].link
    assert products[0]["specs"] == offers[0].specs
    assert summary[0]["count"] == 2
    assert summary[0]["best_name"] == "Notebook completo"
    assert summary[0]["best_price"] == 500
    assert summary[0]["best_store"] == "Negozio"
    assert summary[0]["avvisi"] == []
    assert summary[1]["count"] == 1
    assert summary[1]["best_name"] is None
    assert summary[1]["best_price"] is None
    assert summary[1]["best_store"] is None
    assert summary[1]["avvisi"] == get_offer_warnings("Notebook per ricambi")

"""Regressioni visibili: niente prezzi o esportazioni da risultati nascosti."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from offerte.models import Offerta
from ui import comparison
from ui.results import comparable_offers, filter_visible_offers, offer_condition


def offer(title="Notebook completo", price=500, source="comet.it"):
    return Offerta(title, price, "Negozio", f"https://example.com/{price}", source)


def test_unknown_marketplace_condition_is_not_presented_as_new():
    assert offer_condition(offer(source="ebay.it")) == "n.d."
    assert offer_condition(offer(source="wallapop.it")) == "n.d."
    assert offer_condition(offer(source="ebay.it"), "usato") == "usato"
    assert offer_condition(offer(source="comet.it")) == "nuovo"


def test_filter_does_not_reintroduce_hidden_products():
    offers = [offer()]
    assert (
        filter_visible_offers(
            offers,
            sources=[],
            price_range=(0, 1000),
            condition="usato",
        )
        == []
    )
    assert comparable_offers([offer("Notebook solo scatola", 5)]) == []


def test_comparison_prices_ignore_flagged_listings_but_show_warnings(monkeypatch):
    rendered = []
    monkeypatch.setattr(
        comparison,
        "st",
        SimpleNamespace(
            markdown=lambda value, **kw: rendered.append(value),
            warning=lambda *a: None,
            columns=lambda count, **kw: [nullcontext() for _ in range(count)],
        ),
    )
    offers = [offer("Notebook solo scatola", 5), offer()]
    comparison._render_comparison_board({"notebook": offers})
    assert "€ 500,00" in rendered[0] and "€ 5,00" not in rendered[0]
    assert "sola scatola" in rendered[1]
    rendered.clear()
    comparison._render_manual_comparison_matrix(offers)
    assert rendered[0].count("is-best") == 1
    assert "sola scatola" in rendered[0]


@pytest.mark.parametrize("case", ["empty_filter", "empty_search", "all_flagged", "mixed"])
def test_metrics_export_and_history_follow_eligible_visible_offers(monkeypatch, case):
    import search_history
    import watchlist
    from offerte import log
    from streamlit.testing.v1 import AppTest
    from ui import ai_client

    monkeypatch.setenv("APP_TEST_MODE", "1")
    monkeypatch.setattr(log, "configure_logging", lambda: None)
    monkeypatch.setattr(ai_client, "_get_ai_api_key", lambda: "")
    monkeypatch.setattr(ai_client, "_get_ai_client", lambda key: None)
    monkeypatch.setattr(search_history, "load_history", lambda: [])
    monkeypatch.setattr(watchlist, "load", lambda: [])
    offers = [offer()] if case == "empty_filter" else [offer("Notebook solo scatola", 5)]
    if case == "mixed":
        offers.append(offer())
    elif case == "empty_search":
        offers = []
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state["presearch_ready"] = True
    app.session_state["query_ottimizzata"] = "notebook"
    app.session_state["ricerca_effettuata"] = True
    app.session_state["ultima_query"] = "notebook"
    app.session_state["risultati"] = offers
    app.session_state["search_snapshot"] = {
        "sources": ["comet"],
        "statuses": {
            "comet": {"fonte": "comet", "state": "ok", "detail": "", "results": len(offers)},
        },
        "params": {"condizione": "tutti"},
    }
    app.session_state["prezzo_nuovo_minimo"] = True
    app.session_state["prezzo_minimo_corrente"] = 5
    if case == "empty_filter":
        app.session_state["filtro_condizione_tabella"] = "usato"
    app.run()
    assert not app.exception
    metrics = {metric.label: metric.value for metric in app.metric}
    if case == "empty_search":
        assert not metrics
        assert any("Stato fonti" in item.value for item in app.markdown)
        assert any("Nessuna offerta trovata" in item.value for item in app.warning)
        return
    assert metrics["Prezzo piu basso"] == ("€ 500,00" if case == "mixed" else "n.d.")
    assert not any("Nuovo minimo storico" in item.value for item in app.success)
    if case == "empty_filter":
        assert metrics["Risultati"] == "0/1"
        assert any("Ho trovato 0 risultati" in item.value for item in app.code)
    assert any("Stato fonti" in item.value for item in app.markdown)

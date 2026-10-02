"""Regressioni UI: confronti incompleti, stato ricerca e preferiti persistenti."""

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from offerte.models import Offerta
from ui import comparison


def _offer() -> Offerta:
    return Offerta(
        "Prodotto A",
        100.0,
        "Negozio A",
        "https://example.com/a",
        "amazon.it",
        specs={"ram": "16 GB"},
    )


@pytest.mark.parametrize("with_offer", [True, False])
def test_comparison_keeps_queries_without_offers(monkeypatch, with_offer):
    rendered = []
    warnings = []
    columns = []

    def make_columns(count, **kwargs):
        columns.append(count)
        return [nullcontext() for _ in range(count)]

    monkeypatch.setattr(
        comparison,
        "st",
        SimpleNamespace(
            markdown=lambda value, **kwargs: rendered.append(value),
            warning=warnings.append,
            columns=make_columns,
        ),
    )
    comparison._render_comparison_board(
        {
            "query A": [_offer()] if with_offer else [],
            "query B": [],
        }
    )

    table = rendered[0]
    assert "Query A" in table and "Query B" in table
    assert "0 risultati" in table
    assert "n.d." in table and "Nessun risultato" in table
    assert columns == [2]
    assert "Query B" in rendered[-1] and "Nessun risultato" in rendered[-1]
    if with_offer:
        assert "16 GB" in table
        assert not warnings
    else:
        assert warnings


def test_comparison_replaces_previous_search_state(monkeypatch):
    state = {
        "ultima_query": "vecchia query",
        "filtro_fonti_tabella": ["vinted.it"],
        "filtro_prezzo_range_tabella": (1.0, 5.0),
        "filtro_condizione_tabella": "usato",
        "comparatore_selezione": ["https://example.com/old"],
        "watchlist_add_selezione": ["https://example.com/old"],
        "prezzo_nuovo_minimo": True,
        "prezzo_minimo_prec": 10.0,
        "prezzo_minimo_corrente": 5.0,
        "_last_search_params": {"query": "vecchia query"},
        "filtri_ai": {"ram": "16 GB", "storage": "512 GB"},
    }
    status = MagicMock()
    monkeypatch.setattr(
        comparison,
        "st",
        SimpleNamespace(
            session_state=state,
            secrets={},
            write=lambda *args: None,
            status=lambda *args, **kwargs: nullcontext(status),
        ),
    )
    search = MagicMock(side_effect=[[_offer()], []])
    monkeypatch.setattr(comparison, "cerca_offerte", search)
    comparison._run_comparison_search(
        queries=["query A", "query B"],
        prezzo_min=50,
        budget_max=500,
        top_n=7,
        condizione="nuovo",
        fonti_backend=["amazon"],
        cerebras_client=None,
    )

    assert state["ultima_query"] == "query A vs query B"
    assert state["ultimo_prezzo_min"] == 50
    assert state["ultimo_prezzo_max"] == 500
    assert state["ultimo_top_n"] == 7
    assert state["condizione"] == "nuovo"
    assert state["filtro_fonti_tabella"] == []
    assert state["filtro_prezzo_range_tabella"] is None
    assert state["filtro_condizione_tabella"] == "tutti"
    assert state["comparatore_selezione"] == []
    assert state["watchlist_add_selezione"] == []
    assert state["prezzo_nuovo_minimo"] is False
    assert state["prezzo_minimo_prec"] is None
    assert state["prezzo_minimo_corrente"] is None
    assert "_last_search_params" not in state
    assert state["filtri_ai_ultima_ricerca"] == {"ram": "16 GB", "storage": "512 GB"}
    assert state["comparison_results"]["query B"] == []
    assert state["risultati"] == [_offer()]
    assert search.call_count == 2
    for call in search.call_args_list:
        assert call.kwargs["filtri_ai"] == {"ram": "16 GB", "storage": "512 GB"}
        assert call.kwargs["filtri_ai"] is not state["filtri_ai"]
        assert call.kwargs["filtri_ai"] is not state["filtri_ai_ultima_ricerca"]
    assert (
        search.call_args_list[0].kwargs["filtri_ai"]
        is not search.call_args_list[1].kwargs["filtri_ai"]
    )


@pytest.mark.parametrize("scenario", ["startup", "empty_search", "empty_filter"])
def test_saved_favourites_accessible_without_visible_results(monkeypatch, scenario):
    import knowledge_base
    import search_history
    import watchlist
    from offerte import log
    from streamlit.testing.v1 import AppTest
    from ui import ai_client, sources

    monkeypatch.setenv("APP_TEST_MODE", "1")
    monkeypatch.setattr(log, "configure_logging", lambda: None)
    monkeypatch.setattr(ai_client, "_get_ai_api_key", lambda: "")
    monkeypatch.setattr(ai_client, "_get_ai_client", lambda key: None)
    monkeypatch.setattr(knowledge_base, "init_kb_on_startup", lambda key: None)
    monkeypatch.setattr(search_history, "load_history", lambda: [])
    monkeypatch.setattr(sources, "_render_source_status_monitor", lambda *args: None)
    items = [
        {
            "nome": "Preferito salvato",
            "prezzo": 99,
            "link": "https://example.com/saved",
            "fonte": "amazon.it",
        }
    ]
    monkeypatch.setattr(watchlist, "load", lambda: items.copy())
    monkeypatch.setattr(watchlist, "remove", lambda link: items.clear())
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    if scenario != "startup":
        app.session_state["ricerca_effettuata"] = True
        app.session_state["ultima_query"] = "query A"
        app.session_state["risultati"] = [_offer()] if scenario == "empty_filter" else []
        if scenario == "empty_filter":
            app.session_state["filtro_condizione_tabella"] = "usato"
    app.run()

    assert not app.exception
    assert any(expander.label == "⭐ Preferiti salvati (1)" for expander in app.expander)
    assert any("Preferito salvato" in element.value for element in app.markdown)
    app.button(key="wl_del_https://example.com/saved").click().run()
    assert not app.exception
    assert not any("Preferiti salvati" in expander.label for expander in app.expander)


def test_refresh_routes_saved_search_parameters_with_current_client(monkeypatch):
    import knowledge_base
    import search_history
    import watchlist
    from offerte import log
    from streamlit.testing.v1 import AppTest
    from ui import ai_client, search

    current_client = SimpleNamespace(name="current provider")
    monkeypatch.setenv("APP_TEST_MODE", "1")
    monkeypatch.setattr(log, "configure_logging", lambda: None)
    monkeypatch.setattr(ai_client, "_get_ai_api_key", lambda: "test-key")
    monkeypatch.setattr(ai_client, "_get_ai_client", lambda key: current_client)
    monkeypatch.setattr(knowledge_base, "init_kb_on_startup", lambda key: None)
    monkeypatch.setattr(knowledge_base, "get_status", lambda: "")
    monkeypatch.setattr(search_history, "load_history", lambda: [])
    monkeypatch.setattr(watchlist, "load", lambda: [])
    run_search = MagicMock()
    monkeypatch.setattr(search, "_run_search", run_search)
    saved_params = {
        "query": "notebook precedente",
        "prezzo_min": 50,
        "budget_max": 500,
        "top_n": 7,
        "condizione": "nuovo",
        "fonti_backend": ["ebay"],
        "categoria": "tech",
        "filtri_ai": {"ram": "16 GB", "storage": "512 GB"},
    }
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
    app.session_state["ricerca_effettuata"] = True
    app.session_state["presearch_ready"] = True
    app.session_state["query_ottimizzata"] = "nuova query"
    app.session_state["price_min_input"] = 300
    app.session_state["budget_max_input"] = 1500
    app.session_state["ultimo_top_n"] = 40
    app.session_state["condizione"] = "usato"
    app.session_state["fonti_selezionate"] = ["Amazon"]
    app.session_state["categoria"] = "altro"
    app.session_state["filtri_ai"] = {"ram": "32 GB", "storage": "1 TB"}
    app.session_state["_last_search_params"] = saved_params
    app.run()
    assert not app.exception
    run_search.assert_not_called()

    app.button(key="refresh_offerte").click().run()
    assert not app.exception
    run_search.assert_called_once_with(
        **saved_params,
        cerebras_client=current_client,
        force_refresh=True,
    )

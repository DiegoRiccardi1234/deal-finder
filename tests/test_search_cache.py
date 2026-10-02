"""Regressioni della cache attraverso il percorso di ricerca della UI."""

from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from offerte import cache
from offerte.models import Offerta
from ui import search


@pytest.fixture
def search_ui(monkeypatch, tmp_path):
    state = {"filtri_ai": {}, "categoria": "notebook"}
    ui = SimpleNamespace(
        session_state=state,
        secrets={},
        status=lambda *a, **kw: nullcontext(MagicMock()),
        toast=MagicMock(),
        write=MagicMock(),
        error=MagicMock(),
    )
    path = str(tmp_path / "cache.sqlite3")
    disk = SimpleNamespace(
        make_cache_key=cache.make_cache_key,
        read=lambda key, ttl: cache.read(key, ttl=ttl, path=path),
        write=lambda key, data: cache.write(key, data, path=path),
    )
    calls = []

    def backend(**kwargs):
        calls.append(kwargs)
        return [
            Offerta(
                nome=f"Notebook ricerca {len(calls)} offerta {i}",
                prezzo=100 + i,
                negozio="Negozio",
                link=f"https://example.com/{len(calls)}/{i}",
                fonte="comet.it",
            )
            for i in range(kwargs["top_n"])
        ]

    monkeypatch.setattr(search, "st", ui)
    monkeypatch.setattr(search, "_disk_cache", disk)
    monkeypatch.setattr(search, "_is_test_mode", lambda: False)
    monkeypatch.setattr(search, "_save_search", lambda **kw: None)
    monkeypatch.setattr(search, "price_history", None)
    monkeypatch.setattr(search, "cerca_offerte", backend)
    return ui, calls


def run_search(**overrides):
    params = dict(
        query="notebook",
        prezzo_min=0,
        budget_max=800,
        top_n=5,
        condizione="nuovo",
        fonti_backend=["comet"],
        cerebras_client=None,
    )
    search._run_search(**(params | overrides))


@pytest.mark.parametrize("disk_only", [False, True])
@pytest.mark.parametrize("change", ["top_n", "filtri", "categoria", "ai"])
def test_changed_search_does_not_reuse_partial_or_incompatible_results(
    search_ui, disk_only, change
):
    ui, calls = search_ui
    run_search()
    if disk_only:
        ui.session_state.pop("_search_cache", None)
    overrides = {}
    if change == "top_n":
        overrides["top_n"] = 20
    elif change == "filtri":
        ui.session_state["filtri_ai"] = {"ram": "16gb", "storage": "1tb"}
    elif change == "categoria":
        ui.session_state["categoria"] = "tablet"
    else:
        overrides["cerebras_client"] = object()
    run_search(**overrides)
    assert len(calls) == 2
    assert len(ui.session_state["risultati"]) == overrides.get("top_n", 5)
    assert ui.session_state["risultati"][0].nome.startswith("Notebook ricerca 2")


@pytest.mark.parametrize("disk_only", [False, True])
def test_identical_search_reuses_cache_and_explicit_refresh_fetches_again(search_ui, disk_only):
    ui, calls = search_ui
    run_search()
    if disk_only:
        ui.session_state.pop("_search_cache", None)
    run_search()
    assert len(calls) == 1
    run_search(force_refresh=True)
    assert len(calls) == 2
    assert ui.session_state["risultati"][0].nome.startswith("Notebook ricerca 2")
    run_search()
    assert len(calls) == 2


def test_cached_search_resets_obsolete_result_filters_and_keeps_filter_snapshot(search_ui):
    ui, calls = search_ui
    filters = {"ram": "16gb"}
    ui.session_state["filtri_ai"] = filters
    run_search()
    filters["ram"] = "32gb"
    assert ui.session_state["filtri_ai_ultima_ricerca"] == {"ram": "16gb"}
    assert ui.session_state["_last_search_params"]["filtri_ai"] == {"ram": "16gb"}
    ui.session_state["filtri_ai"] = {"ram": "16gb"}
    ui.session_state["filtro_fonti_tabella"] = ["ebay.it"]
    ui.session_state["comparatore_selezione"] = ["https://obsolete.example"]
    run_search()
    assert len(calls) == 1
    assert ui.session_state["filtro_fonti_tabella"] == []
    assert ui.session_state["comparatore_selezione"] == []


def test_cache_key_canonicalizes_filter_and_source_order():
    first = cache.make_cache_key(
        "Notebook",
        0,
        800,
        "nuovo",
        ["comet", "ebay"],
        top_n=20,
        categoria="notebook",
        filtri_ai={"ram": "16gb", "storage": "1tb"},
    )
    second = cache.make_cache_key(
        " notebook ",
        0,
        800,
        "nuovo",
        ["ebay", "comet"],
        top_n=20,
        categoria="notebook",
        filtri_ai={"storage": "1tb", "ram": "16gb"},
    )
    assert first == second

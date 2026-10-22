"""Fonti dichiarate, condizioni verificabili e isolamento fra ricerche."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from offerte import orchestrator, source_status
from offerte.models import Offerta
from offerte.source_registry import (
    SOURCE_REGISTRY,
    default_source_labels,
    labels_to_keys,
    source_options,
)


def test_registry_exposes_trovaprezzi_and_verified_vinted():
    assert "Trovaprezzi" in source_options()
    assert "Trovaprezzi" in default_source_labels()
    assert "Vinted" in source_options()
    assert "Vinted" in default_source_labels()
    assert labels_to_keys(["Trovaprezzi", "Amazon"]) == ["trovaprezzi", "amazon"]


def test_used_condition_skips_sources_without_a_verifiable_filter(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Una fonte che non verifica usato non deve partire")

    monkeypatch.setattr(orchestrator, "scrape_comet", unexpected)
    monkeypatch.setattr(orchestrator, "scrape_wallapop", unexpected)
    assert (
        orchestrator.cerca_offerte("notebook", condizione="usato", fonti=["comet", "wallapop"])
        == []
    )
    statuses = source_status.snapshot()
    assert all(status.state == source_status.DISABLED for status in statuses.values())
    assert "usato" in statuses["comet"].detail


def test_used_condition_retains_capable_sources(monkeypatch):
    offer = Offerta("Notebook usato", 200, "Test", "https://example.com", "amazon.it")
    monkeypatch.setattr(orchestrator, "scrape_amazon", lambda *a: [offer])
    monkeypatch.setattr(orchestrator, "fetch_specs_ai", lambda *a: None)
    assert orchestrator.cerca_offerte("notebook", condizione="usato", fonti=["amazon"]) == [offer]


def test_empty_explicit_source_selection_never_fetches_defaults(monkeypatch):
    monkeypatch.setattr(
        orchestrator, "scrape_amazon", lambda *a: pytest.fail("No sources selected")
    )
    assert orchestrator.cerca_offerte("notebook", fonti=[]) == []


def test_unconfigured_ebay_is_a_final_status():
    assert orchestrator.cerca_offerte("notebook", fonti=["ebay"]) == []
    status = source_status.get("ebay")
    assert status.state == source_status.DISABLED
    assert "chiavi" in status.detail


def test_late_worker_cannot_change_a_new_search_or_frozen_timeout():
    old = source_status.reset()
    started, finish = threading.Event(), threading.Event()

    def late():
        with source_status.use_registry(old):
            started.set()
            finish.wait(2)
            source_status.report_ok("amazon", 99)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(late)
        assert started.wait(2)
        source_status.report_error("amazon", "timeout ricerca")
        source_status.finish()
        new = source_status.reset()
        source_status.report_empty("amazon")
        finish.set()
        future.result(timeout=2)
    assert source_status.get("amazon").state == source_status.EMPTY
    with source_status.use_registry(old):
        assert source_status.get("amazon").state == source_status.ERROR
    with source_status.use_registry(new):
        assert source_status.get("amazon").state == source_status.EMPTY


@pytest.mark.parametrize("code,state", [(404, "error"), (403, "blocked"), (429, "blocked")])
def test_adapter_diagnostics_keep_only_http_status(code, state):
    source_status.reset()
    source_status.report_error("vinted", RuntimeError(f"secret-cookie-123 error code: {code}"))
    status = source_status.get("vinted")
    assert status.state == state
    assert status.detail == f"HTTP {code}"
    assert "secret" not in status.describe()


@pytest.mark.parametrize("photo_format", ["photos", "image", "photo"])
def test_vinted_compatibility_supports_new_and_legacy_images(monkeypatch, photo_format):
    from types import SimpleNamespace
    from offerte.scrapers import vinted

    image = "https://example.com/photo.jpg"
    item = SimpleNamespace(title="Notebook", price=100, url="https://www.vinted.it/items/123")
    setattr(
        item,
        photo_format,
        [SimpleNamespace(url=image)]
        if photo_format == "photos"
        else {"url": image}
        if photo_format == "photo"
        else image,
    )
    scraper = SimpleNamespace(search=lambda params: [item])
    monkeypatch.setattr(vinted, "VintedScraper", lambda url: scraper)
    result = vinted.scrape_vinted("notebook", 0, 500, ["notebook"])
    assert len(result) == 1
    assert result[0].immagine == image


def test_vinted_missing_dependency_has_explicit_status(monkeypatch):
    from offerte.scrapers import vinted

    source_status.reset()
    monkeypatch.setattr(vinted, "VintedScraper", None)
    assert vinted.scrape_vinted("notebook", 0, 500, ["notebook"]) == []
    assert source_status.get("vinted").state == source_status.DISABLED


def test_source_monitor_uses_saved_sources_and_status_not_current_registry():
    from ui.sources import _status_rows_for_sources

    source_status.reset()
    source_status.report_blocked("comet", 403)
    snapshot = {
        "sources": ["comet"],
        "statuses": {"comet": {"fonte": "comet", "state": "empty", "detail": "", "results": 0}},
    }
    rows = _status_rows_for_sources([], ["amazon"], "", snapshot=snapshot)
    assert len(rows) == 1
    assert rows[0]["label"] == "Comet.it"
    assert rows[0]["status"] == "ONLINE"
    assert "nessun risultato" in rows[0]["detail"]


def test_source_monitor_does_not_mark_filtered_success_as_pending():
    from ui.sources import _status_rows_for_sources

    snapshot = {
        "sources": ["comet"],
        "statuses": {"comet": {"fonte": "comet", "state": "ok", "detail": "", "results": 20}},
    }
    rows = _status_rows_for_sources([], ["comet"], "", snapshot=snapshot)
    assert rows[0]["status"] == "ONLINE"
    assert "nessuna offerta finale" in rows[0]["detail"]

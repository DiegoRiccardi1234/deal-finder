"""Product families, explicit variants and capacity evidence regressions."""

import threading
import time

import pytest

from offerte import orchestrator
from offerte.cache import make_cache_key
from offerte.models import Offerta
from offerte.parsing import tokenize_query
from offerte.product_constraints import different_storage, matches_product, storage_values


@pytest.mark.parametrize(
    "query,title",
    [
        ("iphone 15", "Apple iPhone 15 Pro Max 128GB"),
        ("iphone 15", "iPhone 15 Plus 256 GB"),
        ("iPhone 18", "Apple iPhone 18 Pro Max 1TB"),
        ("Galaxy S26", "Samsung Galaxy S26+ 256GB"),
        ("Galaxy S26", "Samsung GalaxyS26 Ultra 256GB"),
        ("Pura80", "Huawei Pura80 256GB"),
        ("Pixel 10", "Google Pixel 10 Pro XL 512 GB"),
        ("Xiaomi 17", "Xiaomi 17 Ultra 512GB"),
        ("OnePlus 15", "OnePlus 15R 256GB"),
        ("Redmi Note 15", "Xiaomi Redmi Note 15 Pro Plus 256GB"),
        ("iphone 29", "iPhone 29 Plus 256GB"),
    ],
)
def test_family_includes_variants_without_generation_whitelist(query, title):
    assert matches_product(title, query, variant_mode="family")


@pytest.mark.parametrize(
    "query,title",
    [
        ("iphone 15", "iPhone 16 128GB"),
        ("iphone 15", "iPhone 115 128GB"),
        ("iPhone Air", "iPhone 17 Pro 256GB"),
        ("iPhone Duo", "iPhone 18 Pro Max 512GB"),
        ("Galaxy S26", "Samsung Galaxy S25 Ultra 256GB"),
        ("Pixel 10", "Google Pixel 9 256GB"),
    ],
)
def test_family_never_substitutes_other_model(query, title):
    assert not matches_product(title, query)


def test_exact_and_exclusions_survive_family_default():
    assert not matches_product("iPhone 15 Pro 128GB", "iPhone 15", variant_mode="exact")
    assert not matches_product("iPhone 15 Pro Max", "iPhone 15 Pro")
    assert not matches_product("iPhone SE 2020", "iPhone SE 2022", variant_mode="exact")
    assert matches_product("Samsung Galaxy S26 Plus 256GB", "Galaxy S26+", variant_mode="exact")
    assert not matches_product("iPhone 15 Plus", "iPhone 15", excluded_variants=["Plus"])
    assert "plus" in tokenize_query("Galaxy S26+")


def test_storage_requires_real_different_capacities():
    assert not different_storage("iPhone 15 128GB", "iPhone 15 128 GB")
    assert not different_storage("Notebook 1TB SSD", "Notebook 1024 GB SSD")
    assert not different_storage("iPhone 15", "iPhone 15 128 GB")
    assert not different_storage("iPhone 128GB/256GB", "iPhone 256GB")
    assert not different_storage("iPhone 128/256GB", "iPhone 256GB")
    assert different_storage("iPhone 128GB", "iPhone 256 GB")
    assert storage_values("Phone 8GB RAM 256 GB") == {256}


def test_collect_never_calls_configured_ai(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Collect must never invoke an AI client, ranker or enrichment")

    monkeypatch.setattr(orchestrator, "filtra_risultati_con_ai", forbidden)
    monkeypatch.setattr(orchestrator, "fetch_specs_ai", forbidden)
    monkeypatch.setattr(orchestrator, "print_results", lambda *args: None)
    monkeypatch.setattr(
        orchestrator,
        "scrape_amazon",
        lambda *args: [
            Offerta("Notebook 8GB RAM 128GB SSD", 100, "Test", "https://test/1"),
            Offerta("Notebook 16GB RAM 512GB SSD", 200, "Test", "https://test/2"),
        ],
    )
    result = orchestrator.cerca_offerte(
        "notebook",
        fonti=["amazon"],
        filtri_ai={"ram": "16GB"},
        analysis_mode="collect",
        cerebras_client=object(),
    )
    assert [offer.prezzo for offer in result] == [200]


def test_collect_with_filters_ranks_before_truncation(monkeypatch):
    monkeypatch.setattr(orchestrator, "print_results", lambda *args: None)
    monkeypatch.setattr(
        orchestrator,
        "scrape_amazon",
        lambda *args: [
            Offerta("Notebook 16GB RAM", price, "Test", f"https://test/{price}")
            for price in [900, 800, 100]
        ],
    )
    result = orchestrator.cerca_offerte(
        "notebook", fonti=["amazon"], filtri_ai={"ram": "16GB"}, analysis_mode="collect", top_n=1
    )
    assert result[0].prezzo == 100


def test_cancellation_returns_available_results_without_waiting_for_http(monkeypatch):
    event = threading.Event()
    monkeypatch.setattr(orchestrator, "print_results", lambda *args: None)
    monkeypatch.setattr(
        orchestrator,
        "scrape_amazon",
        lambda *args: [
            Offerta("Notebook", 200, "Test", "https://test/2"),
        ],
    )

    def slow(*args):
        event.wait(0.15)
        event.set()
        time.sleep(0.5)
        return []

    monkeypatch.setattr(orchestrator, "scrape_mediaworld", slow)
    started = time.monotonic()
    result = orchestrator.cerca_offerte(
        "notebook",
        fonti=["amazon", "mediaworld"],
        analysis_mode="collect",
        cancel_event=event,
    )
    assert len(result) == 1
    assert time.monotonic() - started < 0.5


def test_cache_separates_nullable_budget_and_analysis_context():
    base = ("iphone 15", 0, None, "tutti", ["amazon"])
    key = make_cache_key(*base, analysis_mode="collect")
    assert key != make_cache_key(*base, analysis_mode="auto")
    assert key != make_cache_key(*base, analysis_mode="collect", variant_mode="exact")
    assert key != make_cache_key(*base, analysis_mode="collect", connection_id="codex")

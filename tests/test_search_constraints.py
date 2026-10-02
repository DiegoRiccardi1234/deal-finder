"""Regressioni per requisiti tecnici e selezione delle offerte migliori."""

import pytest

from offerte import ai, orchestrator
from offerte.filters import _hard_spec_mismatch_reasons
from offerte.models import Offerta


def _offer(nome: str, prezzo: float = 300.0) -> Offerta:
    return Offerta(nome=nome, prezzo=prezzo, negozio="Test", link=f"https://test/{prezzo}")


@pytest.mark.parametrize("filtri", [{"ram": "16gb"}, {"storage_gb": "512"}])
def test_all_hard_mismatches_return_empty_without_ai(monkeypatch, filtri) -> None:
    def unexpected_ai():
        pytest.fail("Non serve ranking AI se nessuna offerta supera i requisiti")

    monkeypatch.setattr(ai, "_get_cerebras_client", unexpected_ai)
    offers = [_offer("Notebook 8GB RAM 256GB SSD")]

    assert ai.filtra_risultati_con_ai(offers, filtri) == []


def test_soft_fallback_keeps_only_hard_compatible_offers(monkeypatch) -> None:
    monkeypatch.setattr(ai, "_get_cerebras_client", lambda: None)
    incompatible = _offer("Notebook nero 8GB RAM", 200)
    compatible = _offer("Notebook nero 32GB RAM", 400)

    result = ai.filtra_risultati_con_ai(
        [incompatible, compatible], {"ram_gb": "16", "colore": "rosa"}
    )

    assert result == [compatible]


@pytest.mark.parametrize("target", ["1tb", "1 TB", "1024gb", "1024"])
def test_storage_units_reject_smaller_disk_and_accept_requested_disk(target) -> None:
    filtri = {"storage": target}

    assert _hard_spec_mismatch_reasons(_offer("Notebook 256GB SSD"), filtri)
    assert _hard_spec_mismatch_reasons(_offer("Notebook 1TB SSD"), filtri) == []


def test_best_offer_survives_source_order_before_global_ranking(monkeypatch) -> None:
    offers = [_offer(f"Notebook {i}", 100 + i) for i in range(15)]
    cheapest = _offer("Notebook miglior prezzo", 50)
    offers.append(cheapest)
    monkeypatch.setattr(orchestrator, "scrape_amazon", lambda *args: offers)
    monkeypatch.setattr(orchestrator, "fetch_specs_ai", lambda *args: None)
    monkeypatch.setattr(orchestrator, "print_results", lambda *args: None)

    result = orchestrator.cerca_offerte("notebook", top_n=10, fonti=["amazon"])

    assert len(result) == 10
    assert result[0] == cheapest

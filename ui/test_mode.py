"""ui: ui/test_mode.py"""

from __future__ import annotations

from typing import Any


try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]

from offerte_tech import Offerta

try:
    from search_history import load_history, save_search as _save_search
except ImportError:

    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None


def _build_mock_results(
    query: str, categoria: str, prezzo_min: int, budget_max: int
) -> list[Offerta]:
    base_results = [
        Offerta(
            nome="Apple iPhone 17 128GB",
            prezzo=799.0,
            negozio="Mock Store",
            link="https://example.com/iphone-17-128",
            fonte="amazon.it",
            spedizione="Prime ✅",
            specs={"display": '6.1" OLED', "processore": "A19", "ram": "8 GB", "storage": "128 GB"},
        ),
        Offerta(
            nome="Apple iPhone 17 256GB",
            prezzo=899.0,
            negozio="Mock Store Plus",
            link="https://example.com/iphone-17-256",
            fonte="ebay.it",
            spedizione="€ 7,99",
            specs={"display": '6.1" OLED', "processore": "A19", "ram": "8 GB", "storage": "256 GB"},
        ),
        Offerta(
            nome="Samsung Galaxy S25 256GB",
            prezzo=749.0,
            negozio="Mock Galaxy Shop",
            link="https://example.com/galaxy-s25",
            fonte="amazon.it",
            spedizione="Gratuita ✅",
            specs={
                "display": '6.2" AMOLED',
                "processore": "Snapdragon",
                "ram": "12 GB",
                "storage": "256 GB",
            },
        ),
    ]
    categoria_norm = str(categoria or "altro").lower()
    results = (
        base_results
        if categoria_norm == "tech" or "iphone" in query.lower()
        else [
            Offerta(
                nome="Nike Felpa Donna M Cotone",
                prezzo=59.0,
                negozio="Mock Fashion",
                link="https://example.com/felpa",
                fonte="vinted.it",
                spedizione="€ 4,99",
                specs={"brand": "Nike", "taglia": "M", "materiale": "cotone", "genere": "donna"},
            )
        ]
    )
    return [item for item in results if prezzo_min <= item.prezzo <= budget_max]

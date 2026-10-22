"""Catalogo condiviso delle fonti e dei vincoli realmente applicabili."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceInfo:
    label: str
    ui_label: str
    default_enabled: bool = True
    credentials: tuple[str, ...] = ()
    conditions: frozenset[str] = frozenset({"tutti", "nuovo"})
    optional_dependency: str = ""


_ALL_CONDITIONS = frozenset({"tutti", "nuovo", "usato"})
SOURCE_REGISTRY = {
    "amazon": SourceInfo("Amazon.it", "Amazon", conditions=_ALL_CONDITIONS),
    "ebay": SourceInfo(
        "eBay.it",
        "eBay",
        credentials=("EBAY_APP_ID", "EBAY_CERT_ID"),
        conditions=_ALL_CONDITIONS,
    ),
    "trovaprezzi": SourceInfo("Trovaprezzi.it", "Trovaprezzi"),
    # Verifica live 22/10: il nuovo adapter 5.1 torna a produrre offerte.
    "vinted": SourceInfo("Vinted.it", "Vinted", conditions=frozenset({"tutti"})),
    "euronics": SourceInfo("Euronics.it", "Euronics"),
    "unieuro": SourceInfo("Unieuro.it", "Unieuro"),
    "mediaworld": SourceInfo("MediaWorld.it", "MediaWorld", conditions=_ALL_CONDITIONS),
    # Il marketplace contiene anche articoli nuovi: non equivale a "solo usato".
    "wallapop": SourceInfo("Wallapop.it", "Wallapop", conditions=frozenset({"tutti"})),
    "comet": SourceInfo("Comet.it", "Comet"),
    "expert": SourceInfo("Expert.it", "Expert"),
    "aliexpress": SourceInfo("AliExpress", "AliExpress", False, optional_dependency="playwright"),
    "subito": SourceInfo("Subito.it", "Subito", False, conditions=frozenset({"tutti"})),
    "temu": SourceInfo("Temu", "Temu", False),
    "alibaba": SourceInfo("Alibaba", "Alibaba", False),
}


def source_options() -> list[str]:
    """Le fonti offerte dalla UI, incluse quelle opzionali già presenti."""
    return [
        info.ui_label
        for key, info in SOURCE_REGISTRY.items()
        if key not in {"subito", "temu", "alibaba"}
    ]


def default_source_labels() -> list[str]:
    return [info.ui_label for info in SOURCE_REGISTRY.values() if info.default_enabled]


def labels_to_keys(labels: list[str]) -> list[str]:
    mapping = {info.ui_label: key for key, info in SOURCE_REGISTRY.items()}
    return [mapping[label] for label in labels if label in mapping]

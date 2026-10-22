"""Filtri e riepiloghi dei soli risultati visibili, senza inferire dati mancanti."""

from __future__ import annotations

import re

from offerte.models import Offerta
from offerte.offer_warnings import get_offer_warnings
from offerte.source_registry import SOURCE_REGISTRY


def comparable_offers(offers: list[Offerta]) -> list[Offerta]:
    return [offer for offer in offers if not get_offer_warnings(offer.nome)]


def offer_condition(offer: Offerta, searched_condition: str = "tutti") -> str:
    source = str(offer.fonte).lower().replace(" ", "")
    info = next(
        (
            info
            for key, info in SOURCE_REGISTRY.items()
            if source in {key, info.label.lower().replace(" ", ""), f"{key}.it"}
        ),
        None,
    )
    # Un vincolo applicato dallo scraper vale più di una deduzione dal titolo.
    if info and searched_condition in {"nuovo", "usato"} and searched_condition in info.conditions:
        return searched_condition
    title = offer.nome.lower()
    if re.search(r"\b(?:usato|ricondizionato|refurbished|rigenerato|used)\b", title):
        return "usato"
    if re.search(r"\b(?:nuovo|nuovissimo|sigillato)\b", title):
        return "nuovo"
    if (
        info
        and info.default_enabled
        and "usato" not in info.conditions
        and "nuovo" in info.conditions
    ):
        return "nuovo"
    return "n.d."


def filter_visible_offers(
    offers: list[Offerta],
    *,
    sources: list[str],
    price_range: tuple[float, float],
    condition: str,
    searched_condition: str = "tutti",
) -> list[Offerta]:
    return [
        offer
        for offer in offers
        if (not sources or offer.fonte in sources)
        and price_range[0] <= offer.prezzo <= price_range[1]
        and (condition == "tutti" or offer_condition(offer, searched_condition) == condition)
    ]

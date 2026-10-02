"""Segnali espliciti nel titolo da verificare prima di confrontare i prezzi.

Non determina affidabilità del venditore e non scarta offerte. I prezzi non
sono confrontati statisticamente: titolo e specs non garantiscono che modello,
variante, condizione e contenuto della confezione siano davvero comparabili.
"""

from __future__ import annotations

import re


_EMPTY_BOX = re.compile(r"\b(?:scatola\s+vuota|solo\s+(?:la\s+)?scatola)\b", re.IGNORECASE)
_PARTS = re.compile(r"\b(?:per\s+ricambi|non\s+funzionant[ei])\b", re.IGNORECASE)
_DEPOSIT = re.compile(r"\bacconto\b", re.IGNORECASE)
_PARTIAL_PRICE = re.compile(
    r"\b(?:prezzo|canone|rata)\s+mensile\b"
    r"|\b(?:rata|rate)\s+(?:(?:mensile|mensili)\s+)?(?:da\s+|di\s+)?"
    r"\d+(?:[,.]\d+)?\s*(?:€|euro\b|eur\b)"
    r"|\b\d+(?:[,.]\d+)?\s*(?:€|euro\b|eur\b)\s*"
    r"(?:/\s*mese\b|al\s+mese\b|mensili\b|a\s+rata\b|per\s+rata\b)",
    re.IGNORECASE,
)
_NEGATION = re.compile(r"\b(?:senza|nessun|nessuno|no|non|zero)\s*$", re.IGNORECASE)
_ZERO_DEPOSIT = re.compile(r"\s*(?:di\s+)?(?:zero|0(?:[,.]0+)?)(?![\w,.])", re.IGNORECASE)


def get_offer_warnings(title: str) -> list[str]:
    """Motiva solo segnali espliciti, senza inferire difetti dai prezzi bassi."""
    title = str(title or "")
    warnings = []
    for pattern, explanation in (
        (_EMPTY_BOX, "potrebbe riferirsi alla sola scatola, non al prodotto"),
        (_PARTS, "potrebbe riferirsi a un prodotto guasto o destinato ai ricambi"),
        (_DEPOSIT, "il prezzo potrebbe essere un acconto, non il totale"),
        (_PARTIAL_PRICE, "il prezzo potrebbe essere una rata o un costo mensile, non il totale"),
    ):
        for match in pattern.finditer(title):
            if _NEGATION.search(title[: match.start()]):
                continue
            if pattern is _DEPOSIT and _ZERO_DEPOSIT.match(title[match.end() :]):
                continue
            warnings.append(f"Il titolo indica «{match.group(0)}»: {explanation}.")
            break
    return warnings

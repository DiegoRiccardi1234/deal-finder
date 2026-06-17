"""offerte: offerte/scrapers/vinted.py"""

from __future__ import annotations

import math

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant


def scrape_vinted(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """Scraper per Vinted.it tramite libreria vinted-scraper (gestione cookie automatica)."""
    if condizione == "nuovo":
        print("\nℹ️ Vinted mostra solo articoli usati — salto per condizione=nuovo")
        return []
    print(f'\n🔍 Cerco su Vinted.it: "{query}"')
    risultati: list[Offerta] = []
    if VintedScraper is None:
        print("    ⚠️  Vinted.it: libreria vinted-scraper non installata.")
        return risultati
    try:
        scraper = VintedScraper("https://www.vinted.it")
        items = scraper.search({"search_text": query, "order": "price_low_to_high", "per_page": 48})
        print(f"    ✅ Vinted.it: {len(items)} risultati")
        for item in items:
            try:
                nome = str(item.title or "").strip()
                if not nome:
                    continue
                prezzo = float(item.price)
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                link = str(item.url)
                foto = item.photo
                img_url = foto.get("url", "") if isinstance(foto, dict) else ""
                risultati.append(
                    Offerta(
                        nome=nome,
                        prezzo=prezzo,
                        negozio="Vinted",
                        link=link,
                        fonte="vinted.it",
                        spedizione="n.d.",
                        immagine=img_url,
                    )
                )
            except (AttributeError, TypeError, ValueError):
                continue
    except Exception as exc:
        print(f"    ❌ Vinted.it: errore → {exc}")
    return risultati

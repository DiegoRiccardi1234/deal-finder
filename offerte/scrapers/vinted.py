"""offerte: offerte/scrapers/vinted.py"""

from __future__ import annotations

import math
import contextlib

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant
from offerte.log import get_logger
from offerte.source_status import report_error, report_disabled, get as get_status

log = get_logger(__name__)


def scrape_vinted(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """Scraper per Vinted.it tramite libreria vinted-scraper (gestione cookie automatica)."""
    if condizione != "tutti":
        report_disabled("vinted", f"filtro '{condizione}' non verificabile su questa fonte")
        return []
    print(f'\n🔍 Cerco su Vinted.it: "{query}"')
    risultati: list[Offerta] = []
    if VintedScraper is None:
        print("    ⚠️  Vinted.it: libreria vinted-scraper non installata.")
        report_disabled("vinted", "dipendenza vinted-scraper mancante")
        return risultati
    scraper = None
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
                link = str(item.url or "").strip()
                if not link:
                    continue
                # 5.1 espone photos: list[VintedImage], mentre i vecchi oggetti
                # e i fixture possono ancora avere photo: dict.
                foto = getattr(item, "photo", None)
                img_url = foto.get("url", "") if isinstance(foto, dict) else ""
                photos = getattr(item, "photos", None) or []
                if not img_url and photos:
                    first = photos[0]
                    img_url = (
                        first.get("url", "")
                        if isinstance(first, dict)
                        else getattr(first, "url", "")
                    )
                if not img_url:
                    img_url = getattr(item, "image", "") or ""
                risultati.append(
                    Offerta(
                        nome=nome,
                        prezzo=prezzo,
                        negozio="Vinted",
                        link=link,
                        fonte="vinted.it",
                        spedizione="n.d.",
                        immagine=str(img_url or ""),
                    )
                )
            except (AttributeError, TypeError, ValueError):
                continue
    except Exception as exc:
        report_error("vinted", exc)
        status = get_status("vinted")
        log.error("Vinted.it: %s", status.describe() if status else type(exc).__name__)
    finally:
        # Le versioni 4 e 5 supportano il context manager. Chiudiamo anche se
        # la ricerca o il parsing fallisce, senza richiedere quel protocollo ai fixture.
        close = getattr(scraper, "__exit__", None)
        if callable(close):
            with contextlib.suppress(Exception):
                close(None, None, None)
    return risultati

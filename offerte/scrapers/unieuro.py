"""offerte: offerte/scrapers/unieuro.py"""

from __future__ import annotations

import json
import math

import requests

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.http import _random_delay
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant

# Unieuro usa Algolia come motore di ricerca prodotti. Non serve Playwright né
# OAuth token.
# La chiave è *search-only pubblica*: il sito la pubblica nel proprio bundle JS,
# dove il browser di qualsiasi visitatore la legge, e questo scraper chiama lo
# stesso endpoint di ricerca del loro frontend. Non è una credenziale di questo
# progetto e concede solo la ricerca del catalogo in lettura.
# Vedi la sezione "Third-party public keys in source" in SECURITY.md.
_UNIEURO_ALGOLIA_URL = (
    "https://mnbcenyfii-dsn.algolia.net/1/indexes/*/queries"
    "?x-algolia-api-key=977ed8d06b718d4929ca789c78c4107a"
    "&x-algolia-application-id=MNBCENYFII"
)


def scrape_unieuro(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per Unieuro.it tramite Algolia (API pubblica embedded nel frontend)."""
    print(f'\n🔍 Cerco su Unieuro.it: "{query}"')
    risultati: list[Offerta] = []
    try:
        payload = json.dumps(
            {
                "requests": [
                    {
                        "indexName": "sgmproducts_prod",
                        "query": query,
                        "hitsPerPage": 48,
                        "page": 0,
                        "facetFilters": [],
                        "numericFilters": [],
                    }
                ]
            }
        )
        headers = {
            "Content-Type": "text/plain",
            "Origin": "https://www.unieuro.it",
            "Referer": "https://www.unieuro.it/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        }
        resp = requests.post(_UNIEURO_ALGOLIA_URL, data=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("results", [{}])[0].get("hits", [])
        if not hits:
            print("    ⚠️  Unieuro.it: nessun prodotto trovato via Algolia.")
            return risultati
        print(f"    ✅ Unieuro.it (Algolia): {len(hits)} risultati")
        for hit in hits:
            try:
                nome = str(hit.get("title_it") or hit.get("name") or "").strip()
                if not nome:
                    continue
                prezzo_raw = (
                    hit.get("discountedPrice") or hit.get("facetPrice") or hit.get("originalPrice")
                )
                if prezzo_raw is None:
                    continue
                prezzo = parse_price(str(prezzo_raw))
                if not math.isfinite(prezzo):
                    continue
                url_path = str(hit.get("productUrl_it") or hit.get("url") or "").strip()
                if not url_path:
                    continue
                link = (
                    url_path if url_path.startswith("http") else f"https://www.unieuro.it{url_path}"
                )
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                spedizione = "Spedizione gratuita" if hit.get("hasFreeDelivery") else "n.d."
                img_path = str(hit.get("imageUrl") or "")
                img_url = (
                    f"https://www.unieuro.it{img_path}"
                    if img_path and not img_path.startswith("http")
                    else img_path
                )
                risultati.append(
                    Offerta(
                        nome=nome,
                        prezzo=prezzo,
                        negozio="Unieuro",
                        link=link,
                        fonte="unieuro.it",
                        spedizione=spedizione,
                        immagine=img_url,
                    )
                )
            except (AttributeError, TypeError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Unieuro.it: errore HTTP {status}.")
    except Exception as exc:
        print(f"    ❌ Unieuro.it: errore inatteso → {exc}")

    _random_delay()
    return risultati

"""offerte: offerte/scrapers/comet.py"""

from __future__ import annotations

import json
import math

import requests

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant

# SCRAPER — comet.it  (Algolia API)
# ===========================================================================
_COMET_ALGOLIA_URL = "https://mvk2s77iyi-dsn.algolia.net/1/indexes/*/queries"
_COMET_ALGOLIA_APP_ID = "MVK2S77IYI"
_COMET_ALGOLIA_API_KEY = "f7f4f516742fcb4597c1e71641f7d0ed"


def scrape_comet(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per Comet.it tramite Algolia (API pubblica embedded nel frontend).

    NOTE API (valide ad aprile 2026):
        App ID: MVK2S77IYI  |  Index: products  |  Filter: visible=1
        Hit fields: name, pFinale (prezzo finale), url (assoluto), image (assoluto)
    """
    print(f'\n🔍 Cerco su Comet.it: "{query}"')
    risultati: list[Offerta] = []
    try:
        payload = json.dumps(
            {
                "requests": [
                    {
                        "indexName": "products",
                        "query": query,
                        "hitsPerPage": 40,
                        "page": 0,
                        "filters": "visible=1",
                    }
                ]
            }
        )
        headers = {
            "Content-Type": "application/json",
            "x-algolia-api-key": _COMET_ALGOLIA_API_KEY,
            "x-algolia-application-id": _COMET_ALGOLIA_APP_ID,
            "Origin": "https://www.comet.it",
            "Referer": "https://www.comet.it/",
        }
        resp = requests.post(_COMET_ALGOLIA_URL, data=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        hits = resp.json().get("results", [{}])[0].get("hits", [])
        if not hits:
            print("    ⚠️  Comet.it: nessun prodotto trovato.")
            return risultati
        print(f"    ✅ Comet.it (Algolia): {len(hits)} risultati")
        for hit in hits:
            try:
                nome = str(hit.get("name") or "").strip()
                if not nome:
                    continue
                prezzo = float(hit.get("pFinale") or hit.get("pListino") or 0)
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not hit.get("isAcquistabile", True):
                    continue
                link = str(hit.get("url") or "").strip()
                if not link:
                    continue
                img_url = str(hit.get("image") or "").strip()
                risultati.append(
                    Offerta(
                        nome=nome,
                        prezzo=prezzo,
                        negozio="Comet",
                        link=link,
                        fonte="comet.it",
                        spedizione="n.d.",
                        immagine=img_url,
                    )
                )
            except (AttributeError, TypeError, ValueError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Comet.it: errore HTTP {status}.")
    except Exception as exc:
        print(f"    ❌ Comet.it: errore inatteso → {exc}")
    return risultati

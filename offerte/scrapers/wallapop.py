"""offerte: offerte/scrapers/wallapop.py"""

from __future__ import annotations

import math

import requests

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.http import get_headers
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant
from offerte.log import get_logger
from offerte.source_status import report_blocked, report_error

log = get_logger(__name__)

# SCRAPER — wallapop.com
# ===========================================================================
_WALLAPOP_COMPONENTS_URL = "https://api.wallapop.com/api/v3/search/components"
_WALLAPOP_SECTION_URL = "https://api.wallapop.com/api/v3/search/section"


def scrape_wallapop(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """
    Scraper per Wallapop tramite API pubblica (due step: components → section).

    NOTE API (valide ad aprile 2026):
        Step 1: GET /api/v3/search/components → estrae search_id e category_id
        Step 2: GET /api/v3/search/section con search_id → items[]
        Campi: title, price.amount, web_slug, images[0].urls.small
        URL articolo: https://it.wallapop.com/item/{web_slug}
        Richiede header x-deviceid (UUID random), x-appversion, mpid.
    """
    import uuid as _uuid

    print(f'\n🔍 Cerco su Wallapop: "{query}"')
    risultati: list[Offerta] = []
    _headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it,it-IT;q=0.9",
        "Referer": "https://it.wallapop.com/",
        "User-Agent": get_headers().get("User-Agent", "Mozilla/5.0"),
        "deviceos": "0",
        "x-deviceos": "0",
        "x-appversion": "818810",
        "x-deviceid": str(_uuid.uuid4()),
        "mpid": "-3421950124112390907",
        "trackinguserid": "-3421950124112390907",
    }
    try:
        r1 = requests.get(
            _WALLAPOP_COMPONENTS_URL,
            params={
                "keywords": query,
                "latitude": 41.9028,
                "longitude": 12.4964,
                "source": "search_box",
            },
            headers=_headers,
            timeout=TIMEOUT,
        )
        r1.raise_for_status()

        search_id = category_id = None
        for comp in r1.json().get("components", []):
            qp = (comp.get("type_data") or {}).get("query_params") or {}
            if qp.get("search_id"):
                search_id = qp["search_id"]
                category_id = qp.get("category_id")
                break
        if not search_id:
            print("    ⚠️  Wallapop: search_id non trovato nella risposta components.")
            return risultati

        params2: dict = {
            "keywords": query,
            "source": "search_box",
            "search_id": search_id,
            "latitude": 41.9028,
            "longitude": 12.4964,
            "order_by": "most_relevance",
            "section_type": "organic_search_results",
        }
        if category_id:
            params2["category_id"] = category_id

        r2 = requests.get(_WALLAPOP_SECTION_URL, params=params2, headers=_headers, timeout=TIMEOUT)
        r2.raise_for_status()
        items = r2.json().get("data", {}).get("section", {}).get("items", [])
        print(f"    ✅ Wallapop: {len(items)} risultati")

        for item in items:
            try:
                nome = str(item.get("title") or "").strip()
                if not nome:
                    continue
                price_data = item.get("price") or {}
                prezzo = float(price_data.get("amount", 0))
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                web_slug = str(item.get("web_slug") or "").strip()
                if not web_slug:
                    continue
                link = f"https://it.wallapop.com/item/{web_slug}"
                imgs = item.get("images") or []
                img_url = imgs[0].get("urls", {}).get("small", "") if imgs else ""
                ship = item.get("shipping") or {}
                spedizione = (
                    "Spedizione disponibile" if ship.get("user_allows_shipping") else "Solo ritiro"
                )
                if condizione == "nuovo" and item.get("is_refurbished"):
                    continue
                risultati.append(
                    Offerta(
                        nome=nome,
                        prezzo=prezzo,
                        negozio="Wallapop",
                        link=link,
                        fonte="wallapop.com",
                        spedizione=spedizione,
                        immagine=img_url,
                    )
                )
            except (AttributeError, TypeError, ValueError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        log.warning("Wallapop: errore HTTP %s.", status)
        report_blocked("wallapop", status)
    except Exception as exc:
        log.error("Wallapop: errore inatteso → %s", exc)
        report_error("wallapop", exc)
    return risultati

"""offerte: offerte/scrapers/ebay.py"""

from __future__ import annotations

import time

import requests

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.http import _random_delay
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant
from offerte.log import get_logger
from offerte.source_status import report_blocked, report_error

log = get_logger(__name__)
from offerte.scrapers._base import _get_ebay_token


def scrape_ebay(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    condizione: str,
    query_tokens: list[str],
    app_id: str,
    cert_id: str,
) -> list[Offerta]:
    """Scraper eBay.it tramite eBay Browse API ufficiale."""
    print(f'\n🔍 Cerco su eBay.it API: "{query}"')
    risultati: list[Offerta] = []

    if not app_id or not cert_id:
        print("    ⚠️  eBay non configurato — chiavi mancanti.")
        return risultati

    price_max = budget_max if budget_max is not None else 999999
    price_filter = f"price:[{max(0, prezzo_min)}..{price_max}],priceCurrency:EUR"
    if condizione == "nuovo":
        api_filter = price_filter + ",conditionIds:{1000}"
    elif condizione == "usato":
        api_filter = price_filter + ",conditionIds:{3000|4000|5000}"
    else:
        api_filter = price_filter

    params = {
        "q": query,
        "limit": 50,
        "filter": api_filter,
    }
    endpoint = "https://api.ebay.com/buy/browse/v1/item_summary/search"

    for attempt in range(2):
        try:
            token = _get_ebay_token(app_id, cert_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_IT",
                "Accept": "application/json",
            }
            response = requests.get(endpoint, headers=headers, params=params, timeout=TIMEOUT)

            if response.status_code == 401 and attempt == 0:
                _EBAY_TOKEN_CACHE["token"] = None
                _EBAY_TOKEN_CACHE["expires_at"] = 0.0
                continue

            response.raise_for_status()

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(f"JSON eBay malformato: {exc}") from exc

            items = payload.get("itemSummaries", [])
            for item in items:
                try:
                    nome = str(item.get("title") or "").strip()
                    if not nome or not is_relevant(nome, query_tokens, strict_specs=False):
                        continue

                    price_obj = item.get("price") or {}
                    prezzo = float(price_obj.get("value"))
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    seller = item.get("seller") or {}
                    link = str(item.get("itemWebUrl") or "").strip()
                    if not link:
                        continue

                    try:
                        _img_obj = item.get("image") or {}
                        _img_url = (
                            str(_img_obj.get("imageUrl", "") or "")
                            if isinstance(_img_obj, dict)
                            else ""
                        )
                        if not _img_url:
                            _thumbs = item.get("thumbnailImages") or []
                            if (
                                _thumbs
                                and isinstance(_thumbs, list)
                                and isinstance(_thumbs[0], dict)
                            ):
                                _img_url = str(_thumbs[0].get("imageUrl", "") or "")
                    except Exception:
                        _img_url = ""

                    risultati.append(
                        Offerta(
                            nome=nome,
                            prezzo=prezzo,
                            negozio=str(seller.get("username") or "eBay"),
                            link=link,
                            fonte="ebay.it",
                            spedizione="n.d.",
                            immagine=_img_url,
                        )
                    )
                except (TypeError, ValueError):
                    continue

            log.info("eBay Browse API: %d risultati validi", len(risultati))
            _random_delay()
            return risultati

        except requests.Timeout:
            log.error("eBay Browse API: timeout.")
            report_error("ebay", "timeout")
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "sconosciuto"
            log.error("eBay Browse API: errore HTTP %s.", status)
            report_blocked("ebay", status)
        except requests.ConnectionError:
            log.error("eBay Browse API: errore di connessione.")
            report_error("ebay", "connessione")
        except Exception as exc:
            log.error("eBay Browse API: errore inatteso → %s", exc)
            report_error("ebay", exc)

        if attempt == 0:
            time.sleep(1.0)

    _random_delay()
    return []

"""offerte: offerte/scrapers/alibaba.py"""

from __future__ import annotations

import math
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.http import fetch_with_retry, get_headers, _random_delay
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant
from offerte.log import get_logger
from offerte.source_status import report_disabled

log = get_logger(__name__)


def scrape_alibaba(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per Alibaba.com — DOM vuoto (89KB ma nessun testo estraibile, JS-rendered)."""
    log.info("Alibaba.com: fonte disattivata (DOM JS-rendered, nessun prezzo estraibile)")
    report_disabled("alibaba", "pagina JS-rendered con CAPTCHA, serve un browser headless")
    return []
    # Implementazione HTML conservata per riferimento futuro:
    url = f"https://www.alibaba.com/trade/search?SearchText={quote_plus(query)}&SortType=price_asc"

    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://www.alibaba.com/"
        headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.5"

        resp = fetch_with_retry(url, headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        cards = (
            soup.select('div[class*="organic-list-offer"]')
            or soup.select('div[class*="offer-list-row"]')
            or soup.select(".J-offer-wrapper")
        )

        if not cards:
            print("    ⚠️  Alibaba.com: nessun risultato trovato (possibile blocco o layout JS).")
            return risultati

        print(f"    ✅ Trovate {len(cards)} card grezze su Alibaba.com")

        for card in cards:
            try:
                nome_tag = (
                    card.select_one('[class*="subject"]')
                    or card.select_one('[class*="title"]')
                    or card.select_one("h2")
                )
                if not nome_tag:
                    continue
                nome = nome_tag.get_text(strip=True)
                if not nome:
                    continue

                prezzo_tag = card.select_one('[class*="price"]')
                if not prezzo_tag:
                    continue
                prezzo = parse_price(prezzo_tag.get_text(strip=True))
                if not math.isfinite(prezzo):
                    continue

                link_tag = card.select_one("a[href]")
                if not link_tag:
                    continue
                href = str(link_tag.get("href", "") or "")
                link = (
                    href
                    if href.startswith("http")
                    else "https:" + href
                    if href.startswith("//")
                    else "https://www.alibaba.com" + href
                )

                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue

                risultati.append(
                    Offerta(
                        nome=nome,
                        prezzo=prezzo,
                        negozio="Alibaba",
                        link=link,
                        fonte="alibaba.com",
                        spedizione="n.d.",
                    )
                )
            except (AttributeError, TypeError):
                continue

    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Alibaba.com: accesso bloccato (HTTP {status}), salto la fonte.")
    except requests.Timeout:
        print("    ❌ Alibaba.com: timeout.")
    except requests.ConnectionError:
        print("    ❌ Alibaba.com: impossibile connettersi.")
    except Exception as exc:
        print(f"    ❌ Alibaba.com: errore inatteso → {exc}")

    _random_delay()
    return risultati

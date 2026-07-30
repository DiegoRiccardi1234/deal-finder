"""offerte: offerte/scrapers/euronics.py"""

from __future__ import annotations

import json
import math
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.http import fetch_with_retry, get_headers, _random_delay
from offerte.log import get_logger
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant
from offerte.source_status import report_blocked, report_error

log = get_logger(__name__)


def scrape_euronics(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per euronics.it.

    NOTE SELETTORI (verificati 2026-07-30):
        URL ricerca: endpoint AJAX SFCC `Search-UpdateGrid` (vedi FIX CLOUDFLARE)
        Container:   div.new-product-tile.flex-fill  (grid view — evita i duplicati della list-view)
        Nome:        span.tile-name          (es. "APPLE - iPhone 15 Plus 128GB-Nero")
        Prezzo:      span.price-formatted    (es. "€ 699,00" — prezzo di vendita)
        Link:        a.link-pdp[href] (relativo → prepend euronics.it)

    FIX CLOUDFLARE (2026-07): `/search?q=` risponde 403 (`<title>Just a moment...`),
    mentre la home resta 200 → la regola WAF colpisce il path di ricerca, non il
    sito. Euronics è Salesforce Commerce Cloud (`/on/demandware.static/…`), quindi
    si usa l'endpoint AJAX che la griglia stessa chiama per aggiornarsi:
    `/on/demandware.store/Sites-euronics-Site/it_IT/Search-UpdateGrid?q=…&sz=…`
    con `X-Requested-With: XMLHttpRequest`. Ritorna lo stesso markup delle tile.

    FIX STORICO: Accept-Encoding: identity è obbligatorio — senza, il server restituisce
    una pagina compressa/minimizzata di 38KB (bot-detection) invece dei 686KB reali.

    ATTENZIONE PREZZI: nella tile convivono `span.price-formatted` (prezzo di
    vendita) e `span.value` dentro `.more-price-details` (prezzo *consigliato*,
    più alto). L'ordine dei selettori non è cosmetico: invertirlo fa riportare il
    listino invece dello sconto.
    """
    url = (
        "https://www.euronics.it/on/demandware.store/Sites-euronics-Site/it_IT/"
        f"Search-UpdateGrid?q={quote_plus(query)}&sz=48"
    )
    log.info('Cerco su Euronics.it: "%s"', query)
    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.euronics.it/"
        headers["Accept-Encoding"] = "identity"  # FIX: senza questo, risposta bot-detection 38KB
        headers["X-Requested-With"] = "XMLHttpRequest"  # richiesto dall'endpoint Search-UpdateGrid

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            log.warning(
                "Euronics.it: accesso bloccato (HTTP %s), salto la fonte.", resp.status_code
            )
            report_blocked("euronics", resp.status_code)
            return risultati
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        page_title = str((soup.title.string or "") if soup.title else "")
        if any(
            kw in page_title.lower() for kw in ("captcha", "robot", "sorry", "access denied", "404")
        ):
            log.warning("Euronics.it: blocco anti-bot o pagina non trovata, salto la fonte.")
            report_blocked("euronics", "challenge")
            return risultati

        # ── Strategia 1: CSS selettori div.new-product-tile.flex-fill ──
        # Seleziona solo le card griglia (evita duplicati della list-view: .new-product-tile-list)
        cards = soup.select("div.new-product-tile.flex-fill")
        if not cards:
            # Fallback: qualsiasi tile con classe new-product-tile (esclude esplicitamente list)
            cards = [
                c
                for c in soup.select("[class*='new-product-tile']")
                if "new-product-tile-list" not in " ".join(c.get("class") or [])
            ]

        if cards:
            log.info("Euronics.it: %d card trovate", len(cards))
            seen_links: set[str] = set()
            # Token senza parole-categoria: Euronics filtra già per notebook/laptop.
            # Manteniamo solo token di dimensione/brand (es. "14") per is_relevant.
            _euronics_cat_words = {
                "notebook",
                "laptop",
                "smartphone",
                "tablet",
                "telefono",
                "cellulare",
                "monitor",
                "cuffie",
                "auricolari",
                "pc",
                "ultrabook",
            }
            eu_tokens = [t for t in query_tokens if t not in _euronics_cat_words] or query_tokens
            for card in cards:
                try:
                    # Filtra accessori: accetta solo categorie laptop/notebook
                    # Nome: span.tile-name
                    nome_tag = (
                        card.select_one("span.tile-name")
                        or card.select_one("[class*='tile-name']")
                        or card.select_one("h2")
                        or card.select_one("h3")
                    )
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue

                    # Prezzo di VENDITA: span.price-formatted (es. "€ 699,00").
                    # `span.value` va tenuto solo come fallback: dentro
                    # `.more-price-details` contiene il prezzo *consigliato*, più
                    # alto, e metterlo per primo gonfia i prezzi degli scontati.
                    prezzo_tag = (
                        card.select_one("span.price-formatted")
                        or card.select_one("[class*='price-formatted']")
                        or card.select_one("[class*='price'] span.value")
                        or card.select_one("span.value")
                        or card.select_one("[class*='price']")
                    )
                    if not prezzo_tag:
                        continue
                    prezzo = parse_price(prezzo_tag.get_text(" ", strip=True))
                    if not math.isfinite(prezzo):
                        continue

                    # Link: primo a[href] nel card (href relativo → prepend base url)
                    link_tag = card.select_one("a.link-pdp[href]") or card.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.euronics.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    if not is_relevant(nome, eu_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    try:
                        _img_tag = card.select_one("img")
                        _img_url = (
                            str(_img_tag.get("src", "") or _img_tag.get("data-src", "") or "")
                            if _img_tag
                            else ""
                        )
                    except Exception:
                        _img_url = ""

                    risultati.append(
                        Offerta(
                            nome=nome,
                            prezzo=prezzo,
                            negozio="Euronics",
                            link=link,
                            fonte="euronics.it",
                            spedizione="n.d.",
                            immagine=_img_url,
                        )
                    )
                except (AttributeError, TypeError):
                    continue

        # ── Strategia 2: JSON-LD fallback ──
        if not risultati:
            for script in soup.find_all("script", {"type": "application/ld+json"}):
                try:
                    data = json.loads(str(script.string or ""))
                    # Supporta sia lista che singolo oggetto e ItemList
                    items_raw = []
                    if isinstance(data, list):
                        items_raw = data
                    elif isinstance(data, dict):
                        if data.get("@type") == "ItemList":
                            items_raw = [
                                el.get("item", el) for el in data.get("itemListElement", [])
                            ]
                        else:
                            items_raw = [data]
                    for item_data in items_raw:
                        if not isinstance(item_data, dict):
                            continue
                        nome = str(item_data.get("name", "") or "").strip()
                        if not nome:
                            continue
                        offers = item_data.get("offers", {}) or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        prezzo = parse_price(str(offers.get("price", "") or ""))
                        if not math.isfinite(prezzo):
                            continue
                        link = str(item_data.get("url", "") or offers.get("url", "") or "").strip()
                        if not link:
                            continue
                        if not is_relevant(nome, query_tokens, strict_specs=False):
                            continue
                        if not _within_price_range(prezzo, prezzo_min, budget_max):
                            continue
                        try:
                            _img_raw = item_data.get("image", "")
                            _img_url = str(
                                _img_raw[0]
                                if isinstance(_img_raw, list) and _img_raw
                                else _img_raw or ""
                            )
                        except Exception:
                            _img_url = ""
                        risultati.append(
                            Offerta(
                                nome=nome,
                                prezzo=prezzo,
                                negozio="Euronics",
                                link=link,
                                fonte="euronics.it",
                                spedizione="n.d.",
                                immagine=_img_url,
                            )
                        )
                except Exception:
                    continue

        if not risultati:
            log.warning("Euronics.it: nessun risultato parsabile (selettori cambiati o blocco).")

    except requests.Timeout:
        log.error("Euronics.it: timeout raggiunto anche dopo i retry.")
        report_error("euronics", "timeout")
    except requests.ConnectionError:
        log.error("Euronics.it: impossibile connettersi al sito.")
        report_error("euronics", "connessione")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        log.error("Euronics.it: errore HTTP %s.", status)
        report_blocked("euronics", status)
    except Exception as exc:
        log.error("Euronics.it: errore inatteso → %s", exc)
        report_error("euronics", exc)

    _random_delay()
    return risultati

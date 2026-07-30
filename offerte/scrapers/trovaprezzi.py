"""offerte: offerte/scrapers/trovaprezzi.py"""

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


def scrape_trovaprezzi(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper trovaprezzi.it (sito diretto).

    URL: /categoria.aspx?libera={query}  →  redirect a /{categoria}/offerte/{slug}
    oppure, se la query combacia con un singolo prodotto, a
    /{categoria}/prezzi-scheda-prodotto/{slug}.

    Selettori primari — offerte dei singoli merchant, presenti su entrambi i
    tipi di pagina (verificati 2026-07-30):
        Container: li.listing_item
        Nome:      a.item_name          (l'href è il link /goto/... di redirect)
        Prezzo:    div.item_basic_price (fallback div.item_total_price)
        Negozio:   span.merchant_name
        Spedizione: div.free_shipping | div.item_delivery_price

    Fallback secondari, nell'ordine: card aggregate `a.suggested_product[href]`
    (solo pagine categoria, con `.name` e `.price_range`) e infine JSON-LD.
    """
    url = f"https://www.trovaprezzi.it/categoria.aspx?libera={quote_plus(query)}"
    log.info('Cerco su Trovaprezzi.it: "%s"', query)

    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.trovaprezzi.it/"

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            log.warning(
                "Trovaprezzi.it: accesso bloccato (HTTP %s), salto la fonte.", resp.status_code
            )
            report_blocked("trovaprezzi", resp.status_code)
            return risultati

        resp.raise_for_status()

        base_url = resp.url  # URL finale dopo redirect (usato per la paginazione)

        seen_links: set[str] = set()
        _tp_cat_words = {
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
        }
        tp_tokens = [t for t in query_tokens if t not in _tp_cat_words] or query_tokens

        def _parse_listing_items(soup_p: BeautifulSoup) -> int:
            """Parsa le offerte dei singoli merchant (`li.listing_item`).

            È il blocco presente sia sulle pagine categoria sia sulle schede
            prodotto — dove la ricerca viene rediretta quando la query combacia
            con un solo prodotto — quindi è il selettore più affidabile dei due.
            Ogni voce è un prezzo realmente acquistabile presso un negozio, non
            un intervallo aggregato come in `a.suggested_product`.
            """
            added_li = 0
            for item in soup_p.select("li.listing_item"):
                nome_tag = item.select_one("a.item_name")
                if not nome_tag:
                    continue
                nome = nome_tag.get_text(" ", strip=True)
                if not nome:
                    continue
                prezzo_tag = item.select_one("div.item_basic_price") or item.select_one(
                    "div.item_total_price"
                )
                prezzo = parse_price(prezzo_tag.get_text(" ", strip=True) if prezzo_tag else "")
                if not math.isfinite(prezzo):
                    continue
                href = str(nome_tag.get("href", "") or "")
                if not href:
                    continue
                link = href if href.startswith("http") else f"https://www.trovaprezzi.it{href}"
                if link in seen_links:
                    continue
                seen_links.add(link)
                if not is_relevant(nome, tp_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                # Su un aggregatore il negozio reale è il merchant, non "Trovaprezzi".
                merchant_tag = item.select_one("span.merchant_name")
                merchant = merchant_tag.get_text(strip=True) if merchant_tag else ""
                if item.select_one("div.free_shipping"):
                    spedizione = "Gratuita"
                else:
                    sped_tag = item.select_one("div.item_delivery_price")
                    spedizione = sped_tag.get_text(" ", strip=True) if sped_tag else "n.d."
                img_tag = item.select_one("a.item_image img") or item.select_one("img")
                immagine = (
                    str(img_tag.get("src", "") or img_tag.get("data-src", "") or "")
                    if img_tag
                    else ""
                )
                risultati.append(
                    Offerta(
                        nome=nome,
                        prezzo=prezzo,
                        negozio=merchant or "Trovaprezzi",
                        link=link,
                        fonte="trovaprezzi.it",
                        spedizione=spedizione,
                        immagine=immagine,
                    )
                )
                added_li += 1
            return added_li

        def _parse_page_tp(html: str) -> int:
            """Parsa una pagina trovaprezzi, aggiunge offerte valide, ritorna il numero aggiunto."""
            soup_p = BeautifulSoup(html, "html.parser")
            page_title_p = str((soup_p.title.string or "") if soup_p.title else "")
            if any(
                kw in page_title_p.lower()
                for kw in ("sorry", "captcha", "robot", "unusual traffic", "404")
            ):
                return 0

            # `added` va inizializzato prima di ogni ramo: il fallback JSON-LD
            # sotto lo incrementa e lo ritorna, e prima sollevava UnboundLocalError
            # (mascherato dai due `except Exception` che lo racchiudevano).
            added = 0

            # 1) Offerte dei singoli merchant — il percorso principale.
            added += _parse_listing_items(soup_p)
            if added:
                return added

            # 2) Card prodotto aggregate, presenti solo sulle pagine categoria.
            cards_p = soup_p.select("a.suggested_product[href]")
            if not cards_p:
                cards_p = soup_p.select("[class*='product'][href]")
            if not cards_p:
                # 3) Fallback JSON-LD
                for _script in soup_p.find_all("script", {"type": "application/ld+json"}):
                    try:
                        _ld = json.loads(str(_script.string or ""))
                        _ld_items = (
                            _ld
                            if isinstance(_ld, list)
                            else ([_ld] if isinstance(_ld, dict) else [])
                        )
                        for _ld_item in _ld_items:
                            if not isinstance(_ld_item, dict):
                                continue
                            _nome = str(_ld_item.get("name", "") or "").strip()
                            _url = str(_ld_item.get("url", "") or "").strip()
                            _offers = _ld_item.get("offers", {}) or {}
                            if isinstance(_offers, list):
                                _offers = _offers[0] if _offers else {}
                            _prezzo = parse_price(str(_offers.get("price", "") or ""))
                            if not _nome or not _url or not math.isfinite(_prezzo):
                                continue
                            if _url in seen_links:
                                continue
                            seen_links.add(_url)
                            if not is_relevant(_nome, tp_tokens, strict_specs=False):
                                continue
                            if not _within_price_range(_prezzo, prezzo_min, budget_max):
                                continue
                            try:
                                _img_url = str(_ld_item.get("image", "") or "")
                                if isinstance(_ld_item.get("image"), list):
                                    _img_url = (
                                        str(_ld_item["image"][0]) if _ld_item["image"] else ""
                                    )
                            except Exception:
                                _img_url = ""
                            risultati.append(
                                Offerta(
                                    nome=_nome,
                                    prezzo=_prezzo,
                                    negozio="Trovaprezzi",
                                    link=_url,
                                    fonte="trovaprezzi.it",
                                    spedizione="n.d.",
                                    immagine=_img_url,
                                )
                            )
                            added += 1
                    except Exception:
                        continue
                return added
            for card in cards_p:
                try:
                    nome_tag = card.select_one(".name") or card.select_one("[class*='name']")
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue
                    prezzo_tag = card.select_one(".price_range") or card.select_one(
                        "[class*='price']"
                    )
                    prezzo_txt = prezzo_tag.get_text(" ", strip=True) if prezzo_tag else ""
                    prezzo = parse_price(prezzo_txt)
                    if not math.isfinite(prezzo):
                        continue
                    href = str(card.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.trovaprezzi.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)
                    if not is_relevant(nome, tp_tokens, strict_specs=False):
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
                            negozio="Trovaprezzi",
                            link=link,
                            fonte="trovaprezzi.it",
                            spedizione="n.d.",
                            immagine=_img_url,
                        )
                    )
                    added += 1
                except (AttributeError, TypeError):
                    continue
            return added

        p1 = _parse_page_tp(resp.text)
        if not p1:
            log.warning("Trovaprezzi.it: nessun risultato parsabile (selettori cambiati o blocco).")
        else:
            log.info("Trovaprezzi.it: %d card trovate", len(risultati))
            # Paginazione: prova pagina 2 (stop se vuota o errore)
            for _pn in range(2, 3):
                _sep = "&" if "?" in base_url else "?"
                _page_url = f"{base_url}{_sep}paginaCorrente={_pn}"
                try:
                    _pr = fetch_with_retry(_page_url, {**headers, "Referer": base_url})
                    if _pr.status_code != 200:
                        break
                    _added = _parse_page_tp(_pr.text)
                    if not _added:
                        break
                    log.info("Trovaprezzi.it p.%d: +%d offerte", _pn, _added)
                    _random_delay()
                except Exception:
                    break

        if not risultati:
            log.warning("Trovaprezzi.it: risultati vuoti dopo parsing.")

    except requests.Timeout:
        log.error("Trovaprezzi.it: timeout raggiunto anche dopo i retry.")
        report_error("trovaprezzi", "timeout")
    except requests.ConnectionError:
        log.error("Trovaprezzi.it: impossibile connettersi al sito.")
        report_error("trovaprezzi", "connessione")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        log.error("Trovaprezzi.it: errore HTTP %s.", status)
        report_blocked("trovaprezzi", status)
    except Exception as exc:
        log.error("Trovaprezzi.it: errore inatteso → %s", exc)
        report_error("trovaprezzi", exc)

    _random_delay()
    return risultati

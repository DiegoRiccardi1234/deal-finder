"""offerte: offerte/scrapers/mediaworld.py"""

from __future__ import annotations

import json
import math
import re
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.http import fetch_with_retry, get_headers, _random_delay
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant


def scrape_mediaworld(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """
    Scraper per MediaWorld.it.

    Parsing multi-strategia (priorità decrescente):
      1. article[data-test="mms-product-card"] (confermato a marzo 2026):
           - Nome:    [data-test="product-title"]
           - Link:   primo a[href] nell'article
           - Prezzo: regex su testo grezzo "Consigliato X,–€Y,00€Z,00" → min(prezzi)
      2. JSON-LD <script type="application/ld+json"> @type=ItemList
      3. CSS selettori generici come last-resort
    """
    url = (
        f"https://www.mediaworld.it/it/search.html?q={quote_plus(query)}&sortby=rating&pageNumber=0"
    )
    print(f'\n🔍 Cerco su MediaWorld.it: "{query}"')
    risultati: list[Offerta] = []
    _KW_USATO_MW = {
        "ricondizionato",
        "usato",
        "second life",
        "refurbished",
        "open box",
        "seconda vita",
    }

    def _cond_filter(r: list[Offerta]) -> list[Offerta]:
        if condizione == "tutti":
            return r
        if condizione == "usato":
            return [o for o in r if any(k in o.nome.lower() for k in _KW_USATO_MW)]
        return [o for o in r if not any(k in o.nome.lower() for k in _KW_USATO_MW)]

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.mediaworld.it/it/"

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(
                f"    ⚠️  MediaWorld.it: accesso bloccato (HTTP {resp.status_code}), salto la fonte."
            )
            return risultati
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        page_title = str((soup.title.string or "") if soup.title else "")
        if any(kw in page_title.lower() for kw in ("captcha", "robot", "sorry", "access denied")):
            print("    ⚠️  MediaWorld.it: blocco anti-bot, salto la fonte.")
            return risultati

        # ── Strategia 1: article[data-test="mms-product-card"] ──
        articles = soup.select('article[data-test="mms-product-card"]')
        if not articles:
            articles = soup.select("article")  # fallback senza data-test

        if articles:
            seen_links: set[str] = set()
            for art in articles:
                try:
                    # Nome: data-test="product-title"
                    nome_tag = art.select_one('[data-test="product-title"]')
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue

                    # Link: primo a[href] nell'article
                    link_tag = art.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.mediaworld.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    # Prezzo: prova prima il selettore specifico, poi fallback regex
                    # MediaWorld mostra sia il prezzo pieno sia le rate mensili (/mese).
                    # Occorre ignorare le rate e prendere il prezzo intero.
                    _RATE_KW = ("/mese", "mese", "/rata", "rata", "/mo", "mensil")

                    def _strip_rate_context(
                        raw: str, context: str, _rate_kw: tuple = _RATE_KW
                    ) -> bool:
                        """Restituisce True se 'raw' appare vicino a keyword da rata."""
                        idx = context.find(raw)
                        if idx == -1:
                            return False
                        window = context[idx : idx + len(raw) + 20].lower()
                        return any(k in window for k in _rate_kw)

                    price_tag = art.select_one('[data-test="product-price"]') or art.select_one(
                        '[data-test*="price"]'
                    )
                    price_text_full = art.get_text(" ", strip=True).replace("\u00a0", " ")
                    if price_tag:
                        pt_text = price_tag.get_text(" ", strip=True).replace("\u00a0", " ")
                        # Se il testo del tag contiene indicatori di rata, ignora il tag
                        # e leggi il prezzo dal testo completo dell'article
                        if any(k in pt_text.lower() for k in _RATE_KW):
                            price_tag = None  # forza fallback
                        else:
                            prezzo = parse_price(pt_text)
                    if not price_tag:
                        # fallback regex sul testo grezzo, escludendo le rate
                        raw_prices = re.findall(r"\d{1,4},\d{2}", price_text_full)
                        prices: list[float] = []
                        for rp in raw_prices:
                            if _strip_rate_context(rp, price_text_full):
                                continue
                            p = parse_price(rp)
                            if math.isfinite(p) and p > 20:
                                prices.append(p)
                        if not prices:
                            continue
                        # Prende il massimo: le rate sono sempre il valore minore
                        prezzo = max(prices)
                    if not math.isfinite(prezzo):
                        continue

                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    try:
                        _img_tag = art.select_one("img")
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
                            negozio="MediaWorld",
                            link=link,
                            fonte="mediaworld.it",
                            spedizione="n.d.",
                            immagine=_img_url,
                        )
                    )
                except (AttributeError, TypeError):
                    continue

            if risultati:
                print(f"    ✅ MediaWorld.it (article): {len(risultati)} risultati validi")
                _random_delay()
                return _cond_filter(risultati)

        # ── Strategia 2: JSON-LD con @type="ItemList" ──
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(str(script.string or ""))
                if not isinstance(data, dict):
                    continue
                if data.get("@type") != "ItemList":
                    continue
                item_list = data.get("itemListElement", [])
                if not item_list:
                    continue

                for el in item_list:
                    try:
                        product = el.get("item", el)
                        if not isinstance(product, dict):
                            continue
                        nome = str(product.get("name", "") or "").strip()
                        if not nome:
                            continue
                        product_url = str(product.get("url", "") or "").strip()
                        if not product_url:
                            continue
                        link = (
                            product_url
                            if product_url.startswith("http")
                            else f"https://www.mediaworld.it{product_url}"
                        )

                        offers = product.get("offers", {}) or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        # Il prezzo in ItemList di MediaWorld è già numerico (es. 629)
                        price_raw = offers.get("price", "")
                        prezzo = parse_price(str(price_raw))
                        if not math.isfinite(prezzo):
                            continue

                        if not is_relevant(nome, query_tokens, strict_specs=False):
                            continue
                        if not _within_price_range(prezzo, prezzo_min, budget_max):
                            continue
                        try:
                            _img_raw = product.get("image", "")
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
                                negozio="MediaWorld",
                                link=link,
                                fonte="mediaworld.it",
                                spedizione="n.d.",
                                immagine=_img_url,
                            )
                        )
                    except (TypeError, ValueError, KeyError):
                        continue

                if risultati:
                    print(
                        f"    ✅ MediaWorld.it (JSON-LD ItemList): {len(risultati)} risultati validi"
                    )
                    _random_delay()
                    return _cond_filter(risultati)

            except Exception:
                continue

        # ── Strategia 2: CSS selettori ──
        cards = (
            soup.select(".product-grid__item")
            or soup.select("[class*='ProductItem']")
            or soup.select("[class*='product-item']")
            or soup.select("[data-product-id]")
            or soup.select(".product")
        )

        if cards:
            print(f"    ✅ Trovate {len(cards)} card su MediaWorld.it")
            seen_links: set[str] = set()
            for card in cards:
                try:
                    nome_tag = (
                        card.select_one("[class*='product-name']")
                        or card.select_one("[class*='ProductName']")
                        or card.select_one("h2")
                        or card.select_one("h3")
                        or card.select_one("a[title]")
                    )
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True) or str(nome_tag.get("title", "") or "")
                    if not nome:
                        continue

                    prezzo_tag = card.select_one("[class*='price']") or card.select_one(
                        "[class*='Price']"
                    )
                    if not prezzo_tag:
                        continue
                    prezzo = parse_price(prezzo_tag.get_text(" ", strip=True))
                    if not math.isfinite(prezzo):
                        continue

                    link_tag = card.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.mediaworld.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    if not is_relevant(nome, query_tokens, strict_specs=False):
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
                            negozio="MediaWorld",
                            link=link,
                            fonte="mediaworld.it",
                            spedizione="n.d.",
                            immagine=_img_url,
                        )
                    )
                except (AttributeError, TypeError):
                    continue

        # ── Strategia 3: JSON-LD Product singoli ──
        if not risultati:
            for script in soup.find_all("script", {"type": "application/ld+json"}):
                try:
                    data = json.loads(str(script.string or ""))
                    items: list = data if isinstance(data, list) else [data]
                    for item_data in items:
                        if not isinstance(item_data, dict):
                            continue
                        if item_data.get("@type") != "Product":
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
                                negozio="MediaWorld",
                                link=link,
                                fonte="mediaworld.it",
                                spedizione="n.d.",
                                immagine=_img_url,
                            )
                        )
                except Exception:
                    continue

        if not risultati:
            print("    ⚠️  MediaWorld.it: nessun risultato parsabile (selettori cambiati o blocco).")

    except requests.Timeout:
        print("    ❌ MediaWorld.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ MediaWorld.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ MediaWorld.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ MediaWorld.it: errore inatteso → {exc}")

    _random_delay()
    return _cond_filter(risultati)

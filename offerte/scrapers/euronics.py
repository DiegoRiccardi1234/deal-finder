"""offerte: offerte/scrapers/euronics.py"""
from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

try:
    from cerebras_model import (
        get_best_model as _get_best_model,
        cerebras_chat_with_retry as _cerebras_chat_lib,
    )
except Exception:
    _get_best_model = None  # type: ignore[assignment]
    _cerebras_chat_lib = None  # type: ignore[assignment]

_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"
from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.http import fetch_with_retry, get_headers, _random_delay
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant
from offerte.scrapers._base import _get_ebay_token

def scrape_euronics(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per euronics.it.

    NOTE SELETTORI (validi a marzo 2026):
        URL ricerca: /search?q=
        Container:   div.new-product-tile.flex-fill  (grid view — evita i duplicati della list-view)
        Nome:        span.tile-name
        Prezzo:      span.value  (visibile nella pagina, es. "€ 879,00")
        Link:        a[href] (relativo → prepend euronics.it)

    FIX STORICO: Accept-Encoding: identity è obbligatorio — senza, il server restituisce
    una pagina compressa/minimizzata di 38KB (bot-detection) invece dei 686KB reali.
    """
    url = f"https://www.euronics.it/search?q={quote_plus(query)}"
    print(f"\n🔍 Cerco su Euronics.it: \"{query}\"")
    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.euronics.it/"
        headers["Accept-Encoding"] = "identity"  # FIX: senza questo, risposta bot-detection 38KB

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(f"    ⚠️  Euronics.it: accesso bloccato (HTTP {resp.status_code}), salto la fonte.")
            return risultati
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        page_title = str((soup.title.string or "") if soup.title else "")
        if any(kw in page_title.lower() for kw in ("captcha", "robot", "sorry", "access denied", "404")):
            print("    ⚠️  Euronics.it: blocco anti-bot o pagina non trovata, salto la fonte.")
            return risultati

        # ── Strategia 1: CSS selettori div.new-product-tile.flex-fill ──
        # Seleziona solo le card griglia (evita duplicati della list-view: .new-product-tile-list)
        cards = soup.select("div.new-product-tile.flex-fill")
        if not cards:
            # Fallback: qualsiasi tile con classe new-product-tile (esclude esplicitamente list)
            cards = [c for c in soup.select("[class*='new-product-tile']")
                     if "new-product-tile-list" not in " ".join(c.get("class") or [])]

        if cards:
            print(f"    ✅ Trovate {len(cards)} card su Euronics.it")
            seen_links: set[str] = set()
            # Token senza parole-categoria: Euronics filtra già per notebook/laptop.
            # Manteniamo solo token di dimensione/brand (es. "14") per is_relevant.
            _euronics_cat_words = {
                "notebook", "laptop", "smartphone", "tablet", "telefono",
                "cellulare", "monitor", "cuffie", "auricolari", "pc", "ultrabook",
            }
            eu_tokens = [t for t in query_tokens if t not in _euronics_cat_words] or query_tokens
            for card in cards:
                try:
                    # Filtra accessori: accetta solo categorie laptop/notebook
                    # Nome: span.tile-name
                    nome_tag = (
                        card.select_one("span.tile-name") or
                        card.select_one("[class*='tile-name']") or
                        card.select_one("h2") or
                        card.select_one("h3")
                    )
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue

                    # Prezzo: span.value (il prezzo visibile sulla pagina, es. "€ 879,00")
                    prezzo_tag = (
                        card.select_one("span.value") or
                        card.select_one("[class*='price'] span.value") or
                        card.select_one("[class*='price-formatted']") or
                        card.select_one("[class*='price']")
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
                        _img_url = str(_img_tag.get("src", "") or _img_tag.get("data-src", "") or "") if _img_tag else ""
                    except Exception:
                        _img_url = ""

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="Euronics",
                        link=link, fonte="euronics.it", spedizione="n.d.", immagine=_img_url,
                    ))
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
                            items_raw = [el.get("item", el) for el in data.get("itemListElement", [])]
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
                            _img_url = str(_img_raw[0] if isinstance(_img_raw, list) and _img_raw else _img_raw or "")
                        except Exception:
                            _img_url = ""
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="Euronics",
                            link=link, fonte="euronics.it", spedizione="n.d.", immagine=_img_url,
                        ))
                except Exception:
                    continue

        if not risultati:
            print("    ⚠️  Euronics.it: nessun risultato parsabile (selettori cambiati o blocco).")

    except requests.Timeout:
        print("    ❌ Euronics.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Euronics.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ Euronics.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ Euronics.it: errore inatteso → {exc}")

    _random_delay()
    return risultati



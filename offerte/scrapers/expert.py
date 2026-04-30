"""offerte: offerte/scrapers/expert.py"""
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

def scrape_expert(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per Expert.it tramite JSON-LD ItemList nella pagina di ricerca.

    NOTE SELETTORI (validi ad aprile 2026):
        URL ricerca: /it/it/exp/shop/search?terms={query}
        Data: <script type="application/ld+json"> @type=ItemList
        Campi item: name, offers.price (str→float), url (assoluto), image (assoluto)
    """
    url = f"https://www.expert.it/it/it/exp/shop/search?terms={quote_plus(query)}"
    print(f"\n🔍 Cerco su Expert.it: \"{query}\"")
    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://www.expert.it/"
        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(f"    ⚠️  Expert.it: accesso bloccato (HTTP {resp.status_code}).")
            return risultati
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        ld_tags = soup.select('script[type="application/ld+json"]')
        items: list = []
        for tag in ld_tags:
            try:
                data = json.loads(tag.string or "")
                if data.get("@type") == "ItemList":
                    items = data.get("itemListElement", [])
                    break
            except (json.JSONDecodeError, AttributeError):
                continue

        if not items:
            print("    ⚠️  Expert.it: nessun prodotto trovato (JSON-LD ItemList assente).")
            return risultati
        print(f"    ✅ Expert.it: {len(items)} risultati")

        for el in items:
            try:
                item = el.get("item", {})
                nome = str(item.get("name") or "").strip()
                if not nome:
                    continue
                prezzo = float(item.get("offers", {}).get("price", 0))
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                link = str(item.get("url") or "").strip()
                if not link:
                    continue
                img_url = str(item.get("image") or "").strip()
                risultati.append(Offerta(
                    nome=nome, prezzo=prezzo, negozio="Expert",
                    link=link, fonte="expert.it", spedizione="n.d.", immagine=img_url,
                ))
            except (AttributeError, TypeError, ValueError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Expert.it: errore HTTP {status}.")
    except Exception as exc:
        print(f"    ❌ Expert.it: errore inatteso → {exc}")
    return risultati



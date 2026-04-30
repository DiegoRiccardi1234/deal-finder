"""offerte: offerte/scrapers/subito.py"""
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
    from offerte.ai import (
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

def scrape_subito(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """Scraper per Subito.it — bloccato da Akamai CDN (HTTP 403 con qualsiasi UA)."""
    if condizione == "nuovo":
        print("\nℹ️ Subito.it: skip (solo usato/privati)")
        return []
    print(f"\n🔍 Cerco su Subito.it: \"{query}\"")
    print("    ⚠️  Subito.it: protetto da Akamai CDN (HTTP 403). Fonte non disponibile senza browser headless.")
    return []
    # Implementazione HTML conservata per riferimento futuro:

    url = f"https://www.subito.it/annunci-italia/vendita/usato/?q={quote_plus(query)}&sort=price_asc"
    print(f"\n🔍 Cerco su Subito.it: \"{query}\"")

    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://www.subito.it/"
        headers["Accept"] = "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"

        resp = fetch_with_retry(url, headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Subito usa data-testid o classi CSS per le card annunci
        cards = soup.select('div[class*="item-card"]') or soup.select('article[class*="item"]')
        if not cards:
            # Fallback: cerca tutti i link con /annunci/ nel path
            cards = soup.select('div.items__item')
        if not cards:
            print("    ⚠️  Nessun prodotto trovato su Subito.it — possibile blocco o layout cambiato.")
            return risultati

        print(f"    ✅ Trovate {len(cards)} card grezze su Subito.it")

        for card in cards:
            try:
                nome_tag = (
                    card.select_one('h2[class*="item-title"]')
                    or card.select_one('[class*="item-title"]')
                    or card.select_one('h2')
                    or card.select_one('[data-testid="item-title"]')
                )
                if not nome_tag:
                    continue
                nome = nome_tag.get_text(strip=True)
                if not nome:
                    continue

                prezzo_tag = (
                    card.select_one('[class*="price"]')
                    or card.select_one('[data-testid*="price"]')
                )
                if not prezzo_tag:
                    continue
                prezzo_raw = prezzo_tag.get_text(" ", strip=True)
                prezzo = parse_price(prezzo_raw)
                if not math.isfinite(prezzo):
                    continue

                link_tag = card.select_one("a[href]")
                if not link_tag:
                    continue
                href = str(link_tag.get("href", "") or "")
                if not href:
                    continue
                link = href if href.startswith("http") else urljoin("https://www.subito.it", href)

                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue

                try:
                    img_tag = card.select_one("img")
                    img_url = str(img_tag.get("src", "") or "") if img_tag else ""
                except Exception:
                    img_url = ""

                risultati.append(
                    Offerta(nome=nome, prezzo=prezzo, negozio="Subito.it", link=link,
                            fonte="subito.it", spedizione="n.d.", immagine=img_url)
                )
            except (AttributeError, TypeError):
                continue

    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Subito.it: accesso bloccato (HTTP {status}), salto la fonte.")
    except requests.Timeout:
        print("    ❌ Subito.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Subito.it: impossibile connettersi al sito.")
    except Exception as exc:
        print(f"    ❌ Subito.it: errore inatteso → {exc}")

    _random_delay()
    return risultati



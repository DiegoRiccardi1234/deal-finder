"""offerte: offerte/scrapers/amazon.py"""
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

def scrape_amazon(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """
    Scraper per amazon.it.

    Ricerca su tutte le categorie Amazon (nessun filtro categoria hardcoded).

    NOTE SELETTORI (validi a marzo 2026):
        Ogni prodotto è un <div data-component-type="s-search-result">.
        Aggiornare i selettori qui se Amazon cambia il layout.
    """
    # Nota: il filtro URL `rh=p_n_condition-type` su Amazon.it produce spesso
    # pagine "Nessun risultato" anche per query valide (es. iPhone recenti).
    # Manteniamo una ricerca ampia e applichiamo il filtro condizione lato parser.
    url = f"https://www.amazon.it/s?k={quote_plus(query)}"

    print(f"\n🔍 Cerco su Amazon.it: \"{query}\"")

    risultati: list[Offerta] = []

    try:
        # UA desktop Chrome coerente con i sec-ch-ua headers (evita incongruenze
        # con UA mobile che _random_ua() può generare, causando blocchi Amazon).
        _AMAZON_UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        )
        base_headers = get_headers()
        base_headers["User-Agent"] = _AMAZON_UA
        base_headers["sec-ch-ua"] = '"Chromium";v="133", "Not(A:Brand";v="24", "Google Chrome";v="133"'
        base_headers["sec-ch-ua-mobile"] = "?0"
        base_headers["sec-ch-ua-platform"] = '"Windows"'
        base_headers["sec-fetch-dest"] = "document"
        base_headers["sec-fetch-mode"] = "navigate"
        base_headers["sec-fetch-site"] = "none"
        base_headers["sec-fetch-user"] = "?1"
        base_headers["Cache-Control"] = "max-age=0"

        with requests.Session() as session:
            # Step 1: visita la homepage per ottenere cookies reali (riduce blocchi anti-bot)
            try:
                _home_headers = dict(base_headers)
                _home_headers["Referer"] = "https://www.google.it/"
                session.get("https://www.amazon.it/", headers=_home_headers, timeout=TIMEOUT)
                time.sleep(random.uniform(0.8, 1.5))
            except Exception:
                pass

            # Step 2: ora fa la ricerca con i cookies della sessione
            search_headers = dict(base_headers)
            search_headers["Referer"] = "https://www.amazon.it/"
            resp = fetch_with_retry(url, search_headers, session=session)

            # Fallback cloud-friendly: se desktop search viene bloccata con 503,
            # prova endpoint mobile con header dedicati.
            if resp.status_code == 503:
                mobile_url = f"https://www.amazon.it/gp/aw/s?k={quote_plus(query)}"
                mobile_headers = dict(base_headers)
                mobile_headers["User-Agent"] = (
                    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/133.0.0.0 Mobile Safari/537.36"
                )
                mobile_headers["sec-ch-ua-mobile"] = "?1"
                mobile_headers["sec-fetch-site"] = "same-origin"
                mobile_headers["Referer"] = "https://www.amazon.it/"
                try:
                    resp_mobile = fetch_with_retry(mobile_url, mobile_headers, session=session, max_retries=1)
                    if resp_mobile.status_code == 200:
                        print("    ♻️  Amazon desktop bloccato (503): fallback mobile riuscito")
                        resp = resp_mobile
                except Exception:
                    pass

            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Controlla CAPTCHA / robot check
            page_title = (soup.title.string or "") if soup.title else ""
            body_snippet = soup.get_text(" ", strip=True).lower()[:2000]
            if any(kw in page_title.lower() for kw in ("sorry", "robot", "captcha", "service unavailable")) or \
               any(kw in body_snippet for kw in ("enter the characters", "tipo i caratteri", "not a robot", "unusual traffic")):
                print("    ❌ Amazon.it ha restituito una pagina anti-bot.")
                return risultati

            # ---------------------------------------------------------------
            # Parsing card prodotto
            # ---------------------------------------------------------------
            cards = soup.select('div[data-component-type="s-search-result"]')
            if not cards:
                # Fallback a selettori più permissivi in caso di layout variato.
                cards = soup.select('div.s-result-item[data-asin]')

            if not cards:
                # Retry una volta dopo breve pausa (anti-bot soft block)
                time.sleep(random.uniform(2.0, 3.5))
                try:
                    resp2 = fetch_with_retry(url, search_headers, session=session)
                    resp2.raise_for_status()
                    soup2 = BeautifulSoup(resp2.text, "html.parser")
                    cards = soup2.select('div[data-component-type="s-search-result"]')
                    if not cards:
                        cards = soup2.select('div.s-result-item[data-asin]')
                    if cards:
                        soup = soup2
                        print("    ♻️  Retry Amazon riuscito — trovate card al secondo tentativo")
                except Exception:
                    pass

        if not cards:
            print("    ⚠️  Nessun prodotto trovato su Amazon — selettore cambiato o CAPTCHA.")
            return risultati

        print(f"    ✅ Trovate {len(cards)} card grezze su Amazon.it")

        _KW_RICONDIZIONATO = {
            "ricondizionato", "refurbished", "rigenerato", "reconditioned",
            "second life", "open box", "ricondizionata", "usato", "used",
        }

        for card in cards:
            try:
                # ----- Nome prodotto -----
                nome_tag = card.select_one("h2 span.a-text-normal") or card.select_one("h2 span")
                if not nome_tag:
                    continue
                nome = nome_tag.get_text(strip=True)
                if not nome:
                    continue

                # Controlla condizione solo nel titolo, NON nel testo completo della card.
                # Amazon mostra cross-sell "Disponibile usato da €X" anche sulle card di prodotti nuovi,
                # il che causerebbe uno scarto errato di tutti i risultati.
                has_used_keyword = any(k in nome.lower() for k in _KW_RICONDIZIONATO)
                if condizione == "nuovo" and has_used_keyword:
                    continue
                if condizione == "usato" and not has_used_keyword:
                    continue

                # ----- Prezzo -----
                # Prova prima il tag già formattato (.a-offscreen), poi la composizione
                prezzo_tag = card.select_one(".a-price .a-offscreen")
                prezzo_raw = ""
                if prezzo_tag:
                    prezzo_raw = prezzo_tag.get_text(strip=True)
                else:
                    intero_tag    = card.select_one(".a-price-whole")
                    decimale_tag  = card.select_one(".a-price-fraction")
                    if intero_tag and decimale_tag:
                        intero = intero_tag.get_text(strip=True).replace(".", "").replace(",", "")
                        decimale = decimale_tag.get_text(strip=True)
                        prezzo_raw = f"{intero},{decimale}"
                    else:
                        continue  # Nessun prezzo trovato

                prezzo = parse_price(prezzo_raw)
                if not math.isfinite(prezzo):
                    continue

                # ----- Negozio / Venditore -----
                venduto_tag = card.select_one("span.a-color-secondary")
                if venduto_tag and "venduto da" in venduto_tag.get_text(strip=True).lower():
                    negozio = venduto_tag.get_text(strip=True).replace("Venduto da", "").strip()
                    negozio = negozio[:30] if negozio else "Amazon.it"
                else:
                    negozio = "Amazon.it"

                # ----- Link -----
                link_tag = card.select_one("a.a-link-normal[href]")
                if not link_tag:
                    continue
                href = str(link_tag.get("href", "") or "")

                # Esclude i link sponsorizzati /sspa/click trasformandoli nel link reale.
                if "/sspa/click" in href:
                    parsed = urlparse(href)
                    query_params = parse_qs(parsed.query)
                    raw_target = query_params.get("url", [""])[0]
                    if not raw_target:
                        continue
                    decoded_target = unquote(raw_target)
                    if decoded_target.startswith("http"):
                        link = decoded_target
                    elif decoded_target.startswith("/"):
                        link = "https://www.amazon.it" + decoded_target
                    else:
                        continue
                else:
                    link = href if href.startswith("http") else "https://www.amazon.it" + href

                # Rimuove parametri tracking Amazon (tutto dopo /ref=)
                link = re.sub(r"/ref=.*", "", link)

                # ----- Spedizione -----
                spedizione = "n.d."
                if card.select_one("i.a-icon-prime, span.a-icon-prime"):
                    spedizione = "Prime ✅"
                else:
                    spedizione = _extract_shipping_from_text(card.get_text(" ", strip=True))

                # ----- Filtri -----
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue

                try:
                    _img_tag = card.select_one("img.s-image") or card.select_one("img")
                    _img_url = str(_img_tag.get("src", "") or "") if _img_tag else ""
                except Exception:
                    _img_url = ""

                risultati.append(Offerta(nome=nome, prezzo=prezzo, negozio=negozio,
                                         link=link, fonte="amazon.it", spedizione=spedizione, immagine=_img_url))

            except (AttributeError, TypeError):
                continue

    except requests.Timeout:
        print("    ❌ Amazon.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Amazon.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else 0
        print(f"    ❌ Amazon.it: errore HTTP {status}.")
        if status == 503 and st is not None:
            try:
                st.warning(
                    "⚠️ **Amazon.it non disponibile da cloud** — Amazon blocca le richieste dai server cloud "
                    "(Heroku, Railway, ecc.). Le altre fonti (eBay, Euronics, MediaWorld) "
                    "funzionano normalmente. Per includere Amazon, esegui l'app in locale con: "
                    "`streamlit run app.py`"
                )
            except Exception:
                pass
        if status == 503:
            print("    ❌ Amazon.it: bloccato da cloud (HTTP 503) — funziona solo in locale.")
    except Exception as exc:
        print(f"    ❌ Amazon.it: errore inatteso → {exc}")

    _random_delay()
    return risultati



"""offerte: offerte/scrapers/temu.py"""

from __future__ import annotations

import json
import math
import re
from urllib.parse import quote_plus

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


def scrape_temu(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per Temu — SPA vuota (2.9KB, nessun dato nel DOM)."""
    print(f'\n🔍 Cerco su Temu.com: "{query}"')
    print(
        "    ⚠️  Temu.com: SPA completamente client-side (pagina HTML vuota 2.9KB). Fonte non disponibile senza browser headless."
    )
    return []
    # Implementazione JSON conservata per riferimento futuro:
    url = f"https://www.temu.com/it/search_result.html?search_key={quote_plus(query)}&sort_type=6"

    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://www.temu.com/it/"
        headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.5"

        resp = fetch_with_retry(url, headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Temu inietta dati prodotto in tag <script> come JSON
        for script in soup.find_all("script"):
            script_text = script.string or ""
            if '"goods_list"' in script_text or '"goodsList"' in script_text:
                # Cerca array di prodotti nel JSON
                for pattern in (
                    r'"goods_list"\s*:\s*(\[.*?\])\s*[,}]',
                    r'"goodsList"\s*:\s*(\[.*?\])\s*[,}]',
                ):
                    m = re.search(pattern, script_text, re.DOTALL)
                    if m:
                        try:
                            items = json.loads(m.group(1))
                            for item in items[:40]:
                                nome = str(
                                    item.get("goods_name", "") or item.get("title", "") or ""
                                )
                                if not nome:
                                    continue
                                price_val = item.get("price_info", {})
                                prezzo_raw = str(
                                    price_val.get("price", "")
                                    or price_val.get("min_price", "")
                                    or item.get("price", "")
                                    or ""
                                )
                                if not prezzo_raw:
                                    continue
                                prezzo = parse_price(prezzo_raw)
                                if not math.isfinite(prezzo):
                                    continue
                                goods_id = str(item.get("goods_id", "") or "")
                                link = (
                                    f"https://www.temu.com/it/g-{goods_id}.html" if goods_id else ""
                                )
                                if not link:
                                    continue
                                if not is_relevant(nome, query_tokens, strict_specs=False):
                                    continue
                                if not _within_price_range(prezzo, prezzo_min, budget_max):
                                    continue
                                img_url = str(item.get("goods_thumbnail_url", "") or "")
                                risultati.append(
                                    Offerta(
                                        nome=nome,
                                        prezzo=prezzo,
                                        negozio="Temu",
                                        link=link,
                                        fonte="temu.com",
                                        spedizione="n.d.",
                                        immagine=img_url,
                                    )
                                )
                        except Exception:
                            pass
                        break
                if risultati:
                    break

        if not risultati:
            print(
                "    ⚠️  Temu.com: pagina JS-rendered o bot-protetta, nessun risultato via HTML statico."
            )

    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Temu.com: accesso bloccato (HTTP {status}), salto la fonte.")
    except requests.Timeout:
        print("    ❌ Temu.com: timeout.")
    except requests.ConnectionError:
        print("    ❌ Temu.com: impossibile connettersi.")
    except Exception as exc:
        print(f"    ❌ Temu.com: errore inatteso → {exc}")

    _random_delay()
    return risultati

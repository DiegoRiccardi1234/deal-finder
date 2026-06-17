"""offerte: offerte/scrapers/trovaprezzi.py"""

from __future__ import annotations

import json
import math
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


def scrape_trovaprezzi(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper trovaprezzi.it (sito diretto).

    URL: /categoria.aspx?libera={query}  →  redirect a /{categoria}/offerte/{slug}
    Selettori (validi a marzo 2026):
        Container: a.suggested_product[href]
        Nome:      .product_info .name  (testo dentro il container)
        Prezzo:    .price_range         (es. "da 1.419,00 €")
        Link:      href del container a (relativo → prepend https://www.trovaprezzi.it)
    """
    url = f"https://www.trovaprezzi.it/categoria.aspx?libera={quote_plus(query)}"
    print(f'\n🔍 Cerco su Trovaprezzi.it: "{query}"')

    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.trovaprezzi.it/"

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(
                f"    ⚠️  Trovaprezzi.it: accesso bloccato (HTTP {resp.status_code}), salto la fonte."
            )
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

        def _parse_page_tp(html: str) -> int:
            """Parsa una pagina trovaprezzi, aggiunge offerte valide, ritorna il numero aggiunto."""
            soup_p = BeautifulSoup(html, "html.parser")
            page_title_p = str((soup_p.title.string or "") if soup_p.title else "")
            if any(
                kw in page_title_p.lower()
                for kw in ("sorry", "captcha", "robot", "unusual traffic", "404")
            ):
                return 0
            cards_p = soup_p.select("a.suggested_product[href]")
            if not cards_p:
                cards_p = soup_p.select("[class*='product'][href]")
            if not cards_p:
                # Fallback JSON-LD
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
            added = 0
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
            print(
                "    ⚠️  Trovaprezzi.it: nessun risultato parsabile (selettori cambiati o blocco)."
            )
        else:
            print(f"    ✅ Trovate {len(risultati)} card su Trovaprezzi.it")
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
                    print(f"    ✅ Trovaprezzi.it p.{_pn}: +{_added} offerte")
                    _random_delay()
                except Exception:
                    break

        if not risultati:
            print("    ⚠️  Trovaprezzi.it: risultati vuoti dopo parsing.")

    except requests.Timeout:
        print("    ❌ Trovaprezzi.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Trovaprezzi.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ Trovaprezzi.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ Trovaprezzi.it: errore inatteso → {exc}")

    _random_delay()
    return risultati

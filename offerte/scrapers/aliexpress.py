"""offerte: offerte/scrapers/aliexpress.py"""

from __future__ import annotations

import math
from urllib.parse import quote_plus

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import is_relevant
from offerte.log import get_logger
from offerte.source_status import report_error

log = get_logger(__name__)


def scrape_aliexpress(
    query: str,
    prezzo_min: float,
    budget_max: float | None,
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per AliExpress via Playwright headless."""
    print(f'\n🔍 Cerco su AliExpress.com: "{query}"')
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        print(
            "    ⚠️  AliExpress: playwright non installato. Esegui: pip install playwright && playwright install chromium"
        )
        return []

    url = f"https://it.aliexpress.com/wholesale?SearchText={quote_plus(query)}&SortType=price_asc"
    risultati: list[Offerta] = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            ctx = browser.new_context(
                locale="it-IT",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
            )
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(5000)

                cards = []
                for sel in [
                    '[class*="product-snippet"]',
                    '[class*="manhattan--"]',
                    '[class*="list--gallery--"]',
                    '[data-widget-cid*="product"]',
                    'a[href*="/item/"]',
                ]:
                    found = page.query_selector_all(sel)
                    if len(found) > 3:
                        cards = found
                        print(f"    ✅ AliExpress: {len(cards)} card con selettore '{sel}'")
                        break

                if not cards:
                    print("    ⚠️  AliExpress: nessuna card trovata nella pagina.")
                else:
                    for card in cards[:20]:
                        try:
                            testo = card.inner_text()
                            href = card.get_attribute("href") or ""
                            if not href:
                                link_el = card.query_selector("a[href*='/item/']")
                                href = link_el.get_attribute("href") if link_el else ""
                            if not href or "/item/" not in href:
                                continue
                            link = (
                                href
                                if href.startswith("http")
                                else "https://it.aliexpress.com" + href
                            )

                            righe = [r.strip() for r in testo.splitlines() if r.strip()]
                            nome = righe[0] if righe else ""
                            if not nome or len(nome) < 5:
                                continue

                            prezzo = math.inf
                            for r in righe:
                                p = parse_price(r)
                                if math.isfinite(p) and p > 0:
                                    prezzo = p
                                    break
                            if not math.isfinite(prezzo):
                                continue

                            if not is_relevant(nome, query_tokens, strict_specs=False):
                                continue
                            if not _within_price_range(prezzo, prezzo_min, budget_max):
                                continue

                            risultati.append(
                                Offerta(
                                    nome=nome,
                                    prezzo=prezzo,
                                    negozio="AliExpress",
                                    link=link,
                                    fonte="aliexpress.com",
                                    spedizione="n.d.",
                                )
                            )
                        except Exception:
                            continue

            except PWTimeout:
                print("    ❌ AliExpress: timeout Playwright.")
            finally:
                page.close()
                ctx.close()
                browser.close()

    except Exception as exc:
        log.error("AliExpress: errore Playwright → %s", exc)
        report_error("aliexpress", exc)

    print(f"    → {len(risultati)} risultati AliExpress")
    return risultati

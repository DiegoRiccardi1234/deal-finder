"""
probe_playwright.py — Testa i siti bloccati con Playwright headless.

Verifica se i siti con bot-protection funzionano con un vero browser
(stessa modalità che userebbe Render/cloud). Se funzionano qui,
funzioneranno anche sul server.

Uso:
    python tests/probe_playwright.py
    python tests/probe_playwright.py --query "samsung galaxy s24"
"""

from __future__ import annotations

import argparse
import sys
import time
import re
import json
from pathlib import Path
from urllib.parse import quote_plus

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
except ImportError:
    print(
        "❌ Playwright non installato. Esegui: pip install playwright && playwright install chromium"
    )
    sys.exit(1)

DEFAULT_QUERY = "iphone 256gb"

# ---------------------------------------------------------------------------


def probe_subito(query: str, page) -> dict:
    url = (
        f"https://www.subito.it/annunci-italia/vendita/usato/?q={quote_plus(query)}&sort=price_asc"
    )
    result = {"site": "subito.it", "url": url, "status": "?", "n_products": 0, "sample": []}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
        result["status"] = resp.status if resp else "?"
        page.wait_for_timeout(3000)

        # Subito usa React — cerca card annunci
        for sel in [
            '[class*="item-card"]',
            '[class*="SmallCard"]',
            "article",
            '[data-testid*="item"]',
            '[class*="listing-item"]',
        ]:
            cards = page.query_selector_all(sel)
            if len(cards) > 3:
                result["selector"] = sel
                result["n_raw"] = len(cards)
                # Estrai nome e prezzo dalle prime 3 card
                for card in cards[:3]:
                    try:
                        text = card.inner_text()
                        lines = [l.strip() for l in text.splitlines() if l.strip()]
                        result["sample"].append(lines[:4])
                    except Exception:
                        pass
                break

        # Fallback: cerca prezzi nel testo
        if not result["sample"]:
            prices = page.eval_on_selector_all(
                "*",
                "els => els.filter(e => /€\\s*\\d/.test(e.innerText) && e.children.length == 0).slice(0,5).map(e => e.innerText)",
            )
            result["prices_found"] = prices[:5]

        # Conta prodotti trovati
        result["n_products"] = result.get("n_raw", len(result.get("prices_found", [])))
        result["page_title"] = page.title()
        result["page_len"] = len(page.content())
    except PWTimeout:
        result["status"] = "TIMEOUT"
    except Exception as e:
        result["error"] = str(e)
    return result


def probe_aliexpress(query: str, page) -> dict:
    url = f"https://it.aliexpress.com/wholesale?SearchText={quote_plus(query)}&SortType=price_asc"
    result = {"site": "aliexpress.com", "url": url, "status": "?", "n_products": 0, "sample": []}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
        result["status"] = resp.status if resp else "?"
        page.wait_for_timeout(4000)

        result["page_title"] = page.title()
        result["page_len"] = len(page.content())

        # Cerca card prodotto
        for sel in [
            '[class*="product-snippet"]',
            '[class*="manhattan--"]',
            '[class*="list--gallery--"]',
            '[data-widget-cid*="product"]',
            'a[href*="/item/"]',
        ]:
            cards = page.query_selector_all(sel)
            if len(cards) > 3:
                result["selector"] = sel
                result["n_products"] = len(cards)
                for card in cards[:3]:
                    try:
                        result["sample"].append(card.inner_text()[:200])
                    except Exception:
                        pass
                break

        # Prova a estrarre prezzi
        if not result["n_products"]:
            prices = page.eval_on_selector_all(
                "*",
                "els => els.filter(e => /\\d+[.,]\\d{2}/.test(e.innerText) && e.children.length == 0 && e.innerText.length < 20).slice(0,5).map(e => e.innerText)",
            )
            result["prices_found"] = prices[:5]
            result["n_products"] = len(prices)
    except PWTimeout:
        result["status"] = "TIMEOUT"
    except Exception as e:
        result["error"] = str(e)
    return result


def probe_temu(query: str, page) -> dict:
    url = f"https://www.temu.com/it/search_result.html?search_key={quote_plus(query)}&sort_type=6"
    result = {"site": "temu.com", "url": url, "status": "?", "n_products": 0, "sample": []}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
        result["status"] = resp.status if resp else "?"
        page.wait_for_timeout(5000)  # Temu è lento

        result["page_title"] = page.title()
        result["page_len"] = len(page.content())

        for sel in [
            '[class*="goods-item"]',
            '[class*="product-item"]',
            '[data-testid*="product"]',
            'a[href*="/g-"]',
            '[class*="_goods"]',
        ]:
            cards = page.query_selector_all(sel)
            if len(cards) > 3:
                result["selector"] = sel
                result["n_products"] = len(cards)
                for card in cards[:3]:
                    try:
                        result["sample"].append(card.inner_text()[:200])
                    except Exception:
                        pass
                break

        if not result["n_products"]:
            prices = page.eval_on_selector_all(
                "*",
                "els => els.filter(e => /€\\s*\\d/.test(e.innerText) && e.children.length == 0).slice(0,5).map(e => e.innerText)",
            )
            result["prices_found"] = prices[:5]
            result["n_products"] = len(prices)
    except PWTimeout:
        result["status"] = "TIMEOUT"
    except Exception as e:
        result["error"] = str(e)
    return result


def probe_alibaba(query: str, page) -> dict:
    url = f"https://www.alibaba.com/trade/search?SearchText={quote_plus(query)}&SortType=price_asc"
    result = {"site": "alibaba.com", "url": url, "status": "?", "n_products": 0, "sample": []}
    try:
        resp = page.goto(url, wait_until="domcontentloaded", timeout=20000)
        result["status"] = resp.status if resp else "?"
        page.wait_for_timeout(3000)

        result["page_title"] = page.title()
        result["page_len"] = len(page.content())

        for sel in [
            ".organic-list-offer",
            ".offer-list-row",
            ".J-offer-wrapper",
            '[class*="offer"]',
            'a[href*="/product/"]',
        ]:
            cards = page.query_selector_all(sel)
            if len(cards) > 2:
                result["selector"] = sel
                result["n_products"] = len(cards)
                for card in cards[:3]:
                    try:
                        result["sample"].append(card.inner_text()[:200])
                    except Exception:
                        pass
                break

        if not result["n_products"]:
            prices = page.eval_on_selector_all(
                "*",
                "els => els.filter(e => /USD|\\$\\d/.test(e.innerText) && e.children.length == 0).slice(0,5).map(e => e.innerText)",
            )
            result["prices_found"] = prices[:5]
            result["n_products"] = len(prices)
    except PWTimeout:
        result["status"] = "TIMEOUT"
    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------

PROBES = {
    "subito": probe_subito,
    "aliexpress": probe_aliexpress,
    "temu": probe_temu,
    "alibaba": probe_alibaba,
}


def run(query: str, sites: list[str]) -> None:
    print(f"\n{'=' * 65}")
    print(f"  PLAYWRIGHT HEADLESS PROBE — query: '{query}'")
    print(f"  Siti: {', '.join(sites)}")
    print(f"{'=' * 65}\n")

    summary = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1366, "height": 768},
            locale="it-IT",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/133.0.0.0 Safari/537.36"
            ),
            extra_http_headers={
                "Accept-Language": "it-IT,it;q=0.9,en;q=0.5",
            },
        )

        for site in sites:
            fn = PROBES.get(site)
            if not fn:
                print(f"  ⚠️  Sito sconosciuto: {site}")
                continue

            print(f"{'─' * 55}")
            print(f"  {site.upper()}")
            print(f"{'─' * 55}")

            page = context.new_page()
            # Blocca risorse non necessarie per velocità
            page.route("**/*.{png,jpg,jpeg,gif,svg,woff,woff2,ttf,mp4,webp}", lambda r: r.abort())

            t0 = time.perf_counter()
            result = fn(query, page)
            elapsed = time.perf_counter() - t0
            page.close()

            n = result["n_products"]
            status = result.get("status", "?")
            title = result.get("page_title", "")[:60]
            page_len = result.get("page_len", 0)
            summary.append((site, n, elapsed, status))

            icon = "✅" if n > 0 else "❌"
            print(f"  {icon} Status: {status}  |  Prodotti: {n}  |  Tempo: {elapsed:.1f}s")
            print(f"  Titolo pagina: {title}")
            print(f"  Dimensione DOM: {page_len:,} chars")

            if result.get("selector"):
                print(f"  Selettore trovato: {result['selector']}")
            if result.get("error"):
                print(f"  Errore: {result['error']}")
            if result.get("prices_found"):
                print(f"  Prezzi trovati nel DOM: {result['prices_found']}")
            if result["sample"]:
                print("  Campione prodotti:")
                for s in result["sample"][:2]:
                    print(f"    → {str(s)[:120]}")
            print()

        browser.close()

    print(f"{'=' * 65}")
    print("  VERDETTO FINALE")
    print(f"{'=' * 65}")
    print(f"  {'Sito':<15} {'Prodotti':>9} {'Tempo':>7}  Verdict")
    print(f"  {'─' * 15} {'─' * 9} {'─' * 7}  {'─' * 30}")
    for site, n, elapsed, status in summary:
        if n > 0:
            verdict = "✅ FUNZIONA — integrabile"
        elif str(status) in ("403", "429"):
            verdict = "❌ BLOCCATO anche con browser headless"
        elif str(status) == "TIMEOUT":
            verdict = "⏱️  Timeout — sito troppo lento"
        else:
            verdict = "⚠️  Nessun prodotto trovato nel DOM"
        print(f"  {site:<15} {n:>9} {elapsed:>6.1f}s  {verdict}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument(
        "--sites", nargs="+", default=list(PROBES.keys()), choices=list(PROBES.keys())
    )
    args = parser.parse_args()
    run(args.query, args.sites)


if __name__ == "__main__":
    main()

"""
probe_scrapers.py — Sonda real HTTP per tutti gli scraper di Trova Prezzi Mio.

Esegue ricerche reali (senza mock) su ogni sito configurato e stampa:
  - quante offerte ha restituito
  - i primi 3 risultati
  - diagnostica HTML se 0 risultati (status code, lunghezza pagina, snippet)

Uso:
    python tests/probe_scrapers.py
    python tests/probe_scrapers.py --query "samsung galaxy s24" --budget 800
    python tests/probe_scrapers.py --sites amazon ebay subito
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Aggiungi la root del progetto al path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus

import offerte_tech as ot

# ---------------------------------------------------------------------------
# Configurazione probe
# ---------------------------------------------------------------------------

DEFAULT_QUERY = "iphone 256gb"
DEFAULT_BUDGET = 1200.0
DEFAULT_PREZZO_MIN = 0.0

SITES: dict[str, dict] = {
    "amazon": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_amazon(q, pmin, pmax, tokens, cond),
        "args": ["query", "prezzo_min", "budget_max", "tokens", "condizione"],
        "supports_condizione": True,
    },
    "ebay": {
        "fn": lambda q, pmin, pmax, tokens, cond: _scrape_ebay_html(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "note": "eBay HTML fallback (senza API key)",
        "supports_condizione": False,
    },
    "euronics": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_euronics(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "supports_condizione": False,
    },
    "unieuro": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_unieuro(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "supports_condizione": False,
    },
    "mediaworld": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_mediaworld(q, pmin, pmax, tokens, cond),
        "args": ["query", "prezzo_min", "budget_max", "tokens", "condizione"],
        "supports_condizione": True,
    },
    "vinted": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_vinted(q, pmin, pmax, tokens, cond),
        "args": ["query", "prezzo_min", "budget_max", "tokens", "condizione"],
        "supports_condizione": True,
        "note": "Solo usato — skip se condizione=nuovo",
    },
    "subito": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_subito(q, pmin, pmax, tokens, cond),
        "args": ["query", "prezzo_min", "budget_max", "tokens", "condizione"],
        "supports_condizione": True,
        "note": "Solo usato — skip se condizione=nuovo",
    },
    "aliexpress": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_aliexpress(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "supports_condizione": False,
    },
    "temu": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_temu(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "supports_condizione": False,
    },
    "alibaba": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_alibaba(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "supports_condizione": False,
    },
}


def _scrape_ebay_html(query: str, prezzo_min: float, budget_max, tokens: list[str]):
    """eBay HTML scraper (fallback senza API key)."""
    url = f"https://www.ebay.it/sch/i.html?_nkw={quote_plus(query)}&_sop=15"
    headers = ot.get_headers()
    try:
        resp = ot.fetch_with_retry(url, headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select('li.s-item')
        risultati = []
        for card in cards[:30]:
            nome_tag = card.select_one('.s-item__title')
            prezzo_tag = card.select_one('.s-item__price')
            link_tag = card.select_one('a.s-item__link')
            if not nome_tag or not prezzo_tag or not link_tag:
                continue
            nome = nome_tag.get_text(strip=True)
            if nome.lower() == "shop on ebay":
                continue
            prezzo = ot.parse_price(prezzo_tag.get_text(strip=True))
            import math
            if not math.isfinite(prezzo):
                continue
            link = str(link_tag.get("href", ""))
            if not ot.is_relevant(nome, tokens, strict_specs=False):
                continue
            if not ot._within_price_range(prezzo, prezzo_min, budget_max):
                continue
            risultati.append(ot.Offerta(nome=nome, prezzo=prezzo, negozio="eBay", link=link, fonte="ebay.it"))
        return risultati
    except Exception as e:
        print(f"    eBay HTML error: {e}")
        return []


# ---------------------------------------------------------------------------
# Diagnostica HTML per siti che ritornano 0 risultati
# ---------------------------------------------------------------------------

def _diagnose_site(site_name: str, query: str) -> None:
    """Fa una GET raw al sito e stampa info diagnostiche."""
    urls = {
        "subito": f"https://www.subito.it/annunci-italia/vendita/usato/?q={quote_plus(query)}&sort=price_asc",
        "temu": f"https://www.temu.com/it/search_result.html?search_key={quote_plus(query)}",
        "aliexpress": f"https://it.aliexpress.com/wholesale?SearchText={quote_plus(query)}",
        "alibaba": f"https://www.alibaba.com/trade/search?SearchText={quote_plus(query)}",
    }
    if site_name not in urls:
        return

    url = urls[site_name]
    print(f"\n  [DIAGNOSI {site_name.upper()}]")
    print(f"  URL: {url}")
    try:
        headers = ot.get_headers()
        headers["Referer"] = f"https://www.{site_name}.com/"
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"  Status: {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('Content-Type', '?')}")
        print(f"  Lunghezza risposta: {len(resp.text):,} caratteri")

        soup = BeautifulSoup(resp.text, "html.parser")
        title = (soup.title.string or "").strip() if soup.title else "?"
        print(f"  <title>: {title}")

        # Cerca tutti i div/article con classi potenzialmente rilevanti
        candidate_selectors = [
            'article', 'div[class*="item"]', 'div[class*="product"]',
            'div[class*="card"]', 'div[class*="result"]', 'li[class*="item"]',
            '[data-testid]',
        ]
        print("  Selettori trovati nel DOM:")
        for sel in candidate_selectors:
            found = soup.select(sel)
            if found:
                sample_class = found[0].get("class", [])
                print(f"    {sel}: {len(found)} elementi — esempio class: {sample_class[:3]}")

        # Cerca script con JSON dati prodotti
        scripts_with_data = []
        for s in soup.find_all("script"):
            t = s.string or ""
            if any(kw in t for kw in ('"price"', '"title"', '"goods"', '"items"', '"products"')):
                scripts_with_data.append(len(t))
        if scripts_with_data:
            print(f"  Script con dati JSON: {len(scripts_with_data)} — dimensioni: {scripts_with_data}")
        else:
            print("  Script con dati JSON: nessuno trovato")

        # Snippet testo pagina
        text_snippet = soup.get_text(" ", strip=True)[:400]
        print(f"  Snippet testo: {text_snippet!r}")

    except Exception as e:
        print(f"  Errore diagnostica: {e}")


# ---------------------------------------------------------------------------
# Runner principale
# ---------------------------------------------------------------------------

def run_probe(query: str, budget_max: float, prezzo_min: float, sites: list[str], condizione: str) -> None:
    tokens = ot.tokenize_query(query)
    print(f"\n{'=' * 70}")
    print(f"  PROBE SCRAPERS — query: '{query}'")
    print(f"  Budget: €{prezzo_min:.0f}–€{budget_max:.0f}  |  Condizione: {condizione}")
    print(f"  Token: {tokens}")
    print(f"  Fonti da testare: {', '.join(sites)}")
    print(f"{'=' * 70}\n")

    results_summary: list[tuple[str, int, float]] = []

    for site_name in sites:
        if site_name not in SITES:
            print(f"  ⚠️  Sito sconosciuto: {site_name}, skip.")
            continue

        cfg = SITES[site_name]
        note = cfg.get("note", "")
        print(f"\n{'─' * 60}")
        print(f"  SITO: {site_name.upper()}" + (f"  ({note})" if note else ""))
        print(f"{'─' * 60}")

        t0 = time.perf_counter()
        try:
            offerte = cfg["fn"](query, prezzo_min, budget_max, tokens, condizione)
        except Exception as exc:
            print(f"  ❌ Eccezione: {exc}")
            offerte = []
        elapsed = time.perf_counter() - t0

        n = len(offerte)
        results_summary.append((site_name, n, elapsed))

        print(f"\n  Risultati: {n}  |  Tempo: {elapsed:.2f}s")

        if n == 0:
            _diagnose_site(site_name, query)
        else:
            print(f"  Prime {min(3, n)} offerte:")
            for i, o in enumerate(offerte[:3], 1):
                print(f"    [{i}] €{o.prezzo:,.2f}  |  {o.negozio}  |  {o.nome[:80]}")
                print(f"         {o.link[:100]}")

    # Riepilogo finale
    print(f"\n{'=' * 70}")
    print("  RIEPILOGO")
    print(f"{'=' * 70}")
    print(f"  {'Sito':<15} {'Risultati':>10} {'Tempo':>8}")
    print(f"  {'─'*15} {'─'*10} {'─'*8}")
    for site_name, n, elapsed in results_summary:
        status = "✅" if n > 0 else "❌"
        print(f"  {status} {site_name:<13} {n:>10} {elapsed:>7.2f}s")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe real HTTP scrapers")
    parser.add_argument("--query", default=DEFAULT_QUERY, help=f"Query di ricerca (default: '{DEFAULT_QUERY}')")
    parser.add_argument("--budget", type=float, default=DEFAULT_BUDGET, help=f"Budget max in € (default: {DEFAULT_BUDGET})")
    parser.add_argument("--min", type=float, default=DEFAULT_PREZZO_MIN, dest="prezzo_min", help="Prezzo minimo (default: 0)")
    parser.add_argument("--condizione", default="tutti", choices=["tutti", "nuovo", "usato"])
    parser.add_argument(
        "--sites", nargs="+",
        default=list(SITES.keys()),
        choices=list(SITES.keys()),
        help="Siti da testare (default: tutti)",
    )
    args = parser.parse_args()

    run_probe(
        query=args.query,
        budget_max=args.budget,
        prezzo_min=args.prezzo_min,
        sites=args.sites,
        condizione=args.condizione,
    )


if __name__ == "__main__":
    main()

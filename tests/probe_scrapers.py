"""
probe_scrapers.py — Sonda real HTTP per tutti gli scraper di Deal Finder.

Esegue ricerche reali (senza mock) su ogni sito configurato e stampa:
  - quante offerte ha restituito
  - i primi 3 risultati
  - diagnostica HTML se 0 risultati (status code, lunghezza pagina, snippet)

Copre tutte le 14 fonti del registry `offerte.scrapers.SCRAPERS`. Le chiavi
opzionali vengono lette da `.streamlit/secrets.toml`, così eBay viene sondato
via Browse API come fa l'app e non solo sul fallback HTML.

Uso:
    python tests/probe_scrapers.py
    python tests/probe_scrapers.py --query "samsung galaxy s24" --budget 800
    python tests/probe_scrapers.py --sites amazon ebay subito
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

# Aggiungi la root del progetto al path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from bs4 import BeautifulSoup
from urllib.parse import quote_plus, urlsplit

import offerte_tech as ot
from offerte import source_status

# ---------------------------------------------------------------------------
# Configurazione probe
# ---------------------------------------------------------------------------

DEFAULT_QUERY = "iphone 256gb"
DEFAULT_BUDGET = 1200.0
DEFAULT_PREZZO_MIN = 0.0

SECRETS_PATH = Path(__file__).parent.parent / ".streamlit" / "secrets.toml"


_SECRET_KEYS = ("EBAY_APP_ID", "EBAY_CERT_ID", "CEREBRAS_API_KEY", "APP_PASSWORD")


def _load_secrets() -> dict[str, str]:
    """Chiavi opzionali, da `.streamlit/secrets.toml` o dall'ambiente.

    Il probe gira fuori da Streamlit, quindi `st.secrets` non esiste: senza
    questa lettura eBay cadrebbe sempre sul fallback HTML (403) e il probe
    riporterebbe la fonte come rotta pur essendo funzionante via Browse API.

    L'ambiente ha la precedenza sul file: in CI il canary passa i secret del repo
    come variabili d'ambiente e lì `.streamlit/secrets.toml` non esiste.
    """
    secrets: dict[str, str] = {}
    if SECRETS_PATH.is_file():
        try:
            with SECRETS_PATH.open("rb") as fh:
                raw = tomllib.load(fh)
            secrets.update({k: v for k, v in raw.items() if isinstance(v, str)})
        except (OSError, tomllib.TOMLDecodeError) as exc:
            print(f"  ⚠️  secrets.toml illeggibile ({exc}); proseguo senza chiavi.")
    for key in _SECRET_KEYS:
        val = os.environ.get(key, "").strip()
        if val:
            secrets[key] = val
    return secrets


SECRETS = _load_secrets()

SITES: dict[str, dict] = {
    "amazon": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_amazon(q, pmin, pmax, tokens, cond),
        "args": ["query", "prezzo_min", "budget_max", "tokens", "condizione"],
        "supports_condizione": True,
    },
    "ebay": {
        "fn": lambda q, pmin, pmax, tokens, cond: _scrape_ebay(q, pmin, pmax, tokens, cond),
        "args": ["query", "prezzo_min", "budget_max", "tokens", "condizione"],
        "note": (
            "Browse API (chiavi trovate)"
            if SECRETS.get("EBAY_APP_ID") and SECRETS.get("EBAY_CERT_ID")
            else "HTML fallback — EBAY_APP_ID/EBAY_CERT_ID assenti (secrets.toml o env)"
        ),
        "supports_condizione": True,
    },
    "trovaprezzi": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_trovaprezzi(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "supports_condizione": False,
    },
    "wallapop": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_wallapop(q, pmin, pmax, tokens, cond),
        "args": ["query", "prezzo_min", "budget_max", "tokens", "condizione"],
        "supports_condizione": True,
        "note": "Solo usato — skip se condizione=nuovo",
    },
    "comet": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_comet(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
        "supports_condizione": False,
    },
    "expert": {
        "fn": lambda q, pmin, pmax, tokens, cond: ot.scrape_expert(q, pmin, pmax, tokens),
        "args": ["query", "prezzo_min", "budget_max", "tokens"],
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


def _scrape_ebay(query: str, prezzo_min: float, budget_max, tokens: list[str], condizione: str):
    """eBay via Browse API se le chiavi ci sono, altrimenti fallback HTML.

    Rispecchia il comportamento reale dell'app, che legge le chiavi da
    `st.secrets`: sondare solo l'HTML darebbe un falso negativo (403).
    """
    app_id = SECRETS.get("EBAY_APP_ID", "")
    cert_id = SECRETS.get("EBAY_CERT_ID", "")
    if app_id and cert_id:
        return ot.scrape_ebay(query, prezzo_min, budget_max, condizione, tokens, app_id, cert_id)
    return _scrape_ebay_html(query, prezzo_min, budget_max, tokens)


def _scrape_ebay_html(query: str, prezzo_min: float, budget_max, tokens: list[str]):
    """eBay HTML scraper (fallback senza API key)."""
    url = f"https://www.ebay.it/sch/i.html?_nkw={quote_plus(query)}&_sop=15"
    headers = ot.get_headers()
    try:
        resp = ot.fetch_with_retry(url, headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select("li.s-item")
        risultati = []
        for card in cards[:30]:
            nome_tag = card.select_one(".s-item__title")
            prezzo_tag = card.select_one(".s-item__price")
            link_tag = card.select_one("a.s-item__link")
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
            risultati.append(
                ot.Offerta(nome=nome, prezzo=prezzo, negozio="eBay", link=link, fonte="ebay.it")
            )
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
        # Fonti storicamente funzionanti e poi bloccate: la diagnosi serve a
        # distinguere "selettori cambiati" (HTTP 200) da "blocco" (403/503).
        "amazon": f"https://www.amazon.it/s?k={quote_plus(query)}",
        "euronics": f"https://www.euronics.it/search/?text={quote_plus(query)}",
        "trovaprezzi": f"https://www.trovaprezzi.it/categoria.aspx?libera={quote_plus(query)}",
    }
    if site_name not in urls:
        return

    url = urls[site_name]
    print(f"\n  [DIAGNOSI {site_name.upper()}]")
    print(f"  URL: {url}")
    try:
        headers = ot.get_headers()
        headers["Referer"] = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}/"
        resp = requests.get(url, headers=headers, timeout=15)
        print(f"  Status: {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('Content-Type', '?')}")
        print(f"  Lunghezza risposta: {len(resp.text):,} caratteri")

        soup = BeautifulSoup(resp.text, "html.parser")
        title = (soup.title.string or "").strip() if soup.title else "?"
        print(f"  <title>: {title}")

        # Cerca tutti i div/article con classi potenzialmente rilevanti
        candidate_selectors = [
            "article",
            'div[class*="item"]',
            'div[class*="product"]',
            'div[class*="card"]',
            'div[class*="result"]',
            'li[class*="item"]',
            "[data-testid]",
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
            print(
                f"  Script con dati JSON: {len(scripts_with_data)} — dimensioni: {scripts_with_data}"
            )
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


def run_probe(
    query: str, budget_max: float, prezzo_min: float, sites: list[str], condizione: str
) -> list[dict[str, Any]]:
    """Sonda ogni fonte e restituisce l'esito strutturato.

    Lo *stato* (non solo il conteggio) viene da `offerte.source_status`: gli
    scraper vi segnalano già da sé i casi `blocked` e `disabled`, che sono l'unica
    informazione che il chiamante non può dedurre da una lista vuota. Qui si
    aggiunge `ok`/`empty` in base al risultato, come fa `_timed_call` in
    `offerte.orchestrator`.
    """
    tokens = ot.tokenize_query(query)
    print(f"\n{'=' * 70}")
    print(f"  PROBE SCRAPERS — query: '{query}'")
    print(f"  Budget: €{prezzo_min:.0f}–€{budget_max:.0f}  |  Condizione: {condizione}")
    print(f"  Token: {tokens}")
    print(f"  Fonti da testare: {', '.join(sites)}")
    print(f"{'=' * 70}\n")

    source_status.reset()
    results_summary: list[dict[str, Any]] = []

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
            source_status.report_error(site_name, exc)
            offerte = []
        elapsed = time.perf_counter() - t0

        n = len(offerte)
        if n:
            source_status.report_ok(site_name, n)
        else:
            source_status.report_empty(site_name)

        entry = source_status.get(site_name)
        results_summary.append(
            {
                "fonte": site_name,
                "state": entry.state if entry else source_status.EMPTY,
                "detail": entry.detail if entry else "",
                "results": n,
                "seconds": round(elapsed, 2),
            }
        )

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
    print(f"  {'Sito':<15} {'Stato':<10} {'Risultati':>10} {'Tempo':>8}")
    print(f"  {'─' * 15} {'─' * 10} {'─' * 10} {'─' * 8}")
    for row in results_summary:
        icon = "✅" if row["state"] == source_status.OK else "❌"
        if row["state"] in (source_status.DISABLED, source_status.EMPTY):
            icon = "⏸" if row["state"] == source_status.DISABLED else "➖"
        print(
            f"  {icon} {row['fonte']:<13} {row['state']:<10} "
            f"{row['results']:>10} {row['seconds']:>7.2f}s"
        )
    print()
    return results_summary


# ---------------------------------------------------------------------------
# Confronto con la baseline (usato dal canary in CI)
# ---------------------------------------------------------------------------

BASELINE_PATH = Path(__file__).parent / "sources_baseline.json"


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, str]]:
    """Stato atteso per fonte. Dizionario vuoto se il file manca."""
    try:
        with path.open(encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return {k: v for k, v in raw.items() if isinstance(v, dict)}


def compare_with_baseline(
    results: list[dict[str, Any]], baseline: dict[str, dict[str, str]]
) -> list[dict[str, str]]:
    """Scostamenti fra esito osservato e stato atteso.

    Segnala SOLO i peggioramenti: una fonte attesa `ok` che non risponde più. Non
    segnala il contrario (una fonte attesa bloccata che oggi funziona) perché non
    è un guasto, e non segnala `expected: "any"`, riservato alle fonti note come
    intermittenti — altrimenti l'allerta suonerebbe ogni settimana e verrebbe
    ignorata.
    """
    deviations: list[dict[str, str]] = []
    for row in results:
        fonte = row["fonte"]
        atteso = (baseline.get(fonte) or {}).get("expected")
        if not atteso or atteso == "any":
            continue
        osservato = row["state"]
        if atteso == "ok" and osservato != source_status.OK:
            deviations.append(
                {
                    "fonte": fonte,
                    "atteso": atteso,
                    "osservato": osservato,
                    "detail": str(row.get("detail") or ""),
                    "results": str(row.get("results", 0)),
                }
            )
    return deviations


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe real HTTP scrapers")
    parser.add_argument(
        "--query", default=DEFAULT_QUERY, help=f"Query di ricerca (default: '{DEFAULT_QUERY}')"
    )
    parser.add_argument(
        "--budget",
        type=float,
        default=DEFAULT_BUDGET,
        help=f"Budget max in € (default: {DEFAULT_BUDGET})",
    )
    parser.add_argument(
        "--min",
        type=float,
        default=DEFAULT_PREZZO_MIN,
        dest="prezzo_min",
        help="Prezzo minimo (default: 0)",
    )
    parser.add_argument("--condizione", default="tutti", choices=["tutti", "nuovo", "usato"])
    parser.add_argument(
        "--sites",
        nargs="+",
        default=list(SITES.keys()),
        choices=list(SITES.keys()),
        help="Siti da testare (default: tutti)",
    )
    parser.add_argument(
        "--json",
        dest="json_path",
        metavar="FILE",
        help="Scrive l'esito per fonte in JSON (usato dal canary in CI)",
    )
    parser.add_argument(
        "--check-baseline",
        action="store_true",
        help=(
            "Confronta con tests/sources_baseline.json ed esce con codice 1 se una "
            "fonte attesa 'ok' non risponde più. Senza questo flag l'uscita è sempre 0."
        ),
    )
    args = parser.parse_args()

    results = run_probe(
        query=args.query,
        budget_max=args.budget,
        prezzo_min=args.prezzo_min,
        sites=args.sites,
        condizione=args.condizione,
    )

    if args.json_path:
        payload = {
            "query": args.query,
            "budget_max": args.budget,
            "prezzo_min": args.prezzo_min,
            "condizione": args.condizione,
            "sources": results,
        }
        Path(args.json_path).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"  Esito JSON scritto in: {args.json_path}")

    if not args.check_baseline:
        return

    baseline = load_baseline()
    if not baseline:
        print("  ⚠️  Baseline assente o illeggibile: nessun confronto eseguito.")
        return

    deviations = compare_with_baseline(results, baseline)
    if not deviations:
        print("  ✅ Tutte le fonti combaciano con la baseline.")
        return

    print(f"\n  ❌ {len(deviations)} fonti si discostano dalla baseline:")
    for d in deviations:
        print(
            f"     - {d['fonte']}: atteso '{d['atteso']}', osservato '{d['osservato']}' {d['detail']}"
        )
    sys.exit(1)


if __name__ == "__main__":
    main()

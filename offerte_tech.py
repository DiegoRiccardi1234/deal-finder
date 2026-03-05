"""
offerte_tech.py — Price Scraper per prodotti tech italiani
=========================================================
Cerca offerte su trovaprezzi.it e amazon.it, filtra per
rilevanza e budget, ordina per prezzo crescente.

Installazione dipendenze:
    pip install requests beautifulsoup4 fake-useragent

Utilizzo come funzione:
    from offerte_tech import cerca_offerte
    cerca_offerte(query="notebook 14 pollici 16gb", budget_max=800, top_n=10)

Utilizzo da terminale:
    python offerte_tech.py -q "notebook 14 pollici 16gb" -b 800 -n 10
    python offerte_tech.py -q "ssd 1tb" --export csv
"""

import argparse
import csv
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Tentativo di importare fake_useragent; fallback a lista statica se assente
# ---------------------------------------------------------------------------
try:
    from fake_useragent import UserAgent
    _UA = UserAgent()
    def _random_ua() -> str:
        return _UA.random
except Exception:
    _FALLBACK_UAS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) "
        "Gecko/20100101 Firefox/124.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]
    def _random_ua() -> str:
        return random.choice(_FALLBACK_UAS)

# ---------------------------------------------------------------------------
# Costanti globali
# ---------------------------------------------------------------------------
TIMEOUT        = 10        # secondi per ogni richiesta HTTP
DELAY_MIN      = 1.0       # secondi — delay minimo tra richieste
DELAY_MAX      = 3.5       # secondi — delay massimo tra richieste
MAX_RETRIES    = 2         # tentativi extra in caso di errore (tot: 1 + MAX_RETRIES)
BACKOFF_BASE   = 2.0       # secondi — base per il backoff esponenziale

# Stopword da ignorare nel filtro di rilevanza.
# Include anche unità di misura (pollici, gb, tb…): il numero associato è già
# sufficiente per il matching, e le unità raramente appaiono letteralmente
# nei titoli prodotto (es. "14 pollici" → il titolo ha solo '14"' o '14').
_STOPWORDS = {
    # Articoli e preposizioni italiane
    "e", "da", "con", "per", "di", "a", "in", "il", "la", "i", "le",
    "un", "una", "del", "degli", "su", "al", "dal",
    "usato", "nuovo",
    # Unità di misura — il numero prima di esse è il vero token di filtro
    "pollici", "inch", "inches", "ghz", "mhz", "hz", "watt", "wh",
    "ampere", "volt", "pixel", "megapixel", "mp",
}

# Alias di normalizzazione per il match di rilevanza
# Chiave: token trovato nel titolo prodotto → valore: set di token alternativi
_ALIASES: dict[str, set[str]] = {
    "16gb":  {"16 gb", "16gb"},
    "16 gb": {"16gb", "16 gb"},
    "8gb":   {"8 gb", "8gb"},
    "8 gb":  {"8gb", "8 gb"},
    "32gb":  {"32 gb", "32gb"},
    "32 gb": {"32gb", "32 gb"},
    "1tb":   {"1 tb", "1tb"},
    "1 tb":  {"1tb", "1 tb"},
    "2tb":   {"2 tb", "2tb"},
    "2 tb":  {"2tb", "2 tb"},
    "14":    {"14.0", "14\"", "14'", "14 pollici"},
    "15":    {"15.0", "15\"", "15'", "15 pollici"},
    "13":    {"13.0", "13\"", "13'", "13 pollici"},
}

_ACCESSORY_BLACKLIST = [
    "cover", "custodia", "pellicola", "coque", "funda", "vetro",
    "hülle", "handyhülle", "caricabatterie", "cavo", "protez",
    "paillettes", "sticker", "dessins", "protections",
]

# Categorie statiche trovaprezzi utilizzabili con requests + BeautifulSoup.
# Chiave: token query, valore: path categoria valido sul sito.
CATEGORIE_TROVAPREZZI: dict[str, str] = {
    "notebook": "notebook/offerte/notebook",
    "laptop": "notebook/offerte/notebook",
    "ssd": "ssd/offerte/ssd",
    "smartphone": "smartphone/offerte/smartphone",
    "telefono": "smartphone/offerte/smartphone",
    "iphone": "smartphone/offerte/smartphone",
    "samsung": "smartphone/offerte/smartphone",
    "xiaomi": "smartphone/offerte/smartphone",
    "pixel": "smartphone/offerte/smartphone",
    "android": "smartphone/offerte/smartphone",
    "monitor": "monitor/offerte/monitor",
    "gpu": "schede-video/offerte/schede-video",
    "scheda": "schede-video/offerte/schede-video",
    "ram": "memorie-ram/offerte/memorie-ram",
    "router": "router/offerte/router",
    "wifi": "router/offerte/router",
    "smartwatch": "smartwatch/offerte/smartwatch",
    "cuffie": "cuffie/offerte/cuffie",
    "auricolari": "cuffie/offerte/cuffie",
    "airpods": "cuffie/offerte/cuffie",
    "earbuds": "cuffie/offerte/cuffie",
    "mouse": "mouse/offerte/mouse",
    "tastiera": "tastiere/offerte/tastiere",
    "webcam": "webcam/offerte/webcam",
    "stampante": "stampanti/offerte/stampanti",
    "hard": "hard-disk-esterni/offerte/hard-disk-esterni",
    "hdd": "hard-disk-esterni/offerte/hard-disk-esterni",
    "disco": "hard-disk-esterni/offerte/hard-disk-esterni",
    "tablet": "tablet/offerte/tablet",
    "tv": "televisori-lcd-plasma/offerte/televisori",
}


# ===========================================================================
# DATACLASS
# ===========================================================================

@dataclass(order=False)
class Offerta:
    """Rappresenta un'offerta raccolta da una fonte."""
    nome:    str
    prezzo:  float
    negozio: str
    link:    str
    fonte:   str = field(default="")

    def __str__(self) -> str:
        nome_corto = self.nome[:62] + "…" if len(self.nome) > 63 else self.nome
        prezzo_fmt = f"€ {self.prezzo:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return (
            f"  💰 {prezzo_fmt:<12}  🏪 {self.negozio:<18}  📦 {nome_corto}\n"
            f"     🔗 {self.link}"
        )


# ===========================================================================
# HELPERS GENERALI
# ===========================================================================

def get_headers() -> dict[str, str]:
    """Genera headers HTTP realistici per evitare blocchi."""
    return {
        "User-Agent":      _random_ua(),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.7,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT":             "1",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def parse_price(text: str) -> float:
    """
    Converte una stringa di prezzo in float.
    Gestisce formati come '€ 1.299,00', '1299.00', '1.299,00 - 1.399,00'.
    In caso di range, prende il valore minimo.
    Ritorna float('inf') se il parsing fallisce.
    """
    if not text:
        return float("inf")

    # Normalizza: rimuove simboli valuta e spazi extra
    text = text.replace("€", "").replace("EUR", "").strip()

    # In caso di range (es. "100,00 - 200,00"), prende il primo valore
    parts = re.split(r"\s*[-–]\s*", text)
    text = parts[0].strip()

    # Formato europeo: 1.299,00 → togli i punti migliaia, sostituisci virgola decimale
    if re.search(r"\d\.\d{3},", text):
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." not in text:
        # Solo virgola (es. "129,99")
        text = text.replace(",", ".")
    elif "." in text and "," not in text:
        # Potrebbe essere già in formato anglosassone (es. "129.99") o migliaia (es. "1.299")
        dot_parts = text.split(".")
        if len(dot_parts[-1]) != 2:
            # È separatore migliaia, non decimale
            text = text.replace(".", "")
    else:
        # Rimuove qualsiasi residuo non numerico eccetto il punto decimale
        text = re.sub(r"[^\d.]", "", text)

    try:
        return float(text)
    except ValueError:
        return float("inf")


def tokenize_query(query: str) -> list[str]:
    """
    Divide la query in token significativi, rimuovendo le stopword italiane.
    Normalizza in lowercase.
    Es: "notebook 14 pollici 16GB RAM" → ["notebook", "14", "pollici", "16gb", "ram"]
    """
    tokens = re.findall(r"[\w]+", query.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def is_relevant(nome: str, query_tokens: list[str]) -> bool:
    """
    Filtro strict: TUTTI i token della query devono essere presenti nel nome
    del prodotto (o in uno dei loro alias normalizzati).
    """
    nome_lower = nome.lower()
    for token in query_tokens:
        # Espande il token con gli alias conosciuti
        varianti = _ALIASES.get(token, {token})
        varianti.add(token)
        if not any(v in nome_lower for v in varianti):
            return False
    return True


def is_accessory_mismatch(nome: str, query_originale: str) -> bool:
    """
    Scarta accessori quando la query non li richiede esplicitamente.

    Regola: se il titolo contiene una keyword blacklist ma la query originale
    non contiene la stessa keyword, il risultato viene escluso.
    """
    nome_lower = nome.lower()
    query_lower = query_originale.lower()
    for keyword in _ACCESSORY_BLACKLIST:
        if keyword in nome_lower and keyword not in query_lower:
            return True
    return False


def _random_delay() -> None:
    """Attesa casuale tra DELAY_MIN e DELAY_MAX secondi."""
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


def _select_trovaprezzi_categoria(query_tokens: list[str]) -> Optional[str]:
    """Seleziona il path categoria trovaprezzi in base ai token query."""
    for token in query_tokens:
        if token in CATEGORIE_TROVAPREZZI:
            return CATEGORIE_TROVAPREZZI[token]
    return None


# ===========================================================================
# RETRY CON BACKOFF ESPONENZIALE
# ===========================================================================

def fetch_with_retry(
    url: str,
    headers: dict[str, str],
    *,
    max_retries: int = MAX_RETRIES,
    backoff_base: float = BACKOFF_BASE,
    timeout: int = TIMEOUT,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """
    Esegue una GET con retry automatico in caso di:
      - requests.Timeout
      - requests.ConnectionError
      - HTTP status 429 (Too Many Requests)
      - HTTP status 503 (Service Unavailable)

    Retry policy:
      - Massimo `max_retries` tentativi extra (totale: 1 + max_retries)
      - Backoff esponenziale: attende backoff_base^tentativo secondi tra i retry
        più un jitter casuale (±20%) per evitare thundering herd
      - Alla prima risposta valida (qualunque status != 429/503) ritorna subito
      - Se tutti i tentativi falliscono, rilancia l'ultima eccezione

    Args:
        url:          URL da richiedere.
        headers:      Headers HTTP da inviare.
        max_retries:  Numero massimo di tentativi aggiuntivi (default: 2).
        backoff_base: Base per il calcolo del backoff (default: 2.0 secondi).
        timeout:      Timeout per richiesta (default: TIMEOUT costante).
        session:      requests.Session opzionale; ne crea una usa-e-getta se assente.

    Returns:
        requests.Response dell'ultima risposta riuscita.

    Raises:
        requests.Timeout:         Se tutti i tentativi scadono per timeout.
        requests.ConnectionError: Se tutti i tentativi falliscono per connessione.
        requests.HTTPError:       Se riceve un errore HTTP non recuperabile.
    """
    requester = session or requests
    last_exc: Optional[BaseException] = None

    for attempt in range(1 + max_retries):          # tentativo 0, 1, 2 …
        try:
            resp = requester.get(url, headers=headers, timeout=timeout)

            # 429 Too Many Requests o 503 Service Unavailable → vale la pena riprovare
            if resp.status_code in (429, 503):
                # Rispetta l'header Retry-After se presente (in secondi)
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    suggested = float(retry_after)
                else:
                    # Backoff esponenziale: 2^attempt con jitter ±20%
                    suggested = backoff_base ** (attempt + 1)

                jitter    = suggested * random.uniform(-0.2, 0.2)
                wait_time = max(1.0, suggested + jitter)

                if attempt < max_retries:
                    print(
                        f"    ⚠️  HTTP {resp.status_code} — "
                        f"tentativo {attempt + 1}/{1 + max_retries}, "
                        f"attendo {wait_time:.1f}s…"
                    )
                    time.sleep(wait_time)
                    headers = get_headers()          # ruota lo User-Agent
                    continue
                else:
                    # Ultimo tentativo: ritorna la risposta così com'è
                    return resp

            # Risposta valida (anche 4xx/5xx non gestiti sopra)
            return resp

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait_time = backoff_base ** (attempt + 1)
                jitter    = wait_time * random.uniform(-0.2, 0.2)
                wait_time = max(1.0, wait_time + jitter)
                exc_tipo  = "Timeout" if isinstance(exc, requests.Timeout) else "Errore di connessione"
                print(
                    f"    ⚠️  {exc_tipo} — "
                    f"tentativo {attempt + 1}/{1 + max_retries}, "
                    f"attendo {wait_time:.1f}s…"
                )
                time.sleep(wait_time)
                headers = get_headers()              # ruota lo User-Agent

    # Tutti i tentativi esauriti → rilancia l'ultima eccezione
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"fetch_with_retry: tutti i tentativi ({1 + max_retries}) verso {url} esauriti.")


# ===========================================================================
# SCRAPER — trovaprezzi.it
# ===========================================================================

def scrape_trovaprezzi(
    query: str,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per trovaprezzi.it.

    trovaprezzi.it aggrega offerte da decine di shop italiani (Unieuro,
    MediaWorld, eBay, ecc.), quindi una singola pagina copre molte fonti.

    Strategia: usa pagine categoria statiche (es. /notebook/offerte/notebook)
    e affida la precisione alla funzione is_relevant().

    Se la query non corrisponde a una categoria supportata, la fonte viene
    saltata silenziosamente.
    """
    categoria_path = _select_trovaprezzi_categoria(query_tokens)
    if not categoria_path:
        # Nessuna categoria chiara: passa oltre senza rumore.
        return []

    categoria_url = f"https://www.trovaprezzi.it/{categoria_path}"
    print(f"\n🔍 Cerco su trovaprezzi.it (categoria): \"{categoria_path}\"")

    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.trovaprezzi.it/"

        resp = fetch_with_retry(categoria_url, headers)
        if resp.status_code in (401, 403):
            print("    ⚠️  trovaprezzi.it: accesso bloccato (anti-bot), salto la fonte.")
            return risultati

        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Controlla se il sito ha restituito una pagina CAPTCHA / anti-bot
        page_title = (soup.title.string or "") if soup.title else ""
        if any(kw in page_title.lower() for kw in ("captcha", "verifica", "robot", "challenge")):
            print("    ❌ trovaprezzi.it ha restituito una pagina anti-bot, impossibile proseguire.")
            return risultati

        # ---------------------------------------------------------------
        # Parsing statico categoria trovaprezzi:
        #   link prodotto: a[href*='/goto/']
        #   negozio:       a[href*='/negozi/']
        #   prezzo:        classi price o fallback regex nel testo blocco
        # ---------------------------------------------------------------
        product_links = soup.select("a[href*='/goto/']")
        if not product_links:
            print("    ⚠️  Nessun link prodotto statico trovato su trovaprezzi.")
            return risultati

        print(f"    ✅ Trovati {len(product_links)} link prodotto grezzi su trovaprezzi.it")
        seen_links: set[str] = set()

        for link_tag in product_links:
            try:
                href = str(link_tag.get("href", "") or "")
                if not href:
                    continue
                link = href if href.startswith("http") else urljoin("https://www.trovaprezzi.it", href)
                if link in seen_links:
                    continue
                seen_links.add(link)

                # Trova il blocco più vicino che rappresenta la riga prodotto/offerta
                item = link_tag.find_parent(["li", "article", "div", "section"])
                if item is None:
                    continue

                # ----- Nome prodotto -----
                nome_tag = (
                    item.select_one("h3")
                    or item.select_one("h2")
                    or item.select_one("[class*='name']")
                    or item.select_one("[class*='title']")
                    or item.select_one("[class*='product-name']")
                )
                nome = nome_tag.get_text(strip=True) if nome_tag else ""
                if not nome:
                    continue

                # ----- Prezzo -----
                prezzo_tag = (
                    item.select_one(".price")
                    or item.select_one(".prezzo")
                    or item.select_one("[class*='price']")
                    or item.select_one("[class*='Price']")
                )
                if prezzo_tag:
                    prezzo = parse_price(prezzo_tag.get_text(strip=True))
                else:
                    # Fallback: estrae prezzi dal testo riga (es. "829,00 €")
                    blocco_txt = item.get_text(" ", strip=True)
                    raw_prices = re.findall(r"\d{1,3}(?:[\.\s]\d{3})*,\d{2}\s*€", blocco_txt)
                    parsed_prices = [parse_price(p) for p in raw_prices]
                    valid_prices = [p for p in parsed_prices if p != float("inf") and p >= 30]
                    prezzo = min(valid_prices) if valid_prices else float("inf")

                if prezzo == float("inf"):
                    continue

                # ----- Negozio -----
                negozio_tag = item.select_one("a[href*='/negozi/']")
                if not negozio_tag:
                    negozio_tag = (
                        item.select_one(".merchant")
                        or item.select_one(".store-name")
                        or item.select_one("[class*='merchant']")
                        or item.select_one("[class*='seller']")
                    )
                negozio = negozio_tag.get_text(strip=True) if negozio_tag else "Vari negozi"

                # ----- Filtri -----
                if not is_relevant(nome, query_tokens):
                    continue
                if budget_max is not None and prezzo > budget_max:
                    continue

                risultati.append(Offerta(nome=nome, prezzo=prezzo, negozio=negozio,
                                         link=link, fonte="trovaprezzi.it"))

            except (AttributeError, TypeError):
                # Elemento malformato: salta silenziosamente
                continue

    except requests.Timeout:
        print("    ❌ trovaprezzi.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ trovaprezzi.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ trovaprezzi.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ trovaprezzi.it: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# SCRAPER — amazon.it
# ===========================================================================

def scrape_amazon(
    query: str,
    budget_max: Optional[float],
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """
    Scraper per amazon.it.

    Usa la pagina di ricerca standard nella categoria 'computers'.

    NOTE SELETTORI (validi a marzo 2026):
        Ogni prodotto è un <div data-component-type="s-search-result">.
        Aggiornare i selettori qui se Amazon cambia il layout.
    """
    url = f"https://www.amazon.it/s?k={quote_plus(query)}&i=computers"
    if condizione == "nuovo":
        url += "&rh=p_n_condition-type%3A1294423031"
    elif condizione == "usato":
        url += "&rh=p_n_condition-type%3A1294424031"

    print(f"\n🔍 Cerco su Amazon.it: \"{query}\"")

    risultati: list[Offerta] = []

    try:
        base_headers = get_headers()
        base_headers["sec-ch-ua"] = '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"'
        base_headers["sec-ch-ua-mobile"] = "?0"
        base_headers["sec-ch-ua-platform"] = '"Windows"'
        base_headers["sec-fetch-dest"] = "document"
        base_headers["sec-fetch-mode"] = "navigate"
        base_headers["sec-fetch-site"] = "none"
        base_headers["sec-fetch-user"] = "?1"
        base_headers["Cache-Control"] = "max-age=0"

        with requests.Session() as session:
            home_headers = dict(base_headers)
            home_headers["Referer"] = "https://www.amazon.it/"
            _ = fetch_with_retry("https://www.amazon.it", home_headers, session=session)

            # Delay umano tra apertura homepage e ricerca per ridurre blocchi anti-bot
            time.sleep(random.uniform(2.0, 3.0))

            search_headers = dict(base_headers)
            search_headers["Referer"] = "https://www.amazon.it/"

            resp = fetch_with_retry(url, search_headers, session=session)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Controlla CAPTCHA / robot check
        page_title = (soup.title.string or "") if soup.title else ""
        if any(kw in page_title.lower() for kw in ("sorry", "robot", "captcha", "service unavailable")):
            print("    ❌ Amazon.it ha restituito una pagina anti-bot.")
            return risultati

        # ---------------------------------------------------------------
        # Parsing card prodotto
        # ---------------------------------------------------------------
        cards = soup.select('div[data-component-type="s-search-result"]')

        if not cards:
            print("    ⚠️  Nessun prodotto trovato su Amazon — selettore cambiato o CAPTCHA.")
            return risultati

        print(f"    ✅ Trovate {len(cards)} card grezze su Amazon.it")

        for card in cards:
            try:
                # ----- Nome prodotto -----
                nome_tag = card.select_one("h2 span.a-text-normal") or card.select_one("h2 span")
                if not nome_tag:
                    continue
                nome = nome_tag.get_text(strip=True)
                if not nome:
                    continue

                # ----- Prezzo -----
                # Prova prima il tag già formattato (.a-offscreen), poi la composizione
                prezzo_tag = card.select_one(".a-price .a-offscreen")
                if prezzo_tag:
                    prezzo = parse_price(prezzo_tag.get_text(strip=True))
                else:
                    intero_tag    = card.select_one(".a-price-whole")
                    decimale_tag  = card.select_one(".a-price-fraction")
                    if intero_tag and decimale_tag:
                        prezzo_str = f"{intero_tag.get_text(strip=True)}.{decimale_tag.get_text(strip=True)}"
                        prezzo = parse_price(prezzo_str)
                    else:
                        continue  # Nessun prezzo trovato

                if prezzo == float("inf"):
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

                # ----- Filtri -----
                if not is_relevant(nome, query_tokens):
                    continue
                if budget_max is not None and prezzo > budget_max:
                    continue

                risultati.append(Offerta(nome=nome, prezzo=prezzo, negozio=negozio,
                                         link=link, fonte="amazon.it"))

            except (AttributeError, TypeError):
                continue

    except requests.Timeout:
        print("    ❌ Amazon.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Amazon.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ Amazon.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ Amazon.it: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# SCRAPER — ebay.it
# ===========================================================================

def scrape_ebay(
    query: str,
    budget_max: Optional[float],
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """Scraper per eBay Italia con ordinamento prezzo crescente."""
    url = f"https://www.ebay.it/sch/i.html?_nkw={quote_plus(query)}&_sop=15"
    if condizione == "nuovo":
        url += "&LH_ItemCondition=1000"
    elif condizione == "usato":
        url += "&LH_ItemCondition=3000"

    print(f"\n🔍 Cerco su eBay.it: \"{query}\"")
    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.ebay.it/"

        resp = fetch_with_retry(url, headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        page_title = (soup.title.string or "") if soup.title else ""
        if "ci scusiamo per l'interruzione" in page_title.lower():
            print("    ⚠️  eBay.it: pagina anti-bot/interruzione, salto la fonte.")
            return risultati

        cards = soup.select(".s-item")
        if not cards:
            print("    ⚠️  Nessun prodotto trovato su eBay — selettore cambiato o blocco anti-bot.")
            return risultati

        print(f"    ✅ Trovate {len(cards)} card grezze su eBay.it")

        for card in cards:
            try:
                title_tag = card.select_one(".s-item__title")
                price_tag = card.select_one(".s-item__price")
                link_tag = card.select_one("a.s-item__link[href]")
                if not title_tag or not price_tag or not link_tag:
                    continue

                nome = title_tag.get_text(strip=True)
                if not nome or nome.lower() == "shop on ebay":
                    continue

                prezzo = parse_price(price_tag.get_text(strip=True))
                if prezzo == float("inf"):
                    continue

                href = str(link_tag.get("href", "") or "")
                if not href:
                    continue
                link = href if href.startswith("http") else urljoin("https://www.ebay.it", href)

                if not is_relevant(nome, query_tokens):
                    continue
                if budget_max is not None and prezzo > budget_max:
                    continue

                risultati.append(
                    Offerta(nome=nome, prezzo=prezzo, negozio="eBay.it", link=link, fonte="ebay.it")
                )
            except (AttributeError, TypeError):
                continue

    except requests.Timeout:
        print("    ❌ eBay.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ eBay.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ eBay.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ eBay.it: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# SCRAPER — vinted.it
# ===========================================================================

def scrape_vinted(
    query: str,
    budget_max: Optional[float],
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """Scraper per Vinted Italia (catalog ordinato per prezzo crescente)."""
    if condizione == "nuovo":
        print("\nℹ️ Vinted mostra solo articoli usati")
        return []

    url = f"https://www.vinted.it/catalog?search_text={quote_plus(query)}&order=price_low_to_high"
    print(f"\n🔍 Cerco su Vinted.it: \"{query}\"")

    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://www.vinted.it/"

        resp = fetch_with_retry(url, headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        items = soup.select('[data-testid="grid-item"]')
        if not items:
            print("    ⚠️  Nessun prodotto trovato su Vinted — possibile blocco o layout cambiato.")
            return risultati

        print(f"    ✅ Trovati {len(items)} elementi grezzi su Vinted.it")

        for item in items:
            try:
                nome_tag = (
                    item.select_one('[data-testid$="--description-title"]')
                    or item.select_one('[data-testid="description-title"]')
                )
                prezzo_tag = item.select_one('[data-testid="price-text"]')
                link_tag = item.select_one("a[href]")
                if not prezzo_tag or not link_tag:
                    continue

                nome = nome_tag.get_text(strip=True) if nome_tag else ""
                if not nome or nome.lower() in {"rimosso!", "removed"}:
                    nome = str(link_tag.get("title", "") or "")
                if not nome:
                    img_tag = item.select_one("img")
                    nome = str(img_tag.get("alt", "") or "") if img_tag else ""
                if not nome:
                    continue

                prezzo_raw = prezzo_tag.get_text(" ", strip=True)
                m = re.search(r"\d{1,3}(?:[\.\s]\d{3})*,\d{2}|\d+(?:\.\d{2})?", prezzo_raw)
                if not m:
                    continue
                prezzo = parse_price(m.group(0))
                if prezzo == float("inf"):
                    continue

                href = str(link_tag.get("href", "") or "")
                if not href:
                    continue
                link = href if href.startswith("http") else urljoin("https://www.vinted.it", href)

                if not is_relevant(nome, query_tokens):
                    continue
                if budget_max is not None and prezzo > budget_max:
                    continue

                risultati.append(
                    Offerta(nome=nome, prezzo=prezzo, negozio="Vinted", link=link, fonte="vinted.it")
                )
            except (AttributeError, TypeError):
                continue

        # Fallback: su alcune pagine Vinted i campi prezzo/titolo sono nel title del link.
        if not risultati:
            item_links = soup.select('a[href*="/items/"]')
            seen: set[str] = set()
            for a_tag in item_links:
                try:
                    href = str(a_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else urljoin("https://www.vinted.it", href)
                    if link in seen:
                        continue
                    seen.add(link)

                    title_attr = str(a_tag.get("title", "") or "")
                    if not title_attr:
                        continue

                    nome = title_attr.split(", brand:")[0].strip()
                    if not nome:
                        continue

                    price_match = re.search(r"\d+[\.,]\d{2}", title_attr)
                    if not price_match:
                        continue
                    prezzo = parse_price(price_match.group(0))
                    if prezzo == float("inf"):
                        continue

                    if not is_relevant(nome, query_tokens):
                        continue
                    if budget_max is not None and prezzo > budget_max:
                        continue

                    risultati.append(
                        Offerta(nome=nome, prezzo=prezzo, negozio="Vinted", link=link, fonte="vinted.it")
                    )
                except (AttributeError, TypeError):
                    continue

    except requests.Timeout:
        print("    ❌ Vinted.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Vinted.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ Vinted.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ Vinted.it: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# DEDUPLICAZIONE
# ===========================================================================

def _deduplica(offerte: list[Offerta], soglia_pct: float = 0.05) -> list[Offerta]:
    """
    Rimuove duplicati dove nome identico (case-insensitive) e prezzo entro
    `soglia_pct` percentuale di differenza → mantiene l'offerta più economica.
    """
    uniche: list[Offerta] = []
    for offerta in offerte:
        duplicato = False
        for esistente in uniche:
            nome_simile = offerta.nome.lower() == esistente.nome.lower()
            if nome_simile:
                diff_rel = abs(offerta.prezzo - esistente.prezzo) / max(esistente.prezzo, 1)
                if diff_rel <= soglia_pct:
                    # Tiene la più economica
                    if offerta.prezzo < esistente.prezzo:
                        uniche.remove(esistente)
                        uniche.append(offerta)
                    duplicato = True
                    break
        if not duplicato:
            uniche.append(offerta)
    return uniche


# ===========================================================================
# OUTPUT
# ===========================================================================

def print_results(
    offerte: list[Offerta],
    query: str,
    budget_max: Optional[float],
    top_n: int,
) -> None:
    """Stampa i risultati finali in modo leggibile."""
    print("\n" + "=" * 70)
    print(f"  🛒 RISULTATI per: \"{query}\"")
    if budget_max is not None:
        print(f"  💵 Budget massimo: € {budget_max:,.2f}".replace(",", "."))
    print(f"  📋 Mostrati: {len(offerte)} risultati (top {top_n})")
    print("=" * 70)

    if not offerte:
        print("\n  ⚠️  Nessun risultato trovato. Prova a:\n"
              "       • Allargare il budget\n"
              "       • Usare termini più generici nella query\n"
              "       • Verificare la tua connessione internet\n")
        return

    for i, offerta in enumerate(offerte, start=1):
        print(f"\n  [{i:>2}] {offerta}")

    print("\n" + "=" * 70)


def export_to_csv(offerte: list[Offerta], filename: str = "offerte.csv") -> None:
    """
    Esporta i risultati in un file CSV.
    Usa pandas se disponibile, altrimenti usa il modulo csv stdlib.
    """
    fieldnames = ["posizione", "nome", "prezzo_eur", "negozio", "fonte", "link"]
    rows = [
        {
            "posizione":  i,
            "nome":       o.nome,
            "prezzo_eur": f"{o.prezzo:.2f}",
            "negozio":    o.negozio,
            "fonte":      o.fonte,
            "link":       o.link,
        }
        for i, o in enumerate(offerte, start=1)
    ]

    try:
        import pandas as pd  # opzionale
        df = pd.DataFrame(rows)
        df.to_csv(filename, index=False, encoding="utf-8-sig")  # utf-8-sig per Excel italiano
    except ImportError:
        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    abs_path = os.path.abspath(filename)
    print(f"\n  ✅ Risultati esportati in: {abs_path}")


# ===========================================================================
# FUNZIONE PRINCIPALE
# ===========================================================================

def cerca_offerte(
    query: str,
    budget_max: Optional[float] = None,
    prezzo_min: float = 0,
    top_n: int = 10,
    export_csv: bool = False,
    csv_filename: str = "offerte.csv",
    condizione: str = "tutti",
    fonti: Optional[list[str]] = None,
) -> list[Offerta]:
    """
    Cerca offerte tech su trovaprezzi.it e amazon.it.

    Args:
        query:        Testo della ricerca (es. "notebook 14 pollici 16gb RAM").
        prezzo_min:   Prezzo minimo in euro (default: 0).
        budget_max:   Prezzo massimo in euro. None = nessun limite.
        top_n:        Quante offerte mostrare (default: 10).
        export_csv:   Se True, salva i risultati in un file CSV.
        csv_filename: Nome del file CSV di output (default: "offerte.csv").
        condizione:   Filtro stato prodotto Amazon: "tutti", "nuovo", "usato".
        fonti:        Fonti da usare (amazon, ebay, vinted, trovaprezzi). None = tutte.

    Returns:
        Lista di Offerta ordinata per prezzo crescente (max top_n elementi).
    """
    if not query or not query.strip():
        print("❌ La query di ricerca non può essere vuota.")
        return []

    prezzo_min = max(0.0, float(prezzo_min))

    print(f"\n{'=' * 70}")
    print(f"  🚀 Avvio ricerca: \"{query}\"")
    print(f"  💵 Prezzo min: € {prezzo_min:.2f}")
    if budget_max is not None:
        print(f"  💵 Budget max: € {budget_max:.2f}")
    print(f"  🏷️  Condizione: {condizione}")
    print(f"  📊 Mostra top: {top_n} risultati")
    print(f"{'=' * 70}")

    # Tokenizzazione query per il filtro di rilevanza
    query_tokens = tokenize_query(query)
    print(f"\n  🔑 Token di ricerca: {query_tokens}")

    fonti_norm = {f.strip().lower() for f in (fonti or []) if str(f).strip()}
    if not fonti_norm:
        fonti_norm = {"amazon", "ebay", "vinted", "trovaprezzi"}
    print(f"  🌐 Fonti attive: {', '.join(sorted(fonti_norm))}")

    # Lancio scraper in parallelo sulle fonti selezionate
    offerte: list[Offerta] = []
    jobs = []
    with ThreadPoolExecutor(max_workers=4) as executor:
        if "trovaprezzi" in fonti_norm:
            jobs.append(executor.submit(scrape_trovaprezzi, query, budget_max, query_tokens))
        if "amazon" in fonti_norm:
            jobs.append(executor.submit(scrape_amazon, query, budget_max, query_tokens, condizione))
        if "ebay" in fonti_norm:
            jobs.append(executor.submit(scrape_ebay, query, budget_max, query_tokens, condizione))
        if "vinted" in fonti_norm:
            jobs.append(executor.submit(scrape_vinted, query, budget_max, query_tokens, condizione))

        for future in as_completed(jobs):
            try:
                offerte += future.result()
            except Exception as exc:
                print(f"    ⚠️  Una fonte ha generato un errore inatteso: {exc}")

    print(f"\n  📥 Totale risultati grezzi (post-filtro): {len(offerte)}")

    # Filtro finale relevance + anti-accessori + range prezzo
    offerte = [
        o for o in offerte
        if not is_accessory_mismatch(o.nome, query)
        and o.prezzo >= prezzo_min
        and (budget_max is None or o.prezzo <= budget_max)
    ]
    print(f"  🧹 Dopo filtro anti-accessori/range prezzo: {len(offerte)}")

    # Deduplicazione
    offerte = _deduplica(offerte)
    print(f"  🔄 Dopo deduplicazione: {len(offerte)} offerte uniche")

    # Ordinamento per prezzo crescente
    offerte.sort(key=lambda o: o.prezzo)

    # Tronca a top_n
    offerte_top = offerte[:top_n]

    # Output terminale
    print_results(offerte_top, query, budget_max, top_n)

    # Export CSV opzionale
    if export_csv and offerte_top:
        export_to_csv(offerte_top, csv_filename)

    return offerte_top


# ===========================================================================
# CLI — argparse
# ===========================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="offerte_tech",
        description="🛒 Cerca offerte tech su trovaprezzi.it e amazon.it",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Esempi:\n"
            "  python offerte_tech.py -q \"notebook 14 pollici 16gb\" -b 800 -n 10\n"
            "  python offerte_tech.py -q \"ssd 1tb\" --export csv\n"
        ),
    )
    parser.add_argument(
        "-q", "--query",
        required=True,
        metavar="TESTO",
        help="Query di ricerca (es. \"notebook 14 pollici 16gb\")",
    )
    parser.add_argument(
        "-b", "--budget",
        type=float,
        default=None,
        metavar="EUR",
        help="Budget massimo in euro (opzionale)",
    )
    parser.add_argument(
        "-n", "--top",
        type=int,
        default=10,
        metavar="N",
        help="Quanti risultati mostrare (default: 10)",
    )
    parser.add_argument(
        "--condizione",
        choices=["tutti", "nuovo", "usato"],
        default="tutti",
        metavar="STATO",
        help="Filtro condizione prodotto Amazon (default: tutti)",
    )
    parser.add_argument(
        "--fonti",
        nargs="+",
        choices=["amazon", "ebay", "vinted", "trovaprezzi"],
        default=None,
        metavar="FONTE",
        help="Seleziona le fonti da consultare (default: tutte)",
    )
    parser.add_argument(
        "--export",
        choices=["csv"],
        default=None,
        metavar="FORMATO",
        help="Esporta i risultati (attualmente supportato: csv)",
    )
    parser.add_argument(
        "--output",
        default="offerte.csv",
        metavar="FILE",
        help="Nome file di output per l'export (default: offerte.csv)",
    )
    return parser


if __name__ == "__main__":
    parser = _build_parser()
    args   = parser.parse_args()

    cerca_offerte(
        query        = args.query,
        budget_max   = args.budget,
        prezzo_min   = 0,
        top_n        = args.top,
        export_csv   = (args.export == "csv"),
        csv_filename = args.output,
        condizione   = args.condizione,
        fonti        = args.fonti,
    )

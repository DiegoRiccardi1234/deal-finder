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
import base64
import csv
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
    import streamlit as st
except Exception:
    st = None

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

_EBAY_TOKEN_CACHE: dict[str, object] = {"token": None, "expires_at": 0.0}

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
    # Alias di prodotto — sinonimi per tipi di prodotto comuni
    "notebook": {"laptop", "portatile", "ultrabook", "chromebook"},
    "laptop": {"notebook", "portatile", "ultrabook", "chromebook"},
    "smartphone": {"telefono", "cellulare", "phone"},
    "telefono": {"smartphone", "cellulare", "phone"},
    "cellulare": {"smartphone", "telefono", "phone"},
    "cuffie": {"auricolari", "earbuds", "headphones"},
    "auricolari": {"cuffie", "earbuds", "headphones"},
    "monitor": {"display", "schermo"},
}

_SPEC_PATTERN = re.compile(r'^\d+(?:gb|tb)$')
_SPEC_KEYWORDS = {"ram", "ssd", "hdd", "nvme", "ddr4", "ddr5"}


def _is_spec_token(token: str) -> bool:
    """True se il token rappresenta una specifica tecnica (es. '16gb', 'ram')."""
    return bool(_SPEC_PATTERN.match(token)) or token in _SPEC_KEYWORDS


_TECH_BRANDS = {
    "iphone", "apple", "samsung", "galaxy", "xiaomi", "redmi", "pixel", "google",
    "oneplus", "huawei", "honor", "oppo", "realme", "motorola", "nothing",
}

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
    spedizione: str = field(default="n.d.")
    alternativa: str = field(default="")
    specs: dict[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        nome_corto = self.nome[:62] + "…" if len(self.nome) > 63 else self.nome
        prezzo_fmt = f"€ {self.prezzo:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return (
            f"  💰 {prezzo_fmt:<12}  🏪 {self.negozio:<18}  📦 {nome_corto}\n"
            f"     📦 Spedizione: {self.spedizione}\n"
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
        # Brotli (br) rimosso: requests non decodifica Brotli senza il pacchetto brotli,
        # restituendo contenuto binario illeggibile.
        "Accept-Encoding": "gzip, deflate",
        "DNT":             "1",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _get_cerebras_api_key() -> str:
    """Legge la key Cerebras da Streamlit secrets o variabile ambiente."""
    if st is not None:
        try:
            key = str(st.secrets.get("CEREBRAS_API_KEY", "") or "")
            if key.strip():
                return key.strip()
        except Exception:
            pass
    return os.environ.get("CEREBRAS_API_KEY", "").strip()


def _get_cerebras_client() -> Optional[object]:
    """Crea il client Cerebras se disponibile e configurato."""
    api_key = _get_cerebras_api_key()
    if not api_key or Cerebras is None:
        return None
    try:
        return Cerebras(api_key=api_key)
    except Exception:
        return None


def parse_price(text: str) -> float:
    """
    Converte una stringa di prezzo in float.

    Gestisce tutti i formati reali Amazon/eBay italiani:
      - "€ 899,00"            (spazio dopo €)
      - "A partire da € 1.299,00"
      - "€899,00 con coupon"
      - "EUR 899,00"
      - "1.299,00 €"          (simbolo dopo il numero)
      - "1.299,00 - 1.399,00" (range → prende il minimo)

    Ritorna math.inf se il parsing fallisce o il testo e vuoto.
    """
    if not text:
        return math.inf

    text = str(text)
    # Normalizza spazi speciali
    text = text.replace("\u00a0", " ").replace("\u202f", " ")

    # Rimuove prefissi comuni ("A partire da", "da", ecc.)
    text = re.sub(r"(?i)^.*?(?:a partire da|da)\s*", "", text)
    # Rimuove suffissi comuni ("con coupon", "con sconto", ecc.)
    text = re.sub(r"(?i)\s+con\s+.*$", "", text)

    # Rimuove simboli valuta e parole note
    text = text.replace("EUR", "").replace("€", "").strip()
    text = re.sub(r"[()\[\]]", "", text)
    text = re.sub(r"\s+", " ", text)

    # In caso di range (es. "100,00 - 200,00"), prende il primo valore
    parts = re.split(r"\s*[-–]\s*", text)
    text = parts[0].strip()

    # Estrae il primo frammento numerico plausibile da testo rumoroso.
    number_match = re.search(
        r"\d{1,3}(?:[\.\s]\d{3})+(?:[\.,]\d{2})?|\d+(?:[\.,]\d{2})?",
        text,
    )
    if number_match:
        text = number_match.group(0)

    # Mantiene solo cifre e separatori decimali/migliaia
    text = re.sub(r"[^\d,\.]", "", text)

    if not text:
        return math.inf

    if text.count(".") > 1 and "," not in text:
        parts = [part for part in text.split(".") if part]
        if len(parts) >= 2 and len(parts[-1]) == 2:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = "".join(parts)

    # Formato europeo: 1.299,00 → togli i punti migliaia, sostituisci virgola decimale
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif "." in text and "," not in text:
        # Potrebbe essere già in formato anglosassone (es. "129.99") o migliaia (es. "1.299")
        dot_parts = text.split(".")
        if len(dot_parts[-1]) != 2:
            # È separatore migliaia, non decimale
            text = text.replace(".", "")
    else:
        text = re.sub(r"[^\d.]", "", text)

    try:
        val = float(text)
        return val if val > 0 else math.inf
    except ValueError:
        return math.inf


def _within_price_range(prezzo: float, prezzo_min: float, budget_max: Optional[float]) -> bool:
    """Verifica che il prezzo rientri nel range configurato."""
    if not math.isfinite(prezzo):
        return False
    return prezzo >= max(0.0, float(prezzo_min)) and (budget_max is None or prezzo <= budget_max)


def _normalize_category(categoria: str) -> str:
    """Normalizza categorie granulari verso tech / abbigliamento / altro."""
    categoria_norm = str(categoria or "").strip().lower()
    if categoria_norm in {"tech", "smartphone", "laptop", "tablet", "monitor", "console", "pc"}:
        return "tech"
    if categoria_norm in {"abbigliamento", "scarpe", "moda"}:
        return "abbigliamento"
    return "altro"


def _extract_json_object(raw: str) -> dict[str, object]:
    """Estrae un oggetto JSON da una stringa e ritorna un dict vuoto su errore."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}


def _extract_clothing_specs(nome_prodotto: str) -> dict[str, object]:
    """Estrae specifiche base da titoli abbigliamento senza usare AI."""
    nome = str(nome_prodotto or "").strip()
    lower_name = nome.lower()

    brand_match = re.search(r"\b([A-Z][A-Za-z0-9'&-]+)\b", nome)
    size_match = re.search(r"\b(?:XXS|XS|S|M|L|XL|XXL|36|37|38|39|40|41|42|43|44|45|46|47|48|49|50)\b", nome, flags=re.IGNORECASE)

    materiale = None
    for candidate in ("cotone", "poliestere", "lana", "pelle"):
        if candidate in lower_name:
            materiale = candidate
            break

    genere = None
    if any(token in lower_name for token in ("uomo", "men", "man")):
        genere = "uomo"
    elif any(token in lower_name for token in ("donna", "women", "woman")):
        genere = "donna"
    elif any(token in lower_name for token in ("unisex", "kids", "bambino", "bambina")):
        genere = "unisex"

    return {
        "brand": brand_match.group(1) if brand_match else None,
        "taglia": size_match.group(0).upper() if size_match else None,
        "materiale": materiale,
        "genere": genere,
    }


def fetch_specs_ai(
    offerte: list[Offerta],
    categoria: str,
    cerebras_client: Optional[object],
) -> list[Offerta]:
    """Arricchisce le offerte con specs in parallelo in base alla categoria."""
    if not offerte:
        return offerte

    categoria_norm = _normalize_category(categoria)
    if categoria_norm == "altro":
        for offerta in offerte:
            offerta.specs = {}
        return offerte

    if categoria_norm == "abbigliamento":
        for offerta in offerte:
            offerta.specs = _extract_clothing_specs(offerta.nome)
        return offerte

    if cerebras_client is None:
        for offerta in offerte:
            offerta.specs = {}
        return offerte

    def _fetch_single_specs(offerta: Offerta) -> tuple[Offerta, dict[str, object]]:
        prompt = (
            f"Sei un database di schede tecniche. Per '{offerta.nome}' restituisci SOLO un JSON con: "
            "display, processore, ram, storage, batteria, fotocamera, peso, os. "
            "Campi sconosciuti: null. Solo JSON, nessun testo extra."
        )
        try:
            completion = cerebras_client.chat.completions.create(
                model="gpt-oss-120b",
                messages=[
                    {"role": "system", "content": prompt},
                ],
                temperature=0,
            )
            content = completion.choices[0].message.content if completion and completion.choices else ""
            specs = _extract_json_object(str(content or ""))
            return offerta, specs
        except Exception:
            return offerta, {}

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_fetch_single_specs, offerta) for offerta in offerte]
        for future in as_completed(futures):
            offerta, specs = future.result()
            offerta.specs = specs if isinstance(specs, dict) else {}

    return offerte


def _extract_shipping_from_text(text: str) -> str:
    """Estrae costo spedizione da testo libero con fallback 'n.d.'."""
    text_lower = text.lower()
    if "spedizione gratuita" in text_lower or "consegna gratuita" in text_lower or "free shipping" in text_lower:
        return "Gratuita ✅"

    match = re.search(
        r"((?:€|EUR)\s*\d{1,3}(?:[\.\s]\d{3})*(?:[\.,]\d{2})?)\s*(?:di\s+)?spedizione",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(r"spedizione\s*(?:da|:)?\s*((?:€|EUR)\s*\d{1,3}(?:[\.\s]\d{3})*(?:[\.,]\d{2})?)", text, flags=re.IGNORECASE)
    if match:
        value = match.group(1).replace("EUR", "€").replace("  ", " ").strip()
        return value
    return "n.d."


def tokenize_query(query: str) -> list[str]:
    """
    Divide la query in token significativi, rimuovendo le stopword italiane.
    Normalizza in lowercase.
    Es: "notebook 14 pollici 16GB RAM" → ["notebook", "14", "pollici", "16gb", "ram"]
    """
    tokens = re.findall(r"[\w]+", query.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


def is_relevant(nome: str, query_tokens: list[str], strict_specs: bool = True) -> bool:
    """
    Filtro di rilevanza: i token della query devono essere presenti nel nome
    del prodotto (o in uno dei loro alias normalizzati).

    Con strict_specs=False, i token di specifica tecnica (es. '16gb', 'ram')
    vengono saltati — le specs verranno verificate tramite AI enrichment.
    """
    nome_lower = nome.lower()
    brand_tokens = [token for token in query_tokens if token in _TECH_BRANDS]
    for token in query_tokens:
        if not strict_specs and _is_spec_token(token):
            continue
        if token.isdigit() and len(token) <= 2 and brand_tokens:
            if not any(re.search(rf"\b{re.escape(brand)}\s*{re.escape(token)}\b", nome_lower) for brand in brand_tokens):
                return False
            continue

        # Espande il token con gli alias conosciuti
        varianti = _ALIASES.get(token, {token})
        varianti.add(token)
        if not any(v in nome_lower for v in varianti):
            return False
    return True


def _random_delay() -> None:
    """Attesa casuale tra DELAY_MIN e DELAY_MAX secondi."""
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


def _select_trovaprezzi_categoria(query_tokens: list[str]) -> Optional[str]:
    """Seleziona il path categoria trovaprezzi in base ai token query."""
    for token in query_tokens:
        if token in CATEGORIE_TROVAPREZZI:
            return CATEGORIE_TROVAPREZZI[token]
    return None


def _get_ebay_token(app_id: str, cert_id: str) -> str:
    """Ottiene e cachea un access token eBay OAuth2 tramite client credentials."""
    now = time.time()
    cached_token = str(_EBAY_TOKEN_CACHE.get("token") or "")
    cached_expiry = float(_EBAY_TOKEN_CACHE.get("expires_at") or 0)
    if cached_token and now < cached_expiry - 60:
        return cached_token

    credentials = f"{app_id}:{cert_id}".encode("utf-8")
    headers = {
        "Authorization": f"Basic {base64.b64encode(credentials).decode('ascii')}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }
    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers=headers,
        data=data,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()
    access_token = str(payload.get("access_token") or "").strip()
    expires_in = int(payload.get("expires_in", 7200) or 7200)
    if not access_token:
        raise RuntimeError("Token eBay mancante nella risposta OAuth2")

    _EBAY_TOKEN_CACHE["token"] = access_token
    _EBAY_TOKEN_CACHE["expires_at"] = now + expires_in
    return access_token


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
# SCRAPER — Google Shopping
# ===========================================================================

def scrape_trovaprezzi(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
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
    print(f"\n🔍 Cerco su Trovaprezzi.it: \"{query}\"")

    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.trovaprezzi.it/"

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(f"    ⚠️  Trovaprezzi.it: accesso bloccato (HTTP {resp.status_code}), salto la fonte.")
            return risultati

        resp.raise_for_status()

        base_url = resp.url  # URL finale dopo redirect (usato per la paginazione)

        seen_links: set[str] = set()
        _tp_cat_words = {"notebook", "laptop", "smartphone", "tablet", "telefono",
                         "cellulare", "monitor", "cuffie", "auricolari", "pc"}
        tp_tokens = [t for t in query_tokens if t not in _tp_cat_words] or query_tokens

        def _parse_page_tp(html: str) -> int:
            """Parsa una pagina trovaprezzi, aggiunge offerte valide, ritorna il numero aggiunto."""
            soup_p = BeautifulSoup(html, "html.parser")
            page_title_p = str((soup_p.title.string or "") if soup_p.title else "")
            if any(kw in page_title_p.lower() for kw in ("sorry", "captcha", "robot", "unusual traffic", "404")):
                return 0
            cards_p = soup_p.select("a.suggested_product[href]")
            added = 0
            for card in cards_p:
                try:
                    nome_tag = card.select_one(".name") or card.select_one("[class*='name']")
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue
                    prezzo_tag = card.select_one(".price_range") or card.select_one("[class*='price']")
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
                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="Trovaprezzi",
                        link=link, fonte="trovaprezzi.it", spedizione="n.d.",
                    ))
                    added += 1
                except (AttributeError, TypeError):
                    continue
            return added

        p1 = _parse_page_tp(resp.text)
        if not p1:
            print("    ⚠️  Trovaprezzi.it: nessun risultato parsabile (selettori cambiati o blocco).")
        else:
            print(f"    ✅ Trovate {len(risultati)} card su Trovaprezzi.it")
            # Paginazione: prova pagine 2 e 3 (stop se vuota o errore)
            for _pn in range(2, 4):
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


# ===========================================================================
# SCRAPER — amazon.it
# ===========================================================================

def scrape_amazon(
    query: str,
    prezzo_min: float,
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

                risultati.append(Offerta(nome=nome, prezzo=prezzo, negozio=negozio,
                                         link=link, fonte="amazon.it", spedizione=spedizione))

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
                st.info(
                    "ℹ️ **Amazon.it non raggiungibile** — Amazon blocca le richieste "
                    "provenienti da server cloud. Usa eBay o Trovaprezzi, oppure avvia "
                    "l'app in locale (`streamlit run app.py`) per includere Amazon."
                )
            except Exception:
                pass
    except Exception as exc:
        print(f"    ❌ Amazon.it: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# SCRAPER — ebay.it (Browse API ufficiale)
# ===========================================================================

def scrape_ebay(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    condizione: str,
    query_tokens: list[str],
    app_id: str,
    cert_id: str,
) -> list[Offerta]:
    """Scraper eBay.it tramite eBay Browse API ufficiale."""
    print(f"\n🔍 Cerco su eBay.it API: \"{query}\"")
    risultati: list[Offerta] = []

    if not app_id or not cert_id:
        print("    ⚠️  eBay non configurato — chiavi mancanti.")
        return risultati

    price_max = budget_max if budget_max is not None else 999999
    price_filter = f"price:[{max(0, prezzo_min)}..{price_max}],priceCurrency:EUR"
    if condizione == "nuovo":
        api_filter = price_filter + ",conditionIds:{1000}"
    elif condizione == "usato":
        api_filter = price_filter + ",conditionIds:{3000|4000|5000}"
    else:
        api_filter = price_filter

    params = {
        "q": query,
        "limit": 50,
        "filter": api_filter,
    }
    endpoint = "https://api.ebay.com/buy/browse/v1/item_summary/search"

    for attempt in range(2):
        try:
            token = _get_ebay_token(app_id, cert_id)
            headers = {
                "Authorization": f"Bearer {token}",
                "X-EBAY-C-MARKETPLACE-ID": "EBAY_IT",
                "Accept": "application/json",
            }
            response = requests.get(endpoint, headers=headers, params=params, timeout=TIMEOUT)

            if response.status_code == 401 and attempt == 0:
                _EBAY_TOKEN_CACHE["token"] = None
                _EBAY_TOKEN_CACHE["expires_at"] = 0.0
                continue

            response.raise_for_status()

            try:
                payload = response.json()
            except ValueError as exc:
                raise RuntimeError(f"JSON eBay malformato: {exc}") from exc

            items = payload.get("itemSummaries", [])
            for item in items:
                try:
                    nome = str(item.get("title") or "").strip()
                    if not nome or not is_relevant(nome, query_tokens, strict_specs=False):
                        continue

                    price_obj = item.get("price") or {}
                    prezzo = float(price_obj.get("value"))
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    seller = item.get("seller") or {}
                    link = str(item.get("itemWebUrl") or "").strip()
                    if not link:
                        continue

                    risultati.append(
                        Offerta(
                            nome=nome,
                            prezzo=prezzo,
                            negozio=str(seller.get("username") or "eBay"),
                            link=link,
                            fonte="ebay.it",
                            spedizione="n.d.",
                        )
                    )
                except (TypeError, ValueError):
                    continue

            print(f"    ✅ eBay Browse API: {len(risultati)} risultati validi")
            _random_delay()
            return risultati

        except requests.Timeout:
            print("    ❌ eBay Browse API: timeout.")
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "sconosciuto"
            print(f"    ❌ eBay Browse API: errore HTTP {status}.")
        except requests.ConnectionError:
            print("    ❌ eBay Browse API: errore di connessione.")
        except Exception as exc:
            print(f"    ❌ eBay Browse API: errore inatteso → {exc}")

        if attempt == 0:
            time.sleep(1.0)

    _random_delay()
    return []


# ===========================================================================
# SCRAPER — vinted.it
# ===========================================================================

def scrape_vinted(
    query: str,
    prezzo_min: float,
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
                if not math.isfinite(prezzo):
                    continue

                href = str(link_tag.get("href", "") or "")
                if not href:
                    continue
                link = href if href.startswith("http") else urljoin("https://www.vinted.it", href)

                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue

                spedizione = _extract_shipping_from_text(item.get_text(" ", strip=True))

                risultati.append(
                    Offerta(nome=nome, prezzo=prezzo, negozio="Vinted", link=link, fonte="vinted.it", spedizione=spedizione)
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
                    if not math.isfinite(prezzo):
                        continue

                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    spedizione = _extract_shipping_from_text(title_attr)

                    risultati.append(
                        Offerta(nome=nome, prezzo=prezzo, negozio="Vinted", link=link, fonte="vinted.it", spedizione=spedizione)
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
# SCRAPER — euronics.it
# ===========================================================================

def scrape_euronics(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per euronics.it.

    NOTE SELETTORI (validi a marzo 2026):
        URL ricerca: /search?q=
        Container:   .product-tile  con filtro .tile-category ∈ categorie laptop
        Nome:        span.tile-name
        Prezzo:      span.price-formatted.mr-2  (prezzo principale, non accessori)
        Link:        a.text-dark[href] o primo a[href] (relativo → prepend euronics.it)

    Euronics include nella pagina di ricerca sia notebook sia accessori
    (borse, zaini…): si esegue un filtro per .tile-category per tenere solo
    le categorie di prodotto laptop/notebook.
    """
    # Categorie Euronics che identificano laptop/notebook (non accessori)
    _EURONICS_LAPTOP_CATS = {
        "notebook", "notebook gaming", "notebook convertibili 2-in-1",
        "ultrabook", "chromebook", "laptop",
    }

    url = f"https://www.euronics.it/search?q={quote_plus(query)}"
    print(f"\n🔍 Cerco su Euronics.it: \"{query}\"")
    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.euronics.it/"

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(f"    ⚠️  Euronics.it: accesso bloccato (HTTP {resp.status_code}), salto la fonte.")
            return risultati
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        page_title = str((soup.title.string or "") if soup.title else "")
        if any(kw in page_title.lower() for kw in ("captcha", "robot", "sorry", "access denied", "404")):
            print("    ⚠️  Euronics.it: blocco anti-bot o pagina non trovata, salto la fonte.")
            return risultati

        # ── Strategia 1: CSS selettori .product-tile ──
        cards = soup.select(".product-tile")

        if cards:
            print(f"    ✅ Trovate {len(cards)} card su Euronics.it")
            seen_links: set[str] = set()
            # Token senza parole-categoria: Euronics filtra già per notebook/laptop.
            # Manteniamo solo token di dimensione/brand (es. "14") per is_relevant.
            _euronics_cat_words = {
                "notebook", "laptop", "smartphone", "tablet", "telefono",
                "cellulare", "monitor", "cuffie", "auricolari", "pc", "ultrabook",
            }
            eu_tokens = [t for t in query_tokens if t not in _euronics_cat_words] or query_tokens
            for card in cards:
                try:
                    # Filtra accessori: accetta solo categorie laptop/notebook
                    cat_tag = card.select_one(".tile-category")
                    cat_text = cat_tag.get_text(strip=True).lower() if cat_tag else ""
                    if cat_text and not any(kw in cat_text for kw in _EURONICS_LAPTOP_CATS):
                        continue

                    # Nome: span.tile-name
                    nome_tag = (
                        card.select_one("span.tile-name") or
                        card.select_one("[class*='tile-name']") or
                        card.select_one("h2") or
                        card.select_one("h3")
                    )
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue

                    # Prezzo: span.price-formatted.mr-2 (classe specifica del prezzo principale)
                    prezzo_tag = (
                        card.select_one("span.price-formatted.mr-2") or
                        card.select_one("span.price-formatted") or
                        card.select_one("[class*='price-formatted']") or
                        card.select_one("[class*='price']")
                    )
                    if not prezzo_tag:
                        continue
                    prezzo = parse_price(prezzo_tag.get_text(" ", strip=True))
                    if not math.isfinite(prezzo):
                        continue

                    # Link: prima a[href] nel card
                    link_tag = card.select_one("a.text-dark[href]") or card.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.euronics.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    if not is_relevant(nome, eu_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="Euronics",
                        link=link, fonte="euronics.it", spedizione="n.d.",
                    ))
                except (AttributeError, TypeError):
                    continue

        # ── Strategia 2: JSON-LD fallback ──
        if not risultati:
            for script in soup.find_all("script", {"type": "application/ld+json"}):
                try:
                    data = json.loads(str(script.string or ""))
                    # Supporta sia lista che singolo oggetto e ItemList
                    items_raw = []
                    if isinstance(data, list):
                        items_raw = data
                    elif isinstance(data, dict):
                        if data.get("@type") == "ItemList":
                            items_raw = [el.get("item", el) for el in data.get("itemListElement", [])]
                        else:
                            items_raw = [data]
                    for item_data in items_raw:
                        if not isinstance(item_data, dict):
                            continue
                        nome = str(item_data.get("name", "") or "").strip()
                        if not nome:
                            continue
                        offers = item_data.get("offers", {}) or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        prezzo = parse_price(str(offers.get("price", "") or ""))
                        if not math.isfinite(prezzo):
                            continue
                        link = str(item_data.get("url", "") or offers.get("url", "") or "").strip()
                        if not link:
                            continue
                        if not is_relevant(nome, query_tokens, strict_specs=False):
                            continue
                        if not _within_price_range(prezzo, prezzo_min, budget_max):
                            continue
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="Euronics",
                            link=link, fonte="euronics.it", spedizione="n.d.",
                        ))
                except Exception:
                    continue

        if not risultati:
            print("    ⚠️  Euronics.it: nessun risultato parsabile (selettori cambiati o blocco).")

    except requests.Timeout:
        print("    ❌ Euronics.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Euronics.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ Euronics.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ Euronics.it: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# SCRAPER — unieuro.it
# ===========================================================================

def scrape_unieuro(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per unieuro.it.

    NOTA: il sito usa Ionic Framework (Angular SPA) che richiede JavaScript
    per il rendering. Le richieste HTTP statiche ricevono solo il wrapper HTML
    senza prodotti. La fonte viene saltata con messaggio informativo.
    """
    url = f"https://www.unieuro.it/online/search?q={quote_plus(query)}&sortBy=relevance"
    print(f"\n🔍 Cerco su Unieuro.it: \"{query}\"")
    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.unieuro.it/"

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(f"    ⚠️  Unieuro.it: accesso bloccato (HTTP {resp.status_code}), salto la fonte.")
            return risultati
        resp.raise_for_status()

        # Rileva Ionic SPA: la pagina restituisce sempre lo stesso wrapper JS
        html_snip = resp.text[:3000]
        is_ionic_spa = (
            "ion-ce" in html_snip or
            "Please enable JavaScript" in html_snip or
            "ionic" in html_snip.lower() or
            ("unieuro" in html_snip.lower() and "<ion-" in html_snip)
        )
        if is_ionic_spa:
            print("    ℹ️  Unieuro.it: usa una webapp JavaScript (Ionic/Angular) che richiede rendering lato browser — fonte non disponibile senza Playwright.")
            return risultati

        soup = BeautifulSoup(resp.text, "html.parser")

        page_title = str((soup.title.string or "") if soup.title else "")
        if any(kw in page_title.lower() for kw in ("captcha", "robot", "sorry", "access denied")):
            print("    ⚠️  Unieuro.it: blocco anti-bot, salto la fonte.")
            return risultati

        # CSS selettori (fallback nel caso il sito torni SSR)
        cards = (
            soup.select(".h-product") or
            soup.select("[data-productid]") or
            soup.select(".product-tile") or
            soup.select("article[class*='product']") or
            soup.select("[class*='ProductCard']")
        )

        if cards:
            print(f"    ✅ Trovate {len(cards)} card su Unieuro.it")
            seen_links: set[str] = set()
            for card in cards:
                try:
                    nome_tag = (
                        card.select_one("[class*='product-name']") or
                        card.select_one("[class*='ProductName']") or
                        card.select_one("h2") or
                        card.select_one("h3") or
                        card.select_one("a[title]")
                    )
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True) or str(nome_tag.get("title", "") or "")
                    if not nome:
                        continue

                    prezzo_tag = (
                        card.select_one("[class*='price-value']") or
                        card.select_one("[class*='Price']") or
                        card.select_one("[class*='price']") or
                        card.select_one(".price")
                    )
                    if not prezzo_tag:
                        continue
                    prezzo = parse_price(prezzo_tag.get_text(" ", strip=True))
                    if not math.isfinite(prezzo):
                        continue

                    link_tag = card.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.unieuro.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="Unieuro",
                        link=link, fonte="unieuro.it", spedizione="n.d.",
                    ))
                except (AttributeError, TypeError):
                    continue

        # JSON-LD fallback
        if not risultati:
            for script in soup.find_all("script", {"type": "application/ld+json"}):
                try:
                    data = json.loads(str(script.string or ""))
                    items = data if isinstance(data, list) else [data]
                    for item_data in items:
                        if item_data.get("@type") != "Product":
                            continue
                        nome = str(item_data.get("name", "") or "").strip()
                        if not nome:
                            continue
                        offers = item_data.get("offers", {}) or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        prezzo = parse_price(str(offers.get("price", "") or ""))
                        if not math.isfinite(prezzo):
                            continue
                        link = str(item_data.get("url", "") or offers.get("url", "") or "").strip()
                        if not link:
                            continue
                        if not is_relevant(nome, query_tokens, strict_specs=False):
                            continue
                        if not _within_price_range(prezzo, prezzo_min, budget_max):
                            continue
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="Unieuro",
                            link=link, fonte="unieuro.it", spedizione="n.d.",
                        ))
                except Exception:
                    continue

        if not risultati:
            print("    ⚠️  Unieuro.it: nessun risultato parsabile.")

    except requests.Timeout:
        print("    ❌ Unieuro.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ Unieuro.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ Unieuro.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ Unieuro.it: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# SCRAPER — mediaworld.it
# ===========================================================================

def scrape_mediaworld(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per MediaWorld.it.

    Parsing multi-strategia (priorità decrescente):
      1. article[data-test="mms-product-card"] (confermato a marzo 2026):
           - Nome:    [data-test="product-title"]
           - Link:   primo a[href] nell'article
           - Prezzo: regex su testo grezzo "Consigliato X,–€Y,00€Z,00" → min(prezzi)
      2. JSON-LD <script type="application/ld+json"> @type=ItemList
      3. CSS selettori generici come last-resort
    """
    url = f"https://www.mediaworld.it/it/search.html?q={quote_plus(query)}&sortby=rating&pageNumber=0"
    print(f"\n🔍 Cerco su MediaWorld.it: \"{query}\"")
    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.mediaworld.it/it/"

        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(f"    ⚠️  MediaWorld.it: accesso bloccato (HTTP {resp.status_code}), salto la fonte.")
            return risultati
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        page_title = str((soup.title.string or "") if soup.title else "")
        if any(kw in page_title.lower() for kw in ("captcha", "robot", "sorry", "access denied")):
            print("    ⚠️  MediaWorld.it: blocco anti-bot, salto la fonte.")
            return risultati

        # ── Strategia 1: article[data-test="mms-product-card"] ──
        articles = soup.select('article[data-test="mms-product-card"]')
        if not articles:
            articles = soup.select("article")  # fallback senza data-test

        if articles:
            seen_links: set[str] = set()
            for art in articles:
                try:
                    # Nome: data-test="product-title"
                    nome_tag = art.select_one('[data-test="product-title"]')
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue

                    # Link: primo a[href] nell'article
                    link_tag = art.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.mediaworld.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    # Prezzo: formato MW è "Consigliato X,–\xa0€Y,00€Z,00"
                    # Estrai tutti i prezzi nel formato "NNN,NN" e prendi il minimo
                    # (il minimo è il prezzo reale, non quello MSRP "Consigliato")
                    price_text = art.get_text(" ", strip=True).replace("\u00a0", " ")
                    raw_prices = re.findall(r"\d{2,4},\d{2}", price_text)
                    prices: list[float] = []
                    for rp in raw_prices:
                        p = parse_price(rp)
                        if math.isfinite(p) and p > 20:  # filtra ",00" spurio
                            prices.append(p)
                    if not prices:
                        continue
                    prezzo = min(prices)

                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="MediaWorld",
                        link=link, fonte="mediaworld.it", spedizione="n.d.",
                    ))
                except (AttributeError, TypeError):
                    continue

            if risultati:
                print(f"    ✅ MediaWorld.it (article): {len(risultati)} risultati validi")
                _random_delay()
                return risultati

        # ── Strategia 2: JSON-LD con @type="ItemList" ──
        for script in soup.find_all("script", {"type": "application/ld+json"}):
            try:
                data = json.loads(str(script.string or ""))
                if not isinstance(data, dict):
                    continue
                if data.get("@type") != "ItemList":
                    continue
                item_list = data.get("itemListElement", [])
                if not item_list:
                    continue

                for el in item_list:
                    try:
                        product = el.get("item", el)
                        if not isinstance(product, dict):
                            continue
                        nome = str(product.get("name", "") or "").strip()
                        if not nome:
                            continue
                        product_url = str(product.get("url", "") or "").strip()
                        if not product_url:
                            continue
                        link = product_url if product_url.startswith("http") else f"https://www.mediaworld.it{product_url}"

                        offers = product.get("offers", {}) or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        # Il prezzo in ItemList di MediaWorld è già numerico (es. 629)
                        price_raw = offers.get("price", "")
                        prezzo = parse_price(str(price_raw))
                        if not math.isfinite(prezzo):
                            continue

                        if not is_relevant(nome, query_tokens, strict_specs=False):
                            continue
                        if not _within_price_range(prezzo, prezzo_min, budget_max):
                            continue
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="MediaWorld",
                            link=link, fonte="mediaworld.it", spedizione="n.d.",
                        ))
                    except (TypeError, ValueError, KeyError):
                        continue

                if risultati:
                    print(f"    ✅ MediaWorld.it (JSON-LD ItemList): {len(risultati)} risultati validi")
                    _random_delay()
                    return risultati

            except Exception:
                continue

        # ── Strategia 2: CSS selettori ──
        cards = (
            soup.select(".product-grid__item") or
            soup.select("[class*='ProductItem']") or
            soup.select("[class*='product-item']") or
            soup.select("[data-product-id]") or
            soup.select(".product")
        )

        if cards:
            print(f"    ✅ Trovate {len(cards)} card su MediaWorld.it")
            seen_links: set[str] = set()
            for card in cards:
                try:
                    nome_tag = (
                        card.select_one("[class*='product-name']") or
                        card.select_one("[class*='ProductName']") or
                        card.select_one("h2") or
                        card.select_one("h3") or
                        card.select_one("a[title]")
                    )
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True) or str(nome_tag.get("title", "") or "")
                    if not nome:
                        continue

                    prezzo_tag = (
                        card.select_one("[class*='price']") or
                        card.select_one("[class*='Price']")
                    )
                    if not prezzo_tag:
                        continue
                    prezzo = parse_price(prezzo_tag.get_text(" ", strip=True))
                    if not math.isfinite(prezzo):
                        continue

                    link_tag = card.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    if not href:
                        continue
                    link = href if href.startswith("http") else f"https://www.mediaworld.it{href}"
                    if link in seen_links:
                        continue
                    seen_links.add(link)

                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="MediaWorld",
                        link=link, fonte="mediaworld.it", spedizione="n.d.",
                    ))
                except (AttributeError, TypeError):
                    continue

        # ── Strategia 3: JSON-LD Product singoli ──
        if not risultati:
            for script in soup.find_all("script", {"type": "application/ld+json"}):
                try:
                    data = json.loads(str(script.string or ""))
                    items: list = data if isinstance(data, list) else [data]
                    for item_data in items:
                        if not isinstance(item_data, dict):
                            continue
                        if item_data.get("@type") != "Product":
                            continue
                        nome = str(item_data.get("name", "") or "").strip()
                        if not nome:
                            continue
                        offers = item_data.get("offers", {}) or {}
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        prezzo = parse_price(str(offers.get("price", "") or ""))
                        if not math.isfinite(prezzo):
                            continue
                        link = str(item_data.get("url", "") or offers.get("url", "") or "").strip()
                        if not link:
                            continue
                        if not is_relevant(nome, query_tokens, strict_specs=False):
                            continue
                        if not _within_price_range(prezzo, prezzo_min, budget_max):
                            continue
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="MediaWorld",
                            link=link, fonte="mediaworld.it", spedizione="n.d.",
                        ))
                except Exception:
                    continue

        if not risultati:
            print("    ⚠️  MediaWorld.it: nessun risultato parsabile (selettori cambiati o blocco).")

    except requests.Timeout:
        print("    ❌ MediaWorld.it: timeout raggiunto anche dopo i retry.")
    except requests.ConnectionError:
        print("    ❌ MediaWorld.it: impossibile connettersi al sito.")
    except requests.HTTPError as exc:
        print(f"    ❌ MediaWorld.it: errore HTTP {exc.response.status_code}.")
    except Exception as exc:
        print(f"    ❌ MediaWorld.it: errore inatteso → {exc}")

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


def detect_category_and_questions(testo_utente: str) -> dict[str, object]:
    """
    Classifica la categoria e propone domande di chiarimento per la mini-chat.
    """
    fallback_questions: dict[str, list[str]] = {
        "smartphone": [
            "Hai preferenze di colore?",
            "Ti serve una versione standard o Pro, e con quanto storage?",
        ],
        "laptop": [
            "Qual e l'uso principale? (studio, ufficio, gaming, editing)",
            "Preferisci un modello leggero/portatile o va bene standard?",
        ],
        "abbigliamento": [
            "Che taglia cerchi?",
            "Hai un colore preferito?",
        ],
        "scarpe": [
            "Che numero ti serve?",
            "Uso principale: sportivo o casual?",
        ],
        "elettrodomestico": [
            "Hai preferenze di marca o variante?",
            "Qual e l'uso principale?",
        ],
        "altro": [
            "Hai preferenze di colore/storage/variante?",
            "Preferisci nuovo o usato?",
        ],
    }

    testo = str(testo_utente or "").strip()
    if not testo:
        return {
            "categoria": "altro",
            "domande": fallback_questions["altro"],
            "preferenze_chiare": False,
            "intent_precompilato": {},
        }

    intent_pre = parse_search_intent(testo)

    def _infer_category(lower_text: str) -> str:
        if any(k in lower_text for k in ("iphone", "smartphone", "telefono", "android", "galaxy", "pixel")):
            return "smartphone"
        if any(k in lower_text for k in ("laptop", "notebook", "pc", "macbook", "thinkpad")):
            return "laptop"
        if any(k in lower_text for k in ("scarpe", "sneaker", "stivali", "sandali", "nike", "adidas")):
            return "scarpe"
        if any(k in lower_text for k in ("maglia", "giacca", "pantaloni", "vestito", "abbigliamento", "felpa")):
            return "abbigliamento"
        if any(k in lower_text for k in ("frigorifero", "lavatrice", "forno", "aspirapolvere")):
            return "elettrodomestico"
        return "altro"

    def _questions_for_missing(categoria: str, lower_text: str) -> tuple[list[str], bool]:
        has_color = any(c in lower_text for c in ("nero", "black", "bianco", "white", "rosa", "pink", "blu", "blue", "rosso", "red", "verde", "green", "lavanda", "silver", "graphite"))
        has_storage = re.search(r"\b(64|128|256|512|1024)\s*gb\b|\b1\s*tb\b", lower_text) is not None
        has_variant = any(v in lower_text for v in ("pro max", "pro", "plus", "standard", "base", "ultra", "mini"))
        has_size = re.search(r"\b(?:xxs|xs|s|m|l|xl|xxl|\d{2}(?:[\.,]\d)?)\b", lower_text) is not None
        has_use = any(v in lower_text for v in ("studio", "ufficio", "lavoro", "gaming", "editing", "sportivo", "casual", "running", "trail"))
        has_portability = any(v in lower_text for v in ("leggero", "leggera", "portatile", "ultraleggero", "peso", "sottile"))
        is_apple_phone = any(v in lower_text for v in ("iphone", "apple"))

        questions: list[str] = []
        if categoria == "smartphone":
            if is_apple_phone and not has_storage:
                questions.append("Quanti GB di storage preferisci?")
            if is_apple_phone and not has_variant:
                questions.append("Preferisci il modello standard o Pro?")
            if not is_apple_phone and not has_color:
                questions.append("Hai preferenze di colore?")
            if not is_apple_phone and not has_storage:
                questions.append("Quanti GB di storage preferisci?")
            preferenze_ok = (has_storage and has_variant) if is_apple_phone else (has_storage or has_color)
            return questions[:2], preferenze_ok

        if categoria == "scarpe":
            if not has_size:
                questions.append("Che numero ti serve?")
            if not has_use:
                questions.append("Uso principale: sportivo o casual?")
            return questions[:2], has_size and has_use

        if categoria == "abbigliamento":
            if not has_size:
                questions.append("Che taglia cerchi?")
            if not has_color:
                questions.append("Hai un colore preferito?")
            return questions[:2], has_size and has_color

        if categoria == "laptop":
            if not has_use:
                questions.append("Qual e l'uso principale? (studio, ufficio, gaming, editing)")
            if not has_portability:
                questions.append("Preferisci un modello leggero/portatile o va bene standard?")
            return questions[:2], has_use and has_portability

        if categoria == "elettrodomestico":
            if not any(v in lower_text for v in ("marca", "bosch", "samsung", "lg", "miele", "whirlpool")):
                questions.append("Hai preferenze di marca o variante?")
            if not has_use:
                questions.append("Qual e l'uso principale?")
            return questions[:2], len(questions) == 0

        if not has_color:
            questions.append("Hai preferenze di colore/storage/variante?")
        if not any(v in lower_text for v in ("nuovo", "usato")):
            questions.append("Preferisci nuovo o usato?")
        return questions[:2], len(questions) == 0

    lower = testo.lower()
    categoria_base = _infer_category(lower)
    domande_base, preferenze_chiare_base = _questions_for_missing(categoria_base, lower)

    client = _get_cerebras_client()
    if client is None:
        categoria = categoria_base
        return {
            "categoria": categoria,
            "domande": [] if preferenze_chiare_base else (domande_base or fallback_questions[categoria])[:2],
            "preferenze_chiare": preferenze_chiare_base,
            "intent_precompilato": intent_pre if preferenze_chiare_base else {},
        }

    try:
        prompt = (
            "Sei un assistente acquisti. Identifica categoria prodotto, marca/modello gia specificati "
            "e preferenze gia espresse (colore, storage, taglia, variante, uso).\n\n"
            "REGOLE ASSOLUTE:\n"
            "- Genera SOLO domande sulle preferenze ANCORA mancanti\n"
            "- NON chiedere mai di nuovo modello o colore se sono gia presenti (es: 'iphone 17 nero')\n"
            "- Fai SOLO domande su preferenze utente: colore, storage, variante, taglia, uso, portabilita\n"
            "- NON chiedere mai specifiche tecniche come megapixel, batteria, processore, sistema operativo\n"
            "- Massimo 2 domande totali, una per turno\n"
            "- Se le preferenze minime sono gia chiare, restituisci preferenze_chiare=true e domande=[]\n\n"
            "Minimi per categoria:\n"
            "- smartphone Apple: storage e modello (Pro/standard) se mancanti\n"
            "- scarpe: numero e uso\n"
            "- abbigliamento: taglia e colore\n"
            "- laptop: uso principale e peso/portabilita\n\n"
            "Restituisci SOLO JSON con chiavi: categoria (string tra smartphone, laptop, "
            "abbigliamento, scarpe, elettrodomestico, altro), domande (array di max 2 stringhe), "
            "preferenze_chiare (bool)."
        )
        completion = client.chat.completions.create(
            model="gpt-oss-120b",
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": testo},
            ],
            temperature=0.1,
        )
        content = completion.choices[0].message.content if completion and completion.choices else ""
        raw = str(content or "").strip()
        json_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        payload = json.loads(json_match.group(0) if json_match else raw)
        if not isinstance(payload, dict):
            raise ValueError("Payload non valido")
        categoria = str(payload.get("categoria", "altro") or "altro").strip().lower()
        if categoria not in fallback_questions:
            categoria = "altro"
        domande_raw = payload.get("domande", [])
        domande = [str(d).strip() for d in domande_raw if str(d).strip()]
        domande_missing, preferenze_missing = _questions_for_missing(categoria, lower)
        if not domande:
            domande = domande_missing or fallback_questions[categoria]
        preferenze_ai = bool(payload.get("preferenze_chiare", False)) or preferenze_missing
        return {
            "categoria": categoria,
            "domande": [] if preferenze_ai else (domande_missing or domande)[:2],
            "preferenze_chiare": preferenze_ai,
            "intent_precompilato": intent_pre if preferenze_ai else {},
        }
    except Exception:
        return {
            "categoria": "altro",
            "domande": [] if preferenze_chiare_base else (domande_base or fallback_questions["altro"])[:2],
            "preferenze_chiare": preferenze_chiare_base,
            "intent_precompilato": intent_pre if preferenze_chiare_base else {},
        }


def parse_search_intent(risposta_utente: str) -> dict[str, object]:
    """
    Estrae intent di ricerca da testo libero usando AI.

    Output standard:
      {
        "query": str,
        "prezzo_min": int,
        "prezzo_max": int,
        "condizione": str,
        "filtri": dict[str, str]
      }
    """
    default: dict[str, object] = {
        "query": str(risposta_utente or "").strip(),
        "prezzo_min": 0,
        "prezzo_max": 2000,
        "condizione": "tutti",
        "filtri": {},
    }

    def _sanitize(payload: dict[str, object]) -> dict[str, object]:
        query = str(payload.get("query", "") or "").strip()
        prezzo_min_raw = payload.get("prezzo_min", 0)
        prezzo_max_raw = payload.get("prezzo_max", 2000)
        condizione_raw = str(payload.get("condizione", "tutti") or "tutti").strip().lower()
        filtri_raw = payload.get("filtri", {})

        try:
            prezzo_min = int(float(str(prezzo_min_raw)))
        except Exception:
            prezzo_min = 0
        try:
            prezzo_max = int(float(str(prezzo_max_raw)))
        except Exception:
            prezzo_max = 2000

        prezzo_min = max(0, prezzo_min)
        prezzo_max = max(prezzo_min, prezzo_max)

        if condizione_raw not in {"tutti", "nuovo", "usato"}:
            condizione_raw = "usato" if "usat" in condizione_raw else "tutti"

        filtri: dict[str, str] = {}
        if isinstance(filtri_raw, dict):
            for k, v in filtri_raw.items():
                key = str(k or "").strip().lower()
                val = str(v or "").strip()
                if key and val:
                    filtri[key] = val

        return {
            "query": query,
            "prezzo_min": prezzo_min,
            "prezzo_max": prezzo_max,
            "condizione": condizione_raw,
            "filtri": filtri,
        }

    testo = str(risposta_utente or "").strip()
    if not testo:
        return default

    client = _get_cerebras_client()
    if client is None:
        guess = dict(default)
        m_range = re.search(r"(\d{2,5})\s*[-/a]\s*(\d{2,5})", testo.lower())
        m_min = re.search(r"(?:min(?:imo)?|da|partire da)\s*(\d{2,5})", testo.lower())
        m_max = re.search(r"(?:max(?:imo)?|massimo|fino a|budget)\s*(\d{2,5})", testo.lower())
        if m_range:
            guess["prezzo_min"] = int(m_range.group(1))
            guess["prezzo_max"] = int(m_range.group(2))
        else:
            if m_min:
                guess["prezzo_min"] = int(m_min.group(1))
            if m_max:
                guess["prezzo_max"] = int(m_max.group(1))

        lower = testo.lower()
        if "usat" in lower:
            guess["condizione"] = "usato"
        elif "nuov" in lower:
            guess["condizione"] = "nuovo"

        filtri_local: dict[str, str] = {}
        if "rosa" in lower:
            filtri_local["colore"] = "rosa"
        if "lavanda" in lower:
            filtri_local["colore"] = "lavanda"
        storage_match = re.search(r"\b(\d{2,4})\s*gb\b", lower)
        if storage_match:
            filtri_local["storage"] = f"{storage_match.group(1)}gb"

        guess["query"] = testo
        guess["filtri"] = filtri_local
        return _sanitize(guess)

    try:
        system_prompt = (
            "Estrai un intent di ricerca shopping da testo utente in italiano. "
            "Rispondi SOLO con JSON valido senza markdown con chiavi: "
            "query (string), prezzo_min (int), prezzo_max (int), condizione (tutti|nuovo|usato), "
            "filtri (object con attributi non cercabili direttamente, es colore/storage/taglia)."
        )
        completion = client.chat.completions.create(
            model="gpt-oss-120b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": testo},
            ],
            temperature=0,
        )
        content = completion.choices[0].message.content if completion and completion.choices else ""
        raw = str(content or "").strip()
        json_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        payload = json.loads(json_match.group(0) if json_match else raw)
        if not isinstance(payload, dict):
            return _sanitize(default)
        return _sanitize(payload)
    except Exception:
        return _sanitize(default)


def filtra_risultati_con_ai(risultati: list[Offerta], filtri: dict[str, str]) -> list[Offerta]:
    """
    Applica ranking AI ai risultati in base a filtri semantici non direttamente cercabili.

    Regole:
      - scarta score < 3
      - ordina per score desc, poi prezzo asc
    """
    if not risultati or not filtri:
        return risultati

    scored: list[tuple[int, Offerta]] = []

    client = _get_cerebras_client()
    if client is not None:
        try:
            titoli = [f"{i+1}. {o.nome}" for i, o in enumerate(risultati)]
            prompt = (
                "Valuta la rilevanza 0-10 dei titoli rispetto ai filtri dati. "
                "Considera sinonimi e varianti (es rosa ~ pink ~ lavanda quando plausibile). "
                "Rispondi SOLO JSON: {\"scores\": [{\"idx\":1,\"score\":7}, ...]}"
            )
            completion = client.chat.completions.create(
                model="gpt-oss-120b",
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": f"Filtri: {json.dumps(filtri, ensure_ascii=False)}\nTitoli:\n" + "\n".join(titoli),
                    },
                ],
                temperature=0,
            )
            content = completion.choices[0].message.content if completion and completion.choices else ""
            raw = str(content or "").strip()
            json_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            payload = json.loads(json_match.group(0) if json_match else raw)
            scores_map: dict[int, int] = {}
            for row in payload.get("scores", []):
                idx = int(row.get("idx", 0)) - 1
                score = int(float(str(row.get("score", 0))))
                if 0 <= idx < len(risultati):
                    scores_map[idx] = max(0, min(10, score))

            for idx, offerta in enumerate(risultati):
                scored.append((scores_map.get(idx, 0), offerta))
        except Exception:
            scored = []

    if not scored:
        # Fallback locale lessicale quando AI non disponibile.
        # Espande aliases per spec matching (es. "16gb" -> {"16gb", "16 gb"})
        expanded_values: set[str] = set()
        for v in filtri.values():
            val = str(v).lower().strip()
            if not val:
                continue
            expanded_values.add(val)
            # Aggiungi varianti con/senza spazio per specs
            num_match = re.match(r"^(\d+)(gb|tb)$", val)
            if num_match:
                expanded_values.add(f"{num_match.group(1)} {num_match.group(2)}")
                expanded_values.add(f"{num_match.group(1)}{num_match.group(2)}")
            # Sinonimi colore
            color_syn: dict[str, set[str]] = {
                "rosa": {"pink", "lavanda"}, "nero": {"black", "graphite"},
                "bianco": {"white", "silver"},
            }
            if val in color_syn:
                expanded_values.update(color_syn[val])

        for offerta in risultati:
            titolo = offerta.nome.lower()
            # Controlla anche specs arricchite se disponibili
            specs_text = " ".join(str(sv).lower() for sv in (offerta.specs or {}).values() if sv)
            search_text = f"{titolo} {specs_text}"
            hit = sum(1 for v in expanded_values if v and v in search_text)
            score = min(10, hit * 4)
            scored.append((score, offerta))

    filtered = [(score, o) for score, o in scored if score >= 3]
    # Se il filtro specs è troppo aggressivo e scarta tutto, ritorna tutti con ranking
    if not filtered and scored:
        scored.sort(key=lambda x: (-x[0], x[1].prezzo))
        return [o for _, o in scored]
    filtered.sort(key=lambda x: (-x[0], x[1].prezzo))

    # Alternative detection: suggerisce una variante diversa se costa >10% in meno.
    keys_variante = {"colore", "storage", "variante"}
    filtri_variante = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in filtri.items()
        if str(k).strip().lower() in keys_variante and str(v).strip()
    }
    if filtri_variante:
        color_synonyms = {
            "rosa": {"rosa", "pink", "lavanda", "rose"},
            "nero": {"nero", "black", "graphite"},
            "bianco": {"bianco", "white", "silver"},
        }

        for score, offerta in filtered:
            offerta.alternativa = ""
            if score < 7:
                continue

            titolo_ref = offerta.nome.lower()
            best_alt: Optional[Offerta] = None
            best_diff = 0.0
            best_label = ""

            for _, candidato in filtered:
                if candidato is offerta:
                    continue
                if candidato.prezzo >= offerta.prezzo * 0.90:
                    continue

                titolo_cand = candidato.nome.lower()
                variante_diversa = False
                variante_label = ""

                for key, value in filtri_variante.items():
                    terms = color_synonyms.get(value, {value}) if key == "colore" else {value}
                    ref_has_value = any(t in titolo_ref for t in terms)
                    cand_has_value = any(t in titolo_cand for t in terms)
                    if ref_has_value and not cand_has_value:
                        variante_diversa = True
                        if key == "colore":
                            variante_label = "Versione con colore diverso"
                        elif key == "storage":
                            variante_label = "Versione con storage diverso"
                        else:
                            variante_label = "Versione alternativa"
                        break

                if not variante_diversa:
                    continue

                diff = offerta.prezzo - candidato.prezzo
                if diff > best_diff:
                    best_diff = diff
                    best_alt = candidato
                    best_label = variante_label

            if best_alt and best_diff > 0:
                delta_txt = f"€{best_diff:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                offerta.alternativa = f"💡 {best_label} costa {delta_txt} in meno"

    return [o for _, o in filtered]


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
    fieldnames = ["posizione", "nome", "prezzo_eur", "spedizione", "negozio", "fonte", "link"]
    rows = [
        {
            "posizione":  i,
            "nome":       o.nome,
            "prezzo_eur": f"{o.prezzo:.2f}",
            "spedizione": o.spedizione,
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
    filtri_ai: Optional[dict[str, str]] = None,
    top_n: int = 10,
    export_csv: bool = False,
    csv_filename: str = "offerte.csv",
    condizione: str = "tutti",
    fonti: Optional[list[str]] = None,
    categoria: str = "altro",
    cerebras_client: Optional[object] = None,
    app_id: str = "",
    cert_id: str = "",
    progress_callback: Optional[Callable[[str, int], None]] = None,
) -> list[Offerta]:
    """
    Cerca offerte tech su trovaprezzi.it e amazon.it.

    Args:
        query:        Testo della ricerca (es. "notebook 14 pollici 16gb RAM").
        prezzo_min:   Prezzo minimo in euro (default: 0).
        budget_max:   Prezzo massimo in euro. None = nessun limite.
        filtri_ai:    Filtri semantici post-scraping (es. colore, storage).
        top_n:        Quante offerte mostrare (default: 10).
        export_csv:   Se True, salva i risultati in un file CSV.
        csv_filename: Nome del file CSV di output (default: "offerte.csv").
        condizione:   Filtro stato prodotto Amazon: "tutti", "nuovo", "usato".
        fonti:        Fonti da usare (amazon, ebay, vinted, trovaprezzi). None = tutte.
        categoria:    Categoria normalizzata (tech, abbigliamento, altro).
        cerebras_client: Client Cerebras opzionale per l'arricchimento specs.
        app_id:       App ID eBay Browse API.
        cert_id:      Cert ID eBay Browse API.

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
    future_to_label: dict = {}
    with ThreadPoolExecutor(max_workers=7) as executor:
        if "trovaprezzi" in fonti_norm:
            future_to_label[executor.submit(scrape_trovaprezzi, query, prezzo_min, budget_max, query_tokens)] = "Trovaprezzi.it"
        if "amazon" in fonti_norm:
            future_to_label[executor.submit(scrape_amazon, query, prezzo_min, budget_max, query_tokens, condizione)] = "Amazon.it"
        if "ebay" in fonti_norm:
            if app_id and cert_id:
                future_to_label[executor.submit(scrape_ebay, query, prezzo_min, budget_max, condizione, query_tokens, app_id, cert_id)] = "eBay.it"
            else:
                print("    ⚠️  eBay non configurato — chiavi mancanti.")
                if progress_callback:
                    progress_callback("eBay.it", -2)
        if "vinted" in fonti_norm:
            future_to_label[executor.submit(scrape_vinted, query, prezzo_min, budget_max, query_tokens, condizione)] = "Vinted.it"
        if "euronics" in fonti_norm:
            future_to_label[executor.submit(scrape_euronics, query, prezzo_min, budget_max, query_tokens)] = "Euronics.it"
        if "unieuro" in fonti_norm:
            future_to_label[executor.submit(scrape_unieuro, query, prezzo_min, budget_max, query_tokens)] = "Unieuro.it"
        if "mediaworld" in fonti_norm:
            future_to_label[executor.submit(scrape_mediaworld, query, prezzo_min, budget_max, query_tokens)] = "MediaWorld.it"

        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                new_results = future.result()
                offerte += new_results
                if progress_callback:
                    progress_callback(label, len(new_results))
            except Exception as exc:
                print(f"    ⚠️  {label}: errore inatteso: {exc}")
                if progress_callback:
                    progress_callback(label, -1)

    print(f"\n  📥 Totale risultati grezzi (post-filtro): {len(offerte)}")

    # Filtro finale range prezzo
    offerte = [
        o for o in offerte
        if o.prezzo >= prezzo_min
        and (budget_max is None or o.prezzo <= budget_max)
    ]
    print(f"  🧹 Dopo filtro range prezzo: {len(offerte)}")

    filtri_ai_effettivi = {k: v for k, v in (filtri_ai or {}).items() if str(k).strip() and str(v).strip()}
    if filtri_ai_effettivi:
        offerte = filtra_risultati_con_ai(offerte, filtri_ai_effettivi)
        print(f"  🎯 Dopo filtro/ranking AI: {len(offerte)}")

    # Deduplicazione
    offerte = _deduplica(offerte)
    print(f"  🔄 Dopo deduplicazione: {len(offerte)} offerte uniche")

    # Ordinamento finale con ranking basato su spec tokens trovati nei titoli.
    spec_tokens = {t for t in query_tokens if _is_spec_token(t)}
    if not filtri_ai_effettivi:
        if spec_tokens:
            def _spec_score(o: Offerta) -> int:
                nl = o.nome.lower()
                return sum(1 for t in spec_tokens if any(v in nl for v in _ALIASES.get(t, {t}) | {t}))
            offerte.sort(key=lambda o: (-_spec_score(o), o.prezzo))
        else:
            offerte.sort(key=lambda o: o.prezzo)

    # Tronca a top_n
    offerte_top = offerte[:top_n]

    if offerte_top:
        fetch_specs_ai(offerte_top, categoria, cerebras_client)

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
        choices=["amazon", "ebay", "vinted", "trovaprezzi", "euronics", "unieuro", "mediaworld"],
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
        filtri_ai    = None,
        top_n        = args.top,
        export_csv   = (args.export == "csv"),
        csv_filename = args.output,
        condizione   = args.condizione,
        fonti        = args.fonti,
        categoria    = "altro",
        cerebras_client = None,
        app_id       = "",
        cert_id      = "",
    )

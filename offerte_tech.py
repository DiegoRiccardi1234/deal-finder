"""
offerte_tech.py — Price Scraper universale per Trova Prezzi
===========================================================
Cerca offerte su più fonti italiane, filtra per
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
    from cerebras_model import get_best_model as _get_best_model, cerebras_chat_with_retry as _cerebras_chat_lib
except Exception:
    _get_best_model = None  # type: ignore[assignment]
    _cerebras_chat_lib = None  # type: ignore[assignment]

_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"


def _cerebras_model(client=None) -> str:
    """Restituisce il miglior modello Cerebras disponibile (con cache)."""
    if _get_best_model is not None:
        try:
            return _get_best_model(client)
        except Exception:
            pass
    return _CEREBRAS_MODEL_FALLBACK


def _cerebras_chat(client, messages: list, temperature: float = 0.1, max_retries: int = 4) -> object:
    """Wrapper con retry automatico (404 modello + 429 rate limit)."""
    if _cerebras_chat_lib is not None:
        return _cerebras_chat_lib(
            client=client,
            messages=messages,
            model=None,
            max_retries=max_retries,
            base_delay=2.0,
            temperature=temperature,
        )
    # Fallback diretto senza retry
    return client.chat.completions.create(
        model=_cerebras_model(client),
        messages=messages,
        temperature=temperature,
    )


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
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) "
        "Gecko/20100101 Firefox/136.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/18.3 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    ]
    def _random_ua() -> str:
        return random.choice(_FALLBACK_UAS)

# ---------------------------------------------------------------------------
# Costanti globali
# ---------------------------------------------------------------------------
TIMEOUT        = 10        # secondi per ogni richiesta HTTP
DELAY_MIN      = 0.5       # secondi — delay minimo tra richieste
DELAY_MAX      = 2.0       # secondi — delay massimo tra richieste
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
    # Ottica / occhiali
    "occhiali": {"sunglasses", "glasses", "eyewear", "eyeglasses", "spectacles"},
    "sunglasses": {"occhiali", "occhiali da sole", "glasses"},
    # Scarpe / abbigliamento
    "scarpe": {"shoes", "sneakers", "calzature", "footwear"},
    "scarpa": {"shoe", "sneaker", "calzatura"},
    "abbigliamento": {"clothing", "clothes", "vestiti", "indumenti"},
    "felpa": {"hoodie", "sweatshirt", "felpe"},
    "giacca": {"jacket", "coat", "giubbotto"},
    "pantaloni": {"pants", "trousers", "jeans"},
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
    immagine: str = field(default="")

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
    """Restituisce la categoria normalizzata (lowercase, stripped) senza mappature fisse."""
    return str(categoria or "").strip().lower()


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


def _extract_gb_values(text: str) -> list[int]:
    """Estrae quantità in GB dal testo (es. RAM o storage)."""
    values: list[int] = []
    for m in re.finditer(r"(\d{2,4})\s*gb\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)))
        except Exception:
            continue
    for m in re.finditer(r"(\d{1,2})\s*tb\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)) * 1024)
        except Exception:
            continue
    return values


def _extract_ram_gb_values(text: str) -> list[int]:
    """Estrae valori RAM (GB) quando esplicitamente associati a RAM/memoria."""
    values: list[int] = []
    patterns = [
        r"(\d{1,3})\s*gb\s*(?:di\s*)?ram\b",
        r"memoria\s*(\d{1,3})\s*gb\b",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                values.append(int(m.group(1)))
            except Exception:
                continue
    return values


def _extract_storage_gb_values(text: str) -> list[int]:
    """Estrae valori storage (GB/TB) associati a SSD/HDD/NVMe/disco."""
    values: list[int] = []
    for m in re.finditer(r"(\d{2,4})\s*gb\s*(?:ssd|hdd|nvme|emmc|disco|storage)\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)))
        except Exception:
            continue
    for m in re.finditer(r"(?:ssd|hdd|nvme|emmc|disco|storage)\s*(\d{2,4})\s*gb\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)))
        except Exception:
            continue
    for m in re.finditer(r"(\d{1,2})\s*tb\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)) * 1024)
        except Exception:
            continue
    return values


def _extract_inches_values(text: str) -> list[float]:
    """Estrae dimensioni display in pollici dal testo prodotto."""
    values: list[float] = []
    pattern = r"(\d{1,2}(?:[\.,]\d)?)\s*(?:\"|''|pollici\b)"
    for m in re.finditer(pattern, text, flags=re.IGNORECASE):
        raw = str(m.group(1)).replace(",", ".")
        try:
            values.append(float(raw))
        except Exception:
            continue
    return values


def _parse_target_range(value: str) -> Optional[tuple[float, float]]:
    """Converte '14-15' / '14/15' / '14,15' in range numerico."""
    txt = str(value or "").strip().lower()
    if not txt:
        return None
    nums = re.findall(r"\d{1,2}(?:[\.,]\d)?", txt)
    if not nums:
        return None
    parsed: list[float] = []
    for n in nums:
        try:
            parsed.append(float(n.replace(",", ".")))
        except Exception:
            continue
    if not parsed:
        return None
    if len(parsed) == 1:
        v = parsed[0]
        return (v - 0.2, v + 0.7)
    return (min(parsed), max(parsed) + 0.7)


def _passes_hard_spec_filters(offerta: Offerta, filtri: dict[str, str]) -> bool:
    """Applica vincoli tecnici hard per ridurre falsi positivi su notebook/smartphone."""
    return len(_hard_spec_mismatch_reasons(offerta, filtri)) == 0


def _hard_spec_mismatch_reasons(offerta: Offerta, filtri: dict[str, str]) -> list[str]:
    """Restituisce i motivi di mismatch hard (RAM/storage/display), lista vuota se passa."""
    if not filtri:
        return []

    reasons: list[str] = []

    search_text = f"{offerta.nome} " + " ".join(
        str(v) for v in (offerta.specs or {}).values() if v not in (None, "", [], {})
    )
    search_lower = search_text.lower()

    ram_target = filtri.get("ram_gb") or filtri.get("ram")
    if ram_target:
        m = re.search(r"(\d{1,3})", str(ram_target))
        if m:
            target = int(m.group(1))
            gb_vals = _extract_ram_gb_values(search_lower)
            if not gb_vals or max(gb_vals) < target:
                found = f"trovato={max(gb_vals)}GB" if gb_vals else "trovato=assente"
                reasons.append(f"ram<{target}GB ({found})")

    storage_target = filtri.get("storage_gb") or filtri.get("storage")
    if storage_target:
        m = re.search(r"(\d{2,4})", str(storage_target))
        if m:
            target = int(m.group(1))
            gb_vals = _extract_storage_gb_values(search_lower)
            if not gb_vals:
                gb_vals = _extract_gb_values(search_lower)
            if not gb_vals or max(gb_vals) < target:
                found = f"trovato={max(gb_vals)}GB" if gb_vals else "trovato=assente"
                reasons.append(f"storage<{target}GB ({found})")

    size_target = filtri.get("size_inches") or filtri.get("display")
    if size_target:
        parsed_range = _parse_target_range(str(size_target))
        if parsed_range is not None:
            low, high = parsed_range
            inches_vals = _extract_inches_values(search_lower)
            if not inches_vals or not any(low <= v <= high for v in inches_vals):
                found = ",".join(f"{v:.1f}\"" for v in inches_vals) if inches_vals else "assente"
                reasons.append(f"display fuori range {low:.1f}-{high:.1f}\" (trovato={found})")

    return reasons


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
    """Arricchisce le offerte con specs tramite una singola chiamata AI batch."""
    if not offerte:
        return offerte

    categoria_norm = _normalize_category(categoria)

    if categoria_norm == "abbigliamento":
        for offerta in offerte:
            offerta.specs = _extract_clothing_specs(offerta.nome)
        return offerte

    if cerebras_client is None:
        for offerta in offerte:
            offerta.specs = {}
        return offerte

    # Singola chiamata batch universale: invia tutti i nomi prodotto in un'unica richiesta AI
    nomi = [o.nome for o in offerte]
    elenco = "\n".join(f"{i+1}. {n}" for i, n in enumerate(nomi))
    batch_prompt = (
        "Sei un database prodotti. Per ciascun prodotto nell'elenco numerato "
        "restituisci SOLO un oggetto JSON valido con indici 1,2,3…\n"
        "Ogni valore è un oggetto con le specifiche più rilevanti per quella categoria di prodotto.\n"
        "Esempi: tech → processore, ram, storage, display, os; "
        "abbigliamento → brand, taglia, colore, materiale; "
        "elettrodomestici → marca, potenza, dimensioni, classe_energetica; "
        "scarpe → brand, taglia, materiale, uso.\n"
        "Campi sconosciuti: null. Solo JSON, nessun testo extra.\n"
        f"Elenco prodotti:\n{elenco}"
    )
    try:
        completion = _cerebras_chat(
            cerebras_client,
            messages=[{"role": "user", "content": batch_prompt}],
            temperature=0,
        )
        content = str(completion.choices[0].message.content or "") if completion and completion.choices else ""
        # Il JSON ritornato è tipo {"1": {...}, "2": {...}}
        outer = _extract_json_object(content)
        for i, offerta in enumerate(offerte):
            key = str(i + 1)
            specs = outer.get(key, {})
            offerta.specs = specs if isinstance(specs, dict) else {}
    except Exception:
        for offerta in offerte:
            offerta.specs = {}

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


def parse_comparison_query(query: str) -> list[str]:
    """
    Rileva query di confronto e restituisce la lista di sotto-query individuali.

    Supporta:
    - "iphone 15 vs iphone 16 vs iphone 17"  (vs/versus/contro)
    - "confronta X e Y e Z" / "compare X e Y"
    - "iphone 16 o 17 fammi un confronto"    (confronto + X o Y nel testo)
    - "fammi un confronto tra iphone 16 e 17"

    Restituisce lista vuota se non è una query di confronto.

    Es: "iphone 15 vs iphone 16" → ["iphone 15", "iphone 16"]
        "iphone 16 o 17 fammi un confronto" → ["iphone 16", "iphone 17"]
    """
    stripped = query.strip()
    lower = stripped.lower()

    _STOPWORDS_IT = {
        'un', 'una', 'il', 'la', 'lo', 'i', 'le', 'gli', 'e', 'o',
        'di', 'da', 'in', 'a', 'su', 'per', 'con', 'tra', 'fra',
        'del', 'della', 'dei', 'delle', 'agli', 'allo', 'alle',
        'come', 'se', 'ma', 'che', 'chi', 'cui', 'ne', 'ci', 'non',
        'mi', 'ti', 'si', 'vi', 'li', 'me', 'te', 'loro',
    }

    # ── Pattern 1: esplicito "vs", "versus", "contro" ─────────────────────
    parts = re.split(r'\s+vs\.?\s+|\s+versus\s+|\s+contro\s+', lower)

    # ── Pattern 2: "confronta/compare X e Y" all'inizio ──────────────────
    if len(parts) == 1:
        m = re.match(r'^(?:confronta|compare|compara)\s+(.+)', lower)
        if m:
            body = m.group(1)
            # rimuovi "tra" iniziale: "confronta tra X e Y" → "X e Y"
            body = re.sub(r'^tra\s+|^fra\s+', '', body)
            parts = re.split(r'\s+e\s+|\s+ed\s+|\s*,\s*', body)

    # ── Pattern 3: "confronto"/"confrontare" in testo + "PROD V1 o V2" ───
    # Gestisce: "iphone 16 o 17 fammi un confronto"
    #            "fammi un confronto tra iphone 16 e 17"
    if len(parts) == 1 and re.search(r'\bconfronto\b|\bconfrontare\b|\bconfrontami\b', lower):
        # Cerca "NOME VERSIONE1 o VERSIONE2" oppure "NOME VERSIONE1 e VERSIONE2"
        # dove almeno una versione contiene cifre (numero di modello)
        m = re.search(r'\b(\w+)\s+(\w+)\s+(?:o|e)\s+(\w+)\b', lower)
        if m:
            base, v1, v2 = m.group(1), m.group(2), m.group(3)
            # Salta se la base è una stopword
            if base not in _STOPWORDS_IT and v1 not in _STOPWORDS_IT and v2 not in _STOPWORDS_IT:
                # Almeno una versione deve contenere cifre (modello) o essere un prodotto reale
                if re.search(r'\d', v1 + v2):
                    parts = [f"{base} {v1}", f"{base} {v2}"]

        # Fallback: "confronto tra X e Y" con "tra/fra" nel testo
        if len(parts) == 1:
            m2 = re.search(r'\btra\s+(.+?)\s+(?:e|ed)\s+(.+?)(?:\s*[,?!]|$)', lower)
            if m2:
                p1 = m2.group(1).strip()
                p2 = m2.group(2).strip().split()[0]  # prendi solo la prima parola se è un numero
                if p1 and p2 and p1 not in _STOPWORDS_IT:
                    # Controlla se p2 potrebbe essere continuazione di p1 (es. "iphone 16 tra ... e 17")
                    parts = [p1, f"{p1.split()[0]} {p2}" if re.search(r'^\d+', p2) else p2]

    # ── Pattern 4: confronto implicito senza keyword (es. "iphone 16 o 17") ──
    if len(parts) == 1:
        m3 = re.search(
            r'\b(iphone|galaxy|pixel|xiaomi|redmi|poco)\s+(\d{1,2}[a-z]?)\s+(?:o|oppure)\s+(\d{1,2}[a-z]?)\b',
            lower,
        )
        if m3:
            base, v1, v2 = m3.group(1), m3.group(2), m3.group(3)
            parts = [f"{base} {v1}", f"{base} {v2}"]

    # Pulizia: tronca al primo segno di punteggiatura e rimuovi frasi discorsive
    def _clean_cmp(p: str) -> str:
        p = re.split(r'[,?!;]', p)[0].strip()
        p = re.sub(
            r'\s+(?:quale|qual|come|cosa|dove|quando|fammi|dimmi|voglio|vorrei|'
            r'consiglio|conviene|scegliere|prendere|meglio|migliore|secondo|dei|due).*$',
            '', p, flags=re.IGNORECASE,
        )
        words = p.split()
        return ' '.join(words[:6]) if len(words) > 6 else p.strip()

    parts = [_clean_cmp(p.strip()) for p in parts]
    parts = [p for p in parts if p and len(p) >= 2]
    return parts if len(parts) >= 2 else []


def is_relevant(nome: str, query_tokens: list[str], strict_specs: bool = True) -> bool:
    """
    Filtro di rilevanza: i token della query devono essere presenti nel nome
    del prodotto (o in uno dei loro alias normalizzati).

    Con strict_specs=False, i token di specifica tecnica (es. '16gb', 'ram')
    vengono saltati — le specs verranno verificate tramite AI enrichment.

    Per query corte (<=2 token), basta che almeno 1 token sia presente (OR logic)
    per supportare query generiche tipo 'scarpe', 'libro', ecc.
    """
    nome_lower = nome.lower()
    brand_tokens = [token for token in query_tokens if token in _TECH_BRANDS]
    # Per query corte senza brand tech specifico, applica logica OR:
    # basta un token per considerare rilevante (supporta query generiche come "scarpe", "libro")
    if len(query_tokens) <= 2 and not brand_tokens:
        for token in query_tokens:
            if not strict_specs and _is_spec_token(token):
                continue
            varianti = _ALIASES.get(token, {token})
            varianti.add(token)
            if any(v in nome_lower for v in varianti):
                return True
        return False
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
            if not cards_p:
                cards_p = soup_p.select("[class*='product'][href]")
            if not cards_p:
                # Fallback JSON-LD
                for _script in soup_p.find_all("script", {"type": "application/ld+json"}):
                    try:
                        _ld = json.loads(str(_script.string or ""))
                        _ld_items = _ld if isinstance(_ld, list) else ([_ld] if isinstance(_ld, dict) else [])
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
                                    _img_url = str(_ld_item["image"][0]) if _ld_item["image"] else ""
                            except Exception:
                                _img_url = ""
                            risultati.append(Offerta(nome=_nome, prezzo=_prezzo, negozio="Trovaprezzi", link=_url, fonte="trovaprezzi.it", spedizione="n.d.", immagine=_img_url))
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
                    try:
                        _img_tag = card.select_one("img")
                        _img_url = str(_img_tag.get("src", "") or _img_tag.get("data-src", "") or "") if _img_tag else ""
                    except Exception:
                        _img_url = ""
                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="Trovaprezzi",
                        link=link, fonte="trovaprezzi.it", spedizione="n.d.", immagine=_img_url,
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

                    try:
                        _img_obj = item.get("image") or {}
                        _img_url = str(_img_obj.get("imageUrl", "") or "") if isinstance(_img_obj, dict) else ""
                        if not _img_url:
                            _thumbs = item.get("thumbnailImages") or []
                            if _thumbs and isinstance(_thumbs, list) and isinstance(_thumbs[0], dict):
                                _img_url = str(_thumbs[0].get("imageUrl", "") or "")
                    except Exception:
                        _img_url = ""

                    risultati.append(
                        Offerta(
                            nome=nome,
                            prezzo=prezzo,
                            negozio=str(seller.get("username") or "eBay"),
                            link=link,
                            fonte="ebay.it",
                            spedizione="n.d.",
                            immagine=_img_url,
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

                try:
                    _img_tag = item.select_one("img")
                    _img_url = str(_img_tag.get("src", "") or "") if _img_tag else ""
                except Exception:
                    _img_url = ""

                risultati.append(
                    Offerta(nome=nome, prezzo=prezzo, negozio="Vinted", link=link, fonte="vinted.it", spedizione=spedizione, immagine=_img_url)
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

                    try:
                        _img_tag = a_tag.select_one("img")
                        _img_url = str(_img_tag.get("src", "") or "") if _img_tag else ""
                    except Exception:
                        _img_url = ""

                    risultati.append(
                        Offerta(nome=nome, prezzo=prezzo, negozio="Vinted", link=link, fonte="vinted.it", spedizione=spedizione, immagine=_img_url)
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
        Container:   div.new-product-tile.flex-fill  (grid view — evita i duplicati della list-view)
        Nome:        span.tile-name
        Prezzo:      span.value  (visibile nella pagina, es. "€ 879,00")
        Link:        a[href] (relativo → prepend euronics.it)

    FIX STORICO: Accept-Encoding: identity è obbligatorio — senza, il server restituisce
    una pagina compressa/minimizzata di 38KB (bot-detection) invece dei 686KB reali.
    """
    url = f"https://www.euronics.it/search?q={quote_plus(query)}"
    print(f"\n🔍 Cerco su Euronics.it: \"{query}\"")
    risultati: list[Offerta] = []

    try:
        headers = get_headers()
        headers["Referer"] = "https://www.euronics.it/"
        headers["Accept-Encoding"] = "identity"  # FIX: senza questo, risposta bot-detection 38KB

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

        # ── Strategia 1: CSS selettori div.new-product-tile.flex-fill ──
        # Seleziona solo le card griglia (evita duplicati della list-view: .new-product-tile-list)
        cards = soup.select("div.new-product-tile.flex-fill")
        if not cards:
            # Fallback: qualsiasi tile con classe new-product-tile (esclude esplicitamente list)
            cards = [c for c in soup.select("[class*='new-product-tile']")
                     if "new-product-tile-list" not in " ".join(c.get("class") or [])]

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

                    # Prezzo: span.value (il prezzo visibile sulla pagina, es. "€ 879,00")
                    prezzo_tag = (
                        card.select_one("span.value") or
                        card.select_one("[class*='price'] span.value") or
                        card.select_one("[class*='price-formatted']") or
                        card.select_one("[class*='price']")
                    )
                    if not prezzo_tag:
                        continue
                    prezzo = parse_price(prezzo_tag.get_text(" ", strip=True))
                    if not math.isfinite(prezzo):
                        continue

                    # Link: primo a[href] nel card (href relativo → prepend base url)
                    link_tag = card.select_one("a.link-pdp[href]") or card.select_one("a[href]")
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

                    try:
                        _img_tag = card.select_one("img")
                        _img_url = str(_img_tag.get("src", "") or _img_tag.get("data-src", "") or "") if _img_tag else ""
                    except Exception:
                        _img_url = ""

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="Euronics",
                        link=link, fonte="euronics.it", spedizione="n.d.", immagine=_img_url,
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
                        try:
                            _img_raw = item_data.get("image", "")
                            _img_url = str(_img_raw[0] if isinstance(_img_raw, list) and _img_raw else _img_raw or "")
                        except Exception:
                            _img_url = ""
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="Euronics",
                            link=link, fonte="euronics.it", spedizione="n.d.", immagine=_img_url,
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
# SCRAPER — unieuro.it  (Algolia API)
# ===========================================================================
# Unieuro usa Algolia come motore di ricerca prodotti (chiave pubblica esposta
# nel bundle JS del sito). Non serve Playwright né OAuth token.
_UNIEURO_ALGOLIA_URL = (
    "https://mnbcenyfii-dsn.algolia.net/1/indexes/*/queries"
    "?x-algolia-api-key=977ed8d06b718d4929ca789c78c4107a"
    "&x-algolia-application-id=MNBCENYFII"
)


def scrape_unieuro(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per Unieuro.it tramite Algolia (API pubblica embedded nel frontend)."""
    print(f"\n🔍 Cerco su Unieuro.it: \"{query}\"")
    risultati: list[Offerta] = []
    try:
        payload = json.dumps({
            "requests": [{
                "indexName": "sgmproducts_prod",
                "query": query,
                "hitsPerPage": 48,
                "page": 0,
                "facetFilters": [],
                "numericFilters": [],
            }]
        })
        headers = {
            "Content-Type": "text/plain",
            "Origin": "https://www.unieuro.it",
            "Referer": "https://www.unieuro.it/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        }
        resp = requests.post(_UNIEURO_ALGOLIA_URL, data=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("results", [{}])[0].get("hits", [])
        if not hits:
            print("    ⚠️  Unieuro.it: nessun prodotto trovato via Algolia.")
            return risultati
        print(f"    ✅ Unieuro.it (Algolia): {len(hits)} risultati")
        for hit in hits:
            try:
                nome = str(hit.get("title_it") or hit.get("name") or "").strip()
                if not nome:
                    continue
                prezzo_raw = hit.get("discountedPrice") or hit.get("facetPrice") or hit.get("originalPrice")
                if prezzo_raw is None:
                    continue
                prezzo = parse_price(str(prezzo_raw))
                if not math.isfinite(prezzo):
                    continue
                url_path = str(hit.get("productUrl_it") or hit.get("url") or "").strip()
                if not url_path:
                    continue
                link = url_path if url_path.startswith("http") else f"https://www.unieuro.it{url_path}"
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                spedizione = "Spedizione gratuita" if hit.get("hasFreeDelivery") else "n.d."
                img_path = str(hit.get("imageUrl") or "")
                img_url = f"https://www.unieuro.it{img_path}" if img_path and not img_path.startswith("http") else img_path
                risultati.append(Offerta(
                    nome=nome, prezzo=prezzo, negozio="Unieuro",
                    link=link, fonte="unieuro.it", spedizione=spedizione, immagine=img_url,
                ))
            except (AttributeError, TypeError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Unieuro.it: errore HTTP {status}.")
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
    condizione: str = "tutti",
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
    _KW_USATO_MW = {"ricondizionato", "usato", "second life", "refurbished", "open box", "seconda vita"}

    def _cond_filter(r: list[Offerta]) -> list[Offerta]:
        if condizione == "tutti":
            return r
        if condizione == "usato":
            return [o for o in r if any(k in o.nome.lower() for k in _KW_USATO_MW)]
        return [o for o in r if not any(k in o.nome.lower() for k in _KW_USATO_MW)]

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

                    # Prezzo: prova prima il selettore specifico, poi fallback regex
                    # MediaWorld mostra sia il prezzo pieno sia le rate mensili (/mese).
                    # Occorre ignorare le rate e prendere il prezzo intero.
                    _RATE_KW = ("/mese", "mese", "/rata", "rata", "/mo", "mensil")

                    def _strip_rate_context(raw: str, context: str) -> bool:
                        """Restituisce True se 'raw' appare vicino a keyword da rata."""
                        idx = context.find(raw)
                        if idx == -1:
                            return False
                        window = context[idx: idx + len(raw) + 20].lower()
                        return any(k in window for k in _RATE_KW)

                    price_tag = art.select_one('[data-test="product-price"]') or art.select_one('[data-test*="price"]')
                    price_text_full = art.get_text(" ", strip=True).replace("\u00a0", " ")
                    if price_tag:
                        pt_text = price_tag.get_text(" ", strip=True).replace("\u00a0", " ")
                        # Se il testo del tag contiene indicatori di rata, ignora il tag
                        # e leggi il prezzo dal testo completo dell'article
                        if any(k in pt_text.lower() for k in _RATE_KW):
                            price_tag = None  # forza fallback
                        else:
                            prezzo = parse_price(pt_text)
                    if not price_tag:
                        # fallback regex sul testo grezzo, escludendo le rate
                        raw_prices = re.findall(r"\d{1,4},\d{2}", price_text_full)
                        prices: list[float] = []
                        for rp in raw_prices:
                            if _strip_rate_context(rp, price_text_full):
                                continue
                            p = parse_price(rp)
                            if math.isfinite(p) and p > 20:
                                prices.append(p)
                        if not prices:
                            continue
                        # Prende il massimo: le rate sono sempre il valore minore
                        prezzo = max(prices)
                    if not math.isfinite(prezzo):
                        continue

                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue

                    try:
                        _img_tag = art.select_one("img")
                        _img_url = str(_img_tag.get("src", "") or _img_tag.get("data-src", "") or "") if _img_tag else ""
                    except Exception:
                        _img_url = ""

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="MediaWorld",
                        link=link, fonte="mediaworld.it", spedizione="n.d.", immagine=_img_url,
                    ))
                except (AttributeError, TypeError):
                    continue

            if risultati:
                print(f"    ✅ MediaWorld.it (article): {len(risultati)} risultati validi")
                _random_delay()
                return _cond_filter(risultati)

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
                        try:
                            _img_raw = product.get("image", "")
                            _img_url = str(_img_raw[0] if isinstance(_img_raw, list) and _img_raw else _img_raw or "")
                        except Exception:
                            _img_url = ""
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="MediaWorld",
                            link=link, fonte="mediaworld.it", spedizione="n.d.", immagine=_img_url,
                        ))
                    except (TypeError, ValueError, KeyError):
                        continue

                if risultati:
                    print(f"    ✅ MediaWorld.it (JSON-LD ItemList): {len(risultati)} risultati validi")
                    _random_delay()
                    return _cond_filter(risultati)

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

                    try:
                        _img_tag = card.select_one("img")
                        _img_url = str(_img_tag.get("src", "") or _img_tag.get("data-src", "") or "") if _img_tag else ""
                    except Exception:
                        _img_url = ""

                    risultati.append(Offerta(
                        nome=nome, prezzo=prezzo, negozio="MediaWorld",
                        link=link, fonte="mediaworld.it", spedizione="n.d.", immagine=_img_url,
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
                        try:
                            _img_raw = item_data.get("image", "")
                            _img_url = str(_img_raw[0] if isinstance(_img_raw, list) and _img_raw else _img_raw or "")
                        except Exception:
                            _img_url = ""
                        risultati.append(Offerta(
                            nome=nome, prezzo=prezzo, negozio="MediaWorld",
                            link=link, fonte="mediaworld.it", spedizione="n.d.", immagine=_img_url,
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
    return _cond_filter(risultati)


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
        "televisore": [
            "Che diagonale cerchi (es. 55, 65 pollici)?",
            "Preferisci OLED, QLED o LED?",
        ],
        "libri": [
            "Preferisci versione cartacea o ebook?",
            "Hai preferenze di edizione o lingua?",
        ],
        "sport": [
            "Per quale sport o attività?",
            "Hai una taglia o misura?",
        ],
        "casa": [
            "Hai preferenze di colore o stile?",
            "Che dimensioni cerchi?",
        ],
        "beauty": [
            "Hai preferenze di marca?",
            "Per che tipo di pelle/uso è?",
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
        if any(k in lower_text for k in ("tv", "televisore", "smart tv", "oled", "qled")):
            return "televisore"
        if any(k in lower_text for k in ("libro", "romanzo", "fumetto")):
            return "libri"
        if any(k in lower_text for k in ("bici", "tapis roulant", "manubri", "pallone")):
            return "sport"
        if any(k in lower_text for k in ("divano", "lampada", "tenda", "scrivania")):
            return "casa"
        if any(k in lower_text for k in ("crema", "profumo", "shampoo")):
            return "beauty"
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
            "Restituisci SOLO JSON con chiavi: categoria (string tra smartphone, laptop, tablet, televisore, "
            "elettrodomestico, abbigliamento, scarpe, sport, libri, beauty, casa, altro), "
            "domande (array di max 2 stringhe), preferenze_chiare (bool)."
        )
        completion = _cerebras_chat(
            client,
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
        completion = _cerebras_chat(
            client,
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

    # Filtro hard su vincoli tecnici (ram/storage/display) con log motivazionale.
    # MENO AGGRESSIVO: skip hard filter se filtri_ai è vuoto o contiene solo query generiche
    has_hard_specs = any(
        k in str(filtri).lower() for k in ["ram", "storage", "display", "size", "gb", "ssd"]
    )

    hard_filtered: list[Offerta] = []
    hard_dropped: list[tuple[Offerta, list[str]]] = []

    if has_hard_specs:  # Applica hard filter SOLO se ci sono specs tecniche
        for offerta in risultati:
            reasons = _hard_spec_mismatch_reasons(offerta, filtri)
            if reasons:
                hard_dropped.append((offerta, reasons))
            else:
                hard_filtered.append(offerta)
    else:
        # Se no hard specs, accetta tutti i risultati
        hard_filtered = risultati

    if hard_dropped:
        print(f"  🧾 Log filtro AI: scartate {len(hard_dropped)} offerte per hard constraints")
        for offerta, reasons in hard_dropped[:20]:
            short_name = offerta.nome[:90]
            print(f"    - SCARTO hard | {short_name} | motivo: {', '.join(reasons)}")
        extra = len(hard_dropped) - 20
        if extra > 0:
            print(f"    ... altri {extra} scarti hard non mostrati")

    if hard_filtered:
        risultati = hard_filtered

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
            completion = _cerebras_chat(
                client,
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
    dropped_by_score = [(score, o) for score, o in scored if score < 3]
    if dropped_by_score:
        print(f"  🧾 Log filtro AI: scartate {len(dropped_by_score)} offerte per score < 3")
        for score, offerta in dropped_by_score[:20]:
            short_name = offerta.nome[:90]
            print(f"    - SCARTO score={score} | {short_name}")
        extra = len(dropped_by_score) - 20
        if extra > 0:
            print(f"    ... altri {extra} scarti score non mostrati")
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
    Encoding: UTF-8-SIG per compatibilità Excel italiano.
    """
    fieldnames = ["posizione", "nome", "prezzo_eur", "spedizione", "negozio", "fonte", "specs", "link"]
    rows = [
        {
            "posizione":  i,
            "nome":       o.nome,
            "prezzo_eur": o.prezzo,
            "spedizione": o.spedizione,
            "negozio":    o.negozio,
            "fonte":      o.fonte,
            "specs":      ", ".join(f"{k}={v}" for k, v in (o.specs or {}).items()),
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
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_NONNUMERIC)
            writer.writeheader()
            writer.writerows(rows)

    abs_path = os.path.abspath(filename)
    print(f"\n  ✅ Risultati esportati in: {abs_path}")


# ===========================================================================
# SCRAPER — subito.it
# ===========================================================================

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


# ===========================================================================
# SCRAPER — aliexpress.com
# ===========================================================================

def scrape_aliexpress(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per AliExpress — pagina anti-bot cifrata (1.8KB, nessun dato)."""
    print(f"\n🔍 Cerco su AliExpress.com: \"{query}\"")
    print("    ⚠️  AliExpress.com: risposta anti-bot cifrata (challenge page). Fonte non disponibile senza browser headless.")
    return []
    # Implementazione HTML/JSON conservata per riferimento futuro:
    url = f"https://it.aliexpress.com/wholesale?SearchText={quote_plus(query)}&SortType=price_asc"

    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://it.aliexpress.com/"
        headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.5"

        resp = fetch_with_retry(url, headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # AliExpress usa componenti React; su alcune pagine inietta JSON nel DOM
        # Prova prima il parsing JSON embedded, poi fallback HTML
        json_data = None
        for script in soup.find_all("script"):
            script_text = script.string or ""
            if "window._dida_config_" in script_text or '"items"' in script_text:
                m = re.search(r'"items"\s*:\s*(\[.*?\])\s*[,}]', script_text, re.DOTALL)
                if m:
                    try:
                        json_data = json.loads(m.group(1))
                        break
                    except Exception:
                        pass

        if json_data:
            for item in json_data[:40]:
                try:
                    nome = str(item.get("title", "") or item.get("subject", "") or "")
                    if not nome:
                        continue
                    prezzo_raw = str(item.get("price", {}).get("minPrice", {}).get("value", "") or
                                     item.get("salePrice", {}).get("minPrice", {}).get("value", "") or "")
                    if not prezzo_raw:
                        continue
                    prezzo = parse_price(prezzo_raw)
                    if not math.isfinite(prezzo):
                        continue
                    item_id = str(item.get("itemId", "") or item.get("productId", "") or "")
                    link = f"https://it.aliexpress.com/item/{item_id}.html" if item_id else ""
                    if not link:
                        continue
                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue
                    img_url = str(item.get("imageUrl", "") or "")
                    risultati.append(
                        Offerta(nome=nome, prezzo=prezzo, negozio="AliExpress", link=link,
                                fonte="aliexpress.com", spedizione="n.d.", immagine=img_url)
                    )
                except Exception:
                    continue
        else:
            # Fallback HTML: cerca card prodotto nel DOM renderizzato
            cards = soup.select('[class*="product-snippet"]') or soup.select('[class*="manhattan--"]')
            if not cards:
                print("    ⚠️  AliExpress.it: pagina JS-rendered, nessun risultato via HTML statico.")
                return risultati

            print(f"    ✅ Trovate {len(cards)} card grezze su AliExpress.com")
            for card in cards:
                try:
                    nome_tag = card.select_one('[class*="title"]') or card.select_one('a')
                    if not nome_tag:
                        continue
                    nome = nome_tag.get_text(strip=True)
                    if not nome:
                        continue
                    prezzo_tag = card.select_one('[class*="price"]')
                    if not prezzo_tag:
                        continue
                    prezzo = parse_price(prezzo_tag.get_text(strip=True))
                    if not math.isfinite(prezzo):
                        continue
                    link_tag = card.select_one("a[href]")
                    if not link_tag:
                        continue
                    href = str(link_tag.get("href", "") or "")
                    link = href if href.startswith("http") else "https://it.aliexpress.com" + href
                    if not is_relevant(nome, query_tokens, strict_specs=False):
                        continue
                    if not _within_price_range(prezzo, prezzo_min, budget_max):
                        continue
                    risultati.append(
                        Offerta(nome=nome, prezzo=prezzo, negozio="AliExpress", link=link,
                                fonte="aliexpress.com", spedizione="n.d.")
                    )
                except (AttributeError, TypeError):
                    continue

    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  AliExpress.com: accesso bloccato (HTTP {status}), salto la fonte.")
    except requests.Timeout:
        print("    ❌ AliExpress.com: timeout.")
    except requests.ConnectionError:
        print("    ❌ AliExpress.com: impossibile connettersi.")
    except Exception as exc:
        print(f"    ❌ AliExpress.com: errore inatteso → {exc}")

    _random_delay()
    return risultati


# ===========================================================================
# SCRAPER — temu.com
# ===========================================================================

def scrape_temu(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per Temu — SPA vuota (2.9KB, nessun dato nel DOM)."""
    print(f"\n🔍 Cerco su Temu.com: \"{query}\"")
    print("    ⚠️  Temu.com: SPA completamente client-side (pagina HTML vuota 2.9KB). Fonte non disponibile senza browser headless.")
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
                for pattern in (r'"goods_list"\s*:\s*(\[.*?\])\s*[,}]',
                                 r'"goodsList"\s*:\s*(\[.*?\])\s*[,}]'):
                    m = re.search(pattern, script_text, re.DOTALL)
                    if m:
                        try:
                            items = json.loads(m.group(1))
                            for item in items[:40]:
                                nome = str(item.get("goods_name", "") or item.get("title", "") or "")
                                if not nome:
                                    continue
                                price_val = item.get("price_info", {})
                                prezzo_raw = str(price_val.get("price", "") or
                                                 price_val.get("min_price", "") or
                                                 item.get("price", "") or "")
                                if not prezzo_raw:
                                    continue
                                prezzo = parse_price(prezzo_raw)
                                if not math.isfinite(prezzo):
                                    continue
                                goods_id = str(item.get("goods_id", "") or "")
                                link = f"https://www.temu.com/it/g-{goods_id}.html" if goods_id else ""
                                if not link:
                                    continue
                                if not is_relevant(nome, query_tokens, strict_specs=False):
                                    continue
                                if not _within_price_range(prezzo, prezzo_min, budget_max):
                                    continue
                                img_url = str(item.get("goods_thumbnail_url", "") or "")
                                risultati.append(
                                    Offerta(nome=nome, prezzo=prezzo, negozio="Temu", link=link,
                                            fonte="temu.com", spedizione="n.d.", immagine=img_url)
                                )
                        except Exception:
                            pass
                        break
                if risultati:
                    break

        if not risultati:
            print("    ⚠️  Temu.com: pagina JS-rendered o bot-protetta, nessun risultato via HTML statico.")

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


# ===========================================================================
# SCRAPER — alibaba.com
# ===========================================================================

def scrape_alibaba(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """Scraper per Alibaba.com — DOM vuoto (89KB ma nessun testo estraibile, JS-rendered)."""
    print(f"\n🔍 Cerco su Alibaba.com: \"{query}\"")
    print("    ⚠️  Alibaba.com: pagina JS-rendered (89KB senza testo/prezzi estraibili). Fonte non disponibile senza browser headless.")
    return []
    # Implementazione HTML conservata per riferimento futuro:
    url = f"https://www.alibaba.com/trade/search?SearchText={quote_plus(query)}&SortType=price_asc"

    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://www.alibaba.com/"
        headers["Accept-Language"] = "it-IT,it;q=0.9,en;q=0.5"

        resp = fetch_with_retry(url, headers)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        cards = (
            soup.select('div[class*="organic-list-offer"]')
            or soup.select('div[class*="offer-list-row"]')
            or soup.select('.J-offer-wrapper')
        )

        if not cards:
            print("    ⚠️  Alibaba.com: nessun risultato trovato (possibile blocco o layout JS).")
            return risultati

        print(f"    ✅ Trovate {len(cards)} card grezze su Alibaba.com")

        for card in cards:
            try:
                nome_tag = (
                    card.select_one('[class*="subject"]')
                    or card.select_one('[class*="title"]')
                    or card.select_one('h2')
                )
                if not nome_tag:
                    continue
                nome = nome_tag.get_text(strip=True)
                if not nome:
                    continue

                prezzo_tag = card.select_one('[class*="price"]')
                if not prezzo_tag:
                    continue
                prezzo = parse_price(prezzo_tag.get_text(strip=True))
                if not math.isfinite(prezzo):
                    continue

                link_tag = card.select_one("a[href]")
                if not link_tag:
                    continue
                href = str(link_tag.get("href", "") or "")
                link = href if href.startswith("http") else "https:" + href if href.startswith("//") else "https://www.alibaba.com" + href

                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue

                risultati.append(
                    Offerta(nome=nome, prezzo=prezzo, negozio="Alibaba", link=link,
                            fonte="alibaba.com", spedizione="n.d.")
                )
            except (AttributeError, TypeError):
                continue

    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Alibaba.com: accesso bloccato (HTTP {status}), salto la fonte.")
    except requests.Timeout:
        print("    ❌ Alibaba.com: timeout.")
    except requests.ConnectionError:
        print("    ❌ Alibaba.com: impossibile connettersi.")
    except Exception as exc:
        print(f"    ❌ Alibaba.com: errore inatteso → {exc}")

    _random_delay()
    return risultati


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
    Cerca offerte tech su amazon.it, ebay.it, euronics.it e mediaworld.it.

    Args:
        query:        Testo della ricerca (es. "notebook 14 pollici 16gb RAM").
        prezzo_min:   Prezzo minimo in euro (default: 0).
        budget_max:   Prezzo massimo in euro. None = nessun limite.
        filtri_ai:    Filtri semantici post-scraping (es. colore, storage).
        top_n:        Quante offerte mostrare (default: 10).
        export_csv:   Se True, salva i risultati in un file CSV.
        csv_filename: Nome del file CSV di output (default: "offerte.csv").
        condizione:   Filtro stato prodotto Amazon: "tutti", "nuovo", "usato".
        fonti:        Fonti da usare (amazon, ebay, vinted, euronics, unieuro, mediaworld). None = tutte.
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
        # subito/aliexpress/temu/alibaba sono bloccati da bot-protection — esclusi dal default
        fonti_norm = {"amazon", "ebay", "vinted", "euronics", "unieuro", "mediaworld"}
    print(f"  🌐 Fonti attive: {', '.join(sorted(fonti_norm))}")

    # Lancio scraper in parallelo sulle fonti selezionate
    offerte: list[Offerta] = []
    future_to_label: dict = {}
    def _timed_call(fn: Callable, label: str, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            res = fn(*args, **kwargs)
        finally:
            elapsed = time.perf_counter() - t0
            # Solo print: il thread non può accedere a st.session_state (ScriptRunContext warning)
            print(f"[scrape] {label}: {elapsed:.2f}s")
        return res
    with ThreadPoolExecutor(max_workers=10) as executor:
        if "amazon" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_amazon, "Amazon.it", query, prezzo_min, budget_max, query_tokens, condizione)] = "Amazon.it"
        if "ebay" in fonti_norm:
            if app_id and cert_id:
                future_to_label[executor.submit(_timed_call, scrape_ebay, "eBay.it", query, prezzo_min, budget_max, condizione, query_tokens, app_id, cert_id)] = "eBay.it"
            else:
                print("    ⚠️  eBay non configurato — chiavi mancanti.")
                if progress_callback:
                    progress_callback("eBay.it", -2)
        if "vinted" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_vinted, "Vinted.it", query, prezzo_min, budget_max, query_tokens, condizione)] = "Vinted.it"
        if "euronics" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_euronics, "Euronics.it", query, prezzo_min, budget_max, query_tokens)] = "Euronics.it"
        if "unieuro" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_unieuro, "Unieuro.it", query, prezzo_min, budget_max, query_tokens)] = "Unieuro.it"
        if "mediaworld" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_mediaworld, "MediaWorld.it", query, prezzo_min, budget_max, query_tokens, condizione)] = "MediaWorld.it"
        if "subito" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_subito, "Subito.it", query, prezzo_min, budget_max, query_tokens, condizione)] = "Subito.it"
        if "aliexpress" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_aliexpress, "AliExpress.com", query, prezzo_min, budget_max, query_tokens)] = "AliExpress.com"
        if "temu" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_temu, "Temu.com", query, prezzo_min, budget_max, query_tokens)] = "Temu.com"
        if "alibaba" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_alibaba, "Alibaba.com", query, prezzo_min, budget_max, query_tokens)] = "Alibaba.com"

        # Cap per-source: evita che una singola fonte (es. eBay con 50 risultati) soffochi le altre.
        # Distribuiamo top_n diviso per numer fonti per non avere un dominio assoluto, + extra safety margin
        _per_source_cap = max((top_n // max(1, len(fonti_norm))) + 5, 10)
        for future in as_completed(future_to_label):
            label = future_to_label[future]
            try:
                new_results = future.result()
                new_results = new_results[:_per_source_cap]
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

    # Filtro post-scraping condizione: rimuove ricondizionati/usati se l'utente vuole solo "nuovo"
    _KW_RICONDIZIONATO = {
        "ricondizionato", "refurbished", "rigenerato", "reconditioned",
        "second life", "open box", "ricondizionata", "usato", "used",
    }
    if condizione == "nuovo":
        offerte = [o for o in offerte if not any(k in o.nome.lower() for k in _KW_RICONDIZIONATO)]
        print(f"  🏷️  Dopo filtro 'nuovo' (no ricondizionati): {len(offerte)}")
    elif condizione == "usato":
        # Per usato su Amazon/store fisici filtra solo ricondizionati espliciti; eBay usa già conditionIds
        pass

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
        description="🛒 Cerca offerte tech su amazon.it, ebay.it ed altri",
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
        choices=["amazon", "ebay", "vinted", "euronics", "unieuro", "mediaworld"],
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

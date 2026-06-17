"""offerte: offerte/_constants.py"""

from __future__ import annotations
import os
import random
import re

try:
    import streamlit as st
except Exception:
    st = None

try:
    from vinted_scraper import VintedScraper
except ImportError:
    VintedScraper = None  # type: ignore[assignment,misc]

# ---------------------------------------------------------------------------
# Tentativo di importare fake_useragent; fallback a lista statica se assente
# ---------------------------------------------------------------------------
_FALLBACK_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:136.0) Gecko/20100101 Firefox/136.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/18.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
]
try:
    from fake_useragent import UserAgent

    _UA = UserAgent()

    def _random_ua() -> str:
        try:
            return _UA.random
        except Exception:
            return random.choice(_FALLBACK_UAS)
except Exception:
    UserAgent = None  # type: ignore[assignment,misc]
    _UA = None

    def _random_ua() -> str:
        return random.choice(_FALLBACK_UAS)


# ---------------------------------------------------------------------------
# Costanti globali
# ---------------------------------------------------------------------------
TIMEOUT = 10  # secondi per ogni richiesta HTTP
DELAY_MIN = 0.5  # secondi — delay minimo tra richieste
DELAY_MAX = 2.0  # secondi — delay massimo tra richieste
MAX_RETRIES = 2  # tentativi extra in caso di errore (tot: 1 + MAX_RETRIES)
BACKOFF_BASE = 2.0  # secondi — base per il backoff esponenziale

_EBAY_TOKEN_CACHE: dict[str, object] = {"token": None, "expires_at": 0.0}

# Stopword da ignorare nel filtro di rilevanza.
# Include anche unità di misura (pollici, gb, tb…): il numero associato è già
# sufficiente per il matching, e le unità raramente appaiono letteralmente
# nei titoli prodotto (es. "14 pollici" → il titolo ha solo '14"' o '14').
_STOPWORDS = {
    # Articoli e preposizioni italiane
    "e",
    "da",
    "con",
    "per",
    "di",
    "a",
    "in",
    "il",
    "la",
    "i",
    "le",
    "un",
    "una",
    "del",
    "degli",
    "su",
    "al",
    "dal",
    "usato",
    "nuovo",
    # Unità di misura — il numero prima di esse è il vero token di filtro
    "pollici",
    "inch",
    "inches",
    "ghz",
    "mhz",
    "hz",
    "watt",
    "wh",
    "ampere",
    "volt",
    "pixel",
    "megapixel",
    "mp",
}

# Alias di normalizzazione per il match di rilevanza
# Chiave: token trovato nel titolo prodotto → valore: set di token alternativi
_ALIASES: dict[str, set[str]] = {
    "16gb": {"16 gb", "16gb"},
    "16 gb": {"16gb", "16 gb"},
    "8gb": {"8 gb", "8gb"},
    "8 gb": {"8gb", "8 gb"},
    "32gb": {"32 gb", "32gb"},
    "32 gb": {"32gb", "32 gb"},
    "1tb": {"1 tb", "1tb"},
    "1 tb": {"1tb", "1 tb"},
    "2tb": {"2 tb", "2tb"},
    "2 tb": {"2tb", "2 tb"},
    "14": {"14.0", '14"', "14'", "14 pollici"},
    "15": {"15.0", '15"', "15'", "15 pollici"},
    "13": {"13.0", '13"', "13'", "13 pollici"},
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

_SPEC_PATTERN = re.compile(r"^\d+(?:gb|tb)$")
_SPEC_KEYWORDS = {"ram", "ssd", "hdd", "nvme", "ddr4", "ddr5"}


def _is_spec_token(token: str) -> bool:
    """True se il token rappresenta una specifica tecnica (es. '16gb', 'ram')."""
    return bool(_SPEC_PATTERN.match(token)) or token in _SPEC_KEYWORDS


_TECH_BRANDS = {
    "iphone",
    "apple",
    "samsung",
    "galaxy",
    "xiaomi",
    "redmi",
    "pixel",
    "google",
    "oneplus",
    "huawei",
    "honor",
    "oppo",
    "realme",
    "motorola",
    "nothing",
}


__all__ = [
    "annotations",
    "os",
    "random",
    "re",
    "st",
    "VintedScraper",
    "UserAgent",
    "_UA",
    "_random_ua",
    "_FALLBACK_UAS",
    "TIMEOUT",
    "DELAY_MIN",
    "DELAY_MAX",
    "MAX_RETRIES",
    "BACKOFF_BASE",
    "_EBAY_TOKEN_CACHE",
    "_STOPWORDS",
    "_ALIASES",
    "_SPEC_PATTERN",
    "_SPEC_KEYWORDS",
    "_is_spec_token",
    "_TECH_BRANDS",
]

"""Cache persistente su disco per i risultati di ricerca, con TTL.

A differenza della cache in `st.session_state` (volatile, muore al refresh e
non è condivisa tra sessioni), questa sopravvive ai riavvii e riduce scraping
ripetuto / rate-limit sulle fonti.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(__file__))
_DATA_DIR = os.path.join(_ROOT, "data")
CACHE_FILE = os.path.join(_DATA_DIR, "search_cache.json")


def make_cache_key(query: str, prezzo_min: int, budget_max: int, condizione: str, fonti) -> str:
    """Chiave stabile e indipendente dall'ordine delle fonti."""
    raw = "|".join(
        [
            str(query or "").strip().lower(),
            str(int(prezzo_min)),
            str(int(budget_max)),
            str(condizione or "tutti"),
            ",".join(sorted(str(f) for f in (fonti or []))),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load(path: str) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def read(key: str, *, ttl: float, now: float | None = None, path: str = CACHE_FILE) -> Any | None:
    """Restituisce i dati in cache se presenti e più freschi di `ttl` secondi."""
    now = time.time() if now is None else now
    entry = _load(path).get(key)
    if not entry:
        return None
    if (now - float(entry.get("ts", 0))) >= ttl:
        return None
    return entry.get("data")


def write(key: str, data: Any, *, now: float | None = None, path: str = CACHE_FILE) -> None:
    """Salva `data` sotto `key` con timestamp `now`."""
    now = time.time() if now is None else now
    store = _load(path)
    store[key] = {"ts": float(now), "data": data}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False)
    except OSError:
        pass

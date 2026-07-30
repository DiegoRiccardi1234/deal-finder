"""Cache persistente dei risultati di ricerca, con TTL.

A differenza della cache in `st.session_state` (volatile, muore al refresh e
non è condivisa tra sessioni), questa sopravvive ai riavvii e riduce scraping
ripetuto / rate-limit sulle fonti.

Persistita su SQLite (`offerte.db`). La versione precedente teneva tutto in un
unico JSON e per scrivere faceva load → mutate → dump senza lock: con più
sessioni Streamlit due scritture concorrenti si perdevano a vicenda, e la
lettura ingoiava `JSONDecodeError` restituendo `{}`, così un file corrotto
sembrava una cache vuota e veniva sovrascritto. Ora è un singolo UPSERT atomico.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from typing import Any

from offerte.db import DEFAULT_DB_PATH, get_db

#: Mantenuto per compatibilità: i chiamanti passano `path=` per scegliere il db.
CACHE_FILE = DEFAULT_DB_PATH


def make_cache_key(
    query: str,
    prezzo_min: int,
    budget_max: int,
    condizione: str,
    fonti: list[str] | tuple[str, ...] | None,
) -> str:
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


def read(
    key: str, *, ttl: float, now: float | None = None, path: str = DEFAULT_DB_PATH
) -> Any | None:
    """Restituisce i dati in cache se presenti e più freschi di `ttl` secondi."""
    now = time.time() if now is None else now
    try:
        row = get_db(path).query_one("SELECT ts, data FROM search_cache WHERE key = ?", (key,))
    except sqlite3.Error:
        return None
    if row is None:
        return None
    if (now - float(row["ts"])) >= ttl:
        return None
    try:
        return json.loads(row["data"])
    except json.JSONDecodeError:
        # Payload illeggibile: si comporta come cache miss (il chiamante rifà la
        # ricerca), ma non azzera nulla e la riga verrà sovrascritta dal write.
        return None


def write(key: str, data: Any, *, now: float | None = None, path: str = DEFAULT_DB_PATH) -> None:
    """Salva `data` sotto `key` con timestamp `now`."""
    now = time.time() if now is None else now
    try:
        payload = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError):
        # Dati non serializzabili: non è un errore fatale per una cache.
        return
    try:
        get_db(path).execute(
            "INSERT INTO search_cache(key, ts, data) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET ts = excluded.ts, data = excluded.data",
            (key, float(now), payload),
        )
    except sqlite3.Error:
        # Una cache che non riesce a scrivere non deve far fallire la ricerca.
        pass


def purge_expired(*, ttl: float, now: float | None = None, path: str = DEFAULT_DB_PATH) -> int:
    """Elimina le voci più vecchie di `ttl`. Ritorna quante ne ha rimosse.

    Col JSON lo store cresceva senza limite, perché nessuno rileggeva le voci
    scadute per cancellarle.
    """
    now = time.time() if now is None else now
    try:
        cur = get_db(path).execute("DELETE FROM search_cache WHERE ? - ts >= ?", (float(now), ttl))
    except sqlite3.Error:
        return 0
    return int(cur.rowcount or 0)

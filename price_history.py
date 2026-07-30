"""Storico prezzo-minimo per query, con rilevamento nuovi minimi e alert soglia.

Registra il prezzo minimo trovato per ogni query a ogni ricerca, così da
mostrare il trend nel tempo e segnalare quando un prezzo scende sotto soglia
o tocca un nuovo minimo storico.

Persistito su SQLite (`offerte.db`). Prima era un JSON riscritto per intero a
ogni `record()`: append O(n) sull'intero storico, non atomico, e un file
corrotto veniva letto come lista vuota — quindi il primo `record()` successivo
cancellava definitivamente tutto lo storico dei prezzi.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from offerte.db import DEFAULT_DB_PATH, get_db

#: Mantenuto per compatibilità: i chiamanti passano `path=` per scegliere il db.
PRICE_HISTORY_FILE = DEFAULT_DB_PATH


def _norm(query: str) -> str:
    return str(query or "").strip().lower()


def record(
    query: str,
    min_price: float | None,
    *,
    now: float | None = None,
    path: str = DEFAULT_DB_PATH,
) -> None:
    """Registra il prezzo minimo per `query` al tempo `now`."""
    q = _norm(query)
    if not q or min_price is None:
        return
    now = time.time() if now is None else now
    try:
        get_db(path).execute(
            "INSERT INTO price_history(query, min_price, ts) VALUES (?, ?, ?)",
            (q, float(min_price), float(now)),
        )
    except sqlite3.Error:
        pass


def history_for(query: str, *, path: str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Voci dello storico per `query`, ordinate per tempo crescente."""
    q = _norm(query)
    if not q:
        return []
    try:
        rows = get_db(path).query(
            "SELECT query, min_price, ts FROM price_history WHERE query = ? ORDER BY ts ASC",
            (q,),
        )
    except sqlite3.Error:
        return []
    return [
        {"query": r["query"], "min_price": float(r["min_price"]), "ts": float(r["ts"])}
        for r in rows
    ]


def lowest_ever(query: str, *, path: str = DEFAULT_DB_PATH) -> float | None:
    """Prezzo minimo mai registrato per `query`, o None se non c'è storico."""
    q = _norm(query)
    if not q:
        return None
    try:
        row = get_db(path).query_one(
            "SELECT MIN(min_price) AS low FROM price_history WHERE query = ?", (q,)
        )
    except sqlite3.Error:
        return None
    if row is None or row["low"] is None:
        return None
    return float(row["low"])


def is_new_low(query: str, current_min: float | None, *, path: str = DEFAULT_DB_PATH) -> bool:
    """True se `current_min` è sotto il minimo storico (o non c'è ancora storico).

    Va chiamata PRIMA di `record()`, altrimenti il valore corrente è già nello
    storico e non risulterà mai un nuovo minimo.
    """
    if current_min is None:
        return False
    low = lowest_ever(query, path=path)
    return True if low is None else float(current_min) < low


def below_threshold(price: float | None, threshold: float | None) -> bool:
    """True se `price` è <= soglia (alert 'sotto soglia')."""
    if price is None or threshold is None:
        return False
    return float(price) <= float(threshold)

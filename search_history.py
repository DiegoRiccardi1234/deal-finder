"""Persistenza dello storico ricerche per la UI Streamlit.

Su SQLite (`offerte.db`). La dedup per query normalizzata è la PRIMARY KEY
invece di un filtro in Python fra una lettura e una riscrittura non atomiche.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from offerte.db import DEFAULT_DB_PATH, get_db

#: Mantenuto per compatibilità: i chiamanti passano `path=` per scegliere il db.
HISTORY_FILE = DEFAULT_DB_PATH
MAX_ENTRIES = 20


def load_history(*, path: str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Storico ricerche, dalla più recente."""
    try:
        rows = get_db(path).query(
            "SELECT query, budget_min, budget_max, condizione, fonti, results_count, timestamp "
            "FROM search_history ORDER BY timestamp DESC, rowid DESC LIMIT ?",
            (MAX_ENTRIES,),
        )
    except sqlite3.Error:
        return []
    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            fonti = json.loads(r["fonti"])
        except json.JSONDecodeError:
            fonti = []
        out.append(
            {
                "query": r["query"],
                "budget_min": int(r["budget_min"]),
                "budget_max": int(r["budget_max"]),
                "condizione": r["condizione"],
                "fonti": fonti if isinstance(fonti, list) else [],
                "results_count": int(r["results_count"]),
                "timestamp": r["timestamp"],
            }
        )
    return out


def save_search(
    query: str,
    budget_min: int,
    budget_max: int,
    condizione: str,
    fonti: list[str],
    results_count: int,
    *,
    path: str = DEFAULT_DB_PATH,
) -> None:
    """Salva una ricerca in testa allo storico, deduplicando per query."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return
    try:
        db = get_db(path)
        db.execute(
            "INSERT INTO search_history"
            "(query_norm, query, budget_min, budget_max, condizione, fonti, results_count, timestamp)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(query_norm) DO UPDATE SET"
            "   query = excluded.query,"
            "   budget_min = excluded.budget_min,"
            "   budget_max = excluded.budget_max,"
            "   condizione = excluded.condizione,"
            "   fonti = excluded.fonti,"
            "   results_count = excluded.results_count,"
            "   timestamp = excluded.timestamp",
            (
                clean_query.lower(),
                clean_query,
                int(budget_min),
                int(budget_max),
                str(condizione or "tutti"),
                json.dumps(list(fonti or []), ensure_ascii=False),
                int(results_count),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        # Tiene solo le MAX_ENTRIES più recenti. Prima il troncamento avveniva
        # in memoria a ogni salvataggio; qui è una DELETE mirata.
        db.execute(
            "DELETE FROM search_history WHERE query_norm NOT IN ("
            "  SELECT query_norm FROM search_history ORDER BY timestamp DESC, rowid DESC LIMIT ?"
            ")",
            (MAX_ENTRIES,),
        )
    except sqlite3.Error:
        pass

"""Watchlist / preferiti: prodotti salvati dall'utente.

Persistita su SQLite (`offerte.db`). La dedup per link, che prima era una
scansione lineare in Python fra un `load()` e un `_save()` non atomici (due
click ravvicinati potevano inserire un doppione o perdere un inserimento), è
adesso la PRIMARY KEY della tabella: se il link c'è già, `INSERT` non passa.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from offerte.db import DEFAULT_DB_PATH, get_db

#: Mantenuto per compatibilità: i chiamanti passano `path=` per scegliere il db.
WATCHLIST_FILE = DEFAULT_DB_PATH


def load(*, path: str = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    """Preferiti, dal più recente al più vecchio."""
    try:
        rows = get_db(path).query(
            "SELECT nome, prezzo, link, fonte, timestamp FROM watchlist "
            "ORDER BY timestamp DESC, rowid DESC"
        )
    except sqlite3.Error:
        return []
    return [
        {
            "nome": r["nome"],
            "prezzo": float(r["prezzo"]) if r["prezzo"] is not None else None,
            "link": r["link"],
            "fonte": r["fonte"],
            "timestamp": r["timestamp"],
        }
        for r in rows
    ]


def is_watched(link: str, *, path: str = DEFAULT_DB_PATH) -> bool:
    target = str(link or "").strip()
    if not target:
        return False
    try:
        return (
            get_db(path).query_one("SELECT 1 FROM watchlist WHERE link = ?", (target,)) is not None
        )
    except sqlite3.Error:
        return False


def add_item(
    nome: str,
    prezzo: float | None,
    link: str,
    fonte: str,
    *,
    path: str = DEFAULT_DB_PATH,
) -> bool:
    """Aggiunge un prodotto. Dedup per link: ritorna False se già presente."""
    target = str(link or "").strip()
    if not target:
        return False
    try:
        cur = get_db(path).execute(
            "INSERT OR IGNORE INTO watchlist(link, nome, prezzo, fonte, timestamp) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                target,
                str(nome or "").strip(),
                float(prezzo) if prezzo is not None else None,
                str(fonte or ""),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
    except sqlite3.Error:
        return False
    # `OR IGNORE` non inserisce nulla se il link c'è già: rowcount distingue i
    # due casi senza una SELECT preventiva, quindi senza finestra di race.
    return bool(cur.rowcount)


def remove(link: str, *, path: str = DEFAULT_DB_PATH) -> None:
    target = str(link or "").strip()
    if not target:
        return
    try:
        get_db(path).execute("DELETE FROM watchlist WHERE link = ?", (target,))
    except sqlite3.Error:
        pass

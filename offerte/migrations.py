"""Schema del database e migrazione dai vecchi file JSON.

Le migrazioni sono una lista ordinata di funzioni: `schema_version` (il PRAGMA
nativo di SQLite) dice a che punto è il database, così un'installazione esistente
applica solo il delta. Aggiungere una migrazione = appendere alla lista, mai
modificare una già rilasciata.

Solo stdlib: importato da `offerte.db`, che a sua volta non importa nulla di
interno.
"""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

# Nomi dei file JSON legacy, importati una sola volta al primo avvio.
_LEGACY_FILES = {
    "search_cache": "search_cache.json",
    "price_history": "price_history.json",
    "watchlist": "watchlist.json",
    "search_history": "search_history.json",
    "auth_sessions": "auth_sessions.json",
}


def _migration_001_initial(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- Cache ricerche con TTL. La chiave è l'hash stabile prodotto da
        -- `offerte.cache.make_cache_key`; `data` è il payload JSON serializzato.
        CREATE TABLE IF NOT EXISTS search_cache (
            key  TEXT PRIMARY KEY,
            ts   REAL NOT NULL,
            data TEXT NOT NULL
        );

        -- Storico prezzo-minimo: append-only, una riga per ricerca.
        CREATE TABLE IF NOT EXISTS price_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            query     TEXT NOT NULL,
            min_price REAL NOT NULL,
            ts        REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_price_history_query
            ON price_history(query, ts);

        -- Preferiti. La dedup per link diventa un vincolo del database invece di
        -- una scansione in Python.
        CREATE TABLE IF NOT EXISTS watchlist (
            link      TEXT PRIMARY KEY,
            nome      TEXT NOT NULL DEFAULT '',
            prezzo    REAL,
            fonte     TEXT NOT NULL DEFAULT '',
            timestamp TEXT NOT NULL
        );

        -- Storico ricerche. Dedup per query normalizzata, anche questa come
        -- chiave primaria; `fonti` è un array JSON.
        CREATE TABLE IF NOT EXISTS search_history (
            query_norm    TEXT PRIMARY KEY,
            query         TEXT NOT NULL,
            budget_min    INTEGER NOT NULL DEFAULT 0,
            budget_max    INTEGER NOT NULL DEFAULT 0,
            condizione    TEXT NOT NULL DEFAULT 'tutti',
            fonti         TEXT NOT NULL DEFAULT '[]',
            results_count INTEGER NOT NULL DEFAULT 0,
            timestamp     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_search_history_ts
            ON search_history(timestamp DESC);

        -- Token di sessione del gate auth: fingerprint -> scadenza epoch.
        CREATE TABLE IF NOT EXISTS auth_sessions (
            fingerprint TEXT PRIMARY KEY,
            expires_at  REAL NOT NULL
        );
        """
    )


def _migration_002_knowledge_base(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- Stato runtime della knowledge base prodotti: una riga sola.
        -- Serve a smettere di riscrivere `data/knowledge_base.json`, che è
        -- TRACCIATO da git: l'auto-update in background lo modificava a ogni
        -- avvio della UI, sporcando il working tree con un diff di due righe
        -- (`updated_at`, `version`) a ogni run. Quel file torna a essere un seed
        -- di sola lettura; qui vive lo stato che cambia.
        CREATE TABLE IF NOT EXISTS kb_state (
            id         INTEGER PRIMARY KEY CHECK (id = 1),
            updated_at TEXT NOT NULL,
            version    INTEGER NOT NULL DEFAULT 1,
            categorie  TEXT NOT NULL
        );

        -- Item incontrati in chat e non presenti in KB, da includere nel
        -- prossimo report di aggiornamento. La coppia è chiave primaria, quindi
        -- il "già visto?" è un vincolo e non più una lettura fuori dal lock
        -- seguita da una scrittura dentro (TOCTOU: item concorrenti si perdevano).
        CREATE TABLE IF NOT EXISTS kb_unknown_items (
            categoria  TEXT NOT NULL,
            item       TEXT NOT NULL,
            first_seen TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (categoria, item)
        );
        """
    )


#: Migrazioni in ordine. L'indice+1 è la `schema_version` risultante.
MIGRATIONS = [_migration_001_initial, _migration_002_knowledge_base]


def apply_migrations(conn: sqlite3.Connection) -> None:
    """Porta lo schema alla versione corrente e importa i JSON legacy."""
    # `schema_version` è riservato a SQLite: la versione applicativa sta in
    # `user_version`.
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    for index, migration in enumerate(MIGRATIONS, start=1):
        if current >= index:
            continue
        migration(conn)
        conn.execute(f"PRAGMA user_version = {index}")
        conn.commit()

    _import_legacy_json(conn)


# ---------------------------------------------------------------------------
# Import dei dati preesistenti
# ---------------------------------------------------------------------------


def _read_json(path: str) -> Any | None:
    """Legge un JSON legacy. `None` se assente o illeggibile."""
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _is_empty(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
    return row is None


def _import_legacy_json(conn: sqlite3.Connection) -> None:
    """Importa i vecchi file JSON nelle tabelle ancora vuote.

    Idempotente e non distruttivo: i file restano al loro posto (rinominati con
    suffisso `.migrated`) e una tabella già popolata non viene toccata, quindi
    l'import non può sovrascrivere dati più recenti. Serve a non far perdere
    preferiti e storico a chi aggiorna da una versione precedente.
    """
    data_dir = os.path.dirname(os.path.abspath(conn.execute("PRAGMA database_list").fetchone()[2]))
    imported: list[str] = []

    # -- watchlist -----------------------------------------------------------
    path = os.path.join(data_dir, _LEGACY_FILES["watchlist"])
    items = _read_json(path)
    if isinstance(items, list) and _is_empty(conn, "watchlist"):
        for it in items:
            if not isinstance(it, dict):
                continue
            link = str(it.get("link", "") or "").strip()
            if not link:
                continue
            conn.execute(
                "INSERT OR IGNORE INTO watchlist(link, nome, prezzo, fonte, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    link,
                    str(it.get("nome", "") or ""),
                    it.get("prezzo"),
                    str(it.get("fonte", "") or ""),
                    str(it.get("timestamp", "") or ""),
                ),
            )
        imported.append(path)

    # -- storico ricerche ----------------------------------------------------
    path = os.path.join(data_dir, _LEGACY_FILES["search_history"])
    items = _read_json(path)
    if isinstance(items, list) and _is_empty(conn, "search_history"):
        for it in items:
            if not isinstance(it, dict):
                continue
            q = str(it.get("query", "") or "").strip()
            if not q:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO search_history"
                "(query_norm, query, budget_min, budget_max, condizione, fonti,"
                " results_count, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    q.lower(),
                    q,
                    int(it.get("budget_min", 0) or 0),
                    int(it.get("budget_max", 0) or 0),
                    str(it.get("condizione", "tutti") or "tutti"),
                    json.dumps(list(it.get("fonti") or []), ensure_ascii=False),
                    int(it.get("results_count", 0) or 0),
                    str(it.get("timestamp", "") or ""),
                ),
            )
        imported.append(path)

    # -- storico prezzi ------------------------------------------------------
    path = os.path.join(data_dir, _LEGACY_FILES["price_history"])
    items = _read_json(path)
    if isinstance(items, list) and _is_empty(conn, "price_history"):
        for it in items:
            if not isinstance(it, dict) or it.get("min_price") is None:
                continue
            q = str(it.get("query", "") or "").strip().lower()
            if not q:
                continue
            conn.execute(
                "INSERT INTO price_history(query, min_price, ts) VALUES (?, ?, ?)",
                (q, float(it["min_price"]), float(it.get("ts", 0) or 0)),
            )
        imported.append(path)

    # -- sessioni auth -------------------------------------------------------
    path = os.path.join(data_dir, _LEGACY_FILES["auth_sessions"])
    sessions = _read_json(path)
    if isinstance(sessions, dict) and _is_empty(conn, "auth_sessions"):
        for fingerprint, expires in sessions.items():
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO auth_sessions(fingerprint, expires_at) VALUES (?, ?)",
                    (str(fingerprint), float(expires)),
                )
            except (TypeError, ValueError):
                continue
        imported.append(path)

    # La cache ricerche NON viene importata: è rigenerabile, ha un TTL corto e
    # trascinarla dietro non porta valore.

    if imported:
        conn.commit()
        for path in imported:
            try:
                os.replace(path, path + ".migrated")
            except OSError:
                # Se il rename non riesce l'import resta valido: le tabelle non
                # sono più vuote, quindi non verrà rifatto.
                pass

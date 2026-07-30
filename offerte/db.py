"""Persistenza SQLite condivisa.

Sostituisce i file JSON che ogni modulo si scriveva da sé. Il difetto non era
estetico: nessuna di quelle scritture era atomica (`open(w)` + `json.dump`, quindi
un crash a metà lasciava un JSON troncato), la cache faceva read-modify-write
senza lock (lost update garantita con più sessioni Streamlit), e la lettura
ingoiava `JSONDecodeError` restituendo un contenitore vuoto — così un file
corrotto diventava «nessun dato» in silenzio e la prima scrittura successiva ci
sovrascriveva sopra, perdendo lo storico per sempre.

SQLite risolve la classe di problemi invece di tamponarla: commit atomici,
lock del writer gestito dal motore, e un file corrotto dà errore invece di
sembrare vuoto. Stesso approccio del progetto gemello `job-finder`.

Solo stdlib, come `offerte/config.py`, per poter essere importato da qualsiasi
modulo di `offerte/`, `ui/` e dai moduli top-level senza import circolari.
"""

from __future__ import annotations

import functools
import os
import sqlite3
import threading
from collections.abc import Callable
from typing import Any, TypeVar

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(_ROOT, "data")

#: Database condiviso da tutti gli store. Un solo file → un solo WAL e un solo
#: writer lock, invece di sei file che si sporcano a vicenda.
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "deal_finder.db")

_T = TypeVar("_T")

# Una connessione per path, riusata: Streamlit rerun-a nello stesso processo, e
# aprire una connessione per rerun sprecherebbe il WAL e i lock.
_INSTANCES: dict[str, Database] = {}
_INSTANCES_LOCK = threading.Lock()


def _synchronized(method: Callable[..., _T]) -> Callable[..., _T]:
    """Serializza il metodo sull'`RLock` dell'istanza.

    Serve per le sequenze read-modify-write (es. la cache) e perché
    `knowledge_base` scrive da un thread daemon mentre la UI legge.
    Reentrant: un metodo sincronizzato può chiamarne un altro.
    """

    @functools.wraps(method)
    def wrapper(self: Database, *args: Any, **kwargs: Any) -> _T:
        with self.lock:
            return method(self, *args, **kwargs)

    return wrapper


class Database:
    """Wrapper SQLite condiviso tra i thread di Streamlit.

    `check_same_thread=False` perché Streamlit serve i rerun da thread diversi e
    l'auto-update della knowledge base gira in un thread daemon; un `RLock`
    serializza le scritture così le connessioni concorrenti non corrono sui
    cursori né generano `database is locked`. WAL permette letture concorrenti
    durante una scrittura.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        try:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA synchronous=NORMAL")
            # Se un altro processo (una seconda istanza dell'app) tiene il write
            # lock, aspetta invece di alzare subito "database is locked".
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.execute("PRAGMA foreign_keys=ON")
        except sqlite3.DatabaseError:
            # Un filesystem che non supporta WAL non deve impedire l'avvio: il
            # journal di default resta comunque atomico.
            pass

        from offerte.migrations import apply_migrations

        with self.lock:
            apply_migrations(self.conn)

    # ---------------------------------------------------------------- helpers

    @_synchronized
    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        """Esegue una scrittura e committa."""
        cur = self.conn.execute(sql, params)
        self.conn.commit()
        return cur

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """Esegue una lettura. Non serializzata: in WAL i reader non bloccano."""
        return list(self.conn.execute(sql, params).fetchall())

    def query_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def close(self) -> None:
        with self.lock:
            self.conn.close()


def get_db(path: str | None = None) -> Database:
    """Istanza condivisa per `path` (default: il database dell'app).

    Il parametro esiste perché gli store espongono ancora un kwarg `path`: nei
    test punta a un file sotto `tmp_path`, così ogni test ha un database isolato.
    """
    resolved = os.path.abspath(path or DEFAULT_DB_PATH)
    with _INSTANCES_LOCK:
        db = _INSTANCES.get(resolved)
        if db is None:
            db = Database(resolved)
            _INSTANCES[resolved] = db
        return db


def reset_instances() -> None:
    """Chiude e dimentica le connessioni in cache. Solo per i test."""
    with _INSTANCES_LOCK:
        for db in _INSTANCES.values():
            try:
                db.close()
            except sqlite3.Error:
                pass
        _INSTANCES.clear()

"""ui: ui/auth.py"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

import streamlit as st

from offerte.db import get_db

try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]


try:
    from search_history import load_history, save_search as _save_search
except ImportError:

    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)

# Salt del fingerprint di sessione. NON è il nome del prodotto: cambiarlo
# invalida tutti i token di sessione già salvati e obbliga a rifare il login.
# Tenuto come costante separata proprio per non farlo seguire un rebrand per
# sbaglio.
_FINGERPRINT_SALT = "deal-finder-session-v1"


def _load_auth_sessions() -> dict[str, float]:
    """Sessioni valide dal database (fingerprint -> scadenza epoch)."""
    try:
        rows = get_db().query("SELECT fingerprint, expires_at FROM auth_sessions")
    except sqlite3.Error:
        return {}
    return {str(r["fingerprint"]): float(r["expires_at"]) for r in rows}


def _save_auth_sessions(sessions: dict[str, float]) -> None:
    """Riscrive l'insieme delle sessioni valide.

    Firma conservata perché i chiamanti passano già il dict ripulito dalle voci
    scadute. Il DELETE+INSERT gira dentro il lock del writer, quindi non lascia
    una finestra in cui la tabella è vuota per gli altri thread.
    """
    db = get_db()
    try:
        with db.lock:
            db.conn.execute("DELETE FROM auth_sessions")
            db.conn.executemany(
                "INSERT OR REPLACE INTO auth_sessions(fingerprint, expires_at) VALUES (?, ?)",
                [(str(k), float(v)) for k, v in sessions.items()],
            )
            db.conn.commit()
    except (sqlite3.Error, TypeError, ValueError):
        pass


def _get_client_fingerprint() -> str:
    """Costruisce un fingerprint stabile del browser per persistenza auth cross-reopen."""
    ua = ""
    ip_addr = ""
    try:
        _ctx = st.context
        _headers = getattr(_ctx, "headers", {})
        if hasattr(_headers, "get"):
            ua = str(_headers.get("user-agent", "") or _headers.get("User-Agent", ""))
        ip_addr = str(getattr(_ctx, "ip_address", "") or "")
    except Exception:
        pass

    raw = f"{ua}|{ip_addr}|{_FINGERPRINT_SALT}"
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _is_client_authenticated(fingerprint: str, now_ts: float) -> bool:
    sessions = _load_auth_sessions()
    # Cleanup entries scadute
    sessions = {k: v for k, v in sessions.items() if float(v) > now_ts}
    _save_auth_sessions(sessions)
    return float(sessions.get(fingerprint, 0.0)) > now_ts


def _persist_client_auth(fingerprint: str, now_ts: float, ttl_seconds: int = 3600) -> None:
    sessions = _load_auth_sessions()
    sessions[fingerprint] = float(now_ts + ttl_seconds)
    _save_auth_sessions(sessions)

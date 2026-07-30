"""ui: ui/auth.py"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import streamlit as st

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
# invalida tutti i token già in `data/auth_sessions.json` e obbliga a rifare il
# login. Tenuto come costante separata proprio per non farlo seguire un rebrand
# per sbaglio.
_FINGERPRINT_SALT = "deal-finder-session-v1"
_AUTH_SESSIONS_PATH = _DATA_DIR / "auth_sessions.json"


def _load_auth_sessions() -> dict[str, float]:
    try:
        if _AUTH_SESSIONS_PATH.exists():
            data = json.loads(_AUTH_SESSIONS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {str(k): float(v) for k, v in data.items()}
    except Exception:
        pass
    return {}


def _save_auth_sessions(sessions: dict[str, float]) -> None:
    try:
        _AUTH_SESSIONS_PATH.write_text(
            json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
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

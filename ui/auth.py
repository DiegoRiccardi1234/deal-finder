"""ui: ui/auth.py"""
from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

import streamlit as st

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

try:
    from offerte.ai import (
        get_best_model as _get_best_model,
        cerebras_chat_with_retry as _cerebras_chat_lib,
    )
except Exception:
    _get_best_model = None  # type: ignore[assignment]
    _cerebras_chat_lib = None  # type: ignore[assignment]

CEREBRAS_MODEL = "llama-3.3-70b"

try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]

from offerte_tech import Offerta, cerca_offerte, parse_search_intent, parse_comparison_query

try:
    from search_history import load_history, save_search as _save_search
except ImportError:
    def load_history() -> list[dict[str, Any]]:
        return []
    def _save_search(**kw: Any) -> None:
        return None


_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
_DATA_DIR.mkdir(exist_ok=True)
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
        _AUTH_SESSIONS_PATH.write_text(json.dumps(sessions, ensure_ascii=False, indent=2), encoding="utf-8")
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

    raw = f"{ua}|{ip_addr}|trova-prezzi-mio"
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



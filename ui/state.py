"""ui: ui/state.py"""
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


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "risultati": [],
        "log_ricerca": "",
        "ultima_query": "",
        "query_input": "",
        "_query_prefilled": "",
        "ricerca_effettuata": False,
        "condizione": "tutti",
        "ultimo_prezzo_min": 0,
        "ultimo_prezzo_max": 800,
        "ultimo_top_n": 20,
        "fonti_selezionate": ["Amazon", "eBay", "Vinted", "Euronics", "MediaWorld", "Unieuro", "Wallapop", "Comet", "Expert"],
        "price_min_input": 0,
        "budget_max_input": 800,
        "price_range_slider": (0, 800),
        "presearch_messages": [
            {
                "role": "assistant",
                "content": "Raccontami la richiesta del tuo amico su Trova Prezzi — qualsiasi tipo di prodotto "
                "(tech, abbigliamento, elettrodomestici, sport, libri...). "
                "Ti faccio al massimo 2 domande e poi avvio lo scraping.",
            }
        ],
        "presearch_question_count": 0,
        "presearch_ready": False,
        "query_ottimizzata": "",
        "categoria": "altro",
        "preferenze_utente": {"messaggi": [], "trascrizione": ""},
        "prezzo_min": 0,
        "budget_max": 800,
        "final_chat_messages": [],
        "filtri_ai": {},
        "filtri_ai_ultima_ricerca": {},
        "auto_recommend_tried": False,
        # Cache ricerca
        "_search_cache": {},
        # Filtri post-ricerca
        "filtro_fonti_tabella": [],
        "filtro_prezzo_range_tabella": None,
        "filtro_condizione_tabella": "tutti",
        # Comparatore
        "comparatore_selezione": [],
        # Confronto multiplo (vs mode)
        "comparison_mode": False,
        "comparison_queries": [],
        "comparison_results": {},
        "_pending_price_sync": None,
        "ui_theme": _theme_mode,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _queue_price_sync(prezzo_min: int, budget_max: int) -> None:
    """Accoda la sincronizzazione dei widget prezzo al prossimo rerun sicuro."""
    pmin = max(0, min(int(prezzo_min), 5000))
    pmax = max(pmin, min(int(budget_max), 5000))
    st.session_state["prezzo_min"] = pmin
    st.session_state["budget_max"] = pmax
    st.session_state["_pending_price_sync"] = {"prezzo_min": pmin, "budget_max": pmax}


def _flush_pending_price_sync() -> None:
    """Applica eventuale sync prezzi prima che i widget vengano instanziati."""
    pending = st.session_state.get("_pending_price_sync")
    if not isinstance(pending, dict):
        return
    pmin = int(pending.get("prezzo_min", st.session_state.get("prezzo_min", 0)) or 0)
    pmax = int(pending.get("budget_max", st.session_state.get("budget_max", 800)) or 800)
    pmin = max(0, min(pmin, 5000))
    pmax = max(pmin, min(pmax, 5000))
    st.session_state["price_min_input"] = pmin
    st.session_state["budget_max_input"] = pmax
    st.session_state["price_range_slider"] = (pmin, pmax)
    st.session_state["_pending_price_sync"] = None


def _format_price(value: float) -> str:
    return f"€ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _sync_from_numbers() -> None:
    min_value = int(st.session_state.get("price_min_input", 0) or 0)
    max_value = int(st.session_state.get("budget_max_input", 800) or 800)
    min_value = max(0, min(min_value, 5000))
    max_value = max(min_value, min(max_value, 5000))
    st.session_state["price_min_input"] = min_value
    st.session_state["budget_max_input"] = max_value
    st.session_state["price_range_slider"] = (min_value, max_value)


def _sync_from_slider() -> None:
    min_value, max_value = st.session_state.get("price_range_slider", (0, 800))
    st.session_state["price_min_input"] = int(min_value)
    st.session_state["budget_max_input"] = int(max_value)



"""Persistenza semplice dello storico ricerche per la UI Streamlit."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

HISTORY_FILE = os.path.join(os.path.dirname(__file__), "search_history.json")
MAX_ENTRIES = 20


def load_history() -> list[dict[str, Any]]:
    """Carica lo storico ricerche dal file JSON, se disponibile."""
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def save_search(
    query: str,
    budget_min: int,
    budget_max: int,
    condizione: str,
    fonti: list[str],
    results_count: int,
) -> None:
    """Salva una ricerca in testa allo storico, deduplicando per query."""
    clean_query = str(query or "").strip()
    if not clean_query:
        return

    history = load_history()
    entry = {
        "query": clean_query,
        "budget_min": int(budget_min),
        "budget_max": int(budget_max),
        "condizione": str(condizione or "tutti"),
        "fonti": list(fonti or []),
        "results_count": int(results_count),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }

    history = [
        item for item in history
        if str(item.get("query", "")).strip().lower() != clean_query.lower()
    ]
    history.insert(0, entry)

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[:MAX_ENTRIES], f, ensure_ascii=False, indent=2)
    except OSError:
        pass

"""Storico prezzo-minimo per query, con rilevamento nuovi minimi e alert soglia.

Registra il prezzo minimo trovato per ogni query a ogni ricerca, così da
mostrare il trend nel tempo e segnalare quando un prezzo scende sotto soglia
o tocca un nuovo minimo storico.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PRICE_HISTORY_FILE = os.path.join(_DATA_DIR, "price_history.json")


def _load(path: str) -> list[dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _norm(query: str) -> str:
    return str(query or "").strip().lower()


def record(query: str, min_price: Optional[float], *, now: Optional[float] = None, path: str = PRICE_HISTORY_FILE) -> None:
    """Registra il prezzo minimo per `query` al tempo `now`."""
    q = _norm(query)
    if not q or min_price is None:
        return
    now = time.time() if now is None else now
    data = _load(path)
    data.append({"query": q, "min_price": float(min_price), "ts": float(now)})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


def history_for(query: str, *, path: str = PRICE_HISTORY_FILE) -> list[dict[str, Any]]:
    """Voci dello storico per `query`, ordinate per tempo crescente."""
    q = _norm(query)
    entries = [e for e in _load(path) if e.get("query") == q]
    entries.sort(key=lambda e: float(e.get("ts", 0)))
    return entries


def lowest_ever(query: str, *, path: str = PRICE_HISTORY_FILE) -> Optional[float]:
    """Prezzo minimo mai registrato per `query`, o None se non c'è storico."""
    prices = [float(e["min_price"]) for e in history_for(query, path=path) if e.get("min_price") is not None]
    return min(prices) if prices else None


def is_new_low(query: str, current_min: Optional[float], *, path: str = PRICE_HISTORY_FILE) -> bool:
    """True se `current_min` è sotto il minimo storico (o non c'è ancora storico)."""
    if current_min is None:
        return False
    low = lowest_ever(query, path=path)
    return True if low is None else float(current_min) < low


def below_threshold(price: Optional[float], threshold: Optional[float]) -> bool:
    """True se `price` è <= soglia (alert 'sotto soglia')."""
    if price is None or threshold is None:
        return False
    return float(price) <= float(threshold)

"""Watchlist / preferiti: prodotti salvati dall'utente, persistiti su disco.

Stesso pattern di persistenza JSON di `search_history.py`.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

_DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
WATCHLIST_FILE = os.path.join(_DATA_DIR, "watchlist.json")


def load(*, path: str = WATCHLIST_FILE) -> list[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def _save(items: list[dict[str, Any]], path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except OSError:
        pass


def is_watched(link: str, *, path: str = WATCHLIST_FILE) -> bool:
    target = str(link or "").strip()
    return any(str(i.get("link", "")).strip() == target for i in load(path=path))


def add_item(
    nome: str, prezzo: float | None, link: str, fonte: str, *, path: str = WATCHLIST_FILE
) -> bool:
    """Aggiunge un prodotto. Dedup per link: ritorna False se già presente."""
    target = str(link or "").strip()
    if not target or is_watched(target, path=path):
        return False
    items = load(path=path)
    items.insert(
        0,
        {
            "nome": str(nome or "").strip(),
            "prezzo": float(prezzo) if prezzo is not None else None,
            "link": target,
            "fonte": str(fonte or ""),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        },
    )
    _save(items, path)
    return True


def remove(link: str, *, path: str = WATCHLIST_FILE) -> None:
    target = str(link or "").strip()
    items = [i for i in load(path=path) if str(i.get("link", "")).strip() != target]
    _save(items, path)

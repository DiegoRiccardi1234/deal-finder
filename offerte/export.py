"""offerte: offerte/export.py"""
from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

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

_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"
from offerte.models import Offerta

def print_results(
    offerte: list[Offerta],
    query: str,
    budget_max: Optional[float],
    top_n: int,
) -> None:
    """Stampa i risultati finali in modo leggibile."""
    print("\n" + "=" * 70)
    print(f"  🛒 RISULTATI per: \"{query}\"")
    if budget_max is not None:
        print(f"  💵 Budget massimo: € {budget_max:,.2f}".replace(",", "."))
    print(f"  📋 Mostrati: {len(offerte)} risultati (top {top_n})")
    print("=" * 70)

    if not offerte:
        print("\n  ⚠️  Nessun risultato trovato. Prova a:\n"
              "       • Allargare il budget\n"
              "       • Usare termini più generici nella query\n"
              "       • Verificare la tua connessione internet\n")
        return

    for i, offerta in enumerate(offerte, start=1):
        print(f"\n  [{i:>2}] {offerta}")

    print("\n" + "=" * 70)


def export_to_csv(offerte: list[Offerta], filename: str = "offerte.csv") -> None:
    """
    Esporta i risultati in un file CSV.
    Usa pandas se disponibile, altrimenti usa il modulo csv stdlib.
    Encoding: UTF-8-SIG per compatibilità Excel italiano.
    """
    fieldnames = ["posizione", "nome", "prezzo_eur", "spedizione", "negozio", "fonte", "specs", "link"]
    rows = [
        {
            "posizione":  i,
            "nome":       o.nome,
            "prezzo_eur": o.prezzo,
            "spedizione": o.spedizione,
            "negozio":    o.negozio,
            "fonte":      o.fonte,
            "specs":      ", ".join(f"{k}={v}" for k, v in (o.specs or {}).items()),
            "link":       o.link,
        }
        for i, o in enumerate(offerte, start=1)
    ]

    try:
        import pandas as pd  # opzionale
        df = pd.DataFrame(rows)
        df.to_csv(filename, index=False, encoding="utf-8-sig")  # utf-8-sig per Excel italiano
    except ImportError:
        with open(filename, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_NONNUMERIC)
            writer.writeheader()
            writer.writerows(rows)

    abs_path = os.path.abspath(filename)
    print(f"\n  ✅ Risultati esportati in: {abs_path}")



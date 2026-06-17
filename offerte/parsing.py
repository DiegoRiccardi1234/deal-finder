"""offerte: offerte/parsing.py"""

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
from typing import Optional
from collections.abc import Callable
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

from offerte._constants import *  # noqa: F401,F403


def parse_price(text: str) -> float:
    """
    Converte una stringa di prezzo in float.

    Gestisce tutti i formati reali Amazon/eBay italiani:
      - "€ 899,00"            (spazio dopo €)
      - "A partire da € 1.299,00"
      - "€899,00 con coupon"
      - "EUR 899,00"
      - "1.299,00 €"          (simbolo dopo il numero)
      - "1.299,00 - 1.399,00" (range → prende il minimo)

    Ritorna math.inf se il parsing fallisce o il testo e vuoto.
    """
    if not text:
        return math.inf

    text = str(text)
    # Normalizza spazi speciali
    text = text.replace("\u00a0", " ").replace("\u202f", " ")

    # Rimuove prefissi comuni ("A partire da", "da", ecc.)
    text = re.sub(r"(?i)^.*?(?:a partire da|da)\s*", "", text)
    # Rimuove suffissi comuni ("con coupon", "con sconto", ecc.)
    text = re.sub(r"(?i)\s+con\s+.*$", "", text)

    # Rimuove simboli valuta e parole note
    text = text.replace("EUR", "").replace("€", "").strip()
    text = re.sub(r"[()\[\]]", "", text)
    text = re.sub(r"\s+", " ", text)

    # In caso di range (es. "100,00 - 200,00"), prende il primo valore
    parts = re.split(r"\s*[-–]\s*", text)
    text = parts[0].strip()

    # Estrae il primo frammento numerico plausibile da testo rumoroso.
    number_match = re.search(
        r"\d{1,3}(?:[\.\s]\d{3})+(?:[\.,]\d{2})?|\d+(?:[\.,]\d{2})?",
        text,
    )
    if number_match:
        text = number_match.group(0)

    # Mantiene solo cifre e separatori decimali/migliaia
    text = re.sub(r"[^\d,\.]", "", text)

    if not text:
        return math.inf

    if text.count(".") > 1 and "," not in text:
        parts = [part for part in text.split(".") if part]
        if len(parts) >= 2 and len(parts[-1]) == 2:
            text = "".join(parts[:-1]) + "." + parts[-1]
        else:
            text = "".join(parts)

    # Formato europeo: 1.299,00 → togli i punti migliaia, sostituisci virgola decimale
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif "." in text and "," not in text:
        # Potrebbe essere già in formato anglosassone (es. "129.99") o migliaia (es. "1.299")
        dot_parts = text.split(".")
        if len(dot_parts[-1]) != 2:
            # È separatore migliaia, non decimale
            text = text.replace(".", "")
    else:
        text = re.sub(r"[^\d.]", "", text)

    try:
        val = float(text)
        return val if val > 0 else math.inf
    except ValueError:
        return math.inf


def _within_price_range(prezzo: float, prezzo_min: float, budget_max: float | None) -> bool:
    """Verifica che il prezzo rientri nel range configurato."""
    if not math.isfinite(prezzo):
        return False
    return prezzo >= max(0.0, float(prezzo_min)) and (budget_max is None or prezzo <= budget_max)


def _normalize_category(categoria: str) -> str:
    """Restituisce la categoria normalizzata (lowercase, stripped) senza mappature fisse."""
    return str(categoria or "").strip().lower()


def _extract_json_object(raw: str) -> dict[str, object]:
    """Estrae un oggetto JSON da una stringa e ritorna un dict vuoto su errore."""
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}


def _extract_gb_values(text: str) -> list[int]:
    """Estrae quantità in GB dal testo (es. RAM o storage)."""
    values: list[int] = []
    for m in re.finditer(r"(\d{2,4})\s*gb\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)))
        except Exception:
            continue
    for m in re.finditer(r"(\d{1,2})\s*tb\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)) * 1024)
        except Exception:
            continue
    return values


def _extract_ram_gb_values(text: str) -> list[int]:
    """Estrae valori RAM (GB) quando esplicitamente associati a RAM/memoria."""
    values: list[int] = []
    patterns = [
        r"(\d{1,3})\s*gb\s*(?:di\s*)?ram\b",
        r"memoria\s*(\d{1,3})\s*gb\b",
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, flags=re.IGNORECASE):
            try:
                values.append(int(m.group(1)))
            except Exception:
                continue
    return values


def _extract_storage_gb_values(text: str) -> list[int]:
    """Estrae valori storage (GB/TB) associati a SSD/HDD/NVMe/disco."""
    values: list[int] = []
    for m in re.finditer(
        r"(\d{2,4})\s*gb\s*(?:ssd|hdd|nvme|emmc|disco|storage)\b", text, flags=re.IGNORECASE
    ):
        try:
            values.append(int(m.group(1)))
        except Exception:
            continue
    for m in re.finditer(
        r"(?:ssd|hdd|nvme|emmc|disco|storage)\s*(\d{2,4})\s*gb\b", text, flags=re.IGNORECASE
    ):
        try:
            values.append(int(m.group(1)))
        except Exception:
            continue
    for m in re.finditer(r"(\d{1,2})\s*tb\b", text, flags=re.IGNORECASE):
        try:
            values.append(int(m.group(1)) * 1024)
        except Exception:
            continue
    return values


def _extract_inches_values(text: str) -> list[float]:
    """Estrae dimensioni display in pollici dal testo prodotto."""
    values: list[float] = []
    pattern = r"(\d{1,2}(?:[\.,]\d)?)\s*(?:\"|''|pollici\b)"
    for m in re.finditer(pattern, text, flags=re.IGNORECASE):
        raw = str(m.group(1)).replace(",", ".")
        try:
            values.append(float(raw))
        except Exception:
            continue
    return values


def _parse_target_range(value: str) -> tuple[float, float] | None:
    """Converte '14-15' / '14/15' / '14,15' in range numerico."""
    txt = str(value or "").strip().lower()
    if not txt:
        return None
    nums = re.findall(r"\d{1,2}(?:[\.,]\d)?", txt)
    if not nums:
        return None
    parsed: list[float] = []
    for n in nums:
        try:
            parsed.append(float(n.replace(",", ".")))
        except Exception:
            continue
    if not parsed:
        return None
    if len(parsed) == 1:
        v = parsed[0]
        return (v - 0.2, v + 0.7)
    return (min(parsed), max(parsed) + 0.7)


def _extract_clothing_specs(nome_prodotto: str) -> dict[str, object]:
    """Estrae specifiche base da titoli abbigliamento senza usare AI."""
    nome = str(nome_prodotto or "").strip()
    lower_name = nome.lower()

    brand_match = re.search(r"\b([A-Z][A-Za-z0-9'&-]+)\b", nome)
    size_match = re.search(
        r"\b(?:XXS|XS|S|M|L|XL|XXL|36|37|38|39|40|41|42|43|44|45|46|47|48|49|50)\b",
        nome,
        flags=re.IGNORECASE,
    )

    materiale = None
    for candidate in ("cotone", "poliestere", "lana", "pelle"):
        if candidate in lower_name:
            materiale = candidate
            break

    genere = None
    if any(token in lower_name for token in ("uomo", "men", "man")):
        genere = "uomo"
    elif any(token in lower_name for token in ("donna", "women", "woman")):
        genere = "donna"
    elif any(token in lower_name for token in ("unisex", "kids", "bambino", "bambina")):
        genere = "unisex"

    return {
        "brand": brand_match.group(1) if brand_match else None,
        "taglia": size_match.group(0).upper() if size_match else None,
        "materiale": materiale,
        "genere": genere,
    }


def _extract_shipping_from_text(text: str) -> str:
    """Estrae costo spedizione da testo libero con fallback 'n.d.'."""
    text_lower = text.lower()
    if (
        "spedizione gratuita" in text_lower
        or "consegna gratuita" in text_lower
        or "free shipping" in text_lower
    ):
        return "Gratuita ✅"

    match = re.search(
        r"((?:€|EUR)\s*\d{1,3}(?:[\.\s]\d{3})*(?:[\.,]\d{2})?)\s*(?:di\s+)?spedizione",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"spedizione\s*(?:da|:)?\s*((?:€|EUR)\s*\d{1,3}(?:[\.\s]\d{3})*(?:[\.,]\d{2})?)",
            text,
            flags=re.IGNORECASE,
        )
    if match:
        value = match.group(1).replace("EUR", "€").replace("  ", " ").strip()
        return value
    return "n.d."


def tokenize_query(query: str) -> list[str]:
    """
    Divide la query in token significativi, rimuovendo le stopword italiane.
    Normalizza in lowercase.
    Es: "notebook 14 pollici 16GB RAM" → ["notebook", "14", "pollici", "16gb", "ram"]
    """
    tokens = re.findall(r"[\w]+", query.lower())
    return [t for t in tokens if t not in _STOPWORDS and len(t) > 1]


__all__ = [
    "annotations",
    "base64",
    "json",
    "math",
    "os",
    "random",
    "re",
    "sys",
    "time",
    "ThreadPoolExecutor",
    "as_completed",
    "dataclass",
    "field",
    "Callable",
    "Optional",
    "parse_qs",
    "quote_plus",
    "unquote",
    "urljoin",
    "urlparse",
    "requests",
    "BeautifulSoup",
    "Cerebras",
    "_get_best_model",
    "_cerebras_chat_lib",
    "parse_price",
    "_within_price_range",
    "_normalize_category",
    "_extract_json_object",
    "_extract_gb_values",
    "_extract_ram_gb_values",
    "_extract_storage_gb_values",
    "_extract_inches_values",
    "_parse_target_range",
    "_extract_clothing_specs",
    "_extract_shipping_from_text",
    "tokenize_query",
]

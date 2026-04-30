"""ui: ui/recommendation.py"""
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
    from cerebras_model import (
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
from ui.ai_client import _cerebras_chat_with_retry, _extract_json_object, _get_cerebras_client


def _build_products_payload(offerte: list[Offerta]) -> list[dict[str, Any]]:
    return [
        {
            "nome": offerta.nome,
            "prezzo": round(offerta.prezzo, 2),
            "negozio": offerta.negozio,
            "link": offerta.link,
            "specs": offerta.specs,
        }
        for offerta in sorted(offerte, key=lambda item: item.prezzo)[:10]
    ]


def _build_comparison_payload() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prepara payload bilanciato per raccomandazione AI in modalità confronto."""
    if not st.session_state.get("comparison_mode"):
        return [], []

    cmp_results = st.session_state.get("comparison_results", {})
    if not isinstance(cmp_results, dict) or not cmp_results:
        return [], []

    products_payload: list[dict[str, Any]] = []
    summary_payload: list[dict[str, Any]] = []

    for query, results in cmp_results.items():
        if not isinstance(results, list):
            continue
        ordered = sorted(results, key=lambda item: item.prezzo)
        if ordered:
            summary_payload.append(
                {
                    "query": str(query),
                    "best_name": ordered[0].nome,
                    "best_price": round(ordered[0].prezzo, 2),
                    "best_store": ordered[0].negozio,
                    "count": len(ordered),
                }
            )

        # Bilancia il contesto: massimo 4 offerte per ogni query del confronto.
        for offerta in ordered[:4]:
            products_payload.append(
                {
                    "query": str(query),
                    "nome": offerta.nome,
                    "prezzo": round(offerta.prezzo, 2),
                    "negozio": offerta.negozio,
                    "link": offerta.link,
                    "specs": offerta.specs,
                }
            )

    return products_payload, summary_payload


def _call_final_recommendation(
    cerebras_client: object,
    offerte: list[Offerta],
    preferenze_utente: dict[str, Any],
    messages: list[dict[str, str]],
) -> str:
    # Includi la trascrizione chat pre-ricerca nel contesto per personalizzare meglio il consiglio
    trascrizione = str(preferenze_utente.get("trascrizione", "") or "").strip()
    contesto_utente = json.dumps({
        k: v for k, v in preferenze_utente.items() if k != "messaggi"
    }, ensure_ascii=False)
    context_block = f"PREFERENZE UTENTE: {contesto_utente}\n"
    if trascrizione:
        context_block += f"CONVERSAZIONE PRE-RICERCA (usa per capire tono e priorita dell'utente):\n{trascrizione}\n"
    comparison_products, comparison_summary = _build_comparison_payload()
    products_payload = comparison_products if comparison_products else _build_products_payload(offerte)

    comparison_block = ""
    if comparison_summary:
        comparison_block = (
            "CONTESTO CONFRONTO ATTIVO:\n"
            f"{json.dumps(comparison_summary, ensure_ascii=False)}\n"
            "Nel confronto cita esplicitamente ogni modello richiesto dall'utente "
            "(es. iPhone 16 e iPhone 17), anche se uno risulta meno conveniente.\n"
        )

    system_prompt = (
        "Sei un consulente shopping esperto italiano. Hai questi dati:\n"
        f"{context_block}"
        f"{comparison_block}"
        f"PRODOTTI TROVATI (ordinati per prezzo):\n{json.dumps(products_payload, ensure_ascii=False)}\n"
        "Rispondi in italiano con una raccomandazione motivata e personalizzata sulle esigenze emerse dalla conversazione. "
        "Cita nome e prezzo dei prodotti consigliati, confronta almeno 2-3 parametri rilevanti per l'utente. "
        "Sii diretto e concreto."
    )
    payload = [{"role": "system", "content": system_prompt}] + messages
    return _cerebras_chat_with_retry(cerebras_client, payload, temperature=0.2)



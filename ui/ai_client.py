"""ui: ui/ai_client.py"""
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


def _get_cerebras_api_key() -> str:
    key = ""
    try:
        key = str(st.secrets.get("CEREBRAS_API_KEY", "") or "")
    except Exception:
        key = ""
    if not key.strip():
        key = os.environ.get("CEREBRAS_API_KEY", "")
    return key.strip()


def _is_test_mode() -> bool:
    return os.environ.get("APP_TEST_MODE", "0").strip() == "1"


class _MockCompletionMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _MockCompletionChoice:
    def __init__(self, content: str) -> None:
        self.message = _MockCompletionMessage(content)


class _MockCompletionResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_MockCompletionChoice(content)]


class _MockChatCompletions:
    def create(self, model: str, messages: list[dict[str, str]], temperature: float = 0.0) -> object:
        system_prompt = next((message.get("content", "") for message in messages if message.get("role") == "system"), "")
        user_payload = messages[-1].get("content", "") if messages else ""

        if "Sei un consulente acquisti esperto italiano" in system_prompt or "Sei un assistente shopping esperto italiano" in system_prompt:
            try:
                payload = json.loads(user_payload)
            except Exception:
                payload = {}
            transcript = str(payload.get("trascrizione", "") or "").lower()
            domande_fatte = int(payload.get("domande_fatte", 0) or 0)
            if domande_fatte <= 0:
                content = json.dumps({"domanda": "Qual e il tuo budget massimo?", "pronto": False}, ensure_ascii=False)
            elif domande_fatte == 1:
                content = json.dumps({"domanda": "Preferisci nuovo o usato?", "pronto": False}, ensure_ascii=False)
            else:
                categoria = "tech" if any(token in transcript for token in ("smartphone", "iphone", "telefono", "cellulare", "notebook", "laptop")) else "altro"
                # Estrai filtri_ai dal transcript
                filtri_ai_mock: dict[str, str] = {}
                ram_m = re.search(r"(\d{1,3})\s*gb\s*(?:di\s*)?ram", transcript)
                if ram_m:
                    filtri_ai_mock["ram"] = f"{ram_m.group(1)}gb"
                content = json.dumps(
                    {
                        "pronto": True,
                        "query": "smartphone nuovo" if "smartphone" in transcript else "notebook 14 pollici" if "notebook" in transcript else "prodotto cercato",
                        "prezzo_min": 200,
                        "budget_max": 800,
                        "categoria": categoria,
                        "filtri_ai": filtri_ai_mock,
                    },
                    ensure_ascii=False,
                )
            return _MockCompletionResponse(content)

        if "Sei un consulente shopping esperto" in system_prompt:
            product_names = re.findall(r'"nome":\s*"([^"]+)"', system_prompt)
            has_iphone_16 = any("iphone 16" in name.lower() for name in product_names)
            has_iphone_17 = any("iphone 17" in name.lower() for name in product_names)
            if has_iphone_16 and has_iphone_17:
                content = (
                    "Confronto rapido: iPhone 16 e iPhone 17 sono entrambi validi. "
                    "iPhone 16 conviene di più per prezzo/prestazioni, mentre iPhone 17 offre vantaggi su display e chip. "
                    "Se vuoi restare sotto i 1000€, consiglio iPhone 16; scegli iPhone 17 se vuoi il modello più recente."
                )
            else:
                product_name = product_names[0] if product_names else "Apple iPhone 17 128GB"
                content = (
                    f"Ti consiglio {product_name} a € 799,00. Per uso quotidiano offre il prezzo migliore, uno storage adeguato "
                    "e un equilibrio piu convincente tra display, autonomia e praticita rispetto alle alternative."
                )
            return _MockCompletionResponse(content)

        return _MockCompletionResponse("{}")


class _MockChat:
    def __init__(self) -> None:
        self.completions = _MockChatCompletions()


class _MockCerebrasClient:
    def __init__(self) -> None:
        self.chat = _MockChat()


def _get_cerebras_client(api_key: str) -> Optional[object]:
    if _is_test_mode():
        return _MockCerebrasClient()
    if not api_key or Cerebras is None:
        return None
    try:
        return Cerebras(api_key=api_key)
    except Exception:
        return None


def _cerebras_chat_with_retry(
    client: object,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    max_retries: int = 4,
) -> str:
    """Chiama Cerebras con retry automatico.
    - 404 (modello non trovato): invalida cache, sceglie nuovo modello, riprova.
    - 429 (rate limit): backoff esponenziale fino a max_retries volte.
    """
    if _cerebras_chat_lib is not None:
        completion = _cerebras_chat_lib(
            client=client,
            messages=messages,
            model=None,  # auto-select dal modulo
            max_retries=max_retries,
            base_delay=2.0,
            temperature=temperature,
        )
        content = completion.choices[0].message.content if completion and completion.choices else ""
        return str(content or "").strip()

    # Fallback se il modulo non è disponibile
    last_exc: Optional[BaseException] = None
    for attempt in range(1 + max_retries):
        try:
            _model = _get_best_model(client) if _get_best_model else CEREBRAS_MODEL
            completion = client.chat.completions.create(  # type: ignore[attr-defined]
                model=_model,
                messages=messages,
                temperature=temperature,
            )
            content = completion.choices[0].message.content if completion and completion.choices else ""
            return str(content or "").strip()
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc).lower()
            is_rate_limit = "429" in exc_str or "too_many" in exc_str or "queue" in exc_str
            if is_rate_limit and attempt < max_retries:
                time.sleep(random.uniform(2.0, 3.5))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return ""


def _extract_json_object(raw: str) -> dict[str, Any]:
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



"""
split_app.py — One-shot refactor: spezza app.py nel package ui/.

Esegui:
    python tools/split_app.py

Estrae le 45+ funzioni helper di app.py in moduli ui/{auth,state,ai_client,
sources,presearch,recommendation,test_mode,export,cards,comparison,search}.py
e riscrive app.py mantenendo il top-level orchestration.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "app.py"

# --------------------------------------------------------------------------- #
# Mapping: function/class name → target module                                 #
# --------------------------------------------------------------------------- #
MAP: dict[str, str] = {
    # auth
    "_load_auth_sessions": "ui/auth.py",
    "_save_auth_sessions": "ui/auth.py",
    "_get_client_fingerprint": "ui/auth.py",
    "_is_client_authenticated": "ui/auth.py",
    "_persist_client_auth": "ui/auth.py",
    # state
    "_init_state": "ui/state.py",
    "_queue_price_sync": "ui/state.py",
    "_flush_pending_price_sync": "ui/state.py",
    "_format_price": "ui/state.py",
    "_sync_from_numbers": "ui/state.py",
    "_sync_from_slider": "ui/state.py",
    # sources
    "_status_rows_for_sources": "ui/sources.py",
    "_render_source_status_monitor": "ui/sources.py",
    # ai_client
    "_get_cerebras_api_key": "ui/ai_client.py",
    "_is_test_mode": "ui/ai_client.py",
    "_MockCompletionMessage": "ui/ai_client.py",
    "_MockCompletionChoice": "ui/ai_client.py",
    "_MockCompletionResponse": "ui/ai_client.py",
    "_MockChatCompletions": "ui/ai_client.py",
    "_MockChat": "ui/ai_client.py",
    "_MockCerebrasClient": "ui/ai_client.py",
    "_get_cerebras_client": "ui/ai_client.py",
    "_cerebras_chat_with_retry": "ui/ai_client.py",
    "_extract_json_object": "ui/ai_client.py",
    # presearch
    "_infer_categoria_from_query": "ui/presearch.py",
    "_sanitize_presearch_payload": "ui/presearch.py",
    "_reset_presearch_chat": "ui/presearch.py",
    "_apply_presearch_result": "ui/presearch.py",
    "_presearch_fallback": "ui/presearch.py",
    "_run_presearch_step": "ui/presearch.py",
    # recommendation
    "_build_products_payload": "ui/recommendation.py",
    "_build_comparison_payload": "ui/recommendation.py",
    "_call_final_recommendation": "ui/recommendation.py",
    # test_mode
    "_build_mock_results": "ui/test_mode.py",
    # export
    "_offerte_to_copy_text": "ui/export.py",
    "_offerte_to_csv_bytes": "ui/export.py",
    "_specs_from_name": "ui/export.py",
    "_summarize_specs": "ui/export.py",
    "_offerte_to_records": "ui/export.py",
    # cards
    "_render_offerta_card": "ui/cards.py",
    "_render_results_grid": "ui/cards.py",
    "_render_specs_grid": "ui/cards.py",
    # comparison
    "_extract_comparison_spec_keys": "ui/comparison.py",
    "_spec_value_for_key": "ui/comparison.py",
    "_render_comparison_board": "ui/comparison.py",
    "_render_manual_comparison_matrix": "ui/comparison.py",
    "_run_comparison_search": "ui/comparison.py",
    # search
    "_run_search": "ui/search.py",
}

# Header comune per ogni modulo ui/
COMMON_HEADER = '''\
"""ui: {module_path}"""
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
'''

# Cross-imports tra moduli ui (evitiamo dipendenze circolari pesanti)
LOCAL_IMPORTS: dict[str, str] = {
    "ui/auth.py": "",
    "ui/state.py": "",
    "ui/sources.py": "",
    "ui/ai_client.py": "",
    "ui/presearch.py": (
        "from ui.ai_client import (\n"
        "    _cerebras_chat_with_retry, _extract_json_object,\n"
        "    _get_cerebras_client, _is_test_mode,\n"
        ")\n"
    ),
    "ui/recommendation.py": (
        "from ui.ai_client import _cerebras_chat_with_retry, _extract_json_object, _get_cerebras_client\n"
    ),
    "ui/test_mode.py": "",
    "ui/export.py": "",
    "ui/cards.py": (
        "from ui.export import _specs_from_name, _summarize_specs\n"
        "from ui.state import _format_price\n"
    ),
    "ui/comparison.py": (
        "from ui.cards import _render_offerta_card\n"
        "from ui.export import _specs_from_name\n"
        "from ui.state import _format_price\n"
        "from ui.recommendation import _build_comparison_payload, _call_final_recommendation\n"
    ),
    "ui/search.py": (
        "from ui.cards import _render_offerta_card, _render_results_grid, _render_specs_grid\n"
        "from ui.comparison import _render_comparison_board, _render_manual_comparison_matrix, _run_comparison_search\n"
        "from ui.export import _offerte_to_copy_text, _offerte_to_csv_bytes, _offerte_to_records, _specs_from_name, _summarize_specs\n"
        "from ui.recommendation import _build_products_payload, _build_comparison_payload, _call_final_recommendation\n"
        "from ui.state import _format_price\n"
        "from ui.test_mode import _build_mock_results\n"
        "from ui.sources import _status_rows_for_sources, _render_source_status_monitor\n"
    ),
}


def parse_source() -> tuple[list[str], list[tuple[ast.AST, int, int]]]:
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    nodes: list[tuple[ast.AST, int, int]] = []
    for n in tree.body:
        s = n.lineno
        if hasattr(n, "decorator_list") and n.decorator_list:
            s = n.decorator_list[0].lineno
        e = (n.end_lineno or n.lineno) + 1
        nodes.append((n, s, e))
    return lines, nodes


def block(lines: list[str], s: int, e: int) -> str:
    return "".join(lines[s - 1 : e - 1])


def main() -> None:
    lines, nodes = parse_source()

    # 1) Per ogni modulo target, raccogli funzioni assegnate a esso
    pkg = ROOT / "ui"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text('"""ui: pacchetto Streamlit."""\n', encoding="utf-8")

    by_module: dict[str, list[tuple[str, str]]] = {}
    for n, s, e in nodes:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mod = MAP.get(n.name)
            if mod is None:
                continue
            by_module.setdefault(mod, []).append((n.name, block(lines, s, e)))

    for mod, items in by_module.items():
        out = COMMON_HEADER.format(module_path=mod)
        out += LOCAL_IMPORTS.get(mod, "") + "\n\n"
        for _name, body in items:
            out += body + "\n\n"
        (ROOT / mod).write_text(out, encoding="utf-8")
        print(f"  wrote {mod}: {len(items)} defs")

    # 2) Ricostruisci app.py: tutto il top-level NON-def/class + import dai moduli ui
    extracted_names = set(MAP.keys())
    new_app: list[str] = []

    # Header
    header = '''\
"""app.py — Streamlit Tool per Trova Prezzi.

Le funzioni helper sono nel package `ui/`. Questo file contiene:
- import + page config
- gate auth
- orchestrazione top-level del rendering (presearch, search, comparison)
"""
'''
    new_app.append(header)

    # Inietta gli import dai moduli ui prima del top-level body originale
    ui_imports = '''\
# Helper modulari
from ui.ai_client import (
    _MockCerebrasClient, _MockChat, _MockChatCompletions,
    _MockCompletionChoice, _MockCompletionMessage, _MockCompletionResponse,
    _cerebras_chat_with_retry, _extract_json_object,
    _get_cerebras_api_key, _get_cerebras_client, _is_test_mode,
)
from ui.auth import (
    _get_client_fingerprint, _is_client_authenticated,
    _load_auth_sessions, _persist_client_auth, _save_auth_sessions,
)
from ui.cards import _render_offerta_card, _render_results_grid, _render_specs_grid
from ui.comparison import (
    _extract_comparison_spec_keys, _render_comparison_board,
    _render_manual_comparison_matrix, _run_comparison_search,
    _spec_value_for_key,
)
from ui.export import (
    _offerte_to_copy_text, _offerte_to_csv_bytes, _offerte_to_records,
    _specs_from_name, _summarize_specs,
)
from ui.presearch import (
    _apply_presearch_result, _infer_categoria_from_query,
    _presearch_fallback, _reset_presearch_chat, _run_presearch_step,
    _sanitize_presearch_payload,
)
from ui.recommendation import (
    _build_comparison_payload, _build_products_payload, _call_final_recommendation,
)
from ui.search import _run_search
from ui.sources import _render_source_status_monitor, _status_rows_for_sources
from ui.state import (
    _flush_pending_price_sync, _format_price, _init_state,
    _queue_price_sync, _sync_from_numbers, _sync_from_slider,
)
from ui.test_mode import _build_mock_results

'''

    # Itera tutti i top-level: salta def/class che sono stati estratti
    for n, s, e in nodes:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if n.name in extracted_names:
                continue
        new_app.append(block(lines, s, e))

    # Inserisci ui_imports subito dopo l'ultima Try di import (cioè dopo il primo
    # blocco di import). Strategia semplice: appendi prima della prima riga
    # che contiene 'st.set_page_config'.
    body = "".join(new_app)
    if "st.set_page_config" in body:
        body = body.replace("st.set_page_config", ui_imports + "\nst.set_page_config", 1)
    else:
        body += "\n" + ui_imports

    SRC.write_text(body, encoding="utf-8")
    print(f"  rewrote app.py: {len(body.splitlines())} lines")


if __name__ == "__main__":
    main()

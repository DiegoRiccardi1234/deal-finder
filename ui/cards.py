"""ui: ui/cards.py"""
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
from ui.export import _specs_from_name, _summarize_specs
from ui.state import _format_price


def _render_offerta_card(offerta: Offerta, idx: int, best_price: float = 0) -> str:
    import html as _html
    source_label = offerta.fonte.replace(".it", "").replace(".com", "").upper()
    price_str = _format_price(offerta.prezzo)
    spedizione_raw = offerta.spedizione if offerta.spedizione and offerta.spedizione != "n.d." else ""
    spedizione_html = f"<span class='card-shipping'>{_html.escape(spedizione_raw)}</span>" if spedizione_raw else ""
    title = _html.escape(offerta.nome[:90] + ("\u2026" if len(offerta.nome) > 90 else ""))
    specs_line = _summarize_specs(offerta.specs, offerta.nome)
    specs_html = f"<p class='card-specs'>{_html.escape(specs_line)}</p>" if specs_line else ""
    is_best = best_price > 0 and offerta.prezzo == best_price
    best_badge = "<span class='best-badge'>Miglior Prezzo</span>" if is_best else ""
    img_html = ""
    if getattr(offerta, "immagine", ""):
        img_src = _html.escape(offerta.immagine, quote=True)
        img_html = (
            f"<div class='card-img-wrap'>"
            f"<img src='{img_src}' alt='' loading='lazy' "
            f"onerror=\"this.parentElement.style.display='none'\">"
            f"</div>"
        )
    else:
        img_html = f"<div class='card-img-wrap card-img-placeholder'><span>{source_label[0]}</span></div>"
    negozio_escaped = _html.escape(offerta.negozio)
    link_escaped = _html.escape(offerta.link, quote=True)
    return (
        f"<div class='offerta-card{' offerta-best' if is_best else ''}'>"
        f"{best_badge}"
        f"<span class='source-badge'>{source_label}</span>"
        f"{img_html}"
        f"<div class='card-body'>"
        f"<p class='card-title'>{title}</p>"
        f"<p class='card-price'>{price_str}</p>"
        f"<p class='card-meta'>{negozio_escaped}{spedizione_html}</p>"
        f"{specs_html}"
        f"</div>"
        f"<a class='card-cta' href='{link_escaped}' target='_blank'>Vai all\u2019offerta \u2192</a>"
        f"</div>"
    )


def _render_results_grid(offerte: list[Offerta]) -> None:
    """Renders tutti i risultati come card grid con immagini."""
    if not offerte:
        return
    best_price = min(o.prezzo for o in offerte) if offerte else 0
    cards_html = "".join(
        _render_offerta_card(o, i, best_price=best_price)
        for i, o in enumerate(offerte)
    )
    st.markdown(f"<div class='results-grid'>{cards_html}</div>", unsafe_allow_html=True)


def _render_specs_grid(offerte: list[Offerta]) -> None:
    """Renders una griglia delle specifiche per le offerte con dati specs."""
    # Filtra solo le offerte che hanno specifiche valide
    offerte_con_specs = [o for o in offerte if o.specs and isinstance(o.specs, dict) and any(v not in (None, "", [], {}) for v in o.specs.values())]
    if not offerte_con_specs:
        st.info("\U0001f4cb Nessun dato di specifiche rilevato per i prodotti.")
        return

    st.markdown(
        "<div class='section-heading'><h3>Specs rilevate</h3><p>Arricchimento automatico basato sulla categoria della ricerca.</p></div>",
        unsafe_allow_html=True,
    )
    # Mostra al massimo 6 prodotti nella grid
    preview = offerte_con_specs[:6]
    for start in range(0, len(preview), 2):
        cols = st.columns(2, gap="medium")
        for idx, offerta in enumerate(preview[start:start + 2]):
            cols[idx].markdown(_render_offerta_card(offerta, start + idx), unsafe_allow_html=True)



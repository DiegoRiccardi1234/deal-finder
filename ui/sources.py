"""ui: ui/sources.py"""

from __future__ import annotations

from typing import Any

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

from offerte_tech import Offerta

try:
    from search_history import load_history, save_search as _save_search
except ImportError:

    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None


_FONTE_LABELS: dict[str, str] = {
    "amazon": "Amazon.it",
    "ebay": "eBay.it",
    "vinted": "Vinted.it",
    "euronics": "Euronics.it",
    "unieuro": "Unieuro.it",
    "mediaworld": "MediaWorld.it",
    "wallapop": "Wallapop.it",
    "comet": "Comet.it",
    "expert": "Expert.it",
    "aliexpress": "AliExpress",
}


def _status_rows_for_sources(
    offerte: list[Offerta],
    fonti_backend: list[str],
    log_text: str,
) -> list[dict[str, str]]:
    """Costruisce righe stato fonti per pannello monitor."""
    counts_by_source: dict[str, int] = {}
    for o in offerte:
        fonte = str(o.fonte or "").lower()
        counts_by_source[fonte] = counts_by_source.get(fonte, 0) + 1

    log_lower = str(log_text or "").lower()
    rows: list[dict[str, str]] = []
    for key in fonti_backend:
        label = _FONTE_LABELS.get(key, key.title())
        domain_hint = label.lower().replace(" ", "")
        found = 0
        for fonte_name, c in counts_by_source.items():
            if key in fonte_name or domain_hint.split(".")[0] in fonte_name:
                found += c

        source_error = any(
            token in log_lower
            for token in [
                f"{key} -> errore",
                f"errore {key}",
                f"{key} timeout",
                f"{key} 403",
                f"{key} 429",
            ]
        )

        if source_error:
            status = "BLOCCATA"
            dot_class = "is-error"
            detail = "Errore o blocco"
        elif found > 0:
            status = "ONLINE"
            dot_class = "is-ok"
            detail = f"{found} risultati"
        else:
            status = "IN ATTESA"
            dot_class = "is-warn"
            detail = "0 risultati"

        rows.append(
            {
                "label": label,
                "status": status,
                "detail": detail,
                "dot_class": dot_class,
            }
        )
    return rows


def _render_source_status_monitor(
    offerte: list[Offerta],
    fonti_backend: list[str],
    log_text: str,
) -> None:
    rows = _status_rows_for_sources(offerte, fonti_backend, log_text)
    st.markdown("<div class='status-monitor-card'>", unsafe_allow_html=True)
    st.markdown("<h4>Stato fonti</h4>", unsafe_allow_html=True)
    if not rows:
        st.caption("Nessuna fonte selezionata.")
    for row in rows:
        st.markdown(
            "<div class='status-row'>"
            f"<span class='status-dot {row['dot_class']}'></span>"
            f"<span class='status-label'>{row['label']}</span>"
            f"<span class='status-pill'>{row['status']}</span>"
            f"<span class='status-detail'>{row['detail']}</span>"
            "</div>",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)

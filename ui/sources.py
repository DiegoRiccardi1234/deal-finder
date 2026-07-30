"""ui: ui/sources.py"""

from __future__ import annotations

from typing import Any

import streamlit as st

try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]

from offerte import source_status
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
    "trovaprezzi": "Trovaprezzi.it",
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
    """Costruisce righe stato fonti per pannello monitor.

    Lo stato arriva da `offerte.source_status`, popolato dagli scraper durante la
    ricerca. Prima veniva *dedotto* cercando nel testo del log stringhe come
    `f"{key} -> errore"` o `f"{key} 403"`: un formato che nessuno stampava, quindi
    lo stato "BLOCCATA" era irraggiungibile e una fonte che ci aveva rifiutati
    risultava indistinguibile da una senza risultati.

    `log_text` resta nella firma per retrocompatibilità con i chiamanti, ma non è
    più la fonte di verità.
    """
    counts_by_source: dict[str, int] = {}
    for o in offerte:
        fonte = str(o.fonte or "").lower()
        counts_by_source[fonte] = counts_by_source.get(fonte, 0) + 1

    statuses = source_status.snapshot()
    rows: list[dict[str, str]] = []
    for key in fonti_backend:
        label = _FONTE_LABELS.get(key, key.title())
        domain_hint = label.lower().replace(" ", "")
        found = 0
        for fonte_name, c in counts_by_source.items():
            if key in fonte_name or domain_hint.split(".")[0] in fonte_name:
                found += c

        st_entry = statuses.get(key)
        state = st_entry.state if st_entry else None

        if state == source_status.BLOCKED:
            status, dot_class = "BLOCCATA", "is-error"
            detail = st_entry.describe()
        elif state == source_status.ERROR:
            status, dot_class = "ERRORE", "is-error"
            detail = st_entry.describe()
        elif state == source_status.DISABLED:
            status, dot_class = "DISATTIVATA", "is-warn"
            detail = st_entry.describe()
        elif found > 0:
            status, dot_class = "ONLINE", "is-ok"
            detail = f"{found} risultati"
        elif state == source_status.EMPTY:
            status, dot_class = "ONLINE", "is-ok"
            detail = "nessun risultato per questa ricerca"
        else:
            status, dot_class = "IN ATTESA", "is-warn"
            detail = "in corso"

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

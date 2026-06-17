"""ui: ui/search.py"""

from __future__ import annotations

import contextlib
import io
import os
import time
from typing import Any

import streamlit as st

try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]

from offerte_tech import Offerta, cerca_offerte

try:
    from search_history import load_history, save_search as _save_search
except ImportError:

    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None


try:
    import price_history
    from offerte import cache as _disk_cache
    from dataclasses import asdict as _asdict
except Exception:
    price_history = None  # type: ignore[assignment]
    _disk_cache = None  # type: ignore[assignment]
from ui.ai_client import _is_test_mode
from ui.presearch import _infer_categoria_from_query
from ui.test_mode import _build_mock_results


def _run_search(
    *,
    query: str,
    prezzo_min: int,
    budget_max: int,
    top_n: int,
    condizione: str,
    fonti_backend: list[str],
    cerebras_client: object | None,
) -> None:
    st.session_state["ricerca_effettuata"] = True
    st.session_state["ultima_query"] = query
    st.session_state["_query_prefilled"] = query
    st.session_state["ultimo_prezzo_min"] = int(prezzo_min)
    st.session_state["ultimo_prezzo_max"] = int(budget_max)
    st.session_state["ultimo_top_n"] = int(top_n)
    st.session_state["condizione"] = condizione
    st.session_state["final_chat_messages"] = []
    st.session_state["auto_recommend_tried"] = False
    st.session_state["risultati"] = []
    st.session_state["log_ricerca"] = ""

    categoria = str(st.session_state.get("categoria", "altro") or "altro")
    if not categoria:
        categoria = _infer_categoria_from_query(query)

    try:
        ebay_app_id = str(st.secrets.get("EBAY_APP_ID", "") or "")
    except Exception:
        ebay_app_id = ""
    try:
        ebay_cert_id = str(st.secrets.get("EBAY_CERT_ID", "") or "")
    except Exception:
        ebay_cert_id = ""
    ebay_app_id = ebay_app_id or os.environ.get("EBAY_APP_ID", "")
    ebay_cert_id = ebay_cert_id or os.environ.get("EBAY_CERT_ID", "")

    log_buffer = io.StringIO()
    if _is_test_mode():
        risultati = _build_mock_results(query, categoria, prezzo_min, budget_max)
        st.session_state["risultati"] = risultati
        st.session_state["log_ricerca"] = (
            "[mock-mode] risultati generati localmente per la suite UI"
        )
        st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
        return

    # ── Cache: stessa ricerca entro 5 minuti → riusa i risultati ──────────────
    _cache_key = (
        query.strip().lower(),
        int(prezzo_min),
        int(budget_max),
        condizione,
        tuple(sorted(fonti_backend)),
    )
    _cache = st.session_state.get("_search_cache", {})
    if _cache.get("key") == _cache_key and (time.time() - float(_cache.get("ts", 0))) < 300:
        st.session_state["risultati"] = _cache["risultati"]
        st.session_state["log_ricerca"] = _cache.get("log", "")
        st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
        st.toast("⚡ Risultati dalla cache (< 5 min) — clicca di nuovo Cerca per aggiornare.")
        return

    # ── Cache su disco: persiste tra sessioni/riavvii ─────────────────────────
    _disk_key = (
        _disk_cache.make_cache_key(query, prezzo_min, budget_max, condizione, fonti_backend)
        if _disk_cache
        else None
    )
    if _disk_key:
        _disk_hit = _disk_cache.read(_disk_key, ttl=300)
        if _disk_hit is not None:
            try:
                st.session_state["risultati"] = [Offerta(**_d) for _d in _disk_hit]
                st.session_state["log_ricerca"] = "♻️ Risultati dalla cache su disco (< 5 min)."
                st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
                st.toast("♻️ Risultati dalla cache su disco — clicca di nuovo Cerca per aggiornare.")
                return
            except Exception:
                pass

    # Reset filtri tabella per la nuova ricerca
    st.session_state["filtro_fonti_tabella"] = []
    st.session_state["filtro_prezzo_range_tabella"] = None
    st.session_state["filtro_condizione_tabella"] = "tutti"
    st.session_state["comparatore_selezione"] = []
    # Reset chat AI post-ricerca e flag auto top-3 ad ogni nuova ricerca
    st.session_state["final_chat_messages"] = []
    st.session_state["auto_recommend_tried"] = False

    try:
        with st.status(
            "⏳ Ricerca in corso sulle fonti selezionate...", expanded=True
        ) as search_status:

            def on_source_done(source_label: str, count: int) -> None:
                if count > 0:
                    st.write(
                        f"✅ **{source_label}** → {count} {'risultato' if count == 1 else 'risultati'}"
                    )
                elif count == -2:
                    st.write(f"⚙️ **{source_label}** → non configurato (chiavi API mancanti)")
                elif count == -1:
                    st.write(f"❌ **{source_label}** → errore inatteso durante lo scraping")
                else:
                    st.write(f"⚪ **{source_label}** → nessun risultato nel range selezionato")

            try:
                with contextlib.redirect_stdout(log_buffer):
                    risultati = cerca_offerte(
                        query=query,
                        budget_max=float(budget_max),
                        prezzo_min=float(prezzo_min),
                        filtri_ai=st.session_state.get("filtri_ai", {}),
                        top_n=int(top_n),
                        export_csv=False,
                        condizione=condizione,
                        fonti=fonti_backend,
                        categoria=categoria,
                        cerebras_client=cerebras_client,
                        app_id=ebay_app_id,
                        cert_id=ebay_cert_id,
                        progress_callback=on_source_done,
                    )
                n = len(risultati)
                search_status.update(
                    label=f"✅ Ricerca completata — {n} {'offerta trovata' if n == 1 else 'offerte trovate'}",
                    state="complete",
                    expanded=False,
                )
            except Exception:
                search_status.update(label="❌ Errore durante la ricerca", state="error")
                raise

        st.session_state["risultati"] = risultati
        st.session_state["log_ricerca"] = log_buffer.getvalue()
        st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
        # Salva in cache per 5 minuti
        st.session_state["_search_cache"] = {
            "key": _cache_key,
            "ts": time.time(),
            "risultati": risultati,
            "log": log_buffer.getvalue(),
        }
        _save_search(
            query=query,
            budget_min=prezzo_min,
            budget_max=budget_max,
            condizione=condizione,
            fonti=fonti_backend,
            results_count=len(risultati),
        )
        # Cache su disco + storico prezzo minimo (best-effort, non bloccante)
        if _disk_key and _disk_cache:
            try:
                _disk_cache.write(_disk_key, [_asdict(o) for o in risultati])
            except Exception:
                pass
        if price_history is not None:
            try:
                _min_price = min((o.prezzo for o in risultati if o.prezzo), default=None)
                if _min_price is not None:
                    price_history.record(query, _min_price)
            except Exception:
                pass
    except Exception as exc:
        st.session_state["log_ricerca"] = log_buffer.getvalue()
        st.error(
            f"❌ Si e verificato un errore durante la ricerca:\n\n```\n{exc}\n```\n\n"
            "Verifica la connessione internet e riprova."
        )

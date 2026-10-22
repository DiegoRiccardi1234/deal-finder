"""ui: ui/search.py"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import time
from dataclasses import asdict
from typing import Any

import streamlit as st

try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]

from offerte_tech import Offerta, cerca_offerte
from offerte import source_status
from offerte.offer_warnings import get_offer_warnings
from offerte.source_registry import SOURCE_REGISTRY

SNAPSHOT_SCHEMA = 3


def make_search_snapshot(
    offers: list[Offerta],
    sources: list[str],
    params: dict,
    log: str = "",
    *,
    searched_at: float | None = None,
) -> dict:
    """Payload JSON autosufficiente, utilizzabile anche per ogni confronto."""
    return {
        "schema": SNAPSHOT_SCHEMA,
        "offers": [asdict(offer) for offer in offers],
        "statuses": {key: asdict(status) for key, status in source_status.snapshot().items()},
        "searched_at": time.time() if searched_at is None else searched_at,
        "sources": list(sources),
        "params": copy.deepcopy(params),
        "log": log,
    }


def _restore_snapshot(snapshot: object, origin: str) -> bool:
    if not isinstance(snapshot, dict) or snapshot.get("schema") != SNAPSHOT_SCHEMA:
        return False
    try:
        offers = [Offerta(**item) for item in snapshot["offers"]]
        if not isinstance(snapshot["statuses"], dict) or not isinstance(snapshot["sources"], list):
            return False
        if not isinstance(snapshot["searched_at"], (int, float)):
            return False
        for status in snapshot["statuses"].values():
            source_status.SourceStatus(**status)
    except (KeyError, TypeError, ValueError):
        return False
    st.session_state["risultati"] = offers
    st.session_state["log_ricerca"] = snapshot.get("log", "")
    st.session_state["search_snapshot"] = copy.deepcopy(snapshot)
    st.session_state["search_cache_origin"] = origin
    return True


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
    force_refresh: bool = False,
    categoria: str | None = None,
    filtri_ai: dict[str, Any] | None = None,
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
    st.session_state["search_snapshot"] = {}
    st.session_state["search_cache_origin"] = ""
    st.session_state["prezzo_nuovo_minimo"] = False
    st.session_state["prezzo_minimo_prec"] = None
    st.session_state["prezzo_minimo_corrente"] = None
    # Anche una ricerca in cache deve liberarsi dei filtri dei vecchi risultati.
    st.session_state["filtro_fonti_tabella"] = []
    st.session_state["filtro_prezzo_range_tabella"] = None
    st.session_state["filtro_condizione_tabella"] = "tutti"
    st.session_state["comparatore_selezione"] = []
    st.session_state["watchlist_add_selezione"] = []

    categoria = categoria or str(st.session_state.get("categoria", "altro") or "altro")
    if categoria == "altro":
        categoria = _infer_categoria_from_query(query)
    filtri_ai = copy.deepcopy(
        st.session_state.get("filtri_ai", {}) if filtri_ai is None else filtri_ai
    )
    st.session_state["filtri_ai_ultima_ricerca"] = copy.deepcopy(filtri_ai)
    st.session_state["_last_search_params"] = {
        "query": query,
        "prezzo_min": prezzo_min,
        "budget_max": budget_max,
        "top_n": top_n,
        "condizione": condizione,
        "fonti_backend": list(fonti_backend),
        "categoria": categoria,
        "filtri_ai": copy.deepcopy(filtri_ai),
    }

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
        source_status.reset()
        for key in fonti_backend:
            source_status.report_ok(key, sum(key in offer.fonte for offer in risultati))
        st.session_state["search_snapshot"] = make_search_snapshot(
            risultati,
            fonti_backend,
            st.session_state["_last_search_params"],
            st.session_state["log_ricerca"],
        )
        st.session_state["search_cache_origin"] = "mock"
        return

    # ── Cache: stessa ricerca entro 5 minuti → riusa i risultati ──────────────
    _cache_key = (
        SNAPSHOT_SCHEMA,
        query.strip().lower(),
        int(prezzo_min),
        int(budget_max),
        condizione,
        tuple(sorted(set(fonti_backend))),
        int(top_n),
        categoria,
        json.dumps(filtri_ai, sort_keys=True, ensure_ascii=False),
        cerebras_client is not None,
    )
    _cache = st.session_state.get("_search_cache", {})
    if (
        not force_refresh
        and _cache.get("key") == _cache_key
        and (time.time() - float(_cache.get("ts", 0))) < 300
    ):
        if _restore_snapshot(_cache.get("snapshot"), "session"):
            st.toast(
                "⚡ Risultati dalla cache (< 5 min). Usa Aggiorna offerte per una nuova ricerca."
            )
            return

    # ── Cache su disco: persiste tra sessioni/riavvii ─────────────────────────
    _disk_key = (
        _disk_cache.make_cache_key(
            query,
            prezzo_min,
            budget_max,
            condizione,
            fonti_backend,
            top_n=int(top_n),
            categoria=categoria,
            filtri_ai=filtri_ai,
            ai_enabled=cerebras_client is not None,
        )
        if _disk_cache
        else None
    )
    if _disk_key and not force_refresh:
        _disk_hit = _disk_cache.read(_disk_key, ttl=300)
        if _restore_snapshot(_disk_hit, "disk"):
            st.toast("♻️ Risultati dalla cache su disco. Usa Aggiorna offerte per aggiornarli.")
            return

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
                    key = next(
                        (k for k, info in SOURCE_REGISTRY.items() if info.label == source_label), ""
                    )
                    status = source_status.get(key)
                    st.write(
                        f"⚙️ **{source_label}** → {status.describe() if status else 'fonte esclusa'}"
                    )
                elif count == -1:
                    st.write(f"❌ **{source_label}** → errore inatteso durante lo scraping")
                else:
                    st.write(f"⚪ **{source_label}** → nessun risultato nel range selezionato")

            try:
                source_status.reset()
                with contextlib.redirect_stdout(log_buffer):
                    risultati = cerca_offerte(
                        query=query,
                        budget_max=float(budget_max),
                        prezzo_min=float(prezzo_min),
                        filtri_ai=filtri_ai,
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
        snapshot = make_search_snapshot(
            risultati, fonti_backend, st.session_state["_last_search_params"], log_buffer.getvalue()
        )
        st.session_state["search_snapshot"] = snapshot
        st.session_state["search_cache_origin"] = "live"
        # Salva in cache per 5 minuti
        st.session_state["_search_cache"] = {
            "key": _cache_key,
            "ts": time.time(),
            "snapshot": copy.deepcopy(snapshot),
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
                _disk_cache.write(_disk_key, snapshot)
            except Exception:
                pass
        if price_history is not None:
            try:
                _min_price = min(
                    (o.prezzo for o in risultati if o.prezzo and not get_offer_warnings(o.nome)),
                    default=None,
                )
                if _min_price is not None:
                    # Rileva il nuovo minimo PRIMA di registrarlo (altrimenti
                    # sarebbe già nello storico e non risulterebbe più "nuovo").
                    _prev_low = price_history.lowest_ever(query)
                    st.session_state["prezzo_nuovo_minimo"] = _prev_low is not None and float(
                        _min_price
                    ) < float(_prev_low)
                    st.session_state["prezzo_minimo_prec"] = _prev_low
                    st.session_state["prezzo_minimo_corrente"] = float(_min_price)
                    price_history.record(query, _min_price)
            except Exception:
                pass
    except Exception as exc:
        st.session_state["log_ricerca"] = log_buffer.getvalue()
        st.session_state["search_snapshot"] = make_search_snapshot(
            [], fonti_backend, st.session_state["_last_search_params"], log_buffer.getvalue()
        )
        st.session_state["search_cache_origin"] = "live"
        st.error(
            f"❌ Si e verificato un errore durante la ricerca:\n\n```\n{exc}\n```\n\n"
            "Verifica la connessione internet e riprova."
        )

"""app.py — Streamlit Tool per Deal Finder.

Le funzioni helper sono nel package `ui/`. Questo file contiene:
- import + page config
- gate auth
- orchestrazione top-level del rendering (presearch, search, comparison)
"""

import os
import re
import time
from typing import Any
import streamlit as st
from _shared import load_css, render_nav

try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]
try:
    from offerte_tech import Offerta  # import-availability guard per offerte_tech
except ImportError as _e:
    st.error(
        f"❌ Impossibile importare offerte_tech.py: {_e}\n\n"
        "Assicurati che offerte_tech.py si trovi nella stessa cartella di app.py "
        "e che le dipendenze siano installate correttamente."
    )
    st.stop()
try:
    from search_history import load_history, save_search as _save_search
except ImportError:

    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None


# Helper modulari
from ui.ai_client import (
    _get_ai_api_key,
    _get_ai_client,
)
from ui.auth import (
    _get_client_fingerprint,
    _is_client_authenticated,
    _persist_client_auth,
)
from ui.cards import _render_results_grid, _render_specs_grid

try:
    import watchlist
    import price_history
except Exception:
    watchlist = None  # type: ignore[assignment]
    price_history = None  # type: ignore[assignment]
from ui.comparison import (
    _render_comparison_board,
    _render_manual_comparison_matrix,
    _run_comparison_search,
)
from ui.export import (
    _offerte_to_copy_text,
)
from ui.presearch import (
    _reset_presearch_chat,
    _run_presearch_step,
)
from ui.recommendation import (
    _call_final_recommendation,
)
from ui.search import _run_search
from ui.sources import _render_source_status_monitor
from ui.state import (
    _flush_pending_price_sync,
    _format_price,
    _init_state,
    _queue_price_sync,
    _sync_from_numbers,
    _sync_from_slider,
)


st.set_page_config(
    page_title="Deal Finder",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)
_theme_mode = str(st.session_state.get("ui_theme", "light") or "light").strip().lower()
if _theme_mode not in {"light", "dark"}:
    _theme_mode = "light"
load_css(theme_mode=_theme_mode)
try:
    _APP_PASSWORD = st.secrets.get("APP_PASSWORD", "") if hasattr(st, "secrets") else ""
except Exception:
    _APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
_APP_TEST_MODE = os.environ.get("APP_TEST_MODE", "0").strip() == "1"
if _APP_PASSWORD and not _APP_TEST_MODE:
    _now = time.time()
    _fingerprint = _get_client_fingerprint()
    _session_ok = bool(
        st.session_state.get("_authenticated")
        and (_now - float(st.session_state.get("_auth_time", 0))) < 3600
    )
    _persistent_ok = _is_client_authenticated(_fingerprint, _now)
    _is_valid = _session_ok or _persistent_ok

    if _persistent_ok and not _session_ok:
        st.session_state["_authenticated"] = True
        st.session_state["_auth_time"] = _now

    if not _is_valid:
        st.session_state["_authenticated"] = False
        _lcol, _mcol, _rcol = st.columns([1, 1.2, 1])
        with _mcol:
            st.markdown(
                "<div class='auth-gate-card'>"
                "<p class='auth-kicker'>Accesso riservato</p>"
                "<h2>Deal Finder</h2>"
                "<p class='auth-sub'>Inserisci la password per aprire la dashboard.</p>",
                unsafe_allow_html=True,
            )
            _pwd = st.text_input(
                "Password", type="password", placeholder="Password...", label_visibility="collapsed"
            )
            if st.button("Accedi", use_container_width=True, type="primary"):
                if _pwd == _APP_PASSWORD:
                    st.session_state["_authenticated"] = True
                    st.session_state["_auth_time"] = _now
                    _persist_client_auth(_fingerprint, _now, ttl_seconds=3600)
                    st.rerun()
                else:
                    st.error("Password errata.")
            st.markdown("</div>", unsafe_allow_html=True)
        st.stop()
render_nav(active_page="tool")
_init_state()
_get_ai_api_key()  # bootstrap secret provider → env var
# Selettore provider AI: solo tra quelli con API key configurata
from offerte import providers as _providers

_configured_ai = _providers.configured_providers()
if _configured_ai:
    _cur_ai = _providers.active_provider()
    if _cur_ai not in _configured_ai:
        _cur_ai = _configured_ai[0]
    _sel_ai = st.sidebar.selectbox(
        "🧠 Provider AI",
        _configured_ai,
        index=_configured_ai.index(_cur_ai),
        format_func=lambda p: _providers.PROVIDERS[p].label,
        key="ai_provider_sel",
    )
    if _sel_ai != _providers.active_provider():
        os.environ["AI_PROVIDER"] = _sel_ai
        from offerte.ai import invalidate_model

        invalidate_model()
api_key = _get_ai_api_key()
cerebras_client = _get_ai_client(api_key)
if kb_manager is not None:
    kb_manager.init_kb_on_startup(api_key)
_presearch_done = st.session_state.get("presearch_ready", False)
query_input: str = ""
top_n_input: int = int(st.session_state.get("ultimo_top_n", 10))
condizione: str = st.session_state.get("condizione", "tutti")
_fonti_options = [
    "Amazon",
    "eBay",
    "Vinted",
    "Euronics",
    "MediaWorld",
    "Unieuro",
    "Wallapop",
    "Comet",
    "Expert",
    "AliExpress",
]
_fonti_def = [
    "Amazon",
    "eBay",
    "Vinted",
    "Euronics",
    "MediaWorld",
    "Unieuro",
    "Wallapop",
    "Comet",
    "Expert",
]
fonti_selezionate: list[str] = list(st.session_state.get("fonti_selezionate", _fonti_def))
_fonti_map = {
    "Amazon": "amazon",
    "eBay": "ebay",
    "Vinted": "vinted",
    "Euronics": "euronics",
    "Unieuro": "unieuro",
    "MediaWorld": "mediaworld",
    "Wallapop": "wallapop",
    "Comet": "comet",
    "Expert": "expert",
    "AliExpress": "aliexpress",
}
fonti_backend: list[str] = [_fonti_map[f] for f in fonti_selezionate if f in _fonti_map]
avvia_ricerca: bool = False
_flush_pending_price_sync()
_history = load_history()
if _history:
    with st.expander("Ricerche recenti", expanded=False):
        for entry in _history[:8]:
            q = str(entry.get("query", "") or "")
            if not q:
                continue
            bmax = entry.get("budget_max", "")
            label = f"{q} · {bmax}€" if str(bmax).strip() else q
            safe_q = re.sub(r"[^a-zA-Z0-9_-]", "_", q[:20])
            if st.button(
                label,
                key=f"hist_{safe_q}_{entry.get('timestamp', '')}",
                help=(
                    f"Ricerca del {entry.get('timestamp', '')} — "
                    f"{entry.get('results_count', 0)} risultati"
                ),
            ):
                st.session_state["query_input"] = q
                st.session_state["budget_max_input"] = int(bmax or 800)
                st.session_state["presearch_ready"] = True
                st.session_state["query_ottimizzata"] = q
                _queue_price_sync(int(entry.get("budget_min", 0)), int(bmax or 800))
                st.rerun()
if not _presearch_done:
    # Unica sorgente delle fonti mostrate, così il conteggio nel testo sotto non
    # può divergere dai chip (diceva "7 siti" mentre i chip erano 9).
    sources = [
        "Amazon",
        "eBay",
        "Trovaprezzi",
        "Vinted",
        "Euronics",
        "Unieuro",
        "MediaWorld",
        "Wallapop",
        "Comet",
        "Expert",
    ]
    st.markdown(
        "<div class='source-strip'><span class='source-strip-label'>Monitoraggio in tempo reale su</span>"
        + "".join(f"<span>{s}</span>" for s in sources)
        + "</div>",
        unsafe_allow_html=True,
    )
st.markdown("<span id='sezione-offerte'></span>", unsafe_allow_html=True)
st.markdown("<div class='section-card'>", unsafe_allow_html=True)
if not _presearch_done:
    # ══════════════════════════════ STATO 1: Chat attiva ═════════════════
    _chat_hdr = st.columns([6, 1])
    with _chat_hdr[0]:
        st.markdown(
            "<div class='section-heading'><h3>Trova le migliori offerte</h3>"
            "<p>Descrivi cosa cerchi: la AI genera query ottimizzata, budget e filtri tecnici. "
            f"Dettagli completi al primo messaggio? Avviamo subito lo scraping su {len(sources)} siti.</p></div>",
            unsafe_allow_html=True,
        )
    with _chat_hdr[1]:
        st.markdown("<div style='padding-top:0.6rem'>", unsafe_allow_html=True)
        st.button("Ricomincia", on_click=_reset_presearch_chat, help="Ricomincia la chat")
        st.markdown("</div>", unsafe_allow_html=True)

    if cerebras_client is None:
        st.info(
            "💡 Per la chat assistita imposta CEREBRAS_API_KEY in secrets o variabile ambiente."
        )

    c_left, c_mid, c_right = st.columns([1, 6, 1])
    with c_mid:
        for _msg in st.session_state.get("presearch_messages", []):
            _role = "assistant" if _msg.get("role") == "assistant" else "user"
            with st.chat_message(_role):
                st.write(_msg.get("content", ""))

        # Chip esempio: solo al primo messaggio (chat ancora vuota lato utente)
        if len(st.session_state.get("presearch_messages", [])) <= 1:
            _examples = [
                'notebook 14" 16GB sotto 800€',
                "felpa Nike taglia M",
                "iPhone usato 300-500€",
            ]
            _chip_cols = st.columns(len(_examples))
            for _i, _ex in enumerate(_examples):
                if _chip_cols[_i].button(_ex, key=f"presearch_chip_{_i}", use_container_width=True):
                    _run_presearch_step(_ex, api_key)
                    st.rerun()

    st.divider()

    with st.expander("🔧 Cerca senza chat (inserimento manuale)", expanded=False):
        st.caption(
            "Per risultati migliori usa la chat sopra. Qui puoi fare una ricerca diretta con query breve (3–6 parole)."
        )
        query_input = st.text_input(
            "Query prodotto",
            placeholder="es. notebook 14 pollici",
            value="",
            key="manual_query_input",
            help="Mantieni la query a 3-6 parole chiave. NO frasi, NO budget.",
        ).strip()
        if query_input:
            _pc = st.columns(2, gap="medium")
            with _pc[0]:
                st.number_input(
                    "Prezzo minimo (€)",
                    min_value=0,
                    max_value=5000,
                    step=10,
                    key="price_min_input",
                    on_change=_sync_from_numbers,
                )
            with _pc[1]:
                st.number_input(
                    "Budget massimo (€)",
                    min_value=0,
                    max_value=5000,
                    step=10,
                    key="budget_max_input",
                    on_change=_sync_from_numbers,
                )
            st.slider(
                "Range prezzo",
                min_value=0,
                max_value=5000,
                step=10,
                key="price_range_slider",
                on_change=_sync_from_slider,
            )
            _lc = st.columns([1, 1.15], gap="medium")
            with _lc[0]:
                top_n_input = st.number_input(
                    "Risultati",
                    min_value=1,
                    max_value=50,
                    value=int(st.session_state.get("ultimo_top_n", 10)),
                    step=1,
                )
            with _lc[1]:
                _cond_m = st.radio(
                    "Condizione",
                    ["Tutti", "Nuovo", "Usato"],
                    horizontal=True,
                    index={"tutti": 0, "nuovo": 1, "usato": 2}.get(
                        st.session_state.get("condizione", "tutti"), 0
                    ),
                )
                condizione = _cond_m.lower()
            fonti_selezionate = st.multiselect(
                "Fonti",
                _fonti_options,
                default=st.session_state.get("fonti_selezionate", _fonti_def),
                key="fonti_ms_manual",
            )
            st.session_state["fonti_selezionate"] = fonti_selezionate
            fonti_backend = [_fonti_map[f] for f in fonti_selezionate if f in _fonti_map]
            avvia_ricerca = st.button(
                "🔍 Cerca offerte", type="primary", width="stretch", key="btn_manual_cerca"
            )

else:
    # ══════════════════════════ STATO 2: Presearch completata ════════════
    _q_opt = st.session_state.get("query_ottimizzata", "")
    _bmin_p = st.session_state.get("prezzo_min", 0)
    _bmax_p = st.session_state.get("budget_max", 800)
    _cond_p = st.session_state.get("condizione", "tutti")
    _filtri_p = st.session_state.get("filtri_ai", {})
    _filtri_str = " · ".join(f"{k}: {v}" for k, v in _filtri_p.items()) if _filtri_p else ""
    # In comparison mode, mostra tutte le query
    if st.session_state.get("comparison_mode"):
        _cmp_qs = st.session_state.get("comparison_queries", [])
        _q_label = " vs ".join(_cmp_qs) if _cmp_qs else _q_opt
    else:
        _q_label = _q_opt
    _parts = [f"**{_q_label}**", f"{_bmin_p}€–{_bmax_p}€", _cond_p]
    if _filtri_str:
        _parts.append(_filtri_str)

    _hcol1, _hcol2 = st.columns([5, 1])
    with _hcol1:
        st.markdown(
            "<div class='section-heading'><h3>Trova le migliori offerte</h3>"
            f"<p>Ricerca pronta: {' · '.join(_parts)}</p></div>",
            unsafe_allow_html=True,
        )
    with _hcol2:
        st.write("")
        st.button(
            "✏️ Modifica",
            on_click=_reset_presearch_chat,
            help="Torna alla chat per cambiare le preferenze",
        )

    query_input = _q_opt

    price_cols = st.columns(2, gap="medium")
    with price_cols[0]:
        st.number_input(
            "Prezzo minimo (€)",
            min_value=0,
            max_value=5000,
            step=10,
            key="price_min_input",
            on_change=_sync_from_numbers,
        )
    with price_cols[1]:
        st.number_input(
            "Budget massimo (€)",
            min_value=0,
            max_value=5000,
            step=10,
            key="budget_max_input",
            on_change=_sync_from_numbers,
        )

    st.slider(
        "Range prezzo sincronizzato",
        min_value=0,
        max_value=5000,
        step=10,
        key="price_range_slider",
        on_change=_sync_from_slider,
    )

    lower_cols = st.columns([1, 1.15], gap="medium")
    with lower_cols[0]:
        top_n_input = st.number_input(
            "Numero risultati",
            min_value=1,
            max_value=50,
            value=int(st.session_state.get("ultimo_top_n", 10)),
            step=1,
        )
    with lower_cols[1]:
        condizione_ui = st.radio(
            "Condizione",
            ["Tutti", "Nuovo", "Usato"],
            horizontal=True,
            index={"tutti": 0, "nuovo": 1, "usato": 2}.get(
                st.session_state.get("condizione", "tutti"), 0
            ),
        )
        condizione = condizione_ui.lower()

    fonti_selezionate = st.multiselect(
        "Fonti da consultare",
        _fonti_options,
        default=st.session_state.get("fonti_selezionate", _fonti_def),
    )
    st.session_state["fonti_selezionate"] = fonti_selezionate
    fonti_backend = [_fonti_map[f] for f in fonti_selezionate if f in _fonti_map]

    avvia_ricerca = st.button(
        "🔍 Cerca offerte", type="primary", width="stretch", disabled=not query_input
    )
    st.caption(
        "💬 Puoi affinare la query o il budget scrivendo nell'input in basso prima di cercare."
    )
    if kb_manager is not None:
        st.caption(f"🧠 {kb_manager.get_status()}")
if not st.session_state.get("ricerca_effettuata", False):
    _pre_placeholder = (
        "Descrivi prodotto, uso, vincoli e preferenze"
        if not _presearch_done
        else "Vuoi affinare la query o il budget? Scrivi qui..."
    )
    _c1, _c2, _c3 = st.columns([1, 6, 1])
    with _c2:
        presearch_input = st.chat_input(_pre_placeholder, key="presearch_input")
        if presearch_input:
            _run_presearch_step(presearch_input, api_key)
            st.rerun()
st.markdown("</div>", unsafe_allow_html=True)
search_triggered = avvia_ricerca or bool(st.session_state.pop("run_ai_query", False))
if search_triggered:
    current_min = int(st.session_state.get("price_min_input", 0) or 0)
    current_max = int(st.session_state.get("budget_max_input", 800) or 800)

    # Modalità confronto (es. "iphone 15 vs iphone 16")
    if st.session_state.get("comparison_mode") and st.session_state.get("comparison_queries"):
        _run_comparison_search(
            queries=st.session_state["comparison_queries"],
            prezzo_min=current_min,
            budget_max=current_max,
            top_n=int(top_n_input),
            condizione=condizione,
            fonti_backend=fonti_backend,
            cerebras_client=cerebras_client,
        )
    else:
        # Ricerca normale
        _opt_q = st.session_state.get("query_ottimizzata", "").strip()
        if _opt_q:
            current_query = _opt_q
        else:
            _raw_words = query_input.split()
            if len(_raw_words) > 7:
                current_query = " ".join(_raw_words[:7])
                st.info(f"💡 Query semplificata per la ricerca: **{current_query}**")
            else:
                current_query = query_input
        if not current_query:
            st.warning("⚠️ Inserisci o genera una query prima di procedere.")
        else:
            _run_search(
                query=current_query,
                prezzo_min=current_min,
                budget_max=current_max,
                top_n=int(top_n_input),
                condizione=condizione,
                fonti_backend=fonti_backend,
                cerebras_client=cerebras_client,
            )
if st.session_state.get("ricerca_effettuata", False):
    st.write("")
    st.markdown("<span id='sezione-confronta'></span>", unsafe_allow_html=True)
    st.markdown("<div class='section-card'>", unsafe_allow_html=True)

    # ── Modalità confronto: comparison board (sopra la tabella normale) ──
    if st.session_state.get("comparison_mode") and st.session_state.get("comparison_results"):
        _cmp_results: dict[str, list[Offerta]] = st.session_state["comparison_results"]
        _render_comparison_board(_cmp_results)

        st.divider()

    st.markdown(
        "<div class='section-heading'><h3>Risultati</h3><p>Tabella ordinabile, log catturato dalla ricerca e dettaglio specs quando disponibile.</p></div>",
        unsafe_allow_html=True,
    )

    if st.session_state.get("log_ricerca", ""):
        with st.expander("Log ricerca", expanded=False):
            st.code(st.session_state.get("log_ricerca", ""), language=None)

    offerte: list[Offerta] = st.session_state.get("risultati", [])
    if not offerte:
        st.warning(
            "⚠️ Nessuna offerta trovata. Prova ad allargare il range prezzo, semplificare la query o ripetere il tentativo tra qualche secondo."
        )
    if offerte:
        # ── Filtri post-ricerca ─────────────────────────────────────────────
        with st.expander("🔍 Filtra risultati", expanded=False):
            _fc1, _fc2, _fc3 = st.columns([2, 1, 1])
            _fonti_disp = sorted({o.fonte for o in offerte})
            _filtro_fonti = _fc1.multiselect(
                "Fonte",
                options=_fonti_disp,
                key="filtro_fonti_tabella",
                placeholder="Tutte le fonti...",
            )
            _p_min_r = float(min(o.prezzo for o in offerte))
            _p_max_r = float(max(o.prezzo for o in offerte))
            _saved_range = st.session_state.get("filtro_prezzo_range_tabella")
            if (
                isinstance(_saved_range, (list, tuple))
                and len(_saved_range) == 2
                and _p_min_r <= float(_saved_range[0]) <= _p_max_r
                and float(_saved_range[0]) <= float(_saved_range[1]) <= _p_max_r
            ):
                _init_range = (float(_saved_range[0]), float(_saved_range[1]))
            else:
                _init_range = (_p_min_r, _p_max_r)
            if _p_min_r < _p_max_r:
                st.session_state["filtro_prezzo_range_tabella"] = _init_range
                _filtro_prezzo: tuple[float, float] = _fc2.slider(
                    "Prezzo €",
                    min_value=_p_min_r,
                    max_value=_p_max_r,
                    key="filtro_prezzo_range_tabella",
                    format="€%.0f",
                )
            else:
                _filtro_prezzo = (_p_min_r, _p_max_r)
            _filtro_cond = _fc3.radio(
                "Condizione",
                ["tutti", "nuovo", "usato"],
                key="filtro_condizione_tabella",
                horizontal=True,
            )

        def _infer_cond_offerta(o: Offerta) -> str:
            if o.fonte in ("vinted.it",):
                return "usato"
            _nl = o.nome.lower()
            if any(
                _k in _nl for _k in ("usato", "ricondizionato", "refurbished", "rigenerato", "used")
            ):
                return "usato"
            return "nuovo"

        offerte_vis = [
            o
            for o in offerte
            if (not _filtro_fonti or o.fonte in _filtro_fonti)
            and _filtro_prezzo[0] <= o.prezzo <= _filtro_prezzo[1]
            and (_filtro_cond == "tutti" or _infer_cond_offerta(o) == _filtro_cond)
        ]

        _metriche_src = offerte_vis if offerte_vis else offerte
        prezzo_min_ris = min(o.prezzo for o in _metriche_src)
        negozio_min = next(o.negozio for o in _metriche_src if o.prezzo == prezzo_min_ris)
        fonti_uniche = len({o.fonte for o in _metriche_src})
        _n_vis, _n_tot = len(offerte_vis), len(offerte)
        _label_count = f"{_n_vis}/{_n_tot}" if _n_vis < _n_tot else str(_n_vis)

        st.subheader(
            f"{_label_count} offerte trovate per \u201c{st.session_state.get('ultima_query', '')}\u201d"
        )
        if st.session_state.get("prezzo_nuovo_minimo"):
            _pmc = st.session_state.get("prezzo_minimo_corrente")
            _pmp = st.session_state.get("prezzo_minimo_prec")
            _msg_low = (
                f"\ud83d\udd3b Nuovo minimo storico per \u201c{st.session_state.get('ultima_query', '')}\u201d: "
                f"{_format_price(_pmc)}"
            )
            if _pmp is not None:
                _msg_low += f" \u2014 prima era {_format_price(_pmp)}"
            st.success(_msg_low)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Risultati", _label_count)
        m2.metric("Prezzo piu basso", _format_price(prezzo_min_ris))
        m3.metric("Negozio migliore", negozio_min)
        m4.metric("Fonti attive", str(fonti_uniche))

        _prices_sorted = sorted([o.prezzo for o in _metriche_src])
        _top_1 = _prices_sorted[0] if _prices_sorted else 0.0
        _top_2 = _prices_sorted[1] if len(_prices_sorted) > 1 else _top_1
        _top_3 = _prices_sorted[2] if len(_prices_sorted) > 2 else _top_2
        _delta_12 = ((_top_2 - _top_1) / _top_1 * 100.0) if _top_1 > 0 else 0.0
        _delta_13 = ((_top_3 - _top_1) / _top_1 * 100.0) if _top_1 > 0 else 0.0

        _ins_l, _ins_r = st.columns([1.55, 1], gap="large")
        with _ins_l:
            st.markdown(
                "<div class='market-snapshot-card'>"
                "<p class='market-kicker'>Snapshot mercato</p>"
                f"<h4>{_format_price(_top_1)}</h4>"
                f"<p>Delta top #1-#2: <strong>+{_delta_12:.1f}%</strong> · "
                f"Delta top #1-#3: <strong>+{_delta_13:.1f}%</strong></p>"
                "<small>Ranking: prezzo, affidabilita fonte, spedizione.</small>"
                "</div>",
                unsafe_allow_html=True,
            )
        with _ins_r:
            _fonti_selected_ui = list(st.session_state.get("fonti_selezionate", _fonti_def))
            _fonti_selected_backend = [_fonti_map[f] for f in _fonti_selected_ui if f in _fonti_map]
            _render_source_status_monitor(
                offerte, _fonti_selected_backend, st.session_state.get("log_ricerca", "")
            )

        if not offerte_vis:
            st.info("ℹ️ Nessun risultato con i filtri correnti. Modifica o rimuovi i filtri.")
        else:
            _render_results_grid(offerte_vis)

            # ── Comparatore fianco a fianco ─────────────────────────────────
            _link_labels = {
                o.link: f"{o.nome[:60]}{'...' if len(o.nome) > 60 else ''} — {_format_price(o.prezzo)}"
                for o in offerte_vis
            }
            _selezione_links = st.multiselect(
                "🔁 Confronto prodotti — seleziona 2–4 per confrontarli fianco a fianco",
                options=list(_link_labels.keys()),
                format_func=lambda lk: _link_labels.get(lk, lk),
                key="comparatore_selezione",
                max_selections=4,
                placeholder="Seleziona 2, 3 o 4 prodotti...",
            )
            _offerte_confronto = [o for o in offerte_vis if o.link in _selezione_links]
            if len(_offerte_confronto) >= 2:
                _render_manual_comparison_matrix(_offerte_confronto)

            # ── Trend prezzo minimo (storico interno multi-fonte) ──────────
            _wl_query = st.session_state.get("ultima_query", "")
            if price_history is not None and _wl_query:
                _hist = price_history.history_for(_wl_query)
                _low = price_history.lowest_ever(_wl_query)
                if _low is not None and len(_hist) >= 2:
                    _cur_min = min((o.prezzo for o in offerte_vis if o.prezzo), default=None)
                    _trend = f"📊 Minimo storico per “{_wl_query}”: {_format_price(_low)}"
                    if _cur_min is not None:
                        _trend += f" · ora {_format_price(_cur_min)}"
                    st.caption(_trend + f" ({len(_hist)} rilevazioni)")

            # ── Preferiti / watchlist ──────────────────────────────────────
            if watchlist is not None:
                _wl_add = st.multiselect(
                    "⭐ Salva prodotti nei preferiti",
                    options=list(_link_labels.keys()),
                    format_func=lambda lk: _link_labels.get(lk, lk),
                    key="watchlist_add_selezione",
                    placeholder="Seleziona prodotti da salvare...",
                )
                if _wl_add and st.button("⭐ Aggiungi ai preferiti", key="watchlist_save_btn"):
                    _added = sum(
                        1
                        for _o in offerte_vis
                        if _o.link in _wl_add
                        and watchlist.add_item(_o.nome, _o.prezzo, _o.link, _o.fonte)
                    )
                    st.toast(f"⭐ {_added} prodotto/i salvato/i nei preferiti")
                _wl_items = watchlist.load()
                if _wl_items:
                    with st.expander(f"⭐ Preferiti salvati ({len(_wl_items)})", expanded=False):
                        for _wi in _wl_items:
                            _wc1, _wc2 = st.columns([6, 1])
                            _wprezzo = (
                                _format_price(_wi["prezzo"])
                                if _wi.get("prezzo") is not None
                                else "n.d."
                            )
                            _wc1.markdown(
                                f"[{_wi.get('nome', '')[:70]}]({_wi.get('link', '')}) — "
                                f"{_wprezzo} · {_wi.get('fonte', '')}"
                            )
                            if _wc2.button("🗑", key=f"wl_del_{_wi.get('link', '')}"):
                                watchlist.remove(_wi.get("link", ""))
                                st.rerun()

            # ── Storico prezzi Amazon (CamelCamelCamel) ────────────────────
            import re as _re

            _amazon_asin_pairs: list = []
            for _ao in offerte_vis:
                if "amazon.it" in _ao.link.lower():
                    _m = _re.search(r"/dp/([A-Z0-9]{10})", _ao.link)
                    if _m:
                        _amazon_asin_pairs.append((_ao, _m.group(1)))
            if _amazon_asin_pairs:
                with st.expander("📈 Storico prezzi Amazon (CamelCamelCamel)", expanded=False):
                    for _cao, _asin in _amazon_asin_pairs[:5]:
                        st.markdown(f"**{_cao.nome[:80]}** — {_format_price(_cao.prezzo)}")
                        _chart_url = (
                            f"https://charts.camelcamelcamel.com/it/{_asin}/amazon.png"
                            f"?force=1&zero=0&w=800&h=200&desired=false&legend=1&ilt=1&tp=all&fo=0&lang=it"
                        )
                        st.image(_chart_url, width="stretch")
                        st.caption(
                            f"[Apri storico completo su CamelCamelCamel →](https://camelcamelcamel.com/product/{_asin})"
                        )
                        st.divider()

            risultati_con_alternativa = [o for o in offerte_vis if str(o.alternativa or "").strip()]
            if risultati_con_alternativa:
                st.markdown("**Alternative smart rilevate**")
                for offerta in risultati_con_alternativa:
                    st.warning(f"{offerta.nome}: {offerta.alternativa}")

            with st.expander("🔬 Specs rilevate", expanded=False):
                _render_specs_grid(offerte_vis)

        _offerte_export = offerte_vis if offerte_vis else offerte
        _copy_query = st.session_state.get("ultima_query", "")
        _copy_text = _offerte_to_copy_text(_offerte_export, _copy_query)
        with st.expander(
            f"📋 Copia risultati per AI ({len(_offerte_export)} prodotti)", expanded=False
        ):
            st.caption(
                "Copia il testo qui sotto e incollalo in Claude, ChatGPT o altra AI per un'analisi approfondita."
            )
            st.code(_copy_text, language=None)

        st.write("")
        with st.expander("💬 Consiglio AI", expanded=True):
            st.caption(
                "Chiedi quale prodotto ti conviene tra quelli trovati, in base al tuo uso e budget."
            )
            if cerebras_client is None:
                st.info("💡 Aggiungi CEREBRAS_API_KEY per ottenere la raccomandazione finale AI.")
            else:
                # Auto top-3 al primo caricamento (al massimo 1 tentativo per ricerca)
                if not st.session_state.get("final_chat_messages") and not st.session_state.get(
                    "auto_recommend_tried"
                ):
                    st.session_state["auto_recommend_tried"] = True
                    with st.spinner("🤖 Analizzo i risultati per la top 3…"):
                        try:
                            auto_query = (
                                "Analizza i prodotti disponibili e consigliami le migliori 3 opzioni con una motivazione "
                                "concisa per ciascuna (nome, prezzo, punto di forza). Poi indica la tua raccomandazione finale."
                            )
                            auto_messages = [{"role": "user", "content": auto_query}]
                            risposta_auto = _call_final_recommendation(
                                cerebras_client,
                                offerte,
                                st.session_state.get("preferenze_utente", {}),
                                auto_messages,
                            )
                            if risposta_auto:
                                st.session_state["final_chat_messages"] = [
                                    {"role": "user", "content": auto_query},
                                    {"role": "assistant", "content": risposta_auto},
                                ]
                                st.rerun()
                        except Exception as exc:
                            _exc_s = str(exc).lower()
                            if not ("429" in _exc_s or "too_many" in _exc_s or "queue" in _exc_s):
                                st.warning(f"⚠️ Auto-raccomandazione non disponibile: {exc}")

                _AUTO_QUERY_PREFIX = "Analizza i prodotti disponibili"
                for message in st.session_state.get("final_chat_messages", []):
                    role = "assistant" if message.get("role") == "assistant" else "user"
                    if role == "user" and message.get("content", "").startswith(_AUTO_QUERY_PREFIX):
                        continue
                    with st.chat_message(role):
                        st.write(message.get("content", ""))

                final_prompt = st.chat_input(
                    "Esempio: quale mi consigli per uso quotidiano?", key="final_advice_input"
                )
                if final_prompt:
                    st.session_state["final_chat_messages"].append(
                        {"role": "user", "content": final_prompt}
                    )
                    with st.chat_message("user"):
                        st.write(final_prompt)
                    with st.spinner("🤖 Sto confrontando i prodotti..."):
                        try:
                            risposta = _call_final_recommendation(
                                cerebras_client,
                                offerte,
                                st.session_state.get("preferenze_utente", {}),
                                st.session_state.get("final_chat_messages", []),
                            )
                            if not risposta:
                                raise RuntimeError("Risposta vuota dal modello")
                            st.session_state["final_chat_messages"].append(
                                {"role": "assistant", "content": risposta}
                            )
                            st.rerun()
                        except Exception as exc:
                            _exc_s = str(exc).lower()
                            if "429" in _exc_s or "too_many" in _exc_s or "queue" in _exc_s:
                                st.warning(
                                    "⚠️ Servizio AI momentaneamente sovraccarico, riprova tra qualche secondo."
                                )
                            else:
                                st.error(f"❌ Errore AI: {exc}")

    st.markdown("</div>", unsafe_allow_html=True)

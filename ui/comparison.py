"""ui: ui/comparison.py"""

from __future__ import annotations

import contextlib
import io
import os
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


from ui.state import _format_price


def _extract_comparison_spec_keys(
    grouped_results: dict[str, list[Offerta]],
    max_keys: int = 6,
) -> list[str]:
    counts: dict[str, int] = {}
    for results in grouped_results.values():
        for offerta in sorted(results, key=lambda item: item.prezzo)[:3]:
            if not isinstance(offerta.specs, dict):
                continue
            for raw_key, raw_value in offerta.specs.items():
                if raw_value in (None, "", [], {}):
                    continue
                key = str(raw_key or "").strip().lower()
                if not key:
                    continue
                counts[key] = counts.get(key, 0) + 1

    if not counts:
        return []

    preferred = ["display", "processore", "ram", "storage", "batteria", "camera", "refresh_rate"]
    ordered = [key for key in preferred if key in counts]
    tail = sorted(
        [key for key in counts if key not in preferred], key=lambda item: (-counts[item], item)
    )
    return (ordered + tail)[:max_keys]


def _spec_value_for_key(offerta: Offerta, key: str) -> str:
    if not isinstance(offerta.specs, dict):
        return "n.d."
    for raw_key, raw_value in offerta.specs.items():
        if str(raw_key or "").strip().lower() == key and raw_value not in (None, "", [], {}):
            return str(raw_value)
    return "n.d."


def _render_comparison_board(cmp_results: dict[str, list[Offerta]]) -> None:
    import html as _html

    ordered_by_query: dict[str, list[Offerta]] = {}
    for query, raw_results in cmp_results.items():
        if isinstance(raw_results, list):
            ordered_by_query[str(query)] = sorted(raw_results, key=lambda item: item.prezzo)

    valid_queries = [query for query, rows in ordered_by_query.items() if rows]
    if not valid_queries:
        st.warning("Nessun risultato disponibile per il confronto richiesto.")
        return

    spec_keys = _extract_comparison_spec_keys(
        {query: ordered_by_query[query] for query in valid_queries}
    )

    header_cells = "".join(
        (
            f"<th>{_html.escape(query.title())}"
            f"<span>{len(ordered_by_query[query])} risultati</span></th>"
        )
        for query in valid_queries
    )

    price_cells = []
    spread_cells = []
    best_offer_cells = []
    for query in valid_queries:
        rows = ordered_by_query[query]
        best = rows[0]
        price_cells.append(
            "<td>"
            f"<span class='value'>{_format_price(best.prezzo)}</span>"
            f"<span class='meta'>{_html.escape(best.negozio)}</span>"
            "</td>"
        )

        if len(rows) >= 3 and rows[0].prezzo > 0:
            spread = ((rows[2].prezzo - rows[0].prezzo) / rows[0].prezzo) * 100.0
            spread_label = f"+{spread:.1f}%"
            spread_detail = "dal #1 al #3"
        elif len(rows) >= 2 and rows[0].prezzo > 0:
            spread = ((rows[1].prezzo - rows[0].prezzo) / rows[0].prezzo) * 100.0
            spread_label = f"+{spread:.1f}%"
            spread_detail = "dal #1 al #2"
        else:
            spread_label = "n.d."
            spread_detail = "campione ridotto"
        spread_cells.append(
            "<td>"
            f"<span class='value'>{_html.escape(spread_label)}</span>"
            f"<span class='meta'>{_html.escape(spread_detail)}</span>"
            "</td>"
        )

        best_name = best.nome[:68] + ("..." if len(best.nome) > 68 else "")
        best_offer_cells.append(
            "<td>"
            f"<a class='comp-link' href='{_html.escape(best.link, quote=True)}' target='_blank' rel='noopener noreferrer'>{_html.escape(best_name)}</a>"
            f"<span class='meta'>{_html.escape(best.fonte)}</span>"
            "</td>"
        )

    specs_rows_html = ""
    for key in spec_keys:
        label = _html.escape(str(key).replace("_", " ").capitalize())
        values = []
        for query in valid_queries:
            best = ordered_by_query[query][0]
            values.append(f"<td>{_html.escape(_spec_value_for_key(best, key))}</td>")
        specs_rows_html += f"<tr><td>{label}</td>{''.join(values)}</tr>"

    table_html = (
        "<div class='comparison-board'>"
        "<div class='comparison-board-head'>"
        "<p class='comparison-kicker'>Comparison board</p>"
        "<h4>Confronto Prodotti</h4>"
        "<p>Vista sintetica per capire subito valore, spread prezzi e differenze tecniche principali.</p>"
        "</div>"
        "<div class='comparison-table-wrap'>"
        "<p class='comparison-scroll-hint'>Scorri orizzontalmente per vedere tutte le colonne.</p>"
        "<table class='comparison-table'>"
        "<thead><tr><th>Parametro</th>"
        f"{header_cells}"
        "</tr></thead>"
        "<tbody>"
        f"<tr class='row-priority'><td>Miglior prezzo</td>{''.join(price_cells)}</tr>"
        f"<tr class='row-priority'><td>Spread prezzo</td>{''.join(spread_cells)}</tr>"
        f"<tr class='row-priority'><td>Best match</td>{''.join(best_offer_cells)}</tr>"
        f"{specs_rows_html}"
        "</tbody>"
        "</table>"
        "</div>"
        "</div>"
    )
    st.markdown(table_html, unsafe_allow_html=True)

    cmp_cols = st.columns(len(valid_queries), gap="medium")
    for col, query in zip(cmp_cols, valid_queries, strict=True):
        rows = ordered_by_query[query]
        stack_items = []
        for rank, offerta in enumerate(rows[:3], start=1):
            nome = offerta.nome[:62] + ("..." if len(offerta.nome) > 62 else "")
            stack_items.append(
                "<div class='comparison-pick'>"
                f"<span class='rank'>#{rank}</span>"
                f"<span class='pick-price'>{_format_price(offerta.prezzo)}</span>"
                f"<a href='{_html.escape(offerta.link, quote=True)}' target='_blank' rel='noopener noreferrer'>{_html.escape(nome)}</a>"
                f"<small>{_html.escape(offerta.negozio)} · {_html.escape(offerta.fonte)}</small>"
                "</div>"
            )
        stack_html = (
            "".join(stack_items)
            if stack_items
            else "<p class='comparison-empty'>Nessun risultato.</p>"
        )
        with col:
            st.markdown(
                "<div class='comparison-stack'>"
                f"<p class='comparison-stack-title'>{_html.escape(query.title())}</p>"
                f"{stack_html}"
                "</div>",
                unsafe_allow_html=True,
            )


def _render_manual_comparison_matrix(offerte: list[Offerta]) -> None:
    import html as _html

    if len(offerte) < 2:
        return

    best_price = min(offerta.prezzo for offerta in offerte)
    spec_keys = _extract_comparison_spec_keys({"manual": offerte}, max_keys=8)

    header_cells = []
    for offerta in offerte:
        name = offerta.nome[:48] + ("..." if len(offerta.nome) > 48 else "")
        best_class = " is-best" if offerta.prezzo == best_price else ""
        header_cells.append(f"<th class='{best_class.strip()}'>{_html.escape(name)}</th>")

    rows_html = ""
    rows_html += (
        "<tr><td>Prezzo</td>"
        + "".join(
            f"<td class='num'>{_html.escape(_format_price(offerta.prezzo))}</td>"
            for offerta in offerte
        )
        + "</tr>"
    )
    rows_html += (
        "<tr><td>Negozio</td>"
        + "".join(f"<td>{_html.escape(offerta.negozio)}</td>" for offerta in offerte)
        + "</tr>"
    )
    rows_html += (
        "<tr><td>Fonte</td>"
        + "".join(f"<td>{_html.escape(offerta.fonte)}</td>" for offerta in offerte)
        + "</tr>"
    )
    rows_html += (
        "<tr><td>Spedizione</td>"
        + "".join(
            f"<td>{_html.escape(str(offerta.spedizione or 'n.d.'))}</td>" for offerta in offerte
        )
        + "</tr>"
    )

    for key in spec_keys:
        label = _html.escape(str(key).replace("_", " ").capitalize())
        row_values = "".join(
            f"<td>{_html.escape(_spec_value_for_key(offerta, key))}</td>" for offerta in offerte
        )
        rows_html += f"<tr><td>{label}</td>{row_values}</tr>"

    rows_html += (
        "<tr><td>Link</td>"
        + "".join(
            (
                "<td>"
                f"<a class='comp-link' href='{_html.escape(offerta.link, quote=True)}' target='_blank' rel='noopener noreferrer'>Apri offerta -></a>"
                "</td>"
            )
            for offerta in offerte
        )
        + "</tr>"
    )

    st.markdown(
        "<div class='manual-compare-wrap'>"
        "<p class='comparison-kicker'>Confronto manuale</p>"
        "<p class='comparison-scroll-hint'>Tip: su mobile scorri la tabella verso destra per il confronto completo.</p>"
        "<table class='manual-compare-table'>"
        "<thead><tr><th>Parametro</th>"
        f"{''.join(header_cells)}"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        "</div>",
        unsafe_allow_html=True,
    )


def _run_comparison_search(
    *,
    queries: list[str],
    prezzo_min: int,
    budget_max: int,
    top_n: int,
    condizione: str,
    fonti_backend: list[str],
    cerebras_client: object | None,
) -> None:
    """Esegue ricerche separate per ciascuna query di confronto e salva i risultati."""
    st.session_state["ricerca_effettuata"] = True
    st.session_state["comparison_results"] = {}
    st.session_state["risultati"] = []
    st.session_state["log_ricerca"] = ""
    st.session_state["final_chat_messages"] = []
    st.session_state["auto_recommend_tried"] = False

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

    categoria = str(st.session_state.get("categoria", "altro") or "altro")
    all_results: dict[str, list[Offerta]] = {}
    combined_log = ""

    with st.status(
        f"⏳ Confronto in corso per {len(queries)} prodotti...", expanded=True
    ) as cmp_status:
        for q in queries:
            st.write(f"🔍 Cercando **{q}**...")
            log_buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(log_buf):
                    res = cerca_offerte(
                        query=q,
                        budget_max=float(budget_max),
                        prezzo_min=float(prezzo_min),
                        top_n=top_n,
                        condizione=condizione,
                        fonti=fonti_backend,
                        categoria=categoria,
                        cerebras_client=cerebras_client,
                        app_id=ebay_app_id,
                        cert_id=ebay_cert_id,
                    )
                all_results[q] = res
                combined_log += f"\n--- {q} ---\n" + log_buf.getvalue()
                st.write(f"  ✅ **{q}** → {len(res)} risultati")
            except Exception as exc:
                all_results[q] = []
                st.write(f"  ❌ **{q}** → errore: {exc}")

        total = sum(len(v) for v in all_results.values())
        cmp_status.update(
            label=f"✅ Confronto completato — {total} offerte totali",
            state="complete",
            expanded=False,
        )

    st.session_state["comparison_results"] = all_results
    st.session_state["log_ricerca"] = combined_log
    # Popola anche risultati flat (utile per export CSV e raccomandazione AI)
    flat = []
    for results_list in all_results.values():
        flat.extend(results_list)
    flat.sort(key=lambda o: o.prezzo)
    st.session_state["risultati"] = flat

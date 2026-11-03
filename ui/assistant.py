"""One message coordinates a bounded group of independent product searches."""

from __future__ import annotations

import uuid

import streamlit as st

from offerte.agent_service import get_service
from offerte.models import Offerta
from ui.ai_settings import _failure
from ui.cards import _render_results_grid

_LABELS = {
    "queued": "In attesa",
    "planning": "Preparo le ricerche",
    "collecting": "Cerco nei negozi",
    "analyzing": "Analizzo le offerte",
    "advising": "Preparo il consiglio",
    "awaiting_budget": "Serve il budget",
    "awaiting_clarification": "Serve un dettaglio",
    "completed": "Ricerca completata",
    "partial": "Ricerca completata con risultati parziali",
    "failed": "Ricerca non completata",
    "cancelled": "Ricerca interrotta",
    "interrupted": "Interrotta alla chiusura dell’app",
    "pending": "In attesa",
    "empty": "Nessuna offerta pertinente",
}
_ACTIVE = {"queued", "planning", "collecting", "analyzing", "advising"}


def render_assistant() -> None:
    service = get_service()
    st.subheader("Cosa cerchiamo?")
    st.caption(
        "Descrivi uno o più prodotti, fino a dieci. Cerco, confronto e ti rispondo qui. Se manca il budget, puoi anche dire “non so”."
    )
    recent = service.list_searches(limit=15)["items"]
    labels = {
        item["id"]: item.get("text")
        or " · ".join(p["parameters"]["query"] for p in item.get("products", []))
        for item in recent
    }
    with st.expander("Riprendi una ricerca"):
        if not recent:
            st.caption("Le ricerche compariranno qui dopo il primo avvio.")
        for item in recent:
            if st.button(
                labels[item["id"]][:90], key=f"open_{item['id']}", use_container_width=True
            ):
                st.session_state["assistant_operation"] = item["id"]
                st.rerun()

    operation_id = st.session_state.get("assistant_operation")
    current = service.get_operation(operation_id) if operation_id else None
    if not current or current["status"] not in _ACTIVE | {
        "awaiting_budget",
        "awaiting_clarification",
    }:
        with st.form("assistant_request", clear_on_submit=True):
            text = st.text_area(
                "Prodotti e preferenze",
                placeholder="Cercami un iPhone 15, una lavatrice da 8 kg e un notebook per lavorare. Per il telefono massimo 500 euro.",
                key="assistant_request_text",
            )
            submitted = st.form_submit_button(
                "Cerca e confronta", type="primary", use_container_width=True
            )
        if submitted and text.strip():
            try:
                operation = service.start_request(text, uuid.uuid4().hex)
                st.session_state["assistant_operation"] = operation["id"]
                st.rerun()
            except Exception as exc:
                _failure(exc)
    if st.session_state.get("assistant_operation"):
        _render_operation()


@st.fragment(run_every="1s")
def _render_operation() -> None:
    service = get_service()
    operation_id = st.session_state.get("assistant_operation")
    if not operation_id:
        return
    try:
        operation = service.get_operation(operation_id)
    except Exception as exc:
        _failure(exc)
        return
    status = operation["status"]
    if operation.get("text"):
        with st.chat_message("user"):
            st.write(operation["text"])
    st.markdown(f"**{_LABELS.get(status, status)}**")
    total = operation.get("total_products", len(operation.get("products", [])))
    completed = operation.get("completed_products", 0)
    if total:
        st.progress(min(1.0, completed / total), text=f"{completed}/{total} prodotti completati")
    if status in _ACTIVE | {"awaiting_budget", "awaiting_clarification"}:
        if st.button("Interrompi", key=f"cancel_{operation_id}"):
            service.cancel_operation(operation_id)
            st.rerun()
    if status in {"awaiting_budget", "awaiting_clarification"}:
        st.info(operation.get("question") or "Aggiungi il dettaglio richiesto.")
        with st.form(f"answer_{operation_id}", clear_on_submit=True):
            answer = st.text_input(
                "La tua risposta",
                placeholder="Telefono 500 €, lavatrice 600 €, per il notebook non so",
            )
            if st.form_submit_button("Continua la ricerca") and answer.strip():
                try:
                    service.answer_request(operation_id, answer)
                    st.rerun()
                except Exception as exc:
                    _failure(exc)
    if operation.get("error"):
        explanations = {
            "timeout": "Il runtime AI ha superato il tempo massimo.",
            "quota_exhausted": "La quota dell’account è temporaneamente esaurita.",
            "protocol_error": "Il runtime AI non ha restituito una risposta utilizzabile.",
            "not_authenticated": "Accedi al client ufficiale per usare questa connessione.",
            "tools_disabled": "Il runtime ha richiesto uno strumento non previsto per questa attività.",
        }
        st.warning(
            explanations.get(operation["error"], "Il lavoro si è fermato.")
            + " I risultati già raccolti restano disponibili. Puoi avviare una nuova richiesta o controllare la connessione nelle impostazioni."
        )
    for product in operation.get("products", []):
        params = product["parameters"]
        budget = params.get("budget_max")
        with st.expander(
            f"{params.get('label') or params['query']} · {product.get('offers_count', 0)} offerte",
            expanded=status not in _ACTIVE,
        ):
            st.caption(
                (f"Fino a {budget:g} €" if budget is not None else "Senza tetto di prezzo")
                + f" · {params.get('condition', 'tutti')} · {_LABELS.get(product['status'], product['status'])}"
            )
            if product.get("offers_count"):
                rows = service.list_offers(operation_id, product["product_id"], limit=100)["items"]
                offers = [
                    Offerta(**{key: row[key] for key in Offerta.__dataclass_fields__ if key in row})
                    for row in rows
                ]
                _render_results_grid(offers)
            for source in product.get("sources", {}).values():
                if isinstance(source, dict) and source.get("state") in {
                    "blocked",
                    "error",
                    "disabled",
                }:
                    st.caption(f"{source['fonte']}: {source['state']} {source.get('detail', '')}")
    if advice := operation.get("advice"):
        with st.chat_message("assistant"):
            st.write(advice)
    for contribution in operation.get("external_analyses", []):
        with st.expander(f"Analisi esterna: {contribution['source']}"):
            st.caption(
                "Modello dichiarato dall’assistente esterno. I dati delle offerte restano quelli raccolti."
            )
            for item in contribution["analysis"]["products"]:
                st.write(item["summary"])
    if status not in _ACTIVE | {"awaiting_budget", "awaiting_clarification"}:
        if st.button("Nuova richiesta", key=f"new_{operation_id}"):
            st.session_state.pop("assistant_operation", None)
            st.rerun()

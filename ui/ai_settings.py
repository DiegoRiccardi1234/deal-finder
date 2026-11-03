"""Native accounts, named endpoints and explicit task routes in existing UI style."""

from __future__ import annotations

import json

import streamlit as st

from offerte.connection_routing import get_router

_TASKS = {
    "plan": "Dialogo e piano",
    "analysis": "Analisi delle offerte",
    "advice": "Consiglio finale",
}


def _failure(exc: Exception) -> None:
    from offerte.agent_runtime import RuntimeConnectionError

    if isinstance(exc, RuntimeConnectionError):
        st.error(str(exc))
    else:
        st.error(
            "Operazione non completata. Controlla connessione, campi e attività ancora in corso."
        )


def render_ai_settings() -> None:
    from offerte.update import is_cloud

    if is_cloud():
        return
    router = get_router()
    registry = router.connections
    with st.expander("AI e assistenti", expanded=False):
        st.caption(
            "Usa il tuo account Codex oppure scegli una connessione per attività. Nessun fallback API è automatico."
        )
        if st.button(
            "Usa Codex per tutte le attività", key="ai_all_codex", use_container_width=True
        ):
            try:
                registry.use_codex_profile()
                st.session_state["ai_settings_notice"] = (
                    "Codex selezionato. Le chiavi API precedenti restano conservate."
                )
                st.rerun()
            except Exception as exc:
                _failure(exc)
        if notice := st.session_state.pop("ai_settings_notice", None):
            st.success(notice)

        for cid, title in (("codex", "Codex"), ("claude", "Claude Code, facoltativo")):
            current = registry.get(cid) or {}
            with st.expander(title):
                st.caption(
                    "Il client ufficiale deve essere installato e autenticato. La diagnostica non genera risposte AI."
                )
                if cid == "claude":
                    st.caption(
                        "Serve un piano compatibile attuale. Non è necessario per usare Deal Finder."
                    )
                with st.form(f"native_{cid}"):
                    enabled = st.checkbox(
                        "Connessione abilitata", value=current.get("enabled", False)
                    )
                    model = st.text_input(
                        "Modello, vuoto per quello del client", value=current.get("model", "")
                    )
                    if st.form_submit_button("Salva connessione"):
                        try:
                            registry.save(
                                cid,
                                {"kind": cid, "name": title, "enabled": enabled, "model": model},
                            )
                            st.success("Connessione salvata.")
                        except Exception as exc:
                            _failure(exc)
                diagnostic, test = st.columns(2)
                if diagnostic.button("Diagnostica", key=f"status_{cid}"):
                    try:
                        st.session_state[f"runtime_status_{cid}"] = router.status(cid)
                    except Exception as exc:
                        _failure(exc)
                if test.button(
                    "Prova sintetica",
                    key=f"test_{cid}",
                    help="Invia una richiesta di prova e usa la quota dell'account.",
                ):
                    try:
                        with st.spinner("Prova con dati sintetici…"):
                            result = router.test_connection(cid)
                        st.success(
                            "Risposta verificata."
                            if result["ok"]
                            else "Risposta ricevuta, contenuto inatteso."
                        )
                    except Exception as exc:
                        _failure(exc)
                if status := st.session_state.get(f"runtime_status_{cid}"):
                    st.json(status)

        compatible = [
            row for row in registry.public_connections() if row["kind"] == "openai_compatible"
        ]
        with st.expander("Endpoint compatibili e Hermes"):
            options = ["Nuova connessione"] + [row["id"] for row in compatible]
            selected = str(
                st.selectbox("Connessione da modificare", options, key="compat_edit")
                or "Nuova connessione"
            )
            previous = registry.get(selected) or {}
            with st.form(f"compatible_{selected}", clear_on_submit=False):
                cid = st.text_input(
                    "Identificativo",
                    value=previous.get("id", ""),
                    placeholder="es. hermes-locale",
                    disabled=bool(previous),
                )
                name = st.text_input("Nome", value=previous.get("name", ""))
                base_url = st.text_input(
                    "URL API",
                    value=previous.get("base_url", ""),
                    placeholder="http://127.0.0.1:8642/v1",
                )
                model = st.text_input("Modello", value=previous.get("model", ""))
                key = st.text_input("Chiave, lascia vuoto per conservarla", type="password")
                clear = st.checkbox("Rimuovi la chiave salvata")
                enabled = st.checkbox("Abilita endpoint", value=previous.get("enabled", True))
                external = st.checkbox(
                    "È un agente con strumenti propri, come Hermes",
                    value=previous.get("external_agent", False),
                )
                acknowledged = st.checkbox(
                    "Conosco strumenti e provider del gateway esterno",
                    value=previous.get("tools_policy_acknowledged", False),
                )
                st.caption(
                    "Un gateway locale può usare servizi a pagamento. Installazione, avvio e policy di Hermes restano nel suo client."
                )
                if st.form_submit_button("Salva endpoint"):
                    try:
                        registry.save(
                            cid,
                            {
                                "kind": "openai_compatible",
                                "name": name,
                                "base_url": base_url,
                                "model": model,
                                "enabled": enabled,
                                "external_agent": external,
                                "tools_policy_acknowledged": acknowledged,
                                "api_key": "" if clear else (key or None),
                            },
                        )
                        st.session_state["ai_settings_notice"] = (
                            "Endpoint salvato. Puoi ora assegnarlo alle attività."
                        )
                        st.rerun()
                    except Exception as exc:
                        _failure(exc)
            if previous and st.button("Rimuovi endpoint", key="delete_endpoint"):
                try:
                    registry.delete(selected)
                    st.rerun()
                except Exception as exc:
                    _failure(exc)

        rows = registry.public_connections()
        labels = {row["id"]: row["name"] for row in rows}
        enabled_ids = [row["id"] for row in rows if row["configured"]]
        routing = registry.routing()
        with st.form("routing_form"):
            st.markdown("**Motore per attività**")
            choices = {}
            for task, label in _TASKS.items():
                saved = routing[task]
                available = list(
                    dict.fromkeys(
                        ["", *enabled_ids, *([saved["primary"]] if saved["primary"] else [])]
                    )
                )
                primary = st.selectbox(
                    label,
                    available,
                    index=available.index(saved["primary"] or ""),
                    format_func=lambda value: (
                        labels.get(value, value) if value else "Provider API attuale"
                    ),
                    key=f"route_{task}",
                )
                fallback_options = [cid for cid in enabled_ids if cid != primary]
                fallbacks = st.multiselect(
                    "Fallback espliciti, nell’ordine scelto",
                    fallback_options,
                    default=[cid for cid in saved["fallbacks"] if cid in fallback_options],
                    format_func=lambda value: labels.get(value, value),
                    key=f"fallback_{task}",
                )
                choices[task] = {"primary": primary or None, "fallbacks": fallbacks}
            if st.form_submit_button("Salva motori"):
                try:
                    registry.save_routing(choices)
                    st.success("Motori salvati.")
                except Exception as exc:
                    _failure(exc)

        with st.expander("Collega un assistente via MCP"):
            st.caption(
                "Mantieni Deal Finder aperto. L'assistente può raccogliere e analizzare offerte con i suoi strumenti, senza inferenze API annidate."
            )
            port = int(
                st.number_input(
                    "Porta locale MCP", min_value=1024, max_value=65535, value=8521, key="mcp_port"
                )
            )
            if st.button("Genera configurazione MCP", key="mcp_setup"):
                try:
                    from offerte.agent_service import get_service

                    st.session_state["mcp_configs"] = get_service().enable_mcp(port)
                except Exception as exc:
                    _failure(exc)
            if st.button("Revoca collegamento MCP", key="mcp_revoke"):
                from offerte.agent_service import get_service

                get_service().revoke()
                st.session_state.pop("mcp_configs", None)
                st.success("Collegamento revocato.")
            if setup := st.session_state.get("mcp_configs"):
                for client, config in setup["configs"].items():
                    st.markdown(f"**{client.replace('_', ' ').title()}**")
                    st.code(
                        config if isinstance(config, str) else json.dumps(config, indent=2),
                        language="toml"
                        if client == "codex"
                        else "yaml"
                        if client == "hermes"
                        else "json",
                    )
                st.caption(
                    "Codex CLI, desktop e IDE usano la configurazione locale. Cowork richiede un plugin MCP locale, non un connettore cloud localhost."
                )

"""Pannello Impostazioni: le chiavi si incollano qui, non in un file TOML.

Prima l'unico modo di accendere l'IA era aprire `.streamlit/secrets.toml` — un
file che nel bundle Windows non esiste nemmeno. E il selettore del provider
spariva del tutto quando non c'era nessuna chiave, cioè proprio all'utente
nuovo: l'unica persona a cui serviva.
"""

from __future__ import annotations

import streamlit as st

from offerte import providers, secrets_store


def _campo_chiave(nome: str, info: dict) -> None:
    """Una riga: stato, campo, salva/rimuovi, e dove prendere la chiave."""
    cfg = providers.PROVIDERS[nome]
    segno = "🟢" if info["configurato"] else "⚪"
    etichetta = f"{segno} {cfg.label}"
    if info["origine"] == "ambiente":
        # Va detto, o l'utente non capisce perché risulta configurato senza aver
        # scritto niente qui — e non capisce nemmeno perché cancellarlo non basta.
        etichetta += " — da variabile d'ambiente"

    with st.expander(etichetta, expanded=False):
        valore = st.text_input(
            "Chiave API",
            type="password",
            key=f"chiave_{nome}",
            placeholder="incolla qui la chiave" if not info["configurato"] else "•••• già salvata",
            label_visibility="collapsed",
        )
        col_salva, col_rimuovi = st.columns(2)
        with col_salva:
            if st.button("Salva", key=f"salva_{nome}", use_container_width=True):
                if not valore.strip():
                    st.warning("Il campo è vuoto: per cancellare usa Rimuovi.")
                else:
                    secrets_store.salva(cfg.key_env, valore)
                    _dimentica_modello()
                    st.success("Salvata. È già attiva, non serve riavviare.")
                    st.rerun()
        with col_rimuovi:
            if st.button("Rimuovi", key=f"rimuovi_{nome}", use_container_width=True):
                secrets_store.salva(cfg.key_env, "")
                _dimentica_modello()
                st.rerun()
        if cfg.signup_url:
            st.caption(f"[Ottieni una chiave]({cfg.signup_url})")


def _dimentica_modello() -> None:
    """Il modello scelto è in cache: dopo un cambio di chiave va rinegoziato.

    Senza questo la chiave nuova risulta salvata ma non succede niente fino al
    riavvio — che è il modo peggiore di sbagliare, perché sembra che il
    salvataggio non abbia funzionato.
    """
    try:
        from offerte.ai import invalidate_model

        invalidate_model()
    except Exception:
        pass


def render_settings() -> None:
    """Il pannello, da mettere nella barra laterale."""
    with st.expander("⚙️ Impostazioni", expanded=False):
        st.caption(
            "La ricerca sui negozi funziona senza chiavi. L'IA serve a capire la "
            "richiesta a parole, scartare i fuori tema e consigliare."
        )

        riepilogo = secrets_store.riepilogo_provider()

        attivi = [n for n, i in riepilogo.items() if i["configurato"]]
        if attivi:
            # Il selettore c'è **solo** se c'è più di una scelta possibile, ma
            # non sparisce più quando le chiavi sono zero: in quel caso sotto
            # c'è comunque il pannello per metterne una.
            corrente = providers.active_provider()
            indice = attivi.index(corrente) if corrente in attivi else 0
            scelto = st.selectbox(
                "Provider AI",
                attivi,
                index=indice,
                format_func=lambda n: providers.PROVIDERS[n].label,
                key="settings_provider",
            )
            if scelto != corrente:
                secrets_store.salva("AI_PROVIDER", scelto)
                _dimentica_modello()
                st.rerun()
        else:
            st.info("Nessuna chiave: l'IA è spenta. Aggiungine una qui sotto.")

        st.markdown("**Gratuiti — nessuna carta**")
        for nome, info in riepilogo.items():
            if providers.PROVIDERS[nome].free:
                _campo_chiave(nome, info)

        st.markdown("**A pagamento**")
        for nome, info in riepilogo.items():
            if not providers.PROVIDERS[nome].free:
                _campo_chiave(nome, info)

        st.markdown("**eBay** — facoltativo")
        st.caption("Senza queste, eBay funziona lo stesso leggendo le pagine HTML.")
        stato = secrets_store.status()
        for campo in ("EBAY_APP_ID", "EBAY_CERT_ID"):
            segno = "🟢" if stato.get(campo) else "⚪"
            with st.expander(f"{segno} {campo}", expanded=False):
                v = st.text_input(
                    campo, type="password", key=f"chiave_{campo}", label_visibility="collapsed"
                )
                if st.button("Salva", key=f"salva_{campo}", use_container_width=True):
                    secrets_store.salva(campo, v)
                    st.rerun()

        st.caption(
            "Le chiavi restano su questo computer, in `data/local_secrets.json`, "
            "e non vengono mai rimostrate a schermo. Gli aggiornamenti non le toccano."
        )

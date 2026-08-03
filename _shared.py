"""_shared.py — Utilities condivise tra le pagine Streamlit."""

from __future__ import annotations

import os
import re
import streamlit as st


def load_css(theme_mode: str = "light") -> None:
    """Legge styles.css, applica variabili dark se necessario, inietta via st.markdown."""
    _dir = os.path.dirname(os.path.abspath(__file__))
    _css_path = os.path.join(_dir, "styles.css")
    if os.path.exists(_css_path):
        with open(_css_path, encoding="utf-8") as f:
            css = f.read()
    else:
        css = ""

    mode = str(theme_mode or "light").strip().lower()
    if mode not in {"light", "dark"}:
        mode = "light"

    if mode == "dark":
        # In dark mode disattiva i blocchi :root (tema light) prima di attivare i selettori dark.
        css = re.sub(r":root\s*\{", ".__light_disabled__ {", css)
        css = css.replace('[data-theme="dark"]', ":root")
    else:
        # Neutralizza: Streamlit può impostare data-theme="dark" autonomamente
        css = css.replace('[data-theme="dark"]', ".__dark_disabled__")

    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_nav(active_page: str = "tool") -> None:
    """Renderizza la navigazione laterale."""
    with st.sidebar:
        st.markdown("<div class='sidebar-brand'>Deal Finder</div>", unsafe_allow_html=True)
        st.markdown("---")
        render_theme_toggle()
        _render_settings()
        _render_update_control()


def _render_settings() -> None:
    """Pannello chiavi. Importato qui e non in cima per non legare `_shared`
    (utility di pagina) al pacchetto `ui`: in test-mode non serve affatto."""
    if os.environ.get("APP_TEST_MODE", "0").strip() == "1":
        return
    try:
        from ui.settings import render_settings
    except Exception:
        return
    render_settings()


def _render_update_control() -> None:
    """Banner non bloccante di auto-update (solo installazioni locali)."""
    if os.environ.get("APP_TEST_MODE", "0").strip() == "1":
        return
    try:
        from offerte import update
    except Exception:
        return
    if update.is_cloud():
        return
    if "update_latest" not in st.session_state:
        try:
            st.session_state["update_latest"] = update.update_available()
        except Exception:
            st.session_state["update_latest"] = None
    latest = st.session_state.get("update_latest")
    if not latest:
        return
    st.markdown("---")
    st.warning(f"🆕 Versione {latest} disponibile (hai v{update.current_version()})")

    if update.is_frozen():
        # Nel bundle l'aggiornamento è completo: scarica, sostituisce e riapre
        # da solo. Il programma si spegne un secondo e mezzo dopo il click, ed è
        # voluto — va detto prima, o la finestra che sparisce sembra un guasto.
        if st.button("⬇️ Aggiorna ora", key="btn_do_update", use_container_width=True):
            with st.spinner("Scarico l'aggiornamento…"):
                res = update.do_update()
            if res.get("ok"):
                st.success("✅ Deal Finder si chiude e si riapre da solo. I tuoi dati restano.")
                st.session_state["update_latest"] = None
            else:
                st.error(f"Non riuscito: {res.get('error') or res.get('message', '')}")
    elif update.is_git_clone():
        if st.button("⬇️ Aggiorna ora", key="btn_do_update", use_container_width=True):
            with st.spinner("Aggiornamento in corso (git pull + dipendenze)…"):
                res = update.do_update()
            if res.get("ok"):
                st.success("✅ Fatto. Riavvia l'app per applicare l'aggiornamento.")
                st.session_state["update_latest"] = None
            else:
                st.error(f"Non riuscito: {res.get('error') or res.get('message', '')}")
    else:
        st.link_button("⬇️ Scarica l'aggiornamento", update.RELEASES_URL, use_container_width=True)


def get_theme_mode() -> str:
    """Restituisce il tema corrente dalla session_state."""
    mode = str(st.session_state.get("ui_theme", "light") or "light").strip().lower()
    return mode if mode in {"light", "dark"} else "light"


def render_theme_toggle() -> None:
    """Renderizza il selettore tema e aggiorna la session_state."""
    st.selectbox(
        "Tema",
        ["light", "dark"],
        key="ui_theme",
        format_func=lambda v: "Light" if v == "light" else "Dark",
    )

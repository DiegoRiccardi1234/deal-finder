"""_shared.py — Utilities condivise tra le pagine Streamlit."""

from __future__ import annotations

import os
import streamlit as st


def load_css(theme_mode: str = "light") -> None:
    """Legge styles.css, applica variabili dark se necessario, inietta via st.markdown."""
    _dir = os.path.dirname(os.path.abspath(__file__))
    _css_path = os.path.join(_dir, "styles.css")
    if os.path.exists(_css_path):
        with open(_css_path, "r", encoding="utf-8") as f:
            css = f.read()
    else:
        css = ""

    mode = str(theme_mode or "light").strip().lower()
    if mode not in {"light", "dark"}:
        mode = "light"

    if mode == "dark":
        css = css.replace('[data-theme="dark"]', ":root")
    else:
        # Neutralizza: Streamlit può impostare data-theme="dark" autonomamente
        css = css.replace('[data-theme="dark"]', ".__dark_disabled__")

    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)


def render_nav(active_page: str = "home") -> None:
    """Renderizza la navigazione laterale usando st.sidebar e st.page_link."""
    with st.sidebar:
        st.markdown("## Trova Prezzi Mio")
        st.page_link("pages/1_Home.py", label="Home", icon="🏠")
        st.page_link("pages/2_Tool.py", label="Cerca Prezzi", icon="🔍")
        st.markdown("---")
        render_theme_toggle()


def get_theme_mode() -> str:
    """Restituisce il tema corrente dalla session_state."""
    mode = str(st.session_state.get("ui_theme", "light") or "light").strip().lower()
    return mode if mode in {"light", "dark"} else "light"


def render_theme_toggle() -> None:
    """Renderizza il selettore tema e aggiorna la session_state."""
    col = st.columns([6, 1])[1]
    with col:
        st.selectbox(
            "Tema",
            ["light", "dark"],
            key="ui_theme",
            format_func=lambda v: "Light" if v == "light" else "Dark",
            label_visibility="collapsed",
        )

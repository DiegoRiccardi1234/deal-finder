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
        with open(_css_path, "r", encoding="utf-8") as f:
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
        st.markdown("<div class='sidebar-brand'>Trova Prezzi Mio</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='sidebar-health'>"
            "<span class='dot'></span>"
            "<span>System health</span>"
            "<strong>99.9%</strong>"
            "</div>",
            unsafe_allow_html=True,
        )
        st.caption("Presets")
        st.caption("History")
        st.caption("Preferiti")
        st.caption("Fast / Advanced")
        st.markdown("---")
        render_theme_toggle()


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

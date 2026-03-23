"""Entrypoint Streamlit minimale: carica stile e reindirizza alla Home."""

import streamlit as st

from _shared import load_css

st.set_page_config(
    page_title="Trova Prezzi",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

_theme_mode = str(st.session_state.get("ui_theme", "light") or "light").strip().lower()
if _theme_mode not in {"light", "dark"}:
    _theme_mode = "light"

load_css(theme_mode=_theme_mode)

import os
home_page = next((f for f in os.listdir("pages") if f.lower() == "1_home.py"), "1_Home.py")
st.switch_page(f"pages/{home_page}")


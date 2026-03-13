"""
app.py — Interfaccia Streamlit per Trova Prezzi
===================================================
Avvio:
    streamlit run app.py
"""

import contextlib
import csv
import io
import json
import os
import random
import re
import time
from typing import Any, Optional

import streamlit as st

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

CEREBRAS_MODEL = "gpt-oss-120b"

try:
    from offerte_tech import Offerta, cerca_offerte, parse_search_intent, parse_comparison_query
except ImportError as _e:
    st.error(
        f"❌ Impossibile importare offerte_tech.py: {_e}\n\n"
        "Assicurati che offerte_tech.py si trovi nella stessa cartella di app.py "
        "e che le dipendenze siano installate correttamente."
    )
    st.stop()

st.set_page_config(
    page_title="Trova Prezzi",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Manrope:wght@400;500;600;700;800&display=swap');

        :root {
            --bg: #0e0e12;
            --panel: rgba(20, 20, 28, 0.96);
            --panel-strong: rgba(22, 22, 30, 0.99);
            --ink: #e8e8e8;
            --muted: #a8a8b8;
            --accent: #c45c2d;
            --accent-dark: #e8825a;
            --line: rgba(255, 255, 255, 0.10);
            --shadow: 0 18px 48px rgba(0, 0, 0, 0.45);
            --radius: 22px;
            --surface: #16161e;
            --input-surface: #2a2a35;
        }

        html, body, [class*="css"] {
            font-family: 'Manrope', sans-serif;
            color: var(--ink);
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at top left, rgba(196, 92, 45, 0.09), transparent 28%),
                radial-gradient(circle at bottom right, rgba(60, 60, 120, 0.06), transparent 24%),
                linear-gradient(180deg, #111116 0%, #0e0e12 100%) !important;
        }

        [data-testid="stHeader"] {
            background: transparent;
        }

        .block-container {
            padding-top: 2.1rem;
            padding-bottom: 2.5rem;
            max-width: 1280px;
        }

        .hero-shell {
            position: relative;
            overflow: hidden;
            padding: 2rem 2.2rem;
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 28px;
            color: var(--ink);
            background:
                linear-gradient(135deg, rgba(30, 25, 20, 0.97), rgba(22, 20, 18, 0.93)),
                repeating-linear-gradient(135deg, rgba(196, 92, 45, 0.045) 0 14px, transparent 14px 28px);
            box-shadow: var(--shadow);
            margin-bottom: 1.2rem;
        }

        .hero-kicker {
            display: inline-block;
            margin-bottom: 0.75rem;
            padding: 0.32rem 0.72rem;
            border-radius: 999px;
            background: rgba(196, 92, 45, 0.22);
            color: #e8825a;
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }

        .hero-title {
            margin: 0;
            font-family: 'Fraunces', serif;
            font-size: clamp(2.4rem, 5vw, 4.2rem);
            line-height: 0.96;
            letter-spacing: -0.04em;
            max-width: 10ch;
        }

        .hero-copy {
            max-width: 52rem;
            margin-top: 0.95rem;
            color: var(--muted);
            font-size: 1rem;
            line-height: 1.7;
        }

        .hero-grid {
            display: grid;
            grid-template-columns: 1.6fr 1fr;
            gap: 1rem;
            align-items: end;
        }

        .hero-note {
            justify-self: end;
            width: 100%;
            max-width: 320px;
            padding: 1rem 1.1rem;
            border-radius: 20px;
            background: rgba(28, 28, 38, 0.97);
            color: var(--ink);
            border: 1px solid rgba(255, 255, 255, 0.08);
            box-shadow: 0 16px 30px rgba(0, 0, 0, 0.4);
        }

        .hero-note strong {
            display: block;
            margin-bottom: 0.35rem;
            font-size: 0.92rem;
            letter-spacing: 0.02em;
        }

        .section-card {
            border: 1px solid rgba(255, 255, 255, 0.5);
            border-radius: var(--radius);
            background: var(--panel);
            backdrop-filter: blur(8px);
            box-shadow: var(--shadow);
            padding: 0.35rem 0.4rem 0.7rem 0.4rem;
        }

        .section-heading {
            padding: 0.75rem 1rem 0.15rem 1rem;
        }

        .section-heading h3 {
            margin: 0;
            font-family: 'Fraunces', serif;
            font-size: 1.45rem;
            letter-spacing: -0.02em;
        }

        .section-heading p {
            margin: 0.3rem 0 0 0;
            color: var(--muted);
            font-size: 0.95rem;
        }

        .chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            margin-top: 0.8rem;
        }

        .chip {
            padding: 0.42rem 0.72rem;
            border-radius: 999px;
            background: rgba(196, 92, 45, 0.1);
            color: var(--ink);
            font-size: 0.84rem;
            font-weight: 700;
        }

        .spec-card {
            height: 100%;
            padding: 1rem 1rem 0.9rem 1rem;
            border: 1px solid var(--line);
            border-radius: 20px;
            background: var(--panel-strong);
        }

        .spec-card h4 {
            margin: 0 0 0.45rem 0;
            font-size: 1rem;
        }

        .spec-card p {
            margin: 0.2rem 0;
            color: var(--muted);
            font-size: 0.9rem;
            line-height: 1.5;
        }

        .spec-card strong {
            color: var(--ink);
        }

        [data-testid="stChatMessage"] {
            border: 1px solid rgba(255, 255, 255, 0.08);
            border-radius: 20px;
            background: rgba(22, 22, 32, 0.97);
            padding: 0.55rem 0.7rem;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.3);
        }

        [data-testid="stChatMessageContent"] p {
            line-height: 1.65;
        }

        [data-testid="stChatMessage"] p,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMetricValue"],
        [data-testid="stMetricLabel"] {
            color: var(--ink);
        }

        [data-testid="stMetric"] {
            border: 1px solid var(--line);
            border-radius: 18px;
            background: rgba(28, 28, 38, 0.98);
            padding: 0.2rem 0.35rem;
        }

        [data-testid="stMetricValue"] {
            color: var(--accent-dark);
            font-size: 1.58rem;
        }

        [data-testid="stMetricLabel"],
        [data-testid="stCaptionContainer"],
        label,
        .stMarkdown p,
        .stMarkdown span {
            color: var(--ink);
        }

        [data-testid="stDataFrame"] {
            border-radius: 18px;
            overflow: hidden;
            border: 1px solid var(--line);
        }

        div[data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        div[data-baseweb="base-input"] > div,
        .stNumberInput input,
        .stTextInput input {
            background: var(--input-surface);
            color: var(--ink);
        }

        .stButton button,
        .stDownloadButton button {
            border-radius: 999px;
            border: 1px solid rgba(196, 92, 45, 0.25);
            background: linear-gradient(180deg, #d46c3c 0%, #be5327 100%);
            color: white;
            font-weight: 800;
            letter-spacing: 0.01em;
            box-shadow: 0 14px 24px rgba(196, 92, 45, 0.18);
        }

        .stButton button:hover,
        .stDownloadButton button:hover {
            border-color: rgba(139, 61, 24, 0.45);
            color: white;
        }

        [data-theme="dark"] {
            --bg: #1a1a1a;
            --panel: rgba(28,28,28,0.96);
            --panel-strong: rgba(30,30,30,0.99);
            --ink: #e8e8e8;
            --muted: #b0b0b0;
            --line: rgba(255,255,255,0.12);
            --surface: #252525;
            --input-surface: #2d2d2d;
            --accent-dark: #e8825a;
        }

        [data-theme="dark"] [data-testid="stAppViewContainer"] {
            background: linear-gradient(180deg, #1a1a1a 0%, #1e1e1e 100%);
        }

        [data-theme="dark"] .hero-title,
        [data-theme="dark"] .hero-kicker,
        [data-theme="dark"] .hero-copy,
        [data-theme="dark"] .hero-note,
        [data-theme="dark"] .hero-note strong,
        [data-theme="dark"] .section-heading h3,
        [data-theme="dark"] .section-heading p,
        [data-theme="dark"] .chip,
        [data-theme="dark"] .spec-card h4,
        [data-theme="dark"] .spec-card p,
        [data-theme="dark"] .spec-card strong { color: var(--ink); }

        [data-theme="dark"] .hero-shell {
            background: linear-gradient(135deg, rgba(40,35,30,0.96), rgba(30,28,26,0.9));
            border-color: rgba(255,255,255,0.08);
        }

        [data-theme="dark"] .hero-note {
            background: rgba(35,35,35,0.97);
            box-shadow: 0 16px 30px rgba(0,0,0,0.4);
        }

        [data-theme="dark"] .section-card {
            background: rgba(28,28,28,0.96);
            border-color: rgba(255,255,255,0.08);
        }

        [data-theme="dark"] .spec-card {
            background: rgba(35,35,35,0.98);
            border-color: rgba(255,255,255,0.1);
        }

        [data-theme="dark"] [data-testid="stChatMessage"] {
            background: rgba(32,32,32,0.97);
            border-color: rgba(255,255,255,0.08);
        }

        [data-theme="dark"] [data-testid="stChatMessage"] p,
        [data-theme="dark"] [data-testid="stMarkdownContainer"] p,
        [data-theme="dark"] [data-testid="stMetricValue"],
        [data-theme="dark"] [data-testid="stMetricLabel"],
        [data-theme="dark"] label,
        [data-theme="dark"] .stMarkdown p,
        [data-theme="dark"] .stMarkdown span { color: #e8e8e8 !important; }

        [data-theme="dark"] [data-testid="stMetric"] { background: rgba(35,35,35,0.98); }

        [data-theme="dark"] .stTextInput input,
        [data-theme="dark"] .stNumberInput input { color: #e8e8e8 !important; background-color: #2d2d2d !important; }

        [data-theme="dark"] .hero-kicker {
            background: rgba(196, 92, 45, 0.22);
            color: #e8825a;
        }

        [data-theme="dark"] .chip {
            background: rgba(196, 92, 45, 0.2);
            color: #e8e8e8;
        }

        [data-theme="dark"] [data-testid="stMetricValue"] {
            color: #e8825a !important;
        }

        [data-theme="dark"] [data-testid="stMetric"] {
            background: rgba(40, 35, 30, 0.98);
            border-color: rgba(255,255,255,0.1);
        }

        [data-theme="dark"] [data-baseweb="tag"] {
            background: rgba(196, 92, 45, 0.3) !important;
            color: #e8e8e8 !important;
        }

        [data-theme="dark"] [data-testid="stExpander"] {
            background: rgba(35,35,35,0.97);
            border-color: rgba(255,255,255,0.08);
        }

        [data-theme="dark"] [data-testid="stCaptionContainer"] p,
        [data-theme="dark"] footer {
            color: #888888 !important;
        }

        [data-theme="dark"] [data-testid="stRadio"] label,
        [data-theme="dark"] [data-testid="stCheckbox"] label {
            color: #e8e8e8 !important;
        }

        [data-theme="dark"] [data-testid="stAlert"] {
            background: rgba(40, 35, 30, 0.97) !important;
            color: #e8e8e8 !important;
            border-color: rgba(196, 92, 45, 0.4) !important;
        }

        [data-theme="dark"] [data-baseweb="select"] > div,
        [data-theme="dark"] div[data-baseweb="input"] > div {
            background: #2d2d2d !important;
            color: #e8e8e8 !important;
            border-color: rgba(255,255,255,0.15) !important;
        }

        /* ── Dark mode: multiselect tags e dropdown ── */
        [data-theme="dark"] [data-baseweb="tag"] {
            background: rgba(196, 92, 45, 0.35) !important;
            color: #f0c8b0 !important;
        }
        [data-theme="dark"] [data-baseweb="menu"] {
            background: #2a2a2a !important;
        }
        [data-theme="dark"] [data-baseweb="menu"] [role="option"] {
            color: #e8e8e8 !important;
        }
        [data-theme="dark"] [data-baseweb="menu"] [aria-selected="true"] {
            background: rgba(196, 92, 45, 0.25) !important;
        }

        /* ── Dark mode: slider ── */
        [data-theme="dark"] [data-testid="stSlider"] [role="slider"] {
            background: var(--accent) !important;
        }
        [data-theme="dark"] [data-testid="stSlider"] [data-baseweb="slider"] > div:last-child {
            background: rgba(196, 92, 45, 0.3) !important;
        }

        /* ── Dark mode: tabella dataframe ── */
        [data-theme="dark"] [data-testid="stDataFrame"] {
            background: rgba(30, 30, 30, 0.98) !important;
        }
        [data-theme="dark"] [data-testid="stDataFrame"] .dvn-scroller,
        [data-theme="dark"] [data-testid="stDataFrame"] canvas {
            background: rgba(30, 30, 30, 0.98) !important;
        }

        /* ── Dark mode: expander ── */
        [data-theme="dark"] details,
        [data-theme="dark"] [data-testid="stExpander"] {
            background: rgba(30, 30, 30, 0.97) !important;
            border-color: rgba(255,255,255,0.08) !important;
        }
        [data-theme="dark"] details summary span,
        [data-theme="dark"] [data-testid="stExpander"] summary p {
            color: #e0e0e0 !important;
        }

        /* ── Dark mode: code block (log) ── */
        [data-theme="dark"] [data-testid="stCode"],
        [data-theme="dark"] [data-testid="stCode"] pre {
            background: #1a1a1a !important;
            color: #b8d4b0 !important;
            border-color: rgba(255,255,255,0.1) !important;
        }

        /* ── Dark mode: spinner / progress ── */
        [data-theme="dark"] [data-testid="stSpinner"] p {
            color: #e8e8e8 !important;
        }

        /* ── Dark mode: download button ── */
        [data-theme="dark"] .stDownloadButton button {
            background: linear-gradient(180deg, #c45c2d 0%, #a04020 100%) !important;
        }

        /* ── Dark mode: warning/info/success boxes ── */
        [data-theme="dark"] [data-testid="stWarningBox"],
        [data-theme="dark"] [data-testid="stInfoBox"] {
            background: rgba(40, 35, 30, 0.97) !important;
            color: #e0d0c0 !important;
        }
        [data-theme="dark"] [data-testid="stSuccessMessage"],
        [data-theme="dark"] [data-testid="stSuccess"] {
            background: rgba(30, 45, 30, 0.97) !important;
            color: #c8e0c8 !important;
        }

        /* ── Dark mode: hero shell ── */
        [data-theme="dark"] [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at top left, rgba(196, 92, 45, 0.10), transparent 28%),
                linear-gradient(180deg, #111116 0%, #0e0e12 100%) !important;
        }

        /* ── Dark mode: caption/footer ── */
        [data-theme="dark"] [data-testid="stCaptionContainer"] p,
        [data-theme="dark"] footer,
        [data-theme="dark"] small {
            color: #666 !important;
        }

        /* ── Chat input (dark di default) ── */
        [data-testid="stChatInput"] textarea,
        [data-testid="stChatInputContainer"] textarea {
            background: var(--input-surface) !important;
            color: var(--ink) !important;
            border-color: var(--line) !important;
        }

        /* ── Chat input dimensione compatta per la chat AI finale ── */
        [data-testid="stChatInputContainer"] {
            max-height: 56px !important;
        }
        [data-testid="stChatInputContainer"] textarea {
            min-height: 40px !important;
            max-height: 40px !important;
            resize: none !important;
            overflow: hidden !important;
            padding: 8px 12px !important;
        }

        /* ── Expander di default dark ── */
        details,
        [data-testid="stExpander"] {
            background: rgba(22, 22, 30, 0.97) !important;
            border-color: var(--line) !important;
        }
        details summary span,
        [data-testid="stExpander"] summary p {
            color: var(--ink) !important;
        }

        /* ── Inputs/select di default dark ── */
        [data-baseweb="select"] > div,
        div[data-baseweb="input"] > div,
        div[data-baseweb="base-input"] > div {
            background: var(--input-surface) !important;
            color: var(--ink) !important;
            border-color: var(--line) !important;
        }
        .stTextInput input,
        .stNumberInput input {
            color: var(--ink) !important;
            background-color: var(--input-surface) !important;
        }

        /* ── Alert/warning di default dark ── */
        [data-testid="stAlert"],
        [data-testid="stWarningBox"],
        [data-testid="stInfoBox"] {
            background: rgba(28, 28, 38, 0.97) !important;
            color: var(--ink) !important;
        }

        /* ── Dark mode: chat input (rinforzo) ── */
        [data-theme="dark"] [data-testid="stChatInput"] textarea,
        [data-theme="dark"] [data-testid="stChatInputContainer"] textarea {
            background: #2d2d2d !important;
            color: #e8e8e8 !important;
            border-color: rgba(255,255,255,0.15) !important;
        }

        /* ── Scrollbar (dark di default) ── */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        ::-webkit-scrollbar-track {
            background: #111116;
        }
        ::-webkit-scrollbar-thumb {
            background: rgba(196, 92, 45, 0.4);
            border-radius: 3px;
        }

        /* ── Dark mode: scrollbar (ridondante ma esplicito) ── */
        [data-theme="dark"] ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }
        [data-theme="dark"] ::-webkit-scrollbar-track {
            background: #1a1a1a;
        }
        [data-theme="dark"] ::-webkit-scrollbar-thumb {
            background: rgba(196, 92, 45, 0.4);
            border-radius: 3px;
        }

        @media (max-width: 900px) {
            .hero-grid {
                grid-template-columns: 1fr;
            }

            .hero-note {
                justify-self: stretch;
                max-width: none;
            }
        }
    </style>
    """,
    unsafe_allow_html=True,
)


def _init_state() -> None:
    defaults: dict[str, Any] = {
        "risultati": [],
        "log_ricerca": "",
        "ultima_query": "",
        "query_input": "",
        "_query_prefilled": "",
        "ricerca_effettuata": False,
        "condizione": "tutti",
        "ultimo_prezzo_min": 0,
        "ultimo_prezzo_max": 800,
        "ultimo_top_n": 20,
        "fonti_selezionate": ["Amazon", "eBay", "Vinted", "Euronics", "MediaWorld"],
        "price_min_input": 0,
        "budget_max_input": 800,
        "price_range_slider": (0, 800),
        "presearch_messages": [
            {
                "role": "assistant",
                "content": "Raccontami cosa cerchi su Trova Prezzi — qualsiasi tipo di prodotto "
                "(tech, abbigliamento, elettrodomestici, sport, libri...). "
                "Ti faccio al massimo 2 domande e poi avvio la ricerca.",
            }
        ],
        "presearch_question_count": 0,
        "presearch_ready": False,
        "query_ottimizzata": "",
        "categoria": "altro",
        "preferenze_utente": {"messaggi": [], "trascrizione": ""},
        "prezzo_min": 0,
        "budget_max": 800,
        "final_chat_messages": [],
        "filtri_ai": {},
        "filtri_ai_ultima_ricerca": {},
        "auto_recommend_tried": False,
        # Cache ricerca
        "_search_cache": {},
        # Filtri post-ricerca
        "filtro_fonti_tabella": [],
        "filtro_prezzo_range_tabella": None,
        "filtro_condizione_tabella": "tutti",
        # Comparatore
        "comparatore_selezione": [],
        # Confronto multiplo (vs mode)
        "comparison_mode": False,
        "comparison_queries": [],
        "comparison_results": {},
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


_init_state()


def _format_price(value: float) -> str:
    return f"€ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _get_cerebras_api_key() -> str:
    key = ""
    try:
        key = str(st.secrets.get("CEREBRAS_API_KEY", "") or "")
    except Exception:
        key = ""
    if not key.strip():
        key = os.environ.get("CEREBRAS_API_KEY", "")
    return key.strip()


def _is_test_mode() -> bool:
    return os.environ.get("APP_TEST_MODE", "0").strip() == "1"


class _MockCompletionMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _MockCompletionChoice:
    def __init__(self, content: str) -> None:
        self.message = _MockCompletionMessage(content)


class _MockCompletionResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_MockCompletionChoice(content)]


class _MockChatCompletions:
    def create(self, model: str, messages: list[dict[str, str]], temperature: float = 0.0) -> object:
        system_prompt = next((message.get("content", "") for message in messages if message.get("role") == "system"), "")
        user_payload = messages[-1].get("content", "") if messages else ""

        if "Sei un consulente acquisti esperto italiano" in system_prompt or "Sei un assistente shopping esperto italiano" in system_prompt:
            try:
                payload = json.loads(user_payload)
            except Exception:
                payload = {}
            transcript = str(payload.get("trascrizione", "") or "").lower()
            domande_fatte = int(payload.get("domande_fatte", 0) or 0)
            if domande_fatte <= 0:
                content = json.dumps({"domanda": "Qual e il tuo budget massimo?", "pronto": False}, ensure_ascii=False)
            elif domande_fatte == 1:
                content = json.dumps({"domanda": "Preferisci nuovo o usato?", "pronto": False}, ensure_ascii=False)
            else:
                categoria = "tech" if any(token in transcript for token in ("smartphone", "iphone", "telefono", "cellulare", "notebook", "laptop")) else "altro"
                # Estrai filtri_ai dal transcript
                filtri_ai_mock: dict[str, str] = {}
                ram_m = re.search(r"(\d{1,3})\s*gb\s*(?:di\s*)?ram", transcript)
                if ram_m:
                    filtri_ai_mock["ram"] = f"{ram_m.group(1)}gb"
                content = json.dumps(
                    {
                        "pronto": True,
                        "query": "smartphone nuovo" if "smartphone" in transcript else "notebook 14 pollici" if "notebook" in transcript else "prodotto cercato",
                        "prezzo_min": 200,
                        "budget_max": 800,
                        "categoria": categoria,
                        "filtri_ai": filtri_ai_mock,
                    },
                    ensure_ascii=False,
                )
            return _MockCompletionResponse(content)

        if "Sei un consulente shopping esperto" in system_prompt:
            product_names = re.findall(r'"nome":\s*"([^"]+)"', system_prompt)
            has_iphone_16 = any("iphone 16" in name.lower() for name in product_names)
            has_iphone_17 = any("iphone 17" in name.lower() for name in product_names)
            if has_iphone_16 and has_iphone_17:
                content = (
                    "Confronto rapido: iPhone 16 e iPhone 17 sono entrambi validi. "
                    "iPhone 16 conviene di più per prezzo/prestazioni, mentre iPhone 17 offre vantaggi su display e chip. "
                    "Se vuoi restare sotto i 1000€, consiglio iPhone 16; scegli iPhone 17 se vuoi il modello più recente."
                )
            else:
                product_name = product_names[0] if product_names else "Apple iPhone 17 128GB"
                content = (
                    f"Ti consiglio {product_name} a € 799,00. Per uso quotidiano offre il prezzo migliore, uno storage adeguato "
                    "e un equilibrio piu convincente tra display, autonomia e praticita rispetto alle alternative."
                )
            return _MockCompletionResponse(content)

        return _MockCompletionResponse("{}")


class _MockChat:
    def __init__(self) -> None:
        self.completions = _MockChatCompletions()


class _MockCerebrasClient:
    def __init__(self) -> None:
        self.chat = _MockChat()


def _get_cerebras_client(api_key: str) -> Optional[object]:
    if _is_test_mode():
        return _MockCerebrasClient()
    if not api_key or Cerebras is None:
        return None
    try:
        return Cerebras(api_key=api_key)
    except Exception:
        return None


def _cerebras_chat_with_retry(
    client: object,
    messages: list[dict[str, str]],
    temperature: float = 0.1,
    max_retries: int = 2,
) -> str:
    """Chiama Cerebras con retry silenzioso su errori 429/too_many_requests (2-3 s di attesa)."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1 + max_retries):
        try:
            completion = client.chat.completions.create(  # type: ignore[attr-defined]
                model=CEREBRAS_MODEL,
                messages=messages,
                temperature=temperature,
            )
            content = completion.choices[0].message.content if completion and completion.choices else ""
            return str(content or "").strip()
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc).lower()
            is_rate_limit = "429" in exc_str or "too_many" in exc_str or "queue" in exc_str
            if is_rate_limit and attempt < max_retries:
                time.sleep(random.uniform(2.0, 3.5))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return ""


def _extract_json_object(raw: str) -> dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}


def _infer_categoria_from_query(query: str) -> str:
    lower = str(query or "").lower()
    if any(token in lower for token in (
        "notebook", "laptop", "smartphone", "telefono", "cellulare", "iphone", "monitor",
        "ssd", "gpu", "tablet", "pc", "cuffie", "smartwatch", "fotocamera", "console"
    )):
        return "tech"
    if any(token in lower for token in ("giacca", "maglia", "vestito", "felpa", "pantaloni", "camicia")):
        return "abbigliamento"
    if any(t in lower for t in ("scarpe", "sneaker", "stivali", "sandali")):
        return "scarpe"
    if any(t in lower for t in ("tv", "televisore", "smart tv", "oled", "qled", "schermo")):
        return "televisore"
    if any(t in lower for t in ("frigo", "frigorifero", "lavatrice", "forno", "lavastoviglie")):
        return "elettrodomestico"
    if any(t in lower for t in ("libro", "romanzo", "fumetto", "manga")):
        return "libri"
    if any(t in lower for t in ("bici", "tapis", "manubri", "pallone", "running", "trail")):
        return "sport"
    return "altro"


def _sanitize_presearch_payload(payload: dict[str, Any], transcript: str) -> dict[str, Any]:
    raw_query = str(payload.get("query", "") or "").strip()
    query = " ".join(raw_query.split())
    if not query:
        intent = parse_search_intent(transcript)
        query = str(intent.get("query", "") or "").strip()
    query = " ".join(query.split()[:5])
    categoria = str(payload.get("categoria", "") or "").strip().lower()
    if not categoria:
        categoria = _infer_categoria_from_query(query)

    try:
        prezzo_min = int(float(str(payload.get("prezzo_min", 0) or 0)))
    except Exception:
        prezzo_min = 0

    try:
        budget_max = int(float(str(payload.get("budget_max", 800) or 800)))
    except Exception:
        budget_max = 800

    prezzo_min = max(0, prezzo_min)
    budget_max = max(prezzo_min, budget_max)

    # Extract filtri_ai (spec filters)
    filtri_ai_raw = payload.get("filtri_ai", {})
    filtri_ai: dict[str, str] = {}
    if isinstance(filtri_ai_raw, dict):
        for k, v in filtri_ai_raw.items():
            key = str(k or "").strip().lower()
            val = str(v or "").strip()
            if key and val:
                filtri_ai[key] = val

    # Estrai condizione dal JSON AI oppure dal transcript come fallback
    raw_cond = str(payload.get("condizione", "") or "").strip().lower()
    if raw_cond in {"nuovo", "usato"}:
        condizione = raw_cond
    elif re.search(r"\busato\b|ricondizionato|second.hand", transcript):
        condizione = "usato"
    elif re.search(r"\bnuovo\b|nuovissimo|sigillato|mai aperto", transcript):
        condizione = "nuovo"
    else:
        condizione = "tutti"

    return {
        "pronto": bool(payload.get("pronto", False)),
        "domanda": str(payload.get("domanda", "") or "").strip(),
        "query": query,
        "prezzo_min": prezzo_min,
        "budget_max": budget_max,
        "categoria": categoria,
        "filtri_ai": filtri_ai,
        "condizione": condizione,
    }


def _sync_from_numbers() -> None:
    min_value = int(st.session_state.get("price_min_input", 0) or 0)
    max_value = int(st.session_state.get("budget_max_input", 800) or 800)
    min_value = max(0, min(min_value, 5000))
    max_value = max(min_value, min(max_value, 5000))
    st.session_state["price_min_input"] = min_value
    st.session_state["budget_max_input"] = max_value
    st.session_state["price_range_slider"] = (min_value, max_value)


def _sync_from_slider() -> None:
    min_value, max_value = st.session_state.get("price_range_slider", (0, 800))
    st.session_state["price_min_input"] = int(min_value)
    st.session_state["budget_max_input"] = int(max_value)


def _reset_presearch_chat() -> None:
    st.session_state["presearch_messages"] = [
        {
            "role": "assistant",
            "content": "Raccontami cosa cerchi — tipo prodotto, budget, uso e preferenze. "
            "Se mi dai già abbastanza informazioni, preparo la query in un colpo solo senza fare altre domande.",
        }
    ]
    st.session_state["presearch_question_count"] = 0
    st.session_state["presearch_ready"] = False
    st.session_state["query_ottimizzata"] = ""
    st.session_state["categoria"] = "altro"
    st.session_state["filtri_ai"] = {}
    st.session_state["preferenze_utente"] = {"messaggi": [], "trascrizione": ""}
    st.session_state["prezzo_min"] = 0
    st.session_state["budget_max"] = int(st.session_state.get("budget_max_input", 800))
    st.session_state["_query_prefilled"] = ""
    st.session_state["condizione"] = "tutti"
    st.session_state["comparison_mode"] = False
    st.session_state["comparison_queries"] = []
    st.session_state["comparison_results"] = {}


def _apply_presearch_result(result: dict[str, Any]) -> None:
    transcript = str(st.session_state.get("preferenze_utente", {}).get("trascrizione", "") or "")
    sanitized = _sanitize_presearch_payload(result, transcript)
    st.session_state["query_ottimizzata"] = sanitized["query"]
    st.session_state["prezzo_min"] = sanitized["prezzo_min"]
    st.session_state["budget_max"] = sanitized["budget_max"]
    st.session_state["categoria"] = sanitized["categoria"]
    st.session_state["filtri_ai"] = sanitized.get("filtri_ai", {})
    # Applica condizione estratta dalla chat (non sovrascrive se già impostata manualmente)
    if sanitized.get("condizione") and sanitized["condizione"] != "tutti":
        st.session_state["condizione"] = sanitized["condizione"]
    st.session_state["presearch_ready"] = True
    st.session_state["ultimo_top_n"] = 20
    st.session_state["_query_prefilled"] = sanitized["query"]
    st.session_state["ultima_query"] = sanitized["query"]
    st.session_state["price_min_input"] = sanitized["prezzo_min"]
    st.session_state["budget_max_input"] = sanitized["budget_max"]
    st.session_state["price_range_slider"] = (sanitized["prezzo_min"], sanitized["budget_max"])
    st.session_state["preferenze_utente"] = {
        **st.session_state.get("preferenze_utente", {}),
        "categoria": sanitized["categoria"],
        "query_finale": sanitized["query"],
        "prezzo_min": sanitized["prezzo_min"],
        "budget_max": sanitized["budget_max"],
        "condizione": sanitized.get("condizione", "tutti"),
    }


def _presearch_fallback() -> dict[str, Any]:
    preferenze = st.session_state.get("preferenze_utente", {})
    transcript = str(preferenze.get("trascrizione", "") or "").lower()
    turni = int(st.session_state.get("presearch_question_count", 0))

    # Estrai budget dal transcript
    prezzo_min = 0
    budget_max = 800
    m_min = re.search(r"(?:min(?:imo)?|da|partire da)\s*(\d{2,5})", transcript)
    m_max = re.search(r"(?:max(?:imo)?|massimo|fino a|budget)\s*(\d{2,5})", transcript)
    m_range = re.search(r"(\d{2,5})\s*[-/a]\s*(\d{2,5})", transcript)
    if m_range:
        prezzo_min = int(m_range.group(1))
        budget_max = int(m_range.group(2))
    else:
        if m_min:
            prezzo_min = int(m_min.group(1))
        if m_max:
            budget_max = int(m_max.group(1))

    has_budget = m_range or m_min or m_max
    has_product = any(kw in transcript for kw in (
        "notebook", "laptop", "smartphone", "telefono", "iphone", "monitor",
        "tablet", "cuffie", "auricolari", "scarpe", "felpa", "giacca", "mouse", "tastiera",
        "ssd", "pc", "console", "smartwatch",
        "frigorifero", "lavatrice", "forno", "tv", "televisore",
        "libro", "romanzo", "sneaker", "bici", "divano",
        "crema", "profumo",
    ))

    # Stima prezzo_min ragionevole in base al tipo prodotto rilevato nel transcript
    if prezzo_min == 0 and has_product:
        _is_used = bool(re.search(r"\busato\b|ricondizionato|second.hand", transcript))
        if "iphone" in transcript or "macbook" in transcript:
            prezzo_min = 300 if _is_used else 700
        elif any(kw in transcript for kw in ("notebook", "laptop", "pc")):
            prezzo_min = 200 if _is_used else 250
        elif any(kw in transcript for kw in ("smartphone", "telefono", "samsung", "xiaomi")):
            prezzo_min = 80 if _is_used else 150
        elif any(kw in transcript for kw in ("tablet",)):
            prezzo_min = 80 if _is_used else 100
        elif any(kw in transcript for kw in ("tv", "televisore")):
            prezzo_min = 150 if _is_used else 200
        elif any(kw in transcript for kw in ("console",)):
            prezzo_min = 100 if _is_used else 200
        elif any(kw in transcript for kw in ("smartwatch",)):
            prezzo_min = 40 if _is_used else 80
        elif any(kw in transcript for kw in ("cuffie", "auricolari")):
            prezzo_min = 15 if _is_used else 30

    # Estrai filtri_ai (specs) dal transcript
    filtri_ai: dict[str, str] = {}
    ram_match = re.search(r"(\d{1,3})\s*gb\s*(?:di\s*)?ram", transcript)
    if ram_match:
        filtri_ai["ram"] = f"{ram_match.group(1)}gb"
    storage_match = re.search(r"(\d{2,4})\s*gb\s*(?:di\s*)?(?:ssd|storage|disco|memoria)", transcript)
    if storage_match:
        filtri_ai["storage"] = f"{storage_match.group(1)}gb"
    tb_match = re.search(r"(\d)\s*tb", transcript)
    if tb_match:
        filtri_ai["storage"] = f"{tb_match.group(1)}tb"
    display_match = re.search(r"(\d{2})\s*(?:pollici|inch|\")", transcript)
    if display_match:
        filtri_ai["display"] = f"{display_match.group(1)}\""
    proc_match = re.search(r"\b(i[357]|i9|ryzen\s*[357]|m[123]|snapdragon|celeron|pentium)\b", transcript)
    if proc_match:
        filtri_ai["processore"] = proc_match.group(1)
    # Estrai condizione
    condizione_match = "tutti"
    if re.search(r"\busato\b|ricondizionato|second.hand", transcript):
        condizione_match = "usato"
    elif re.search(r"\bnuovo\b|nuovissimo|sigillato|mai aperto", transcript):
        condizione_match = "nuovo"

    if turni < 1 and not has_budget:
        return {"pronto": False, "domanda": "Qual e il tuo budget ideale o il range di prezzo?"}
    if turni < 2 and not has_product:
        return {"pronto": False, "domanda": "Che tipo di prodotto stai cercando?"}
    if turni < 2 and has_product and not has_budget:
        return {"pronto": False, "domanda": "Qual e il tuo budget ideale o il range di prezzo?"}

    # Costruisci query pulita dal transcript
    product_tokens = []
    for kw in ("notebook", "laptop", "smartphone", "iphone", "samsung", "xiaomi",
               "monitor", "tablet", "cuffie", "mouse", "tastiera", "ssd", "pc",
               "console", "smartwatch", "scarpe", "felpa", "giacca",
               "frigorifero", "lavatrice", "forno", "tv", "televisore",
               "libro", "romanzo", "sneaker", "bici", "divano", "crema", "profumo"):
        if kw in transcript:
            product_tokens.append(kw)
            break
    # Estrai dimensione (es. 14 pollici)
    dim_match = re.search(r"(\d{2})\s*(?:pollici|\")", transcript)
    if dim_match:
        product_tokens.append(f"{dim_match.group(1)} pollici")
    # Marca
    for brand in ("lenovo", "hp", "dell", "asus", "acer", "apple", "msi", "samsung", "huawei"):
        if brand in transcript and brand not in product_tokens:
            product_tokens.append(brand)
            break

    query = " ".join(product_tokens) if product_tokens else transcript.split("\n")[0].strip()
    query = " ".join(query.split()[:5])

    return {
        "pronto": True,
        "query": query,
        "prezzo_min": prezzo_min,
        "budget_max": budget_max,
        "categoria": _infer_categoria_from_query(query),
        "filtri_ai": filtri_ai,
        "condizione": condizione_match,
    }


def _run_presearch_step(user_message: str, api_key: str) -> None:
    cleaned = str(user_message or "").strip()
    if not cleaned:
        return

    st.session_state["presearch_messages"].append({"role": "user", "content": cleaned})
    preferenze = dict(st.session_state.get("preferenze_utente", {}))
    messaggi = list(preferenze.get("messaggi", []))
    messaggi.append(cleaned)
    transcript = "\n".join(messaggi)
    preferenze["messaggi"] = messaggi
    preferenze["trascrizione"] = transcript
    st.session_state["preferenze_utente"] = preferenze

    # ── Detect confronto (es. "iphone 15 vs iphone 16") ──────────────────
    comp_queries = parse_comparison_query(cleaned)
    if comp_queries:
        # Estrai budget dal messaggio utente
        _budget_m = re.search(r'(?:massimo|max|budget|sotto|meno di|fino a)\s*(\d+)\s*(?:euro|€)?', cleaned.lower())
        _budget = min(int(_budget_m.group(1)), 5000) if _budget_m else 2000
        # Estrai condizione
        _cond = "tutti"
        if re.search(r'\bnuov[oai]\b', cleaned.lower()):
            _cond = "nuovo"
        elif re.search(r'\busat[oai]\b', cleaned.lower()):
            _cond = "usato"
        # Stima prezzo_min in base al tipo prodotto
        _pmin = 0
        _combined_lower = ' '.join(comp_queries).lower()
        if 'iphone' in _combined_lower:
            _pmin = 500 if _cond != 'usato' else 200
        elif any(kw in _combined_lower for kw in ('macbook', 'laptop', 'notebook')):
            _pmin = 400 if _cond != 'usato' else 200
        elif any(kw in _combined_lower for kw in ('samsung', 'galaxy', 'pixel')):
            _pmin = 300 if _cond != 'usato' else 150
        elif any(kw in _combined_lower for kw in ('ipad', 'tablet')):
            _pmin = 200 if _cond != 'usato' else 100

        _cq_label = " · ".join(comp_queries)
        _comp_display = " vs ".join(comp_queries)
        st.session_state["comparison_mode"] = True
        st.session_state["comparison_queries"] = comp_queries
        st.session_state["presearch_ready"] = True
        st.session_state["ultimo_top_n"] = 20
        st.session_state["query_ottimizzata"] = comp_queries[0]
        st.session_state["_query_prefilled"] = _comp_display
        st.session_state["ultima_query"] = _comp_display
        st.session_state["prezzo_min"] = _pmin
        st.session_state["budget_max"] = _budget
        st.session_state["price_min_input"] = _pmin
        st.session_state["budget_max_input"] = _budget
        st.session_state["price_range_slider"] = (_pmin, _budget)
        st.session_state["condizione"] = _cond
        st.session_state["presearch_messages"].append({
            "role": "assistant",
            "content": (
                f"Ho rilevato una ricerca confronto fra **{len(comp_queries)} prodotti**!\n\n"
                f"Cercherò in parallelo: **{_cq_label}**\n\n"
                f"Range prezzo: {_pmin}€ – {_budget}€ · Condizione: {_cond}\n\n"
                "Puoi modificare il range prezzo, poi clicca **Cerca offerte** "
                "per vedere i risultati fianco a fianco."
            ),
        })
        return

    history_text = "\n".join(
        [
            f"{'Assistente' if message.get('role') == 'assistant' else 'Utente'}: {message.get('content', '')}"
            for message in st.session_state.get("presearch_messages", [])
        ]
    )

    client = cerebras_client
    result: dict[str, Any]

    if client is None:
        result = _presearch_fallback()
    else:
        system_prompt = (
            "Sei un consulente acquisti esperto italiano. Il tuo obiettivo e' capire le esigenze dell'utente "
            "per dargli una RACCOMANDAZIONE FINALE motivata e personalizzata.\n"
            "Per farlo devi conoscere: 1) categoria/tipo prodotto, 2) uso principale, 3) budget, "
            "4) preferenze fisiche/hardware.\n"
            "Categorie supportate: smartphone, laptop, tablet, televisore, elettrodomestico, abbigliamento, scarpe, sport, libri, beauty, casa, altro.\n"
            "REGOLE IMPORTANTI:\n"
            "- Se il messaggio contiene gia' tipo prodotto + budget/range e almeno 1 altra preferenza: vai SUBITO a pronto:true\n"
            "- Se manca tipo prodotto OPPURE budget: fai UNA SOLA domanda specifica\n"
            "- NON chiedere cose gia' dette\n"
            "- Dopo max 3 domande vai sempre a pronto:true\n"
            "REGOLE QUERY:\n"
            "- Il campo 'prezzo_min' DEVE essere REALISTICO in base a tipo prodotto + condizione:\n"
            "  iPhone nuovo→700, usato→300 | MacBook/laptop gaming nuovo→900, usato→400\n"
            "  Smartphone Android top→400, fascia media→150 | Laptop base/office→250\n"
            "  Smartwatch→80 | Tablet→100 | TV 50+\"→300 | Cuffie→30 | Console→200\n"
            "  NON usare MAI 0 come prezzo_min — stima sempre un minimo ragionevole\n"
            "- Il campo 'query' deve essere BREVE: tipo/dimensione/marca, MAX 5 parole, NO frasi, NO budget\n"
            "- Se l'utente menziona due modelli alternativi (es 'iphone 16 o 17', 'notebook 14 o 15'): usa la query PIU' GENERICA\n"
            "  che li cattura entrambi. Non fissare il numero di modello specifico — lascia che lo scraper trovi entrambi.\n"
            "  Esempi: 'iphone 16 o 17' → query 'iphone 128gb', '14 o 15 pollici' → 'notebook windows'\n"
            "- Le specifiche tecniche (RAM, storage...) vanno in 'filtri_ai', NON nella query\n"
            "- Le preferenze utente (uso, materiale, marca...) vanno in 'contesto_extra' (stringa)\n"
            "Rispondi SOLO in JSON valido:\n"
            "- Se servono ancora info: {\"domanda\": \"...\", \"pronto\": false}\n"
            "- Se hai abbastanza info: {\"pronto\": true, \"query\": \"...\", \"prezzo_min\": N, \"budget_max\": N, "
            "\"categoria\": \"tech|abbigliamento|televisore|elettrodomestico|scarpe|sport|libri|beauty|casa|altro\", \"condizione\": \"nuovo|usato|tutti\", "
            "\"filtri_ai\": {}, \"contesto_extra\": \"...\"}\n"
            f"Cronologia conversazione finora:\n{history_text}\n\nNuovo messaggio utente: {cleaned}"
        )
        user_payload = {
            "messaggi_utente": messaggi,
            "trascrizione": transcript,
            "domande_fatte": int(st.session_state.get("presearch_question_count", 0)),
            "forza_chiusura": int(st.session_state.get("presearch_question_count", 0)) >= 3,
        }
        try:
            raw = _cerebras_chat_with_retry(
                client,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                temperature=0.1,
            )
            result = _extract_json_object(raw)
        except Exception:
            result = {}

        if not result:
            result = _presearch_fallback()

    sanitized = _sanitize_presearch_payload(result, transcript)
    if sanitized.get("pronto"):
        _apply_presearch_result(sanitized)
        filtri_msg = ""
        filtri_ai_result = sanitized.get("filtri_ai", {})
        if filtri_ai_result:
            filtri_parts = [f"{k}: {v}" for k, v in filtri_ai_result.items()]
            filtri_msg = f" · filtri specs: {', '.join(filtri_parts)}"
        extra_ctx = str(result.get("contesto_extra", "") or "").strip()
        if extra_ctx:
            # Arricchisce preferenze_utente con il contesto extra per la raccomandazione finale
            _prev = st.session_state.get("preferenze_utente", {})
            _prev["contesto_extra"] = extra_ctx
            st.session_state["preferenze_utente"] = _prev
        st.session_state["presearch_messages"].append(
            {
                "role": "assistant",
                "content": (
                    f"Ho preparato la ricerca: **{sanitized['query']}** · "
                    f"range {sanitized['prezzo_min']}€ - {sanitized['budget_max']}€ · "
                    f"categoria {sanitized['categoria']}{filtri_msg}.\n"
                    "Ora puoi affinare le fonti o il range e cliccare **Cerca offerte**."
                ),
            }
        )
        return

    question_count = int(st.session_state.get("presearch_question_count", 0)) + 1
    st.session_state["presearch_question_count"] = question_count

    if question_count >= 3:
        fallback_finale = _presearch_fallback()
        fallback_finale["pronto"] = True
        _apply_presearch_result(fallback_finale)
        st.session_state["presearch_messages"].append(
            {
                "role": "assistant",
                "content": (
                    "Ho abbastanza elementi per partire. Ho precompilato la query finale e il range prezzo."
                ),
            }
        )
        return

    domanda = sanitized.get("domanda") or "Qual e il dettaglio piu importante che vuoi fissare prima di cercare?"
    st.session_state["presearch_messages"].append({"role": "assistant", "content": str(domanda)})


def _build_products_payload(offerte: list[Offerta]) -> list[dict[str, Any]]:
    return [
        {
            "nome": offerta.nome,
            "prezzo": round(offerta.prezzo, 2),
            "negozio": offerta.negozio,
            "link": offerta.link,
            "specs": offerta.specs,
        }
        for offerta in sorted(offerte, key=lambda item: item.prezzo)[:10]
    ]


def _build_comparison_payload() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Prepara payload bilanciato per raccomandazione AI in modalità confronto."""
    if not st.session_state.get("comparison_mode"):
        return [], []

    cmp_results = st.session_state.get("comparison_results", {})
    if not isinstance(cmp_results, dict) or not cmp_results:
        return [], []

    products_payload: list[dict[str, Any]] = []
    summary_payload: list[dict[str, Any]] = []

    for query, results in cmp_results.items():
        if not isinstance(results, list):
            continue
        ordered = sorted(results, key=lambda item: item.prezzo)
        if ordered:
            summary_payload.append(
                {
                    "query": str(query),
                    "best_name": ordered[0].nome,
                    "best_price": round(ordered[0].prezzo, 2),
                    "best_store": ordered[0].negozio,
                    "count": len(ordered),
                }
            )

        # Bilancia il contesto: massimo 4 offerte per ogni query del confronto.
        for offerta in ordered[:4]:
            products_payload.append(
                {
                    "query": str(query),
                    "nome": offerta.nome,
                    "prezzo": round(offerta.prezzo, 2),
                    "negozio": offerta.negozio,
                    "link": offerta.link,
                    "specs": offerta.specs,
                }
            )

    return products_payload, summary_payload


def _call_final_recommendation(
    cerebras_client: object,
    offerte: list[Offerta],
    preferenze_utente: dict[str, Any],
    messages: list[dict[str, str]],
) -> str:
    # Includi la trascrizione chat pre-ricerca nel contesto per personalizzare meglio il consiglio
    trascrizione = str(preferenze_utente.get("trascrizione", "") or "").strip()
    contesto_utente = json.dumps({
        k: v for k, v in preferenze_utente.items() if k != "messaggi"
    }, ensure_ascii=False)
    context_block = f"PREFERENZE UTENTE: {contesto_utente}\n"
    if trascrizione:
        context_block += f"CONVERSAZIONE PRE-RICERCA (usa per capire tono e priorita dell'utente):\n{trascrizione}\n"
    comparison_products, comparison_summary = _build_comparison_payload()
    products_payload = comparison_products if comparison_products else _build_products_payload(offerte)

    comparison_block = ""
    if comparison_summary:
        comparison_block = (
            "CONTESTO CONFRONTO ATTIVO:\n"
            f"{json.dumps(comparison_summary, ensure_ascii=False)}\n"
            "Nel confronto cita esplicitamente ogni modello richiesto dall'utente "
            "(es. iPhone 16 e iPhone 17), anche se uno risulta meno conveniente.\n"
        )

    system_prompt = (
        "Sei un consulente shopping esperto italiano. Hai questi dati:\n"
        f"{context_block}"
        f"{comparison_block}"
        f"PRODOTTI TROVATI (ordinati per prezzo):\n{json.dumps(products_payload, ensure_ascii=False)}\n"
        "Rispondi in italiano con una raccomandazione motivata e personalizzata sulle esigenze emerse dalla conversazione. "
        "Cita nome e prezzo dei prodotti consigliati, confronta almeno 2-3 parametri rilevanti per l'utente. "
        "Sii diretto e concreto."
    )
    payload = [{"role": "system", "content": system_prompt}] + messages
    return _cerebras_chat_with_retry(cerebras_client, payload, temperature=0.2)


def _build_mock_results(query: str, categoria: str, prezzo_min: int, budget_max: int) -> list[Offerta]:
    base_results = [
        Offerta(
            nome="Apple iPhone 17 128GB",
            prezzo=799.0,
            negozio="Mock Store",
            link="https://example.com/iphone-17-128",
            fonte="amazon.it",
            spedizione="Prime ✅",
            specs={"display": "6.1\" OLED", "processore": "A19", "ram": "8 GB", "storage": "128 GB"},
        ),
        Offerta(
            nome="Apple iPhone 17 256GB",
            prezzo=899.0,
            negozio="Mock Store Plus",
            link="https://example.com/iphone-17-256",
            fonte="ebay.it",
            spedizione="€ 7,99",
            specs={"display": "6.1\" OLED", "processore": "A19", "ram": "8 GB", "storage": "256 GB"},
        ),
        Offerta(
            nome="Samsung Galaxy S25 256GB",
            prezzo=749.0,
            negozio="Mock Galaxy Shop",
            link="https://example.com/galaxy-s25",
            fonte="amazon.it",
            spedizione="Gratuita ✅",
            specs={"display": "6.2\" AMOLED", "processore": "Snapdragon", "ram": "12 GB", "storage": "256 GB"},
        ),
    ]
    categoria_norm = str(categoria or "altro").lower()
    results = base_results if categoria_norm == "tech" or "iphone" in query.lower() else [
        Offerta(
            nome="Nike Felpa Donna M Cotone",
            prezzo=59.0,
            negozio="Mock Fashion",
            link="https://example.com/felpa",
            fonte="vinted.it",
            spedizione="€ 4,99",
            specs={"brand": "Nike", "taglia": "M", "materiale": "cotone", "genere": "donna"},
        )
    ]
    return [item for item in results if prezzo_min <= item.prezzo <= budget_max]


def _offerte_to_csv_bytes(offerte: list[Offerta]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["posizione", "nome", "prezzo_eur", "spedizione", "negozio", "fonte", "link"],
        lineterminator="\n",
    )
    writer.writeheader()
    for i, offerta in enumerate(offerte, start=1):
        writer.writerow(
            {
                "posizione": i,
                "nome": offerta.nome,
                "prezzo_eur": f"{offerta.prezzo:.2f}",
                "spedizione": offerta.spedizione,
                "negozio": offerta.negozio,
                "fonte": offerta.fonte,
                "link": offerta.link,
            }
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _specs_from_name(nome: str) -> str:
    """Estrae specs di base dal nome prodotto tramite regex (fallback quando specs strutturate sono assenti)."""
    parts: list[str] = []
    text = nome.lower()
    m = re.search(r'(\d{1,2}[,.]?\d*)\s*(?:"|\'\'{2}|pollici?\b)', text)
    if m:
        parts.append(f"Display: {m.group(1)}\"")
    m = re.search(r'(\d{1,3})\s*gb\s*(?:di\s*)?(?:ram|lpddr|ddr)', text)
    if m:
        parts.append(f"Ram: {m.group(1)}GB")
    m = re.search(r'(\d{1,4})\s*(tb|gb)\s*(?:ssd|nvme|m\.2|emmc)', text)
    if m:
        unit = m.group(2).upper()
        parts.append(f"SSD: {m.group(1)}{unit}")
    m = re.search(r'\b(i[357]-\d{4,5}[a-z]*|i[357]\s+\d{4,5}[a-z]*|ryzen\s*[357]\s*\d{4}[a-z]*|core\s*ultra\s*[57]\s*\d{3}|celeron\s*n\d+)\b', text)
    if m:
        parts.append(f"CPU: {m.group(1).title()}")
    return " · ".join(parts)


def _summarize_specs(specs: dict[str, Any], nome: str = "") -> str:
    if not specs:
        return _specs_from_name(nome) if nome else ""
    parts = []
    for key, value in specs.items():
        if value in (None, "", [], {}):
            continue
        label = str(key).replace("_", " ").capitalize()
        parts.append(f"{label}: {value}")
    if parts:
        return " · ".join(parts)
    return _specs_from_name(nome) if nome else ""


def _offerte_to_records(offerte: list[Offerta]) -> list[dict[str, Any]]:
    return [
        {
            "#": i,
            "Prodotto": offerta.nome,
            "Prezzo €": round(offerta.prezzo, 2),
            "Spedizione": offerta.spedizione,
            "Negozio": offerta.negozio,
            "Fonte": offerta.fonte,
            "Specs": _summarize_specs(offerta.specs, offerta.nome),
            "Link": offerta.link,
        }
        for i, offerta in enumerate(offerte, start=1)
    ]


def _render_specs_grid(offerte: list[Offerta]) -> None:
    """Renders una griglia delle specifiche per le offerte con dati specs."""
    # Filtra solo le offerte che hanno specifiche valide
    offerte_con_specs = [o for o in offerte if o.specs and isinstance(o.specs, dict) and any(v not in (None, "", [], {}) for v in o.specs.values())]
    if not offerte_con_specs:
        st.info("📋 Nessun dato di specifiche rilevato per i prodotti.")
        return

    st.markdown(
        "<div class='section-heading'><h3>Specs rilevate</h3><p>Arricchimento automatico basato sulla categoria della ricerca.</p></div>",
        unsafe_allow_html=True,
    )
    # Mostra al massimo 6 prodotti nella grid
    preview = offerte_con_specs[:6]
    for start in range(0, len(preview), 2):
        cols = st.columns(2, gap="medium")
        for idx, offerta in enumerate(preview[start:start + 2]):
            specs_rows = []
            for key, value in offerta.specs.items():
                if value in (None, "", [], {}):
                    continue
                label = str(key).replace("_", " ").title()
                specs_rows.append(f"<p><strong>{label}</strong>: {value}</p>")
            specs_html = "".join(specs_rows) or "<p>Specifiche non disponibili.</p>"
            cols[idx].markdown(
                (
                    "<div class='spec-card'>"
                    f"<h4>{offerta.nome}</h4>"
                    f"<p><strong>{_format_price(offerta.prezzo)}</strong> · {offerta.negozio}</p>"
                    f"{specs_html}"
                    "</div>"
                ),
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
    cerebras_client: Optional[object],
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

    with st.status(f"⏳ Confronto in corso per {len(queries)} prodotti...", expanded=True) as cmp_status:
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


def _run_search(
    *,
    query: str,
    prezzo_min: int,
    budget_max: int,
    top_n: int,
    condizione: str,
    fonti_backend: list[str],
    cerebras_client: Optional[object],
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

    categoria = str(st.session_state.get("categoria", "altro") or "altro")
    if not categoria:
        categoria = _infer_categoria_from_query(query)

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
        st.session_state["log_ricerca"] = "[mock-mode] risultati generati localmente per la suite UI"
        st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
        return

    # ── Cache: stessa ricerca entro 5 minuti → riusa i risultati ──────────────
    _cache_key = (
        query.strip().lower(), int(prezzo_min), int(budget_max),
        condizione, tuple(sorted(fonti_backend)),
    )
    _cache = st.session_state.get("_search_cache", {})
    if _cache.get("key") == _cache_key and (time.time() - float(_cache.get("ts", 0))) < 300:
        st.session_state["risultati"] = _cache["risultati"]
        st.session_state["log_ricerca"] = _cache.get("log", "")
        st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
        st.toast("⚡ Risultati dalla cache (< 5 min) — clicca di nuovo Cerca per aggiornare.")
        return

    # Reset filtri tabella per la nuova ricerca
    st.session_state["filtro_fonti_tabella"] = []
    st.session_state["filtro_prezzo_range_tabella"] = None
    st.session_state["filtro_condizione_tabella"] = "tutti"
    st.session_state["comparatore_selezione"] = []
    # Reset chat AI post-ricerca e flag auto top-3 ad ogni nuova ricerca
    st.session_state["final_chat_messages"] = []
    st.session_state["auto_recommend_tried"] = False

    try:
        with st.status("⏳ Ricerca in corso sulle fonti selezionate...", expanded=True) as search_status:

            def on_source_done(source_label: str, count: int) -> None:
                if count > 0:
                    st.write(f"✅ **{source_label}** → {count} {'risultato' if count == 1 else 'risultati'}")
                elif count == -2:
                    st.write(f"⚙️ **{source_label}** → non configurato (chiavi API mancanti)")
                elif count == -1:
                    st.write(f"❌ **{source_label}** → errore inatteso durante lo scraping")
                else:
                    st.write(f"⚪ **{source_label}** → nessun risultato nel range selezionato")

            try:
                with contextlib.redirect_stdout(log_buffer):
                    risultati = cerca_offerte(
                        query=query,
                        budget_max=float(budget_max),
                        prezzo_min=float(prezzo_min),
                        filtri_ai=st.session_state.get("filtri_ai", {}),
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
        st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
        # Salva in cache per 5 minuti
        st.session_state["_search_cache"] = {
            "key": _cache_key,
            "ts": time.time(),
            "risultati": risultati,
            "log": log_buffer.getvalue(),
        }
    except Exception as exc:
        st.session_state["log_ricerca"] = log_buffer.getvalue()
        st.error(
            f"❌ Si e verificato un errore durante la ricerca:\n\n```\n{exc}\n```\n\n"
            "Verifica la connessione internet e riprova."
        )


api_key = _get_cerebras_api_key()
cerebras_client = _get_cerebras_client(api_key)

st.markdown(
    """
    <div class='hero-shell'>
        <div class='hero-grid'>
            <div>
                <span class='hero-kicker'>trova prezzi</span>
                <h1 class='hero-title'>Trova Prezzi</h1>
                <p class='hero-copy'>
                    Cerca qualsiasi prodotto su più negozi italiani e online: confronta prezzi reali, ricevi una raccomandazione AI e scegli l'offerta migliore.
                </p>
            </div>
            <div class='hero-note'>
                <strong>Workflow consigliato</strong>
                1. Racconta il prodotto nella chat iniziale<br/>
                2. Controlla query e range sincronizzato<br/>
                3. Avvia la ricerca e chiedi un consiglio finale
            </div>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

chips = [
    "Tutti i prodotti",
    "Amazon",
    "eBay",
    "Vinted",
    "Euronics",
    "Unieuro",
    "MediaWorld",
    "Prezzo min/max sincronizzati",
    "Consiglio AI automatico top-3",
]
st.markdown(
    "<div class='chip-row'>" + "".join(f"<span class='chip'>{chip}</span>" for chip in chips) + "</div>",
    unsafe_allow_html=True,
)

st.write("")

# ── Valori default (sovrascriuti dai widget nel ramo attivo) ──────────────
_presearch_done = st.session_state.get("presearch_ready", False)
query_input: str = ""
top_n_input: int = int(st.session_state.get("ultimo_top_n", 10))
condizione: str = st.session_state.get("condizione", "tutti")
_fonti_def = ["Amazon", "eBay", "Vinted", "Euronics", "MediaWorld", "Unieuro"]
fonti_selezionate: list[str] = list(st.session_state.get("fonti_selezionate", _fonti_def))
_fonti_map = {
    "Amazon": "amazon", "eBay": "ebay", "Vinted": "vinted",
    "Euronics": "euronics", "Unieuro": "unieuro", "MediaWorld": "mediaworld",
}
fonti_backend: list[str] = [_fonti_map[f] for f in fonti_selezionate if f in _fonti_map]
avvia_ricerca: bool = False

st.markdown("<div class='section-card'>", unsafe_allow_html=True)

if not _presearch_done:
    # ══════════════════════════════ STATO 1: Chat attiva ═════════════════
    st.markdown(
        "<div class='section-heading'><h3>🔍 Trova le migliori offerte</h3>"
        "<p>Racconta liberamente cosa stai cercando — la AI genera la query ottimizzata, il budget e i filtri tecnici. "
        "Se il primo messaggio contiene già abbastanza informazioni, andiamo direttamente alla ricerca.</p></div>",
        unsafe_allow_html=True,
    )
    _chat_hdr = st.columns([6, 1])
    with _chat_hdr[1]:
        st.button("Reset", on_click=_reset_presearch_chat, help="Ricomincia la chat")

    if cerebras_client is None:
        st.info("💡 Per la chat assistita imposta CEREBRAS_API_KEY in secrets o variabile ambiente.")

    c_left, c_mid, c_right = st.columns([1, 6, 1])
    with c_mid:
        for _msg in st.session_state.get("presearch_messages", []):
            _role = "assistant" if _msg.get("role") == "assistant" else "user"
            with st.chat_message(_role):
                st.write(_msg.get("content", ""))

    st.divider()

    with st.expander("🔧 Cerca senza chat (inserimento manuale)", expanded=False):
        st.caption("Per risultati migliori usa la chat sopra. Qui puoi fare una ricerca diretta con query breve (3–6 parole).")
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
                st.number_input("Prezzo minimo (€)", min_value=0, max_value=5000, step=10,
                                key="price_min_input", on_change=_sync_from_numbers)
            with _pc[1]:
                st.number_input("Budget massimo (€)", min_value=0, max_value=5000, step=10,
                                key="budget_max_input", on_change=_sync_from_numbers)
            st.slider("Range prezzo", min_value=0, max_value=5000, step=10,
                      key="price_range_slider", on_change=_sync_from_slider)
            _lc = st.columns([1, 1.15], gap="medium")
            with _lc[0]:
                top_n_input = st.number_input("Risultati", min_value=1, max_value=50,
                                              value=int(st.session_state.get("ultimo_top_n", 10)), step=1)
            with _lc[1]:
                _cond_m = st.radio("Condizione", ["Tutti", "Nuovo", "Usato"], horizontal=True,
                                   index={"tutti": 0, "nuovo": 1, "usato": 2}.get(
                                       st.session_state.get("condizione", "tutti"), 0))
                condizione = _cond_m.lower()
            fonti_selezionate = st.multiselect("Fonti", _fonti_def,
                                               default=st.session_state.get("fonti_selezionate", _fonti_def),
                                               key="fonti_ms_manual")
            st.session_state["fonti_selezionate"] = fonti_selezionate
            fonti_backend = [_fonti_map[f] for f in fonti_selezionate if f in _fonti_map]
            avvia_ricerca = st.button("🔍 Cerca offerte", type="primary", width="stretch",
                                      key="btn_manual_cerca")

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
            "<div class='section-heading'><h3>🔍 Trova le migliori offerte</h3>"
            f"<p>Ricerca pronta: {' · '.join(_parts)}</p></div>",
            unsafe_allow_html=True,
        )
    with _hcol2:
        st.write("")
        st.button("✏️ Modifica", on_click=_reset_presearch_chat,
                  help="Torna alla chat per cambiare le preferenze")

    query_input = _q_opt

    price_cols = st.columns(2, gap="medium")
    with price_cols[0]:
        st.number_input("Prezzo minimo (€)", min_value=0, max_value=5000, step=10,
                        key="price_min_input", on_change=_sync_from_numbers)
    with price_cols[1]:
        st.number_input("Budget massimo (€)", min_value=0, max_value=5000, step=10,
                        key="budget_max_input", on_change=_sync_from_numbers)

    st.slider("Range prezzo sincronizzato", min_value=0, max_value=5000, step=10,
              key="price_range_slider", on_change=_sync_from_slider)

    lower_cols = st.columns([1, 1.15], gap="medium")
    with lower_cols[0]:
        top_n_input = st.number_input("Numero risultati", min_value=1, max_value=50,
                                      value=int(st.session_state.get("ultimo_top_n", 10)), step=1)
    with lower_cols[1]:
        condizione_ui = st.radio(
            "Condizione", ["Tutti", "Nuovo", "Usato"], horizontal=True,
            index={"tutti": 0, "nuovo": 1, "usato": 2}.get(
                st.session_state.get("condizione", "tutti"), 0),
        )
        condizione = condizione_ui.lower()

    fonti_selezionate = st.multiselect(
        "Fonti da consultare", _fonti_def,
        default=st.session_state.get("fonti_selezionate", _fonti_def),
    )
    st.session_state["fonti_selezionate"] = fonti_selezionate
    fonti_backend = [_fonti_map[f] for f in fonti_selezionate if f in _fonti_map]

    avvia_ricerca = st.button("🔍 Cerca offerte", type="primary", width="stretch",
                              disabled=not query_input)
    st.caption("💬 Puoi affinare la query o il budget scrivendo nell'input in basso prima di cercare.")
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
    st.markdown("<div class='section-card'>", unsafe_allow_html=True)

    # ── Modalità confronto: risultati side-by-side (sopra la tabella normale) ──
    if st.session_state.get("comparison_mode") and st.session_state.get("comparison_results"):
        _cmp_results: dict[str, list[Offerta]] = st.session_state["comparison_results"]
        _cmp_queries = list(_cmp_results.keys())
        n_cols = len(_cmp_queries)

        st.markdown(
            "<div class='section-heading'><h3>⚔️ Confronto Prodotti</h3>"
            "<p>Risultati affiancati per ogni prodotto ricercato.</p></div>",
            unsafe_allow_html=True,
        )

        cmp_cols = st.columns(n_cols, gap="medium")
        for col_idx, q in enumerate(_cmp_queries):
            res_list = _cmp_results.get(q, [])
            with cmp_cols[col_idx]:
                st.markdown(f"#### 🔹 {q.title()}")
                if not res_list:
                    st.warning("Nessun risultato trovato.")
                else:
                    best = res_list[0]
                    st.metric("Miglior prezzo", _format_price(best.prezzo), delta=None)
                    st.caption(f"{best.nome} · {best.negozio}")
                    for offerta in res_list[:10]:
                        _price_str = _format_price(offerta.prezzo)
                        st.markdown(
                            f"**{_price_str}** — [{offerta.nome[:45]}]({offerta.link})  \n"
                            f"<small>{offerta.negozio} · {offerta.fonte}</small>",
                            unsafe_allow_html=True,
                        )
                    if len(res_list) > 10:
                        st.caption(f"... e altri {len(res_list) - 10} risultati.")

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
                "Fonte", options=_fonti_disp, key="filtro_fonti_tabella",
                placeholder="Tutte le fonti...",
            )
            _p_min_r = float(min(o.prezzo for o in offerte))
            _p_max_r = float(max(o.prezzo for o in offerte))
            _saved_range = st.session_state.get("filtro_prezzo_range_tabella")
            if (
                isinstance(_saved_range, (list, tuple)) and len(_saved_range) == 2
                and _p_min_r <= float(_saved_range[0]) <= _p_max_r
                and float(_saved_range[0]) <= float(_saved_range[1]) <= _p_max_r
            ):
                _init_range = (float(_saved_range[0]), float(_saved_range[1]))
            else:
                _init_range = (_p_min_r, _p_max_r)
            if _p_min_r < _p_max_r:
                st.session_state["filtro_prezzo_range_tabella"] = _init_range
                _filtro_prezzo: tuple[float, float] = _fc2.slider(
                    "Prezzo €", min_value=_p_min_r, max_value=_p_max_r,
                    key="filtro_prezzo_range_tabella", format="€%.0f",
                )
            else:
                _filtro_prezzo = (_p_min_r, _p_max_r)
            _filtro_cond = _fc3.radio(
                "Condizione", ["tutti", "nuovo", "usato"],
                key="filtro_condizione_tabella", horizontal=True,
            )

        def _infer_cond_offerta(o: Offerta) -> str:
            if o.fonte in ("vinted.it",):
                return "usato"
            _nl = o.nome.lower()
            if any(_k in _nl for _k in ("usato", "ricondizionato", "refurbished", "rigenerato", "used")):
                return "usato"
            return "nuovo"

        offerte_vis = [
            o for o in offerte
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

        st.subheader(f"{_label_count} offerte trovate per “{st.session_state.get('ultima_query', '')}”")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Risultati", _label_count)
        m2.metric("Prezzo piu basso", _format_price(prezzo_min_ris))
        m3.metric("Negozio migliore", negozio_min)
        m4.metric("Fonti attive", str(fonti_uniche))

        if not offerte_vis:
            st.info("ℹ️ Nessun risultato con i filtri correnti. Modifica o rimuovi i filtri.")
        else:
            records = _offerte_to_records(offerte_vis)
            st.dataframe(
                records,
                width="stretch",
                hide_index=True,
                height=min(120 + len(records) * 38, 750),
                column_config={
                    "#": st.column_config.NumberColumn("#", width="small", format="%d"),
                    "Prodotto": st.column_config.TextColumn("Prodotto", width="large"),
                    "Prezzo €": st.column_config.NumberColumn("Prezzo", width="small", format="€ %.2f"),
                    "Spedizione": st.column_config.TextColumn("Spedizione", width="medium"),
                    "Negozio": st.column_config.TextColumn("Negozio", width="medium"),
                    "Fonte": st.column_config.TextColumn("Fonte", width="small"),
                    "Specs": st.column_config.TextColumn("Specs", width="large"),
                    "Link": st.column_config.LinkColumn("Link", display_text="Apri →", width="small"),
                },
            )

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
                st.markdown("**Confronto fianco a fianco**")
                _ccols = st.columns(len(_offerte_confronto))
                for _ccol, _co in zip(_ccols, _offerte_confronto):
                    _ccol.markdown(f"**{_co.nome[:80]}**")
                    _ccol.metric("Prezzo", _format_price(_co.prezzo))
                    _ccol.markdown(f"🏪 {_co.negozio} · {_co.fonte}")
                    _ccol.markdown(f"📦 Spedizione: {_co.spedizione}")
                    _ccol.markdown(f"[Apri {_ARROW}]({_co.link})")
                    if _co.specs:
                        _ccol.markdown("---")
                        for _sk, _sv in _co.specs.items():
                            if _sv not in (None, "", [], {}):
                                _ccol.markdown(f"**{str(_sk).replace('_', ' ').capitalize()}**: {_sv}")

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
        csv_bytes = _offerte_to_csv_bytes(_offerte_export)
        nome_file = f"offerte_{st.session_state.get('ultima_query', 'ricerca')[:30].replace(' ', '_')}.csv"
        st.download_button(
            label="↓ Esporta CSV",
            data=csv_bytes,
            file_name=nome_file,
            mime="text/csv",
            help=f"Scarica {len(_offerte_export)} risultati in formato CSV compatibile Excel.",
        )

        st.write("")
        with st.expander("💬 Consiglio AI", expanded=True):
            st.caption("Chiedi quale prodotto ti conviene tra quelli trovati, in base al tuo uso e budget.")
            if cerebras_client is None:
                st.info("💡 Aggiungi CEREBRAS_API_KEY per ottenere la raccomandazione finale AI.")
            else:
                # Auto top-3 al primo caricamento (al massimo 1 tentativo per ricerca)
                if not st.session_state.get("final_chat_messages") and not st.session_state.get("auto_recommend_tried"):
                    st.session_state["auto_recommend_tried"] = True
                    with st.spinner("🤖 Analizzo i risultati per la top 3…"):
                        try:
                            auto_query = (
                                "Analizza i prodotti disponibili e consigliami le migliori 3 opzioni con una motivazione "
                                "concisa per ciascuna (nome, prezzo, punto di forza). Poi indica la tua raccomandazione finale."
                            )
                            auto_messages = [{"role": "user", "content": auto_query}]
                            risposta_auto = _call_final_recommendation(
                                cerebras_client, offerte,
                                st.session_state.get("preferenze_utente", {}), auto_messages,
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

                final_prompt = st.chat_input("Esempio: quale mi consigli per uso quotidiano?", key="final_advice_input")
                if final_prompt:
                    st.session_state["final_chat_messages"].append({"role": "user", "content": final_prompt})
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
                            st.session_state["final_chat_messages"].append({"role": "assistant", "content": risposta})
                            st.rerun()
                        except Exception as exc:
                            _exc_s = str(exc).lower()
                            if "429" in _exc_s or "too_many" in _exc_s or "queue" in _exc_s:
                                st.warning("⚠️ Servizio AI momentaneamente sovraccarico, riprova tra qualche secondo.")
                            else:
                                st.error(f"❌ Errore AI: {exc}")

    st.markdown("</div>", unsafe_allow_html=True)

st.write("")
st.caption(
    "Trova Prezzi · Scraper con delay tra richieste · I prezzi sono indicativi e vanno sempre verificati sul sito del venditore."
)
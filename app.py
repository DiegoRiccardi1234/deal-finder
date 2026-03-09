"""
app.py — Interfaccia Streamlit per offerte_tech.py
===================================================
Avvio:
    streamlit run app.py
"""

import contextlib
import csv
import io
import json
import os
import re
from typing import Any, Optional

import streamlit as st

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

CEREBRAS_MODEL = "openai/gpt-oss-120b"

try:
    from offerte_tech import Offerta, cerca_offerte, parse_search_intent
except ImportError as _e:
    st.error(
        f"❌ Impossibile importare offerte_tech.py: {_e}\n\n"
        "Assicurati che offerte_tech.py si trovi nella stessa cartella di app.py "
        "e che le dipendenze siano installate correttamente."
    )
    st.stop()

st.set_page_config(
    page_title="Offerte Tech Italia",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,700&family=Manrope:wght@400;500;600;700;800&display=swap');

        :root {
            --bg: #f6f1e8;
            --panel: rgba(255, 251, 244, 0.86);
            --panel-strong: rgba(255, 248, 239, 0.96);
            --ink: #1a1a1a;
            --muted: #3f3f3f;
            --accent: #c45c2d;
            --accent-dark: #8b3d18;
            --line: rgba(31, 36, 48, 0.12);
            --shadow: 0 18px 48px rgba(75, 48, 30, 0.12);
            --radius: 22px;
            --surface: #ffffff;
            --input-surface: #ffffff;
        }

        html, body, [class*="css"] {
            font-family: 'Manrope', sans-serif;
            color: var(--ink);
        }

        [data-testid="stAppViewContainer"] {
            background:
                radial-gradient(circle at top left, rgba(196, 92, 45, 0.16), transparent 28%),
                radial-gradient(circle at top right, rgba(102, 128, 96, 0.14), transparent 24%),
                linear-gradient(180deg, #fbf6ef 0%, var(--bg) 100%);
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
            border: 1px solid rgba(255, 255, 255, 0.55);
            border-radius: 28px;
            color: #1a1a1a;
            background:
                linear-gradient(135deg, rgba(255, 247, 237, 0.92), rgba(253, 250, 244, 0.82)),
                repeating-linear-gradient(135deg, rgba(196, 92, 45, 0.025) 0 14px, transparent 14px 28px);
            box-shadow: var(--shadow);
            margin-bottom: 1.2rem;
        }

        .hero-kicker {
            display: inline-block;
            margin-bottom: 0.75rem;
            padding: 0.32rem 0.72rem;
            border-radius: 999px;
            background: rgba(196, 92, 45, 0.11);
            color: var(--accent-dark);
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
            background: rgba(255, 255, 255, 0.94);
            color: var(--ink);
            box-shadow: 0 16px 30px rgba(31, 36, 48, 0.16);
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
            border: 1px solid rgba(255, 255, 255, 0.45);
            border-radius: 20px;
            background: rgba(255, 250, 244, 0.92);
            padding: 0.55rem 0.7rem;
            box-shadow: 0 12px 28px rgba(75, 48, 30, 0.08);
        }

        [data-testid="stChatMessageContent"] p {
            line-height: 1.65;
        }

        [data-testid="stChatMessage"] p,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMetricValue"],
        [data-testid="stMetricLabel"] {
            color: #1a1a1a;
        }

        [data-testid="stMetric"] {
            border: 1px solid var(--line);
            border-radius: 18px;
            background: rgba(255, 255, 255, 0.96);
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
        "ultimo_top_n": 10,
        "fonti_selezionate": ["Amazon", "eBay", "Vinted", "Trovaprezzi"],
        "price_min_input": 0,
        "budget_max_input": 800,
        "price_range_slider": (0, 800),
        "presearch_messages": [
            {
                "role": "assistant",
                "content": "Descrivimi liberamente cosa cerchi. Ti faro una domanda per volta e preparo la query finale. "
                "Se hai specifiche tecniche precise (es. 16GB RAM, 512GB SSD) le usero come filtro separato per trovare risultati piu precisi.",
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

        if "Sei un assistente shopping esperto italiano" in system_prompt:
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
                categoria = "tech" if any(token in transcript for token in ("smartphone", "iphone", "telefono", "cellulare")) else "altro"
                content = json.dumps(
                    {
                        "pronto": True,
                        "query": "smartphone nuovo" if categoria == "tech" else "prodotto cercato",
                        "prezzo_min": 200,
                        "budget_max": 800,
                        "categoria": categoria,
                    },
                    ensure_ascii=False,
                )
            return _MockCompletionResponse(content)

        if "Sei un consulente shopping esperto" in system_prompt:
            product_match = re.search(r'"nome":\s*"([^"]+)"', system_prompt)
            product_name = product_match.group(1) if product_match else "Apple iPhone 17 128GB"
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
    return Cerebras(api_key=api_key)


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
    if any(token in lower for token in ("giacca", "maglia", "vestito", "felpa", "scarpe", "sneaker", "pantaloni", "camicia")):
        return "abbigliamento"
    return "altro"


def _sanitize_presearch_payload(payload: dict[str, Any], transcript: str) -> dict[str, Any]:
    raw_query = str(payload.get("query", "") or "").strip()
    query = " ".join(raw_query.split())
    if not query:
        intent = parse_search_intent(transcript)
        query = str(intent.get("query", "") or "").strip()
    query = " ".join(query.split()[:5])
    categoria = str(payload.get("categoria", "altro") or "altro").strip().lower()
    if categoria not in {"tech", "abbigliamento", "altro"}:
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

    return {
        "pronto": bool(payload.get("pronto", False)),
        "domanda": str(payload.get("domanda", "") or "").strip(),
        "query": query,
        "prezzo_min": prezzo_min,
        "budget_max": budget_max,
        "categoria": categoria,
        "filtri_ai": filtri_ai,
    }


def _sync_from_numbers() -> None:
    min_value = int(st.session_state.get("price_min_input", 0) or 0)
    max_value = int(st.session_state.get("budget_max_input", 800) or 800)
    min_value = max(0, min(min_value, 3000))
    max_value = max(min_value, min(max_value, 3000))
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
            "content": "Descrivimi liberamente cosa cerchi. Ti faro una domanda per volta e preparo la query finale. "
            "Se hai specifiche tecniche precise (es. 16GB RAM, 512GB SSD) le usero come filtro separato per trovare risultati piu precisi.",
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


def _apply_presearch_result(result: dict[str, Any]) -> None:
    transcript = str(st.session_state.get("preferenze_utente", {}).get("trascrizione", "") or "")
    sanitized = _sanitize_presearch_payload(result, transcript)
    st.session_state["query_ottimizzata"] = sanitized["query"]
    st.session_state["prezzo_min"] = sanitized["prezzo_min"]
    st.session_state["budget_max"] = sanitized["budget_max"]
    st.session_state["categoria"] = sanitized["categoria"]
    st.session_state["filtri_ai"] = sanitized.get("filtri_ai", {})
    st.session_state["presearch_ready"] = True
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
    }


def _presearch_fallback() -> dict[str, Any]:
    preferenze = st.session_state.get("preferenze_utente", {})
    transcript = str(preferenze.get("trascrizione", "") or "")
    turni = int(st.session_state.get("presearch_question_count", 0))
    if turni < 2:
        return {"pronto": False, "domanda": "Qual e il budget ideale oppure il range di prezzo che vuoi rispettare?"}

    intent = parse_search_intent(transcript)
    query = str(intent.get("query", "") or transcript).strip()
    return {
        "pronto": True,
        "query": " ".join(query.split()[:5]),
        "prezzo_min": int(intent.get("prezzo_min", 0) or 0),
        "budget_max": int(intent.get("prezzo_max", 800) or 800),
        "categoria": _infer_categoria_from_query(query),
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
            "Sei un assistente shopping esperto italiano.\n"
            "Categoria tech include: smartphone, telefono, cellulare, laptop, notebook, tablet, PC, monitor, SSD, cuffie, smartwatch, fotocamera, console. "
            "Se l'utente menziona uno di questi, usa sempre categoria tech.\n"
            "1. Identifica la categoria: tech / abbigliamento / altro\n"
            "2. Elenca mentalmente TUTTE le variabili che servono per trovare il prodotto giusto per quella categoria\n"
            "3. Identifica quali variabili l'utente NON ha ancora specificato\n"
            "4. Fai UNA SOLA domanda che copre la variabile piu importante mancante\n"
            "5. Dopo max 4 domande, anche se mancano info, genera la query finale\n"
            "Il campo 'query' deve essere una query di ricerca AMPIA di max 5 parole, adatta a un motore di ricerca e-commerce. "
            "Includi SOLO tipo/marca/modello/dimensione del prodotto. NON includere budget o specifiche tecniche dettagliate (RAM, storage, ecc.) nel campo query.\n"
            "Le specifiche tecniche come RAM, storage, risoluzione ecc. vanno nel campo 'filtri_ai' come oggetto separato "
            "(es. {\"ram\": \"16gb\", \"storage\": \"512gb\"}).\n"
            "Rispondi SOLO in JSON valido:\n"
            "- Se servono ancora info: {\"domanda\": \"...\", \"pronto\": false}\n"
            "- Se hai abbastanza info: {\"pronto\": true, \"query\": \"...\", \"prezzo_min\": N, \"budget_max\": N, \"categoria\": \"tech|abbigliamento|altro\", \"filtri_ai\": {}}\n"
            f"Cronologia conversazione finora:\n{history_text}\n\nNuovo messaggio utente: {cleaned}"
        )
        user_payload = {
            "messaggi_utente": messaggi,
            "trascrizione": transcript,
            "domande_fatte": int(st.session_state.get("presearch_question_count", 0)),
            "forza_chiusura": int(st.session_state.get("presearch_question_count", 0)) >= 4,
        }
        try:
            completion = client.chat.completions.create(
                model=CEREBRAS_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
                ],
                temperature=0.1,
            )
            content = completion.choices[0].message.content if completion and completion.choices else ""
            result = _extract_json_object(str(content or ""))
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
        st.session_state["presearch_messages"].append(
            {
                "role": "assistant",
                "content": (
                    f"Ho preparato la ricerca: {sanitized['query']} · "
                    f"range {sanitized['prezzo_min']}€ - {sanitized['budget_max']}€ · "
                    f"categoria {sanitized['categoria']}{filtri_msg}."
                ),
            }
        )
        return

    question_count = int(st.session_state.get("presearch_question_count", 0)) + 1
    st.session_state["presearch_question_count"] = question_count

    if question_count >= 4:
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


def _call_final_recommendation(
    cerebras_client: object,
    offerte: list[Offerta],
    preferenze_utente: dict[str, Any],
    messages: list[dict[str, str]],
) -> str:
    system_prompt = (
        "Sei un consulente shopping esperto. Hai questi dati:\n"
        f"PREFERENZE UTENTE: {json.dumps(preferenze_utente, ensure_ascii=False)}\n"
        f"PRODOTTI DISPONIBILI (max 10 piu economici):\n{json.dumps(_build_products_payload(offerte), ensure_ascii=False)}\n"
        "Rispondi con una raccomandazione motivata, cita nome e prezzo del prodotto consigliato, "
        "confronta almeno 2-3 parametri rilevanti per l'utente. Sii conciso e diretto."
    )
    payload = [{"role": "system", "content": system_prompt}] + messages
    completion = cerebras_client.chat.completions.create(
        model=CEREBRAS_MODEL,
        messages=payload,
        temperature=0.2,
    )
    content = completion.choices[0].message.content if completion and completion.choices else ""
    return str(content or "").strip()


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
            fonte="trovaprezzi",
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


def _summarize_specs(specs: dict[str, Any]) -> str:
    if not specs:
        return ""
    parts = []
    for key, value in specs.items():
        if value in (None, "", [], {}):
            continue
        label = str(key).replace("_", " ").capitalize()
        parts.append(f"{label}: {value}")
    return " · ".join(parts)


def _offerte_to_records(offerte: list[Offerta]) -> list[dict[str, Any]]:
    return [
        {
            "#": i,
            "Prodotto": offerta.nome,
            "Prezzo €": round(offerta.prezzo, 2),
            "Spedizione": offerta.spedizione,
            "Negozio": offerta.negozio,
            "Fonte": offerta.fonte,
            "Specs": _summarize_specs(offerta.specs),
            "Link": offerta.link,
        }
        for i, offerta in enumerate(offerte, start=1)
    ]


def _render_specs_grid(offerte: list[Offerta]) -> None:
    offerte_con_specs = [offerta for offerta in offerte if offerta.specs]
    if not offerte_con_specs:
        return

    st.markdown(
        "<div class='section-heading'><h3>Specs rilevate</h3><p>Arricchimento automatico basato sulla categoria della ricerca.</p></div>",
        unsafe_allow_html=True,
    )
    preview = offerte_con_specs[:6]
    for start in range(0, len(preview), 2):
        cols = st.columns(2, gap="medium")
        for idx, offerta in enumerate(preview[start:start + 2]):
            specs_rows = []
            for key, value in offerta.specs.items():
                if value in (None, "", [], {}):
                    continue
                label = str(key).replace("_", " ").capitalize()
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
    st.session_state["risultati"] = []
    st.session_state["log_ricerca"] = ""

    categoria = str(st.session_state.get("categoria", "altro") or "altro")
    if categoria not in {"tech", "abbigliamento", "altro"}:
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

    with st.spinner("⏳ Sto cercando sulle fonti selezionate..."):
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
                )
            st.session_state["risultati"] = risultati
            st.session_state["log_ricerca"] = log_buffer.getvalue()
            st.session_state["filtri_ai_ultima_ricerca"] = st.session_state.get("filtri_ai", {})
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
                <span class='hero-kicker'>shopping assistant</span>
                <h1 class='hero-title'>Offerte Tech Italia</h1>
                <p class='hero-copy'>
                    Descrivi cosa cerchi, lascia che la chat rifinisca query e budget, poi confronta offerte reali da piu fonti
                    con un layout piu leggibile, specs arricchite e una raccomandazione finale coerente con le tue priorita.
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
    "Amazon",
    "eBay",
    "Vinted",
    "Trovaprezzi",
    "Prezzo minimo e massimo sincronizzati",
    "Chat finale con raccomandazione AI",
]
st.markdown(
    "<div class='chip-row'>" + "".join(f"<span class='chip'>{chip}</span>" for chip in chips) + "</div>",
    unsafe_allow_html=True,
)

st.write("")

pre_col, search_col = st.columns([1.08, 1], gap="large")

with pre_col:
    st.markdown("<div class='section-card'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-heading'><h3>Chat pre-ricerca</h3>"
        "<p>Descrivi il prodotto che cerchi con dettagli come uso, budget e preferenze. "
        "La chat separa automaticamente la query di ricerca dalle specifiche tecniche (RAM, storage…) "
        "per trovare risultati più precisi.</p></div>",
        unsafe_allow_html=True,
    )
    top_actions = st.columns([1, 1.2, 1])
    with top_actions[2]:
        st.button("Reset chat", width="stretch", on_click=_reset_presearch_chat)

    if cerebras_client is None:
        st.info("💡 Per la chat assistita imposta CEREBRAS_API_KEY in secrets o variabile ambiente.")

    for message in st.session_state.get("presearch_messages", []):
        role = "assistant" if message.get("role") == "assistant" else "user"
        with st.chat_message(role):
            st.write(message.get("content", ""))

    presearch_input = st.chat_input("Descrivi prodotto, uso, vincoli e preferenze", key="presearch_input")
    if presearch_input:
        _run_presearch_step(presearch_input, api_key)
        st.rerun()

    if st.session_state.get("presearch_ready", False):
        filtri_ai_display = st.session_state.get("filtri_ai", {})
        filtri_info = ""
        if filtri_ai_display:
            filtri_info = " · specs: " + ", ".join(f"{k}={v}" for k, v in filtri_ai_display.items())
        st.success(
            "Query pronta: "
            f"{st.session_state.get('query_ottimizzata', '')} · "
            f"{st.session_state.get('prezzo_min', 0)}€ - {st.session_state.get('budget_max', 800)}€ · "
            f"categoria {st.session_state.get('categoria', 'altro')}{filtri_info}"
        )

        if st.button("Avvia ricerca con questa query", type="primary", width="stretch"):
            st.session_state["run_ai_query"] = True

    st.markdown("</div>", unsafe_allow_html=True)

with search_col:
    st.markdown("<div class='section-card'>", unsafe_allow_html=True)
    st.markdown(
        "<div class='section-heading'><h3>Pannello ricerca</h3><p>I controlli prezzo restano sincronizzati tra input numerici e slider doppio range.</p></div>",
        unsafe_allow_html=True,
    )

    query_input = st.text_input(
        "Query prodotto",
        placeholder="es. notebook 14 pollici 16gb",
        value=st.session_state.get("_query_prefilled", ""),
    ).strip()
    st.caption(
        f"Range attivo: {st.session_state.get('price_min_input', 0)}€ - {st.session_state.get('budget_max_input', 800)}€"
    )

    price_cols = st.columns(2, gap="medium")
    with price_cols[0]:
        st.number_input(
            "Prezzo minimo (€)",
            min_value=0,
            max_value=3000,
            step=10,
            key="price_min_input",
            on_change=_sync_from_numbers,
        )
    with price_cols[1]:
        st.number_input(
            "Budget massimo (€)",
            min_value=0,
            max_value=3000,
            step=10,
            key="budget_max_input",
            on_change=_sync_from_numbers,
        )

    st.slider(
        "Range prezzo sincronizzato",
        min_value=0,
        max_value=3000,
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
            index={"tutti": 0, "nuovo": 1, "usato": 2}.get(st.session_state.get("condizione", "tutti"), 0),
        )
    condizione = condizione_ui.lower()

    fonti_disponibili = ["Amazon", "eBay", "Vinted", "Trovaprezzi"]
    fonti_selezionate = st.multiselect(
        "Fonti da consultare",
        fonti_disponibili,
        default=st.session_state.get("fonti_selezionate", fonti_disponibili),
    )
    st.session_state["fonti_selezionate"] = fonti_selezionate
    fonti_map = {
        "Amazon": "amazon",
        "eBay": "ebay",
        "Vinted": "vinted",
        "Trovaprezzi": "trovaprezzi",
    }
    fonti_backend = [fonti_map[fonte] for fonte in fonti_selezionate if fonte in fonti_map]

    avvia_ricerca = st.button(
        "Cerca offerte",
        type="primary",
        width="stretch",
        disabled=not query_input,
    )
    st.markdown("</div>", unsafe_allow_html=True)

search_triggered = avvia_ricerca or bool(st.session_state.pop("run_ai_query", False))
if search_triggered:
    current_query = st.session_state.get("query_ottimizzata", "").strip() if not query_input else query_input
    current_min = int(st.session_state.get("price_min_input", 0) or 0)
    current_max = int(st.session_state.get("budget_max_input", 800) or 800)
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
    else:
        prezzo_min_ris = min(offerta.prezzo for offerta in offerte)
        negozio_min = next(offerta.negozio for offerta in offerte if offerta.prezzo == prezzo_min_ris)
        fonti_uniche = len({offerta.fonte for offerta in offerte})

        st.subheader(f"{len(offerte)} offerte trovate per “{st.session_state.get('ultima_query', '')}”")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Risultati", str(len(offerte)))
        m2.metric("Prezzo piu basso", _format_price(prezzo_min_ris))
        m3.metric("Negozio migliore", negozio_min)
        m4.metric("Fonti attive", str(fonti_uniche))

        records = _offerte_to_records(offerte)
        st.dataframe(
            records,
            width="stretch",
            hide_index=True,
            height=min(90 + len(records) * 38, 620),
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

        risultati_con_alternativa = [offerta for offerta in offerte if str(offerta.alternativa or "").strip()]
        if risultati_con_alternativa:
            st.markdown("**Alternative smart rilevate**")
            for offerta in risultati_con_alternativa:
                st.warning(f"{offerta.nome}: {offerta.alternativa}")

        _render_specs_grid(offerte)

        csv_bytes = _offerte_to_csv_bytes(offerte)
        nome_file = f"offerte_{st.session_state.get('ultima_query', 'ricerca')[:30].replace(' ', '_')}.csv"
        st.download_button(
            label="Esporta CSV",
            data=csv_bytes,
            file_name=nome_file,
            mime="text/csv",
            help=f"Scarica {len(offerte)} risultati in formato CSV compatibile Excel.",
        )

        st.write("")
        st.markdown(
            """
            <div class="section-heading" style="margin-top:0.3rem">
                <h3>💬 Consiglio AI</h3>
                <p>Chiedi quale prodotto ti conviene tra quelli trovati, in base al tuo uso e budget.</p>
            </div>
            """,
            unsafe_allow_html=True,
        )

        if cerebras_client is None:
            st.info("💡 Aggiungi CEREBRAS_API_KEY per ottenere la raccomandazione finale AI.")
        else:
            if not st.session_state.get("final_chat_messages"):
                st.markdown(
                    """
                    <div style="padding:1rem 1.2rem; border-radius:16px;
                                background:rgba(196,92,45,0.07);
                                border:1px solid rgba(196,92,45,0.15);
                                margin-bottom:0.8rem">
                        <p style="margin:0; color:var(--muted); font-size:0.9rem; line-height:1.65">
                            💡 <strong>Prova a chiedere:</strong> "quale mi consigli per uso quotidiano?",
                            "qual è il miglior rapporto qualità/prezzo?" oppure
                            "confronta le prime 3 opzioni"
                        </p>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            for message in st.session_state.get("final_chat_messages", []):
                role = "assistant" if message.get("role") == "assistant" else "user"
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
                        st.error(f"❌ Errore AI: {exc}")

    st.markdown("</div>", unsafe_allow_html=True)

st.write("")
st.caption(
    "Offerte Tech Italia · Scraper con delay tra richieste · I prezzi sono indicativi e vanno sempre verificati sul sito del venditore."
)
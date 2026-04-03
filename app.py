"""Pagina Tool Streamlit per Trova Prezzi."""

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
from _shared import load_css, render_nav

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

try:
    from cerebras_model import get_best_model as _get_best_model, cerebras_chat_with_retry as _cerebras_chat_lib
except Exception:
    _get_best_model = None  # type: ignore[assignment]
    _cerebras_chat_lib = None  # type: ignore[assignment]

CEREBRAS_MODEL = "llama-3.3-70b"  # fallback statico

try:
    from offerte_tech import Offerta, cerca_offerte, parse_search_intent, parse_comparison_query
except ImportError as _e:
    st.error(
        f"❌ Impossibile importare offerte_tech.py: {_e}\n\n"
        "Assicurati che offerte_tech.py si trovi nella stessa cartella di app.py "
        "e che le dipendenze siano installate correttamente."
    )
    st.stop()

try:
    from search_history import load_history, save_search as _save_search
except ImportError:
    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None

st.set_page_config(
    page_title="Trova Prezzi",
    page_icon="🔍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Protezione password (solo uso personale) ──────────────────────────────
_APP_PASSWORD = st.secrets.get("APP_PASSWORD", "") if hasattr(st, "secrets") else ""
if _APP_PASSWORD:
    if not st.session_state.get("_authenticated"):
        st.markdown(
            "<style>#MainMenu,footer,[data-testid='stToolbar']{display:none!important}</style>",
            unsafe_allow_html=True,
        )
        st.markdown("## Trova Prezzi Mio")
        _pwd = st.text_input("Password", type="password", placeholder="Inserisci la password...")
        if st.button("Accedi"):
            if _pwd == _APP_PASSWORD:
                st.session_state["_authenticated"] = True
                st.rerun()
            else:
                st.error("Password errata.")
        st.stop()

_theme_mode = str(st.session_state.get("ui_theme", "light") or "light").strip().lower()
if _theme_mode not in {"light", "dark"}:
    _theme_mode = "light"
load_css(theme_mode=_theme_mode)
render_nav(active_page="tool")



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
                "content": "Raccontami la richiesta del tuo amico su Trova Prezzi — qualsiasi tipo di prodotto "
                "(tech, abbigliamento, elettrodomestici, sport, libri...). "
                "Ti faccio al massimo 2 domande e poi avvio lo scraping.",
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
        "_pending_price_sync": None,
        "ui_theme": _theme_mode,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


_init_state()


def _queue_price_sync(prezzo_min: int, budget_max: int) -> None:
    """Accoda la sincronizzazione dei widget prezzo al prossimo rerun sicuro."""
    pmin = max(0, min(int(prezzo_min), 5000))
    pmax = max(pmin, min(int(budget_max), 5000))
    st.session_state["prezzo_min"] = pmin
    st.session_state["budget_max"] = pmax
    st.session_state["_pending_price_sync"] = {"prezzo_min": pmin, "budget_max": pmax}


def _flush_pending_price_sync() -> None:
    """Applica eventuale sync prezzi prima che i widget vengano instanziati."""
    pending = st.session_state.get("_pending_price_sync")
    if not isinstance(pending, dict):
        return
    pmin = int(pending.get("prezzo_min", st.session_state.get("prezzo_min", 0)) or 0)
    pmax = int(pending.get("budget_max", st.session_state.get("budget_max", 800)) or 800)
    pmin = max(0, min(pmin, 5000))
    pmax = max(pmin, min(pmax, 5000))
    st.session_state["price_min_input"] = pmin
    st.session_state["budget_max_input"] = pmax
    st.session_state["price_range_slider"] = (pmin, pmax)
    st.session_state["_pending_price_sync"] = None


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
    max_retries: int = 4,
) -> str:
    """Chiama Cerebras con retry automatico.
    - 404 (modello non trovato): invalida cache, sceglie nuovo modello, riprova.
    - 429 (rate limit): backoff esponenziale fino a max_retries volte.
    """
    if _cerebras_chat_lib is not None:
        completion = _cerebras_chat_lib(
            client=client,
            messages=messages,
            model=None,  # auto-select dal modulo
            max_retries=max_retries,
            base_delay=2.0,
            temperature=temperature,
        )
        content = completion.choices[0].message.content if completion and completion.choices else ""
        return str(content or "").strip()

    # Fallback se il modulo non è disponibile
    last_exc: Optional[BaseException] = None
    for attempt in range(1 + max_retries):
        try:
            _model = _get_best_model(client) if _get_best_model else CEREBRAS_MODEL
            completion = client.chat.completions.create(  # type: ignore[attr-defined]
                model=_model,
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
    _queue_price_sync(sanitized["prezzo_min"], sanitized["budget_max"])
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
        _queue_price_sync(_pmin, _budget)
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


def _render_offerta_card(offerta: Offerta, idx: int, best_price: float = 0) -> str:
    import html as _html
    source_label = offerta.fonte.replace(".it", "").replace(".com", "").upper()
    price_str = _format_price(offerta.prezzo)
    spedizione_raw = offerta.spedizione if offerta.spedizione and offerta.spedizione != "n.d." else ""
    spedizione_html = f"<span class='card-shipping'>{_html.escape(spedizione_raw)}</span>" if spedizione_raw else ""
    title = _html.escape(offerta.nome[:90] + ("\u2026" if len(offerta.nome) > 90 else ""))
    specs_line = _summarize_specs(offerta.specs, offerta.nome)
    specs_html = f"<p class='card-specs'>{_html.escape(specs_line)}</p>" if specs_line else ""
    is_best = best_price > 0 and offerta.prezzo == best_price
    best_badge = "<span class='best-badge'>Miglior Prezzo</span>" if is_best else ""
    img_html = ""
    if getattr(offerta, "immagine", ""):
        img_src = _html.escape(offerta.immagine, quote=True)
        img_html = (
            f"<div class='card-img-wrap'>"
            f"<img src='{img_src}' alt='' loading='lazy' "
            f"onerror=\"this.parentElement.style.display='none'\">"
            f"</div>"
        )
    else:
        img_html = f"<div class='card-img-wrap card-img-placeholder'><span>{source_label[0]}</span></div>"
    negozio_escaped = _html.escape(offerta.negozio)
    link_escaped = _html.escape(offerta.link, quote=True)
    return (
        f"<div class='offerta-card{' offerta-best' if is_best else ''}'>"
        f"{best_badge}"
        f"<span class='source-badge'>{source_label}</span>"
        f"{img_html}"
        f"<div class='card-body'>"
        f"<p class='card-title'>{title}</p>"
        f"<p class='card-price'>{price_str}</p>"
        f"<p class='card-meta'>{negozio_escaped}{spedizione_html}</p>"
        f"{specs_html}"
        f"</div>"
        f"<a class='card-cta' href='{link_escaped}' target='_blank'>Vai all\u2019offerta \u2192</a>"
        f"</div>"
    )


def _render_results_grid(offerte: list[Offerta]) -> None:
    """Renders tutti i risultati come card grid con immagini."""
    if not offerte:
        return
    best_price = min(o.prezzo for o in offerte) if offerte else 0
    cards_html = "".join(
        _render_offerta_card(o, i, best_price=best_price)
        for i, o in enumerate(offerte)
    )
    st.markdown(f"<div class='results-grid'>{cards_html}</div>", unsafe_allow_html=True)


def _render_specs_grid(offerte: list[Offerta]) -> None:
    """Renders una griglia delle specifiche per le offerte con dati specs."""
    # Filtra solo le offerte che hanno specifiche valide
    offerte_con_specs = [o for o in offerte if o.specs and isinstance(o.specs, dict) and any(v not in (None, "", [], {}) for v in o.specs.values())]
    if not offerte_con_specs:
        st.info("\U0001f4cb Nessun dato di specifiche rilevato per i prodotti.")
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
            cols[idx].markdown(_render_offerta_card(offerta, start + idx), unsafe_allow_html=True)


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
        _save_search(
            query=query,
            budget_min=prezzo_min,
            budget_max=budget_max,
            condizione=condizione,
            fonti=fonti_backend,
            results_count=len(risultati),
        )
    except Exception as exc:
        st.session_state["log_ricerca"] = log_buffer.getvalue()
        st.error(
            f"❌ Si e verificato un errore durante la ricerca:\n\n```\n{exc}\n```\n\n"
            "Verifica la connessione internet e riprova."
        )


api_key = _get_cerebras_api_key()
cerebras_client = _get_cerebras_client(api_key)

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

# Applica eventuali aggiornamenti prezzo pendenti PRIMA della creazione widget.
_flush_pending_price_sync()

_history = load_history()
if _history:
    with st.expander("Ricerche recenti", expanded=False):
        for entry in _history[:8]:
            q = str(entry.get("query", "") or "")
            if not q:
                continue
            bmax = entry.get("budget_max", "")
            label = f"{q} · {bmax}€" if str(bmax).strip() else q
            safe_q = re.sub(r"[^a-zA-Z0-9_-]", "_", q[:20])
            if st.button(
                label,
                key=f"hist_{safe_q}_{entry.get('timestamp', '')}",
                help=(
                    f"Ricerca del {entry.get('timestamp', '')} — "
                    f"{entry.get('results_count', 0)} risultati"
                ),
            ):
                st.session_state["query_input"] = q
                st.session_state["budget_max_input"] = int(bmax or 800)
                st.session_state["presearch_ready"] = True
                st.session_state["query_ottimizzata"] = q
                _queue_price_sync(int(entry.get("budget_min", 0)), int(bmax or 800))
                st.rerun()

if not _presearch_done:
    sources = ["Amazon", "eBay", "Vinted", "Euronics", "Unieuro", "MediaWorld", "Trovaprezzi"]
    st.markdown(
        "<div class='source-strip'><span class='source-strip-label'>Monitoraggio in tempo reale su</span>"
        + "".join(f"<span>{s}</span>" for s in sources)
        + "</div>",
        unsafe_allow_html=True,
    )

st.markdown("<span id='sezione-offerte'></span>", unsafe_allow_html=True)
st.markdown("<div class='section-card'>", unsafe_allow_html=True)

if not _presearch_done:
    # ══════════════════════════════ STATO 1: Chat attiva ═════════════════
    _chat_hdr = st.columns([6, 1])
    with _chat_hdr[0]:
        st.markdown(
            "<div class='section-heading'><h3>Trova le migliori offerte</h3>"
            "<p>Descrivi cosa cerchi: la AI genera query ottimizzata, budget e filtri tecnici. "
            "Dettagli completi al primo messaggio? Avviamo subito lo scraping su 7 siti.</p></div>",
            unsafe_allow_html=True,
        )
    with _chat_hdr[1]:
        st.markdown("<div style='padding-top:0.6rem'>", unsafe_allow_html=True)
        st.button("Ricomincia", on_click=_reset_presearch_chat, help="Ricomincia la chat")
        st.markdown("</div>", unsafe_allow_html=True)

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
            "<div class='section-heading'><h3>Trova le migliori offerte</h3>"
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
    st.markdown("<span id='sezione-confronta'></span>", unsafe_allow_html=True)
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

        st.subheader(f"{_label_count} offerte trovate per \u201c{st.session_state.get('ultima_query', '')}\u201d")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Risultati", _label_count)
        m2.metric("Prezzo piu basso", _format_price(prezzo_min_ris))
        m3.metric("Negozio migliore", negozio_min)
        m4.metric("Fonti attive", str(fonti_uniche))

        if not offerte_vis:
            st.info("ℹ️ Nessun risultato con i filtri correnti. Modifica o rimuovi i filtri.")
        else:
            _render_results_grid(offerte_vis)

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
                _best_price = min(o.prezzo for o in _offerte_confronto)
                for _ccol, _co in zip(_ccols, _offerte_confronto):
                    _is_best = _co.prezzo == _best_price
                    if _is_best:
                        _ccol.markdown("<div class='best-value'>", unsafe_allow_html=True)
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
                    if _is_best:
                        _ccol.markdown("</div>", unsafe_allow_html=True)

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
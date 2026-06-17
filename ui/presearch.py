"""ui: ui/presearch.py"""

from __future__ import annotations

import json
import re
from typing import Any

import streamlit as st

try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]

from offerte_tech import parse_search_intent, parse_comparison_query

try:
    from search_history import load_history, save_search as _save_search
except ImportError:

    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None


from ui.ai_client import (
    _cerebras_chat_with_retry,
    _extract_json_object,
    _get_cerebras_client,
)
from ui.state import _queue_price_sync


def _infer_categoria_from_query(query: str) -> str:
    lower = str(query or "").lower()
    if any(
        token in lower
        for token in (
            "notebook",
            "laptop",
            "smartphone",
            "telefono",
            "cellulare",
            "iphone",
            "monitor",
            "ssd",
            "gpu",
            "tablet",
            "pc",
            "cuffie",
            "smartwatch",
            "fotocamera",
            "console",
        )
    ):
        return "tech"
    if any(
        token in lower for token in ("giacca", "maglia", "vestito", "felpa", "pantaloni", "camicia")
    ):
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
    has_product = any(
        kw in transcript
        for kw in (
            "notebook",
            "laptop",
            "smartphone",
            "telefono",
            "iphone",
            "monitor",
            "tablet",
            "cuffie",
            "auricolari",
            "scarpe",
            "felpa",
            "giacca",
            "mouse",
            "tastiera",
            "ssd",
            "pc",
            "console",
            "smartwatch",
            "frigorifero",
            "lavatrice",
            "forno",
            "tv",
            "televisore",
            "libro",
            "romanzo",
            "sneaker",
            "bici",
            "divano",
            "crema",
            "profumo",
        )
    )

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
    storage_match = re.search(
        r"(\d{2,4})\s*gb\s*(?:di\s*)?(?:ssd|storage|disco|memoria)", transcript
    )
    if storage_match:
        filtri_ai["storage"] = f"{storage_match.group(1)}gb"
    tb_match = re.search(r"(\d)\s*tb", transcript)
    if tb_match:
        filtri_ai["storage"] = f"{tb_match.group(1)}tb"
    display_match = re.search(r"(\d{2})\s*(?:pollici|inch|\")", transcript)
    if display_match:
        filtri_ai["display"] = f'{display_match.group(1)}"'
    proc_match = re.search(
        r"\b(i[357]|i9|ryzen\s*[357]|m[123]|snapdragon|celeron|pentium)\b", transcript
    )
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
    for kw in (
        "notebook",
        "laptop",
        "smartphone",
        "iphone",
        "samsung",
        "xiaomi",
        "monitor",
        "tablet",
        "cuffie",
        "mouse",
        "tastiera",
        "ssd",
        "pc",
        "console",
        "smartwatch",
        "scarpe",
        "felpa",
        "giacca",
        "frigorifero",
        "lavatrice",
        "forno",
        "tv",
        "televisore",
        "libro",
        "romanzo",
        "sneaker",
        "bici",
        "divano",
        "crema",
        "profumo",
    ):
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
        _budget_m = re.search(
            r"(?:massimo|max|budget|sotto|meno di|fino a)\s*(\d+)\s*(?:euro|€)?", cleaned.lower()
        )
        _budget = min(int(_budget_m.group(1)), 5000) if _budget_m else 2000
        # Estrai condizione
        _cond = "tutti"
        if re.search(r"\bnuov[oai]\b", cleaned.lower()):
            _cond = "nuovo"
        elif re.search(r"\busat[oai]\b", cleaned.lower()):
            _cond = "usato"
        # Stima prezzo_min in base al tipo prodotto
        _pmin = 0
        _combined_lower = " ".join(comp_queries).lower()
        if "iphone" in _combined_lower:
            _pmin = 500 if _cond != "usato" else 200
        elif any(kw in _combined_lower for kw in ("macbook", "laptop", "notebook")):
            _pmin = 400 if _cond != "usato" else 200
        elif any(kw in _combined_lower for kw in ("samsung", "galaxy", "pixel")):
            _pmin = 300 if _cond != "usato" else 150
        elif any(kw in _combined_lower for kw in ("ipad", "tablet")):
            _pmin = 200 if _cond != "usato" else 100

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
        st.session_state["presearch_messages"].append(
            {
                "role": "assistant",
                "content": (
                    f"Ho rilevato una ricerca confronto fra **{len(comp_queries)} prodotti**!\n\n"
                    f"Cercherò in parallelo: **{_cq_label}**\n\n"
                    f"Range prezzo: {_pmin}€ – {_budget}€ · Condizione: {_cond}\n\n"
                    "Puoi modificare il range prezzo, poi clicca **Cerca offerte** "
                    "per vedere i risultati fianco a fianco."
                ),
            }
        )
        return

    history_text = "\n".join(
        [
            f"{'Assistente' if message.get('role') == 'assistant' else 'Utente'}: {message.get('content', '')}"
            for message in st.session_state.get("presearch_messages", [])
        ]
    )

    client = _get_cerebras_client(api_key)
    result: dict[str, Any]

    if client is None:
        result = _presearch_fallback()
    else:
        # Inietta contesto KB nel prompt se disponibile
        _kb_context_str = ""
        if kb_manager is not None:
            _kb = kb_manager.load_kb()
            _kb_inferred_cat = (
                _infer_categoria_from_query(transcript.split("\n")[0]) if transcript else "altro"
            )
            _kb_context_str = kb_manager.get_category_context(_kb, _kb_inferred_cat)
            # Traccia modelli sconosciuti menzionati dall'utente
            if transcript:
                _known = set(
                    m.lower()
                    for cat_data in _kb.get("categorie", {}).values()
                    for m in (cat_data.get("modelli") or cat_data.get("categorie_item") or [])
                )
                for word in re.findall(
                    r"\b[A-Z][a-z]+(?:\s+[A-Z0-9][a-zA-Z0-9]*){1,3}", transcript
                ):
                    if word.lower() not in _known and len(word) > 5:
                        kb_manager.track_unknown(_kb_inferred_cat, word)

        system_prompt = (
            "Sei un consulente acquisti esperto italiano. Il tuo obiettivo e' capire le esigenze dell'utente "
            "per dargli una RACCOMANDAZIONE FINALE motivata e personalizzata.\n"
            "Per farlo devi conoscere: 1) categoria/tipo prodotto, 2) uso principale, 3) budget, "
            "4) preferenze fisiche/hardware.\n"
            + (f"{_kb_context_str}\n\n" if _kb_context_str else "")
            + "Categorie supportate: smartphone, laptop, tablet, televisore, elettrodomestico, abbigliamento, scarpe, sport, libri, beauty, casa, altro.\n"
            "REGOLE IMPORTANTI:\n"
            "- Se il messaggio contiene gia' tipo prodotto + budget/range e almeno 1 altra preferenza: vai SUBITO a pronto:true\n"
            "- Se manca tipo prodotto OPPURE budget: fai UNA SOLA domanda specifica\n"
            "- NON chiedere cose gia' dette\n"
            "- Dopo max 3 domande vai sempre a pronto:true\n"
            "REGOLE QUERY:\n"
            "- Il campo 'prezzo_min' DEVE essere REALISTICO in base a tipo prodotto + condizione:\n"
            "  iPhone nuovo→700, usato→300 | MacBook/laptop gaming nuovo→900, usato→400\n"
            "  Smartphone Android top→400, fascia media→150 | Laptop base/office→250\n"
            '  Smartwatch→80 | Tablet→100 | TV 50+"→300 | Cuffie→30 | Console→200\n'
            "  NON usare MAI 0 come prezzo_min — stima sempre un minimo ragionevole\n"
            "- Il campo 'query' deve essere BREVE: tipo/dimensione/marca, MAX 5 parole, NO frasi, NO budget\n"
            "- Se l'utente menziona due modelli alternativi (es 'iphone 16 o 17', 'notebook 14 o 15'): usa la query PIU' GENERICA\n"
            "  che li cattura entrambi. Non fissare il numero di modello specifico — lascia che lo scraper trovi entrambi.\n"
            "  Esempi: 'iphone 16 o 17' → query 'iphone 128gb', '14 o 15 pollici' → 'notebook windows'\n"
            "- Le specifiche tecniche (RAM, storage...) vanno in 'filtri_ai', NON nella query\n"
            "- Le preferenze utente (uso, materiale, marca...) vanno in 'contesto_extra' (stringa)\n"
            "Rispondi SOLO in JSON valido:\n"
            '- Se servono ancora info: {"domanda": "...", "pronto": false}\n'
            '- Se hai abbastanza info: {"pronto": true, "query": "...", "prezzo_min": N, "budget_max": N, '
            '"categoria": "tech|abbigliamento|televisore|elettrodomestico|scarpe|sport|libri|beauty|casa|altro", "condizione": "nuovo|usato|tutti", '
            '"filtri_ai": {}, "contesto_extra": "..."}\n'
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

    domanda = (
        sanitized.get("domanda")
        or "Qual e il dettaglio piu importante che vuoi fissare prima di cercare?"
    )
    st.session_state["presearch_messages"].append({"role": "assistant", "content": str(domanda)})

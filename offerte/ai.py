"""offerte: offerte/ai.py"""

from __future__ import annotations

import json
import os
import re
import time


try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None


_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"
from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.config import CEREBRAS_FALLBACK_MODELS, CEREBRAS_MODEL_BLACKLIST
from offerte.parsing import *  # noqa: F401,F403
from offerte.filters import _hard_spec_mismatch_reasons


def _cerebras_model(client=None) -> str:
    """Restituisce il miglior modello disponibile per il provider AI attivo."""
    from offerte import providers

    return providers.best_model(client=client)


def _cerebras_chat(
    client, messages: list, temperature: float = 0.1, max_retries: int = 4
) -> object:
    """Wrapper con retry automatico (404 modello + 429 rate limit)."""
    if _cerebras_chat_lib is not None:
        return _cerebras_chat_lib(
            client=client,
            messages=messages,
            model=None,
            max_retries=max_retries,
            base_delay=2.0,
            temperature=temperature,
        )
    # Fallback diretto senza retry
    return client.chat.completions.create(
        model=_cerebras_model(client),
        messages=messages,
        temperature=temperature,
    )


def _get_cerebras_api_key() -> str:
    """Legge la key Cerebras da Streamlit secrets o variabile ambiente."""
    if st is not None:
        try:
            key = str(st.secrets.get("CEREBRAS_API_KEY", "") or "")
            if key.strip():
                return key.strip()
        except Exception:
            pass
    return os.environ.get("CEREBRAS_API_KEY", "").strip()


def _get_cerebras_client() -> object | None:
    """Crea il client Cerebras se disponibile e configurato."""
    api_key = _get_cerebras_api_key()
    if not api_key or Cerebras is None:
        return None
    try:
        return Cerebras(api_key=api_key)
    except Exception:
        return None


def fetch_specs_ai(
    offerte: list[Offerta],
    categoria: str,
    cerebras_client: object | None,
) -> list[Offerta]:
    """Arricchisce le offerte con specs tramite una singola chiamata AI batch."""
    if not offerte:
        return offerte

    categoria_norm = _normalize_category(categoria)

    if categoria_norm == "abbigliamento":
        for offerta in offerte:
            offerta.specs = _extract_clothing_specs(offerta.nome)
        return offerte

    if cerebras_client is None:
        for offerta in offerte:
            offerta.specs = {}
        return offerte

    # Singola chiamata batch universale: invia tutti i nomi prodotto in un'unica richiesta AI
    nomi = [o.nome for o in offerte]
    elenco = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(nomi))
    batch_prompt = (
        "Sei un database prodotti. Per ciascun prodotto nell'elenco numerato "
        "restituisci SOLO un oggetto JSON valido con indici 1,2,3…\n"
        "Ogni valore è un oggetto con le specifiche più rilevanti per quella categoria di prodotto.\n"
        "Esempi: tech → processore, ram, storage, display, os; "
        "abbigliamento → brand, taglia, colore, materiale; "
        "elettrodomestici → marca, potenza, dimensioni, classe_energetica; "
        "scarpe → brand, taglia, materiale, uso.\n"
        "Campi sconosciuti: null. Solo JSON, nessun testo extra.\n"
        f"Elenco prodotti:\n{elenco}"
    )
    try:
        completion = _cerebras_chat(
            cerebras_client,
            messages=[{"role": "user", "content": batch_prompt}],
            temperature=0,
        )
        content = (
            str(completion.choices[0].message.content or "")
            if completion and completion.choices
            else ""
        )
        # Il JSON ritornato è tipo {"1": {...}, "2": {...}}
        outer = _extract_json_object(content)
        for i, offerta in enumerate(offerte):
            key = str(i + 1)
            specs = outer.get(key, {})
            offerta.specs = specs if isinstance(specs, dict) else {}
    except Exception:
        for offerta in offerte:
            offerta.specs = {}

    return offerte


def parse_comparison_query(query: str) -> list[str]:
    """
    Rileva query di confronto e restituisce la lista di sotto-query individuali.

    Supporta:
    - "iphone 15 vs iphone 16 vs iphone 17"  (vs/versus/contro)
    - "confronta X e Y e Z" / "compare X e Y"
    - "iphone 16 o 17 fammi un confronto"    (confronto + X o Y nel testo)
    - "fammi un confronto tra iphone 16 e 17"

    Restituisce lista vuota se non è una query di confronto.

    Es: "iphone 15 vs iphone 16" → ["iphone 15", "iphone 16"]
        "iphone 16 o 17 fammi un confronto" → ["iphone 16", "iphone 17"]
    """
    stripped = query.strip()
    lower = stripped.lower()

    _STOPWORDS_IT = {
        "un",
        "una",
        "il",
        "la",
        "lo",
        "i",
        "le",
        "gli",
        "e",
        "o",
        "di",
        "da",
        "in",
        "a",
        "su",
        "per",
        "con",
        "tra",
        "fra",
        "del",
        "della",
        "dei",
        "delle",
        "agli",
        "allo",
        "alle",
        "come",
        "se",
        "ma",
        "che",
        "chi",
        "cui",
        "ne",
        "ci",
        "non",
        "mi",
        "ti",
        "si",
        "vi",
        "li",
        "me",
        "te",
        "loro",
    }

    # ── Pattern 1: esplicito "vs", "versus", "contro" ─────────────────────
    parts = re.split(r"\s+vs\.?\s+|\s+versus\s+|\s+contro\s+", lower)

    # ── Pattern 2: "confronta/compare X e Y" all'inizio ──────────────────
    if len(parts) == 1:
        m = re.match(r"^(?:confronta|compare|compara)\s+(.+)", lower)
        if m:
            body = m.group(1)
            # rimuovi "tra" iniziale: "confronta tra X e Y" → "X e Y"
            body = re.sub(r"^tra\s+|^fra\s+", "", body)
            parts = re.split(r"\s+e\s+|\s+ed\s+|\s*,\s*", body)

    # ── Pattern 3: "confronto"/"confrontare" in testo + "PROD V1 o V2" ───
    # Gestisce: "iphone 16 o 17 fammi un confronto"
    #            "fammi un confronto tra iphone 16 e 17"
    if len(parts) == 1 and re.search(r"\bconfronto\b|\bconfrontare\b|\bconfrontami\b", lower):
        # Cerca "NOME VERSIONE1 o VERSIONE2" oppure "NOME VERSIONE1 e VERSIONE2"
        # dove almeno una versione contiene cifre (numero di modello)
        m = re.search(r"\b(\w+)\s+(\w+)\s+(?:o|e)\s+(\w+)\b", lower)
        if m:
            base, v1, v2 = m.group(1), m.group(2), m.group(3)
            # Salta se la base è una stopword
            if base not in _STOPWORDS_IT and v1 not in _STOPWORDS_IT and v2 not in _STOPWORDS_IT:
                # Almeno una versione deve contenere cifre (modello) o essere un prodotto reale
                if re.search(r"\d", v1 + v2):
                    parts = [f"{base} {v1}", f"{base} {v2}"]

        # Fallback: "confronto tra X e Y" con "tra/fra" nel testo
        if len(parts) == 1:
            m2 = re.search(r"\btra\s+(.+?)\s+(?:e|ed)\s+(.+?)(?:\s*[,?!]|$)", lower)
            if m2:
                p1 = m2.group(1).strip()
                p2 = m2.group(2).strip().split()[0]  # prendi solo la prima parola se è un numero
                if p1 and p2 and p1 not in _STOPWORDS_IT:
                    # Controlla se p2 potrebbe essere continuazione di p1 (es. "iphone 16 tra ... e 17")
                    parts = [p1, f"{p1.split()[0]} {p2}" if re.search(r"^\d+", p2) else p2]

    # ── Pattern 4: confronto implicito senza keyword (es. "iphone 16 o 17") ──
    if len(parts) == 1:
        m3 = re.search(
            r"\b(iphone|galaxy|pixel|xiaomi|redmi|poco)\s+(\d{1,2}[a-z]?)\s+(?:o|oppure)\s+(\d{1,2}[a-z]?)\b",
            lower,
        )
        if m3:
            base, v1, v2 = m3.group(1), m3.group(2), m3.group(3)
            parts = [f"{base} {v1}", f"{base} {v2}"]

    # Pulizia: tronca al primo segno di punteggiatura e rimuovi frasi discorsive
    def _clean_cmp(p: str) -> str:
        p = re.split(r"[,?!;]", p)[0].strip()
        p = re.sub(
            r"\s+(?:quale|qual|come|cosa|dove|quando|fammi|dimmi|voglio|vorrei|"
            r"consiglio|conviene|scegliere|prendere|meglio|migliore|secondo|dei|due).*$",
            "",
            p,
            flags=re.IGNORECASE,
        )
        words = p.split()
        return " ".join(words[:6]) if len(words) > 6 else p.strip()

    parts = [_clean_cmp(p.strip()) for p in parts]
    parts = [p for p in parts if p and len(p) >= 2]
    return parts if len(parts) >= 2 else []


def detect_category_and_questions(testo_utente: str) -> dict[str, object]:
    """
    Classifica la categoria e propone domande di chiarimento per la mini-chat.
    """
    fallback_questions: dict[str, list[str]] = {
        "smartphone": [
            "Hai preferenze di colore?",
            "Ti serve una versione standard o Pro, e con quanto storage?",
        ],
        "laptop": [
            "Qual e l'uso principale? (studio, ufficio, gaming, editing)",
            "Preferisci un modello leggero/portatile o va bene standard?",
        ],
        "abbigliamento": [
            "Che taglia cerchi?",
            "Hai un colore preferito?",
        ],
        "scarpe": [
            "Che numero ti serve?",
            "Uso principale: sportivo o casual?",
        ],
        "elettrodomestico": [
            "Hai preferenze di marca o variante?",
            "Qual e l'uso principale?",
        ],
        "televisore": [
            "Che diagonale cerchi (es. 55, 65 pollici)?",
            "Preferisci OLED, QLED o LED?",
        ],
        "libri": [
            "Preferisci versione cartacea o ebook?",
            "Hai preferenze di edizione o lingua?",
        ],
        "sport": [
            "Per quale sport o attività?",
            "Hai una taglia o misura?",
        ],
        "casa": [
            "Hai preferenze di colore o stile?",
            "Che dimensioni cerchi?",
        ],
        "beauty": [
            "Hai preferenze di marca?",
            "Per che tipo di pelle/uso è?",
        ],
        "altro": [
            "Hai preferenze di colore/storage/variante?",
            "Preferisci nuovo o usato?",
        ],
    }

    testo = str(testo_utente or "").strip()
    if not testo:
        return {
            "categoria": "altro",
            "domande": fallback_questions["altro"],
            "preferenze_chiare": False,
            "intent_precompilato": {},
        }

    intent_pre = parse_search_intent(testo)

    def _infer_category(lower_text: str) -> str:
        if any(
            k in lower_text
            for k in ("iphone", "smartphone", "telefono", "android", "galaxy", "pixel")
        ):
            return "smartphone"
        if any(k in lower_text for k in ("laptop", "notebook", "pc", "macbook", "thinkpad")):
            return "laptop"
        if any(
            k in lower_text for k in ("scarpe", "sneaker", "stivali", "sandali", "nike", "adidas")
        ):
            return "scarpe"
        if any(
            k in lower_text
            for k in ("maglia", "giacca", "pantaloni", "vestito", "abbigliamento", "felpa")
        ):
            return "abbigliamento"
        if any(k in lower_text for k in ("frigorifero", "lavatrice", "forno", "aspirapolvere")):
            return "elettrodomestico"
        if any(k in lower_text for k in ("tv", "televisore", "smart tv", "oled", "qled")):
            return "televisore"
        if any(k in lower_text for k in ("libro", "romanzo", "fumetto")):
            return "libri"
        if any(k in lower_text for k in ("bici", "tapis roulant", "manubri", "pallone")):
            return "sport"
        if any(k in lower_text for k in ("divano", "lampada", "tenda", "scrivania")):
            return "casa"
        if any(k in lower_text for k in ("crema", "profumo", "shampoo")):
            return "beauty"
        return "altro"

    def _questions_for_missing(categoria: str, lower_text: str) -> tuple[list[str], bool]:
        has_color = any(
            c in lower_text
            for c in (
                "nero",
                "black",
                "bianco",
                "white",
                "rosa",
                "pink",
                "blu",
                "blue",
                "rosso",
                "red",
                "verde",
                "green",
                "lavanda",
                "silver",
                "graphite",
            )
        )
        has_storage = (
            re.search(r"\b(64|128|256|512|1024)\s*gb\b|\b1\s*tb\b", lower_text) is not None
        )
        has_variant = any(
            v in lower_text for v in ("pro max", "pro", "plus", "standard", "base", "ultra", "mini")
        )
        has_size = (
            re.search(r"\b(?:xxs|xs|s|m|l|xl|xxl|\d{2}(?:[\.,]\d)?)\b", lower_text) is not None
        )
        has_use = any(
            v in lower_text
            for v in (
                "studio",
                "ufficio",
                "lavoro",
                "gaming",
                "editing",
                "sportivo",
                "casual",
                "running",
                "trail",
            )
        )
        has_portability = any(
            v in lower_text
            for v in ("leggero", "leggera", "portatile", "ultraleggero", "peso", "sottile")
        )
        is_apple_phone = any(v in lower_text for v in ("iphone", "apple"))

        questions: list[str] = []
        if categoria == "smartphone":
            if is_apple_phone and not has_storage:
                questions.append("Quanti GB di storage preferisci?")
            if is_apple_phone and not has_variant:
                questions.append("Preferisci il modello standard o Pro?")
            if not is_apple_phone and not has_color:
                questions.append("Hai preferenze di colore?")
            if not is_apple_phone and not has_storage:
                questions.append("Quanti GB di storage preferisci?")
            preferenze_ok = (
                (has_storage and has_variant) if is_apple_phone else (has_storage or has_color)
            )
            return questions[:2], preferenze_ok

        if categoria == "scarpe":
            if not has_size:
                questions.append("Che numero ti serve?")
            if not has_use:
                questions.append("Uso principale: sportivo o casual?")
            return questions[:2], has_size and has_use

        if categoria == "abbigliamento":
            if not has_size:
                questions.append("Che taglia cerchi?")
            if not has_color:
                questions.append("Hai un colore preferito?")
            return questions[:2], has_size and has_color

        if categoria == "laptop":
            if not has_use:
                questions.append("Qual e l'uso principale? (studio, ufficio, gaming, editing)")
            if not has_portability:
                questions.append("Preferisci un modello leggero/portatile o va bene standard?")
            return questions[:2], has_use and has_portability

        if categoria == "elettrodomestico":
            if not any(
                v in lower_text for v in ("marca", "bosch", "samsung", "lg", "miele", "whirlpool")
            ):
                questions.append("Hai preferenze di marca o variante?")
            if not has_use:
                questions.append("Qual e l'uso principale?")
            return questions[:2], len(questions) == 0

        if not has_color:
            questions.append("Hai preferenze di colore/storage/variante?")
        if not any(v in lower_text for v in ("nuovo", "usato")):
            questions.append("Preferisci nuovo o usato?")
        return questions[:2], len(questions) == 0

    lower = testo.lower()
    categoria_base = _infer_category(lower)
    domande_base, preferenze_chiare_base = _questions_for_missing(categoria_base, lower)

    client = _get_cerebras_client()
    if client is None:
        categoria = categoria_base
        return {
            "categoria": categoria,
            "domande": []
            if preferenze_chiare_base
            else (domande_base or fallback_questions[categoria])[:2],
            "preferenze_chiare": preferenze_chiare_base,
            "intent_precompilato": intent_pre if preferenze_chiare_base else {},
        }

    try:
        prompt = (
            "Sei un assistente acquisti. Identifica categoria prodotto, marca/modello gia specificati "
            "e preferenze gia espresse (colore, storage, taglia, variante, uso).\n\n"
            "REGOLE ASSOLUTE:\n"
            "- Genera SOLO domande sulle preferenze ANCORA mancanti\n"
            "- NON chiedere mai di nuovo modello o colore se sono gia presenti (es: 'iphone 17 nero')\n"
            "- Fai SOLO domande su preferenze utente: colore, storage, variante, taglia, uso, portabilita\n"
            "- NON chiedere mai specifiche tecniche come megapixel, batteria, processore, sistema operativo\n"
            "- Massimo 2 domande totali, una per turno\n"
            "- Se le preferenze minime sono gia chiare, restituisci preferenze_chiare=true e domande=[]\n\n"
            "Minimi per categoria:\n"
            "- smartphone Apple: storage e modello (Pro/standard) se mancanti\n"
            "- scarpe: numero e uso\n"
            "- abbigliamento: taglia e colore\n"
            "- laptop: uso principale e peso/portabilita\n\n"
            "Restituisci SOLO JSON con chiavi: categoria (string tra smartphone, laptop, tablet, televisore, "
            "elettrodomestico, abbigliamento, scarpe, sport, libri, beauty, casa, altro), "
            "domande (array di max 2 stringhe), preferenze_chiare (bool)."
        )
        completion = _cerebras_chat(
            client,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": testo},
            ],
            temperature=0.1,
        )
        content = completion.choices[0].message.content if completion and completion.choices else ""
        raw = str(content or "").strip()
        json_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        payload = json.loads(json_match.group(0) if json_match else raw)
        if not isinstance(payload, dict):
            raise ValueError("Payload non valido")
        categoria = str(payload.get("categoria", "altro") or "altro").strip().lower()
        if categoria not in fallback_questions:
            categoria = "altro"
        domande_raw = payload.get("domande", [])
        domande = [str(d).strip() for d in domande_raw if str(d).strip()]
        domande_missing, preferenze_missing = _questions_for_missing(categoria, lower)
        if not domande:
            domande = domande_missing or fallback_questions[categoria]
        preferenze_ai = bool(payload.get("preferenze_chiare", False)) or preferenze_missing
        return {
            "categoria": categoria,
            "domande": [] if preferenze_ai else (domande_missing or domande)[:2],
            "preferenze_chiare": preferenze_ai,
            "intent_precompilato": intent_pre if preferenze_ai else {},
        }
    except Exception:
        return {
            "categoria": "altro",
            "domande": []
            if preferenze_chiare_base
            else (domande_base or fallback_questions["altro"])[:2],
            "preferenze_chiare": preferenze_chiare_base,
            "intent_precompilato": intent_pre if preferenze_chiare_base else {},
        }


def parse_search_intent(risposta_utente: str) -> dict[str, object]:
    """
    Estrae intent di ricerca da testo libero usando AI.

    Output standard:
      {
        "query": str,
        "prezzo_min": int,
        "prezzo_max": int,
        "condizione": str,
        "filtri": dict[str, str]
      }
    """
    default: dict[str, object] = {
        "query": str(risposta_utente or "").strip(),
        "prezzo_min": 0,
        "prezzo_max": 2000,
        "condizione": "tutti",
        "filtri": {},
    }

    def _sanitize(payload: dict[str, object]) -> dict[str, object]:
        query = str(payload.get("query", "") or "").strip()
        prezzo_min_raw = payload.get("prezzo_min", 0)
        prezzo_max_raw = payload.get("prezzo_max", 2000)
        condizione_raw = str(payload.get("condizione", "tutti") or "tutti").strip().lower()
        filtri_raw = payload.get("filtri", {})

        try:
            prezzo_min = int(float(str(prezzo_min_raw)))
        except Exception:
            prezzo_min = 0
        try:
            prezzo_max = int(float(str(prezzo_max_raw)))
        except Exception:
            prezzo_max = 2000

        prezzo_min = max(0, prezzo_min)
        prezzo_max = max(prezzo_min, prezzo_max)

        if condizione_raw not in {"tutti", "nuovo", "usato"}:
            condizione_raw = "usato" if "usat" in condizione_raw else "tutti"

        filtri: dict[str, str] = {}
        if isinstance(filtri_raw, dict):
            for k, v in filtri_raw.items():
                key = str(k or "").strip().lower()
                val = str(v or "").strip()
                if key and val:
                    filtri[key] = val

        return {
            "query": query,
            "prezzo_min": prezzo_min,
            "prezzo_max": prezzo_max,
            "condizione": condizione_raw,
            "filtri": filtri,
        }

    testo = str(risposta_utente or "").strip()
    if not testo:
        return default

    client = _get_cerebras_client()
    if client is None:
        guess = dict(default)
        m_range = re.search(r"(\d{2,5})\s*[-/a]\s*(\d{2,5})", testo.lower())
        m_min = re.search(r"(?:min(?:imo)?|da|partire da)\s*(\d{2,5})", testo.lower())
        m_max = re.search(r"(?:max(?:imo)?|massimo|fino a|budget)\s*(\d{2,5})", testo.lower())
        if m_range:
            guess["prezzo_min"] = int(m_range.group(1))
            guess["prezzo_max"] = int(m_range.group(2))
        else:
            if m_min:
                guess["prezzo_min"] = int(m_min.group(1))
            if m_max:
                guess["prezzo_max"] = int(m_max.group(1))

        lower = testo.lower()
        if "usat" in lower:
            guess["condizione"] = "usato"
        elif "nuov" in lower:
            guess["condizione"] = "nuovo"

        filtri_local: dict[str, str] = {}
        if "rosa" in lower:
            filtri_local["colore"] = "rosa"
        if "lavanda" in lower:
            filtri_local["colore"] = "lavanda"
        storage_match = re.search(r"\b(\d{2,4})\s*gb\b", lower)
        if storage_match:
            filtri_local["storage"] = f"{storage_match.group(1)}gb"

        guess["query"] = testo
        guess["filtri"] = filtri_local
        return _sanitize(guess)

    try:
        system_prompt = (
            "Estrai un intent di ricerca shopping da testo utente in italiano. "
            "Rispondi SOLO con JSON valido senza markdown con chiavi: "
            "query (string), prezzo_min (int), prezzo_max (int), condizione (tutti|nuovo|usato), "
            "filtri (object con attributi non cercabili direttamente, es colore/storage/taglia)."
        )
        completion = _cerebras_chat(
            client,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": testo},
            ],
            temperature=0,
        )
        content = completion.choices[0].message.content if completion and completion.choices else ""
        raw = str(content or "").strip()
        json_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        payload = json.loads(json_match.group(0) if json_match else raw)
        if not isinstance(payload, dict):
            return _sanitize(default)
        return _sanitize(payload)
    except Exception:
        return _sanitize(default)


def filtra_risultati_con_ai(risultati: list[Offerta], filtri: dict[str, str]) -> list[Offerta]:
    """
    Applica ranking AI ai risultati in base a filtri semantici non direttamente cercabili.

    Regole:
      - scarta score < 3
      - ordina per score desc, poi prezzo asc
    """
    if not risultati or not filtri:
        return risultati

    # Filtro hard su vincoli tecnici (ram/storage/display) con log motivazionale.
    # MENO AGGRESSIVO: skip hard filter se filtri_ai è vuoto o contiene solo query generiche
    has_hard_specs = any(
        k in str(filtri).lower() for k in ["ram", "storage", "display", "size", "gb", "ssd"]
    )

    hard_filtered: list[Offerta] = []
    hard_dropped: list[tuple[Offerta, list[str]]] = []

    if has_hard_specs:  # Applica hard filter SOLO se ci sono specs tecniche
        for offerta in risultati:
            reasons = _hard_spec_mismatch_reasons(offerta, filtri)
            if reasons:
                hard_dropped.append((offerta, reasons))
            else:
                hard_filtered.append(offerta)
    else:
        # Se no hard specs, accetta tutti i risultati
        hard_filtered = risultati

    if hard_dropped:
        print(f"  🧾 Log filtro AI: scartate {len(hard_dropped)} offerte per hard constraints")
        for offerta, reasons in hard_dropped[:20]:
            short_name = offerta.nome[:90]
            print(f"    - SCARTO hard | {short_name} | motivo: {', '.join(reasons)}")
        extra = len(hard_dropped) - 20
        if extra > 0:
            print(f"    ... altri {extra} scarti hard non mostrati")

    if hard_filtered:
        risultati = hard_filtered

    scored: list[tuple[int, Offerta]] = []

    client = _get_cerebras_client()
    if client is not None:
        try:
            titoli = [f"{i + 1}. {o.nome}" for i, o in enumerate(risultati)]
            prompt = (
                "Valuta la rilevanza 0-10 dei titoli rispetto ai filtri dati. "
                "Considera sinonimi e varianti (es rosa ~ pink ~ lavanda quando plausibile). "
                'Rispondi SOLO JSON: {"scores": [{"idx":1,"score":7}, ...]}'
            )
            completion = _cerebras_chat(
                client,
                messages=[
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": f"Filtri: {json.dumps(filtri, ensure_ascii=False)}\nTitoli:\n"
                        + "\n".join(titoli),
                    },
                ],
                temperature=0,
            )
            content = (
                completion.choices[0].message.content if completion and completion.choices else ""
            )
            raw = str(content or "").strip()
            json_match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
            payload = json.loads(json_match.group(0) if json_match else raw)
            scores_map: dict[int, int] = {}
            for row in payload.get("scores", []):
                idx = int(row.get("idx", 0)) - 1
                score = int(float(str(row.get("score", 0))))
                if 0 <= idx < len(risultati):
                    scores_map[idx] = max(0, min(10, score))

            for idx, offerta in enumerate(risultati):
                scored.append((scores_map.get(idx, 0), offerta))
        except Exception:
            scored = []

    if not scored:
        # Fallback locale lessicale quando AI non disponibile.
        # Espande aliases per spec matching (es. "16gb" -> {"16gb", "16 gb"})
        expanded_values: set[str] = set()
        for v in filtri.values():
            val = str(v).lower().strip()
            if not val:
                continue
            expanded_values.add(val)
            # Aggiungi varianti con/senza spazio per specs
            num_match = re.match(r"^(\d+)(gb|tb)$", val)
            if num_match:
                expanded_values.add(f"{num_match.group(1)} {num_match.group(2)}")
                expanded_values.add(f"{num_match.group(1)}{num_match.group(2)}")
            # Sinonimi colore
            color_syn: dict[str, set[str]] = {
                "rosa": {"pink", "lavanda"},
                "nero": {"black", "graphite"},
                "bianco": {"white", "silver"},
            }
            if val in color_syn:
                expanded_values.update(color_syn[val])

        for offerta in risultati:
            titolo = offerta.nome.lower()
            # Controlla anche specs arricchite se disponibili
            specs_text = " ".join(str(sv).lower() for sv in (offerta.specs or {}).values() if sv)
            search_text = f"{titolo} {specs_text}"
            hit = sum(1 for v in expanded_values if v and v in search_text)
            score = min(10, hit * 4)
            scored.append((score, offerta))

    filtered = [(score, o) for score, o in scored if score >= 3]
    dropped_by_score = [(score, o) for score, o in scored if score < 3]
    if dropped_by_score:
        print(f"  🧾 Log filtro AI: scartate {len(dropped_by_score)} offerte per score < 3")
        for score, offerta in dropped_by_score[:20]:
            short_name = offerta.nome[:90]
            print(f"    - SCARTO score={score} | {short_name}")
        extra = len(dropped_by_score) - 20
        if extra > 0:
            print(f"    ... altri {extra} scarti score non mostrati")
    # Se il filtro specs è troppo aggressivo e scarta tutto, ritorna tutti con ranking
    if not filtered and scored:
        scored.sort(key=lambda x: (-x[0], x[1].prezzo))
        return [o for _, o in scored]
    filtered.sort(key=lambda x: (-x[0], x[1].prezzo))

    # Alternative detection: suggerisce una variante diversa se costa >10% in meno.
    keys_variante = {"colore", "storage", "variante"}
    filtri_variante = {
        str(k).strip().lower(): str(v).strip().lower()
        for k, v in filtri.items()
        if str(k).strip().lower() in keys_variante and str(v).strip()
    }
    if filtri_variante:
        color_synonyms = {
            "rosa": {"rosa", "pink", "lavanda", "rose"},
            "nero": {"nero", "black", "graphite"},
            "bianco": {"bianco", "white", "silver"},
        }

        for score, offerta in filtered:
            offerta.alternativa = ""
            if score < 7:
                continue

            titolo_ref = offerta.nome.lower()
            best_alt: Offerta | None = None
            best_diff = 0.0
            best_label = ""

            for _, candidato in filtered:
                if candidato is offerta:
                    continue
                if candidato.prezzo >= offerta.prezzo * 0.90:
                    continue

                titolo_cand = candidato.nome.lower()
                variante_diversa = False
                variante_label = ""

                for key, value in filtri_variante.items():
                    terms = color_synonyms.get(value, {value}) if key == "colore" else {value}
                    ref_has_value = any(t in titolo_ref for t in terms)
                    cand_has_value = any(t in titolo_cand for t in terms)
                    if ref_has_value and not cand_has_value:
                        variante_diversa = True
                        if key == "colore":
                            variante_label = "Versione con colore diverso"
                        elif key == "storage":
                            variante_label = "Versione con storage diverso"
                        else:
                            variante_label = "Versione alternativa"
                        break

                if not variante_diversa:
                    continue

                diff = offerta.prezzo - candidato.prezzo
                if diff > best_diff:
                    best_diff = diff
                    best_alt = candidato
                    best_label = variante_label

            if best_alt and best_diff > 0:
                delta_txt = (
                    f"€{best_diff:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                )
                offerta.alternativa = f"💡 {best_label} costa {delta_txt} in meno"

    return [o for _, o in filtered]


# === Cerebras model resolver + chat-with-retry (ex cerebras_model.py) ======== #
_BLACKLIST = CEREBRAS_MODEL_BLACKLIST
# Candidati di fallback (lista ordinata), usati solo se non si può interrogare
# l'API. La scelta normale è dinamica: vedi get_best_model().
_FALLBACK_MODELS = CEREBRAS_FALLBACK_MODELS
_FALLBACK_MODEL = _FALLBACK_MODELS[0]
_cached_model: str | None = None


def get_best_model(client=None, force_refresh: bool = False) -> str:
    """Sceglie dinamicamente il miglior modello DISPONIBILE per il provider attivo.

    Delega a `offerte.providers.best_model()`: preferisce un candidato noto del
    provider effettivamente disponibile, altrimenti il modello col context_window
    più ampio. Nessun modello hardcodato come scelta. Risultato in cache (svuota
    con `invalidate_model()`, es. al cambio provider).
    """
    global _cached_model
    if _cached_model and not force_refresh:
        return _cached_model
    from offerte import providers

    _cached_model = providers.best_model(client=client)
    return _cached_model


def invalidate_model() -> None:
    """Svuota la cache del modello (es. dopo un 404)."""
    global _cached_model
    _cached_model = None


def cerebras_chat_with_retry(
    client,
    messages: list,
    model: str | None = None,
    max_retries: int = 4,
    base_delay: float = 2.0,
    **kwargs,
):
    """Chiama client.chat.completions.create() con retry su 404/429."""
    if model is None:
        model = get_best_model(client=client)
    last_exc = None
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                **kwargs,
            )
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc)
            if "404" in exc_str or "model_not_found" in exc_str or "does not exist" in exc_str:
                invalidate_model()
                model = get_best_model(client=client, force_refresh=True)
                time.sleep(1.0)
                continue
            if "429" in exc_str or "rate_limit" in exc_str or "too many" in exc_str.lower():
                wait = base_delay * (2**attempt)
                time.sleep(wait)
                continue
            if attempt < max_retries - 1:
                time.sleep(base_delay)
    raise last_exc  # type: ignore[misc]


# =============================================================================

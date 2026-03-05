"""
app.py — Interfaccia Streamlit per offerte_tech.py
===================================================
Avvio:
    streamlit run app.py
"""

import contextlib
import csv
import io
import os

import streamlit as st

try:
    from groq import Groq
except Exception:
    Groq = None

# ---------------------------------------------------------------------------
# Configurazione pagina (deve essere la PRIMA chiamata Streamlit)
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Offerte Tech Italia",
    page_icon="🛒",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ---------------------------------------------------------------------------
# Import modulo scraper locale
# ---------------------------------------------------------------------------
try:
    from offerte_tech import Offerta, cerca_offerte
except ImportError as _e:
    st.error(
        f"❌ Impossibile importare `offerte_tech.py`: {_e}\n\n"
        "Assicurati che `offerte_tech.py` si trovi nella stessa cartella di `app.py` "
        "e di aver installato le dipendenze:\n"
        "```\npip install requests beautifulsoup4 fake-useragent\n```"
    )
    st.stop()

# ---------------------------------------------------------------------------
# Stile CSS minimale
# ---------------------------------------------------------------------------
st.markdown(
    """
    <style>
        /* Rimpicciolisce il padding superiore della pagina */
        .block-container { padding-top: 1.5rem; }

        /* Colore accent per le metriche */
        [data-testid="stMetricValue"] { color: #1f7a1f; font-size: 1.6rem; }

        /* Bordo arrotondato attorno ai risultati */
        [data-testid="stDataFrame"] { border-radius: 8px; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Inizializzazione session_state
# ---------------------------------------------------------------------------
if "risultati" not in st.session_state:
    st.session_state["risultati"] = []
if "log_ricerca" not in st.session_state:
    st.session_state["log_ricerca"] = ""
if "ultima_query" not in st.session_state:
    st.session_state["ultima_query"] = ""
if "ricerca_effettuata" not in st.session_state:
    st.session_state["ricerca_effettuata"] = False
if "condizione" not in st.session_state:
    st.session_state["condizione"] = "tutti"
if "ultimo_budget" not in st.session_state:
    st.session_state["ultimo_budget"] = 800
if "ultimo_top_n" not in st.session_state:
    st.session_state["ultimo_top_n"] = 10
if "fonti_selezionate" not in st.session_state:
    st.session_state["fonti_selezionate"] = ["Amazon", "eBay", "Vinted", "Trovaprezzi"]
if "messaggi_chat" not in st.session_state:
    st.session_state["messaggi_chat"] = []
if "chat_attiva" not in st.session_state:
    st.session_state["chat_attiva"] = False
if "intro_chat_tentato" not in st.session_state:
    st.session_state["intro_chat_tentato"] = False
if "contesto_chat" not in st.session_state:
    st.session_state["contesto_chat"] = ""

SYSTEM_PROMPT_AI = (
    "Sei un assistente esperto di tecnologia che aiuta gli utenti a scegliere "
    "prodotti tech al miglior prezzo. Rispondi sempre in italiano, sii conciso "
    "e diretto. Quando consigli un prodotto cita sempre il numero (#) e il prezzo. "
    "Non inventare prodotti che non sono nella lista. "
    "Quando i dati tecnici dei prodotti sono incompleti, usa la tua conoscenza generale "
    "delle specifiche tecniche dei modelli elencati per rispondere. Per schede video, "
    "CPU, laptop e componenti hardware, sei autorizzato a basarti sui benchmark e "
    "specifiche tecniche che conosci per dare consigli precisi e dettagliati."
)


def _get_groq_api_key() -> str:
    """Legge la key da Streamlit secrets, con fallback variabile ambiente."""
    key = ""
    try:
        key = str(st.secrets.get("GROQ_API_KEY", "") or "")
    except Exception:
        key = ""
    if not key.strip():
        key = os.environ.get("GROQ_API_KEY", "")
    return key.strip()


def _build_results_summary(query: str, budget: float | None, offerte: list[Offerta]) -> str:
    """Costruisce il messaggio iniziale con elenco risultati per il prompt AI."""
    budget_txt = "nessun limite" if budget is None else f"€{budget:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    righe = []
    for i, o in enumerate(offerte, start=1):
        prezzo_txt = f"€{o.prezzo:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        righe.append(f"#{i} | {prezzo_txt} | {o.negozio} | {o.nome}")

    elenco = "\n".join(righe)
    return (
        f"Ho cercato \"{query}\" con budget {budget_txt} e ho trovato questi risultati:\n"
        f"{elenco}\n\n"
        "Analizza i risultati e consigliami quale comprare e perché.\n"
        "Sii diretto e vai al punto."
    )


def _call_groq_chat(
    user_messages: list[dict[str, str]],
    api_key: str,
    contesto_iniziale: str = "",
) -> str:
    """Invia la chat a Groq e ritorna il testo risposta assistant."""
    if Groq is None:
        raise RuntimeError("Pacchetto groq non installato. Esegui: pip install groq")

    client = Groq(api_key=api_key)
    payload = [{"role": "system", "content": SYSTEM_PROMPT_AI}]
    if contesto_iniziale.strip():
        payload.append({"role": "user", "content": contesto_iniziale})
    payload += user_messages

    completion = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=payload,
        temperature=0.3,
    )
    content = completion.choices[0].message.content if completion and completion.choices else ""
    return str(content or "").strip()

# ---------------------------------------------------------------------------
# Helper: converti lista Offerta → bytes CSV (senza dipendenze extra)
# ---------------------------------------------------------------------------
def _offerte_to_csv_bytes(offerte: list[Offerta]) -> bytes:
    """Serializza la lista di Offerta in bytes UTF-8 BOM (compatibile Excel)."""
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["posizione", "nome", "prezzo_eur", "negozio", "fonte", "link"],
        lineterminator="\n",
    )
    writer.writeheader()
    for i, o in enumerate(offerte, start=1):
        writer.writerow({
            "posizione":  i,
            "nome":       o.nome,
            "prezzo_eur": f"{o.prezzo:.2f}",
            "negozio":    o.negozio,
            "fonte":      o.fonte,
            "link":       o.link,
        })
    # utf-8-sig → BOM per apertura corretta in Excel italiano
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


# ---------------------------------------------------------------------------
# Helper: converti lista Offerta → lista di dict per st.dataframe
# ---------------------------------------------------------------------------
def _offerte_to_records(offerte: list[Offerta]) -> list[dict]:
    return [
        {
            "#":        i,
            "Prodotto": o.nome,
            "Prezzo €": round(o.prezzo, 2),
            "Negozio":  o.negozio,
            "Fonte":    o.fonte,
            "Link":     o.link,
        }
        for i, o in enumerate(offerte, start=1)
    ]


# ===========================================================================
# INTESTAZIONE
# ===========================================================================
st.title("🛒 Offerte Tech Italia")
st.caption(
    "Cerca prodotti tech su **trovaprezzi.it**, **Amazon.it**, **eBay.it** e **Vinted.it** · "
    "Risultati ordinati per prezzo crescente"
)
st.divider()

# ===========================================================================
# PANNELLO DI RICERCA  (3 colonne: query | budget | top_n)
# ===========================================================================
col_query, col_budget, col_top = st.columns([4, 2, 1], gap="medium")

with col_query:
    query_input = st.text_input(
        "🔎 Prodotto da cercare",
        placeholder="es. notebook 14 pollici 16gb",
        help="Inserisci i termini chiave separati da spazio.",
        value=st.session_state.get("ultima_query", ""),
        key="query_input",
        on_change=lambda: st.session_state.update({"ultima_query": st.session_state.get("query_input", "")}),
    ) or ""

with col_budget:
    budget_input = st.slider(
        "💶 Budget massimo (€)",
        min_value=0,
        max_value=3000,
        value=int(st.session_state.get("ultimo_budget", 800)),
        step=50,
        help="Filtra i prodotti oltre questo prezzo. Imposta 0 per nessun limite.",
    )
    budget_max = float(budget_input) if budget_input > 0 else None

with col_top:
    top_n_input = st.number_input(
        "📊 N° risultati",
        min_value=1,
        max_value=50,
        value=int(st.session_state.get("ultimo_top_n", 10)),
        step=1,
        help="Quante offerte mostrare al massimo.",
    )

condizione_ui = st.radio(
    "🏷️ Condizione",
    ["Tutti", "Nuovo", "Usato"],
    horizontal=True,
    index={"tutti": 0, "nuovo": 1, "usato": 2}.get(st.session_state.get("condizione", "tutti"), 0),
)
condizione = condizione_ui.lower()

fonti_disponibili = ["Tutte", "Amazon", "eBay", "Vinted", "Trovaprezzi"]
fonti_selezionate = st.multiselect(
    "🌐 Fonti da consultare",
    fonti_disponibili[1:],
    default=st.session_state.get("fonti_selezionate", fonti_disponibili[1:]),
)
fonti_map = {
    "Amazon": "amazon",
    "eBay": "ebay",
    "Vinted": "vinted",
    "Trovaprezzi": "trovaprezzi",
}
fonti_backend = [fonti_map[f] for f in fonti_selezionate if f in fonti_map]

# Bottone di ricerca centrato
_, col_btn, _ = st.columns([3, 2, 3])
with col_btn:
    avvia_ricerca = st.button(
        "🔍 Cerca offerte",
        type="primary",
        width="stretch",
        disabled=not query_input.strip(),
    )

# ===========================================================================
# LOGICA DI RICERCA
# ===========================================================================
if avvia_ricerca:
    if not query_input.strip():
        st.warning("⚠️ Inserisci un termine di ricerca prima di procedere.")
    else:
        st.session_state["ricerca_effettuata"] = True
        st.session_state["ultima_query"]         = query_input.strip()
        st.session_state["risultati"]            = []
        st.session_state["log_ricerca"]          = ""
        st.session_state["condizione"]           = condizione
        st.session_state["ultimo_budget"]        = int(budget_input)
        st.session_state["ultimo_top_n"]         = int(top_n_input)
        st.session_state["fonti_selezionate"]    = fonti_selezionate
        st.session_state["messaggi_chat"]        = []
        st.session_state["chat_attiva"]          = False
        st.session_state["intro_chat_tentato"]   = False
        st.session_state["contesto_chat"]        = ""

        # Cattura i print() interni di cerca_offerte() senza mostrarli nel terminale
        log_buffer = io.StringIO()

        with st.spinner("⏳ Sto cercando sulle fonti selezionate…"):
            try:
                with contextlib.redirect_stdout(log_buffer):
                    risultati = cerca_offerte(
                        query        = query_input.strip(),
                        budget_max   = budget_max,
                        top_n        = int(top_n_input),
                        export_csv   = False,
                        condizione   = condizione,
                        fonti        = fonti_backend,
                    )
                st.session_state["risultati"]   = risultati
                st.session_state["log_ricerca"] = log_buffer.getvalue()
                st.session_state["chat_attiva"] = bool(risultati)

            except Exception as exc:
                st.session_state["log_ricerca"] = log_buffer.getvalue()
                st.error(
                    f"❌ Si è verificato un errore durante la ricerca:\n\n"
                    f"```\n{exc}\n```\n\n"
                    "Verifica la connessione internet e riprova."
                )

# ===========================================================================
# RISULTATI
# ===========================================================================
if st.session_state.get("ricerca_effettuata", False):

    st.divider()

    # ---- Log di avanzamento (collassato di default) ----
    if st.session_state.get("log_ricerca", ""):
        with st.expander("📋 Log ricerca", expanded=False):
            st.code(st.session_state.get("log_ricerca", ""), language=None)

    offerte: list[Offerta] = st.session_state.get("risultati", [])

    # ---- Nessun risultato ----
    if not offerte:
        st.warning(
            "⚠️ **Nessuna offerta trovata** per la query corrente.\n\n"
            "Prova a:\n"
            "- 🔼 Aumentare il budget massimo\n"
            "- ✏️ Usare termini più generici (es. rimuovi il numero di pollici)\n"
            "- 🔄 Verificare la tua connessione internet\n"
            "- ⏱️ Attendere qualche secondo e riprovare (possibile blocco anti-bot)"
        )

    else:
        # ---- Metriche riepilogative ----
        prezzo_min   = min(o.prezzo for o in offerte)
        negozio_min  = next(o.negozio for o in offerte if o.prezzo == prezzo_min)
        fonti_uniche = len({o.fonte for o in offerte})

        st.subheader(
            f"✅ {len(offerte)} offert{'a' if len(offerte) == 1 else 'e'} trovate "
            f"per \"{st.session_state.get('ultima_query', '')}\""
        )

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("🏆 Risultati trovati",     str(len(offerte)))
        m2.metric("💰 Prezzo più basso",       f"€ {prezzo_min:,.2f}".replace(",", "."))
        m3.metric("🏪 Negozio più economico",  negozio_min)
        m4.metric("🌐 Fonti consultate",        str(fonti_uniche))

        st.divider()

        # ---- Tabella interattiva ----
        records = _offerte_to_records(offerte)

        st.dataframe(
            records,
            width="stretch",
            hide_index=True,
            height=min(80 + len(records) * 36, 600),   # altezza dinamica, max 600px
            column_config={
                "#": st.column_config.NumberColumn(
                    "#", width="small", format="%d"
                ),
                "Prodotto": st.column_config.TextColumn(
                    "📦 Prodotto", width="large"
                ),
                "Prezzo €": st.column_config.NumberColumn(
                    "💶 Prezzo €",
                    format="€ %.2f",
                    width="small",
                ),
                "Negozio": st.column_config.TextColumn(
                    "🏪 Negozio", width="medium"
                ),
                "Fonte": st.column_config.TextColumn(
                    "🌐 Fonte", width="small"
                ),
                "Link": st.column_config.LinkColumn(
                    "🔗 Link",
                    display_text="Apri →",
                    width="small",
                ),
            },
        )

        st.caption(
            "💡 Clicca sull'intestazione di una colonna per ordinare · "
            "La colonna **Link** apre la pagina del prodotto in una nuova scheda"
        )

        st.divider()

        # ---- Export CSV ----
        csv_bytes = _offerte_to_csv_bytes(offerte)
        nome_file = (
            f"offerte_{st.session_state.get('ultima_query', 'ricerca')[:30].replace(' ', '_')}.csv"
        )

        st.download_button(
            label     = "📥 Esporta CSV",
            data      = csv_bytes,
            file_name = nome_file,
            mime      = "text/csv",
            help      = f"Scarica i {len(offerte)} risultati in formato CSV (compatibile Excel)",
        )

        st.divider()

        groq_api_key = _get_groq_api_key()
        if not groq_api_key:
            st.info("💡 Aggiungi la tua GROQ_API_KEY in .streamlit/secrets.toml per abilitare l'assistente AI.")
        else:
            with st.container(border=True):
                st.subheader("🤖 Assistente AI")

                # Primo messaggio automatico AI al primo render utile dopo una ricerca.
                if st.session_state.get("chat_attiva", False) and not st.session_state.get("intro_chat_tentato", False):
                    st.session_state["intro_chat_tentato"] = True
                    st.session_state["contesto_chat"] = _build_results_summary(
                        st.session_state.get("ultima_query", ""),
                        budget_max,
                        offerte,
                    )
                    with st.spinner("🤖 L'AI sta analizzando..."):
                        try:
                            risposta_ai = _call_groq_chat(
                                st.session_state.get("messaggi_chat", []),
                                groq_api_key,
                                contesto_iniziale=st.session_state.get("contesto_chat", ""),
                            )
                            if not risposta_ai:
                                raise RuntimeError("Risposta vuota dal modello. Controlla i limiti su console.groq.com")
                            st.session_state["messaggi_chat"].append({"role": "assistant", "content": risposta_ai})
                            st.rerun()
                        except Exception as exc:
                            msg = str(exc)
                            if "RateLimit" in msg or "429" in msg:
                                st.error(f"❌ Errore AI: {msg}. Riprova tra qualche secondo.")
                            else:
                                st.error(f"❌ Errore AI: {msg}. Riprova tra qualche secondo.")
                                st.info("Se il problema persiste, controlla limiti e stato API su console.groq.com")

                if st.session_state.get("chat_attiva", False):
                    st.caption(f"🔍 Analisi completata su {len(offerte)} prodotti trovati")

                for m in st.session_state.get("messaggi_chat", []):
                    role = "assistant" if m.get("role") == "assistant" else "user"
                    with st.chat_message(role):
                        st.write(m.get("content", ""))

                user_prompt = st.chat_input(
                    "Fai una domanda sui prodotti trovati...",
                    disabled=not st.session_state.get("chat_attiva", False),
                )
                if user_prompt and st.session_state.get("chat_attiva", False):
                    st.session_state["messaggi_chat"].append({"role": "user", "content": user_prompt})
                    with st.spinner("🤖 L'AI sta analizzando..."):
                        try:
                            risposta_ai = _call_groq_chat(
                                st.session_state["messaggi_chat"],
                                groq_api_key,
                                contesto_iniziale=st.session_state.get("contesto_chat", ""),
                            )
                            if not risposta_ai:
                                raise RuntimeError("Risposta vuota dal modello. Controlla i limiti su console.groq.com")
                            st.session_state["messaggi_chat"].append({"role": "assistant", "content": risposta_ai})
                            st.rerun()
                        except Exception as exc:
                            msg = str(exc)
                            if "RateLimit" in msg or "429" in msg:
                                st.error(f"❌ Errore AI: {msg}. Riprova tra qualche secondo.")
                            else:
                                st.error(f"❌ Errore AI: {msg}. Riprova tra qualche secondo.")
                                st.info("Se il modello non risponde, controlla limiti e quota su console.groq.com")

# ===========================================================================
# FOOTER
# ===========================================================================
st.divider()
st.caption(
    "🛒 **Offerte Tech Italia** · Scraper etico con delay tra le richieste · "
    "I prezzi sono indicativi e possono variare · "
    "Verifica sempre il prezzo definitivo sul sito del venditore."
)

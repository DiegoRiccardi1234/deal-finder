# Project Guidelines

## Code Style
- Usa Python con type hints chiari sulle funzioni pubbliche e sui return types.
- Mantieni funzioni piccole e focalizzate (parsing prezzi, tokenizzazione, scraping per fonte, output).
- Preferisci `snake_case` per funzioni/variabili e `PascalCase` per classi (`Offerta`).
- Aggiungi docstring concise per funzioni pubbliche e sezioni commentate solo quando aiutano la manutenzione.
- Per output user-facing mantieni messaggi chiari in italiano; emoji consentite nei messaggi terminal/UI.

## Architecture
- Il progetto ha due entrypoint:
  - `offerte_tech.py`: libreria + CLI (`cerca_offerte` e parser argparse).
  - `app.py`: UI Streamlit che wrappa `cerca_offerte`.
- `cerca_offerte(query, budget_max, top_n, export_csv, csv_filename, condizione, fonti, prezzo_min, categoria, app_id, cert_id)` e` il punto centrale del flusso:
  tokenizzazione query -> scraping (tutte le fonti in parallelo) -> filtro rilevanza/budget -> deduplica -> ordinamento.
- Ogni fonte di scraping va implementata in funzione separata (`scrape_<fonte>`), con parsing e gestione errori isolati.
- `ThreadPoolExecutor(max_workers=7)` gestisce le 7 fonti in parallelo.

## Build and Test
- Ambiente consigliato:
  - `python -m venv .venv`
  - `.venv\Scripts\Activate.ps1`
- Dipendenze minime:
  - `pip install requests beautifulsoup4 fake-useragent streamlit cerebras-cloud-sdk`
- Avvio UI:
  - `streamlit run app.py`
- Avvio CLI scraper:
  - `python offerte_tech.py -q "notebook 14 pollici 16gb" -b 800 -n 10`
- Test unitari (senza Playwright):
  - `python -m pytest tests/test_suite.py -k "not page and not chat_prericerca and not no_ripetizioni and not range_prezzo and not avvia_ricerca and not chat_finale" -v`
- Controllo statico rapido dopo modifiche:
  - usa i problemi editor/Pylance e assicurati che `get_errors` non riporti errori.

## Scraping Conventions
- Mantieni anti-bot sempre attivo:
  - User-Agent realistico (fake-useragent con fallback statico)
  - delay random tra richieste
  - retry automatico con backoff esponenziale su timeout/connessione/HTTP 429/503
- Gestisci sempre i casi:
  - timeout, connessione assente, HTTP error, selettori HTML cambiati, pagina CAPTCHA/robot check
- Se i selettori CSS cambiano, aggiorna solo la funzione della fonte interessata senza impattare le altre.
- **Fonti supportate**: `trovaprezzi.it` (via Google Shopping), `amazon.it`, `ebay.it`, `vinted.it`, `euronics.it`, `unieuro.it`, `mediaworld.it`.
- Strategia multi-parsing per Euronics/Unieuro/MediaWorld: 1) CSS selettori card, 2) JSON-LD `<script type="application/ld+json">`, 3) `__NEXT_DATA__` per siti Next.js (MediaWorld).
- `cerca_offerte` deve rispettare il filtro `fonti` (selezione esplicita delle fonti) e `condizione`.
- eBay: usa la Browse API ufficiale con OAuth2 client credentials (`EBAY_APP_ID`, `EBAY_CERT_ID`) e marketplace `EBAY_IT`.
- Se le credenziali eBay mancano, la fonte va saltata con log chiaro ma senza interrompere la ricerca.
- Amazon su ambienti cloud può restituire HTTP 503 per blocco anti-bot: mostra un messaggio informativo Streamlit senza alterare la logica di retry.

## Runtime Validation Notes (2026-04-05)
- Per validare eBay in ambiente reale (Render/Streamlit), verifica sempre il flusso Browse API (`scrape_ebay` con `EBAY_APP_ID` + `EBAY_CERT_ID`) dai log di `cerca_offerte`.
- `tests/probe_scrapers.py` usa eBay HTML fallback (`_scrape_ebay_html`) e non è adatto a certificare lo stato della Browse API.
- Se eBay risulta 0 nel probe HTML ma funziona in app con chiavi configurate, considera valido il risultato dell'app.
- AliExpress con Playwright può essere query/IP dipendente: stessa sessione può alternare CAPTCHA e risultati validi. Prima di dichiararlo "non utilizzabile", testare almeno 2 query diverse.

## AI / Cerebras Conventions
- Selezione modello dinamica via `cerebras_model.py` → `get_best_model(client)` interroga `/v1/models` e sceglie il modello con context_window più grande (con cache in-memory). Fallback statico: `llama-3.3-70b`.
- Costante centrale in app.py: `CEREBRAS_MODEL = "llama-3.3-70b"` (fallback); a runtime si usa `_get_best_model(client)`.
- In offerte_tech.py tutti i `client.chat.completions.create(model=...)` usano `_cerebras_model(client)` (helper locale che chiama `get_best_model`).
- `_get_cerebras_client()` deve avere try/except per gestire errori di inizializzazione del client.
- La chat pre-ricerca (`_run_presearch_step`) raccoglie: tipo prodotto, uso principale, budget, specifiche hardware. Obiettivo: raccogliere abbastanza info per la raccomandazione finale, non solo la query di ricerca.
- `filtri_ai` (RAM, storage, display…) viene separato dalla query di ricerca per un matching più preciso.
- Al termine di ogni ricerca l'AI genera automaticamente una top-3 raccomandazione (`_call_final_recommendation`).

## Data Conventions
- Il prezzo va normalizzato a `float` (supporto formato italiano `1.299,00` e varianti).
- `parse_price()` gestisce tutti questi formati reali: `"€ 899,00"`, `"A partire da € 1.299,00"`, `"€899,00 con coupon"`, `"EUR 899,00"`, `"1.299,00 €"`, range `"100,00 - 200,00"` (prende il minimo). Restituisce `None` se non parsabile.
- L'oggetto dati standard e` `Offerta(nome, prezzo, negozio, link, fonte)`.
- Ordina sempre i risultati per prezzo crescente prima dell'output.
- CSV: usa UTF-8 con BOM (`utf-8-sig`) per compatibilita con Excel in locale italiano.

## Streamlit UI Conventions
- Tema: dark mode di default via `.streamlit/config.toml` (`base = "dark"`). Il CSS in app.py usa `[data-theme="dark"]` per gli override granulari.
- Mantieni lo stato risultati in `st.session_state` per evitare ricerche duplicate su export/download.
- Cattura i `print()` di `cerca_offerte` e mostrali nella UI (expander log), non nel terminale.
- Gestisci stati UX esplicitamente: loading spinner, nessun risultato (`st.warning`), errori (`st.error`).
- La tabella risultati deve avere colonna link cliccabile (`st.column_config.LinkColumn`).

## Common Pitfalls
- Evita type annotation su assegnazioni dinamiche di `st.session_state` (Pylance segnala errore): usa accesso con chiavi `st.session_state["..."]`.
- Con BeautifulSoup, i valori `tag.get(...)` possono non essere `str`: normalizza con `str(... or "")` prima di usare metodi stringa.
- Quando usi `soup.title.string`, considera che puo essere `None`: fallback a stringa vuota prima di `.lower()`.
- Non introdurre dipendenze extra non richieste senza reale necessita.

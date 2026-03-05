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
- `cerca_offerte(query, budget_max, top_n, export_csv, csv_filename, condizione, fonti)` e` il punto centrale del flusso:
  tokenizzazione query -> scraping trovaprezzi + amazon + ebay + vinted -> filtro rilevanza/budget -> deduplica -> ordinamento.
- Ogni fonte di scraping va implementata in funzione separata (`scrape_<fonte>`), con parsing e gestione errori isolati.

## Build and Test
- Ambiente consigliato:
  - `python -m venv .venv`
  - `.venv\Scripts\Activate.ps1`
- Dipendenze minime:
  - `pip install requests beautifulsoup4 fake-useragent streamlit`
- Avvio UI:
  - `streamlit run app.py`
- Avvio CLI scraper:
  - `python offerte_tech.py -q "notebook 14 pollici 16gb" -b 800 -n 10`
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
- Fonti supportate: `trovaprezzi.it`, `amazon.it`, `ebay.it`, `vinted.it`.
- `cerca_offerte` deve rispettare il filtro `fonti` (selezione esplicita delle fonti) e `condizione`.
- eBay: scraping diretto HTML della pagina di ricerca (`/sch/i.html`), **non** RSS (rss.ebay.it non esiste). Selettori `li[data-viewport]` con classi `s-card__*`.

## Data Conventions
- Il prezzo va normalizzato a `float` (supporto formato italiano `1.299,00` e varianti).
- `parse_price()` gestisce tutti questi formati reali: `"€ 899,00"`, `"A partire da € 1.299,00"`, `"€899,00 con coupon"`, `"EUR 899,00"`, `"1.299,00 €"`, range `"100,00 - 200,00"` (prende il minimo). Restituisce `None` se non parsabile.
- L'oggetto dati standard e` `Offerta(nome, prezzo, negozio, link, fonte)`.
- Ordina sempre i risultati per prezzo crescente prima dell'output.
- CSV: usa UTF-8 con BOM (`utf-8-sig`) per compatibilita con Excel in locale italiano.

## Streamlit UI Conventions
- Mantieni lo stato risultati in `st.session_state` per evitare ricerche duplicate su export/download.
- Cattura i `print()` di `cerca_offerte` e mostrali nella UI (expander log), non nel terminale.
- Gestisci stati UX esplicitamente: loading spinner, nessun risultato (`st.warning`), errori (`st.error`).
- La tabella risultati deve avere colonna link cliccabile (`st.column_config.LinkColumn`).

## Common Pitfalls
- Evita type annotation su assegnazioni dinamiche di `st.session_state` (Pylance segnala errore): usa accesso con chiavi `st.session_state["..."]`.
- Con BeautifulSoup, i valori `tag.get(...)` possono non essere `str`: normalizza con `str(... or "")` prima di usare metodi stringa.
- Quando usi `soup.title.string`, considera che puo essere `None`: fallback a stringa vuota prima di `.lower()`.
- Non introdurre dipendenze extra non richieste senza reale necessita.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Inizio sessione obbligatorio
Usa il tool ctx_index sulla directory del progetto come prima operazione di ogni sessione.

## Context Management — OBBLIGATORIO
- MAI usare Read su file > 30 righe — usa ctx_search con keyword specifiche
- Usa ctx_search per trovare la sezione esatta prima di leggere qualsiasi file
- Read è permesso SOLO per file < 30 righe

## Regole per subagent (Agent())
- NON scrivere "leggi prima il file" nei prompt dei subagent
- NON istruire i subagent su come leggere i file — ci pensa context-mode
- Nei prompt Agent(), descrivi SOLO cosa fare, non come farlo
- Il subagent ha accesso a ctx_search e ctx_batch_execute automaticamente
- MAI lanciare più di 2 Agent() in parallelo. Cerca di usarli il meno possibile o in occasioni necessarie
- Implementa le fasi del piano in sequenza, non tutte insieme

## Project Overview

**Trova Prezzi Mio** (aka "Offerte Tech Italia") is a Python price-comparison tool that scrapes tech product deals from multiple Italian e-commerce sites (trovaprezzi.it, amazon.it, ebay.it, vinted.it, euronics.it, unieuro.it, mediaworld.it) and surfaces results via a Streamlit web UI with AI-powered chat recommendations.

## Commands

**Setup:**
```bash
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
pip install -r requirements.txt
pip install -r requirements-test.txt  # for testing
```

**Run UI:**
```bash
streamlit run app.py
```

**Run CLI:**
```bash
python offerte_tech.py -q "notebook 14 pollici 16gb" -b 800 --condizione nuovo -n 5
```

**Run all tests:**
```bash
pytest
```

**Run a single test:**
```bash
pytest tests/test_suite.py::test_scrape_amazon_retry_second_attempt_with_open_session
```

**Run tests excluding Playwright (UI tests require Streamlit server):**
```bash
pytest tests/test_suite.py -k "not playwright"
```

## Architecture

Il progetto è organizzato in due package principali + script top-level + test suite:

### `offerte/` — Core scraping engine (ex `offerte_tech.py`)
- `offerte/models.py` — `Offerta` dataclass (product result)
- `offerte/orchestrator.py` — `cerca_offerte(query, budget, condizione, n)` (ThreadPoolExecutor su tutti gli scraper + dedup + filtri AI)
- `offerte/scrapers/{amazon,ebay,vinted,euronics,unieuro,mediaworld,trovaprezzi,wallapop,comet,expert,subito,aliexpress,temu,alibaba}.py` — uno scraper per fonte
- `offerte/scrapers/__init__.py` — registry `SCRAPERS`
- `offerte/scrapers/_base.py` — helper condivisi (eBay token, ecc.)
- `offerte/ai.py` — Cerebras: `parse_search_intent`, `parse_comparison_query`, `detect_category_and_questions`, `fetch_specs_ai`, `filtra_risultati_con_ai`, `get_best_model`, `cerebras_chat_with_retry` (assorbito da ex `cerebras_model.py`)
- `offerte/parsing.py` — `parse_price`, `_extract_*` helpers (gb, ram, storage, inches, clothing, shipping), `tokenize_query`
- `offerte/filters.py` — `is_relevant`, hard spec filters
- `offerte/dedup.py` — `_deduplica` (cross-source dedup)
- `offerte/http.py` — `fetch_with_retry`, `_random_delay`, `get_headers`
- `offerte/_constants.py` — costanti globali (UA, stopwords, alias, brand)
- `offerte/export.py` — `print_results`, `export_to_csv`
- `offerte/cli.py` — `main()` argparse + entry-point CLI
- `offerte_tech.py` — shim sottile che ri-esporta `from offerte import *` per backward-compat

### `ui/` — Streamlit web UI (ex `app.py`)
- `ui/auth.py` — auth gate + persistenza session token (`data/auth_sessions.json`)
- `ui/state.py` — `_init_state`, sync prezzo slider/numbers, `_format_price`
- `ui/sources.py` — status fonti + `_FONTE_LABELS`
- `ui/ai_client.py` — Cerebras client UI-side, mock client per test, `_extract_json_object`
- `ui/presearch.py` — chat di pre-ricerca (raccolta requisiti utente)
- `ui/recommendation.py` — payload prodotti + chat finale "consiglia"
- `ui/test_mode.py` — `_build_mock_results` per `APP_TEST_MODE=1`
- `ui/export.py` — `_offerte_to_copy_text`, `_offerte_to_csv_bytes`, `_specs_from_name`, `_summarize_specs`
- `ui/cards.py` — render card, grid risultati, grid specs
- `ui/comparison.py` — flow comparison "X vs Y" (board + matrix manuale)
- `ui/search.py` — `_run_search` (orchestrazione completa ricerca)
- `app.py` — entry point Streamlit: page config, gate auth, top-level render flow (~620 righe)
- `_shared.py` — `load_css`, `render_nav`, theme toggle (utility cross-pagina)

### `data/` — Dati persistenti (runtime)
- `knowledge_base.json`, `kb_unknown_items.json` — KB prodotti (auto-update via Cerebras ogni 7gg)
- `search_history.json` — storico ricerche utente
- `auth_sessions.json` — token sessione (in `.gitignore`)

### Altri moduli top-level
- `knowledge_base.py` — gestione KB (auto-update background, delega Cerebras a `offerte.ai`)
- `search_history.py` — persistenza storico

### `tests/`
- `test_suite.py` — unit tests con `monkeypatch` su `offerte.scrapers.<fonte>.X` o `offerte.orchestrator.scrape_*`
- `conftest.py` — fixtures: `cerebras_mock`, `streamlit_server` (Playwright E2E)
- `probe_*.py` — script di probing real-network (non automatizzati nella suite)

### `tools/`
- `split_offerte.py`, `split_app.py`, `migrate_test_patches.py` — script one-shot del refactor (riutilizzabili per riproduzione/audit)

## Configuration

API credentials go in `.streamlit/secrets.toml` (not committed):
```toml
CEREBRAS_API_KEY = "..."
EBAY_APP_ID = "..."
EBAY_CERT_ID = "..."
```

All keys are optional — scraping still works without them (eBay Browse API falls back to HTML scraping, AI features are disabled).

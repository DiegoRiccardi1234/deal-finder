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

The project has two main files and a test suite:

### `offerte_tech.py` — Core scraping engine
- Defines the `Offerta` dataclass (product result)
- `cerca_offerte(query, budget, condizione, n)` — main entry point, orchestrates all scrapers concurrently via `ThreadPoolExecutor`
- Per-source scraper functions: `scrape_trovaprezzi`, `scrape_amazon`, `scrape_ebay`, `scrape_vinted`, etc.
- `parse_search_intent(query)` — AI-powered intent parsing (Cerebras) to extract specs (RAM, storage, display size, budget)
- `parse_comparison_query(query)` — detects "X vs Y" comparison queries
- `filtra_risultati_con_ai(results, query)` — AI post-filter to remove irrelevant results
- Hard filters: `filtra_hard(results, intent)` — enforces RAM/storage/display size constraints
- `fetch_with_retry(url, session)` — HTTP wrapper with bot-detection fallback and retry logic
- `_deduplica(results)` — deduplicates across sources by normalized title

### `app.py` — Streamlit web UI
- Imports `Offerta`, `cerca_offerte`, `parse_search_intent`, `parse_comparison_query` from `offerte_tech`
- Two modes: pre-search AI chat (Cerebras `gpt-oss-120b`) and product search results
- Custom dark-mode CSS (accent `#c45c2d`, bg `#0e0e12`) with Manrope/Fraunces fonts
- `APP_TEST_MODE=1` env var disables certain features for Playwright testing

### `tests/`
- `test_suite.py` — unit tests using `monkeypatch` to mock HTTP responses (`_FakeResponse`, `_FakeSession`)
- `conftest.py` — shared fixtures: `cerebras_mock` (mocks AI client), `streamlit_server` (starts app on port 8501 for Playwright E2E tests)

## Configuration

API credentials go in `.streamlit/secrets.toml` (not committed):
```toml
CEREBRAS_API_KEY = "..."
EBAY_APP_ID = "..."
EBAY_CERT_ID = "..."
```

All keys are optional — scraping still works without them (eBay Browse API falls back to HTML scraping, AI features are disabled).

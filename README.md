# 🛒 Trova Prezzi Mio

> AI-assisted price-comparison tool that scrapes Italian e-commerce sites in parallel and recommends the best deal through a conversational interface.

[![CI](https://github.com/DiegoRiccardi1234/trova-prezzi/actions/workflows/ci.yml/badge.svg)](https://github.com/DiegoRiccardi1234/trova-prezzi/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.11-blue)
![Streamlit](https://img.shields.io/badge/UI-Streamlit-FF4B4B)
![License](https://img.shields.io/badge/license-MIT-green)

Describe what you want in plain language — the AI turns it into an optimized query, budget and technical filters, then scrapes up to 14 sources in parallel and ranks the results. A built-in chat recommends which product to buy and why.

![Screenshot](docs/screenshot.png)

---

## ✨ Features

- **Conversational search** — an LLM extracts query, budget and hardware specs from a free-text request, then auto-generates a ranked top-3 recommendation.
- **Multi-provider AI** — swap the LLM backend at runtime: **Cerebras, Groq, OpenAI, OpenRouter, Anthropic (Claude), Google Gemini**. Model is picked dynamically among the available ones.
- **14 scrapers in parallel** — Amazon, eBay, Vinted, Euronics, Unieuro, MediaWorld, Trovaprezzi, Wallapop, Comet, Expert, Subito, AliExpress, Temu, Alibaba — aggregated, de-duplicated and price-ranked via a `ThreadPoolExecutor`.
- **Watchlist** — save products and review them later.
- **Price history + alerts** — tracks the minimum price per query over time and flags new lows / below-threshold deals.
- **Persistent search cache** — disk-backed with TTL, to cut repeated scraping and rate-limits.
- **CSV export**, side-by-side comparison, Amazon price history (CamelCamelCamel).
- **Resilient by design** — retries, randomized delays, rotating user agents, graceful degradation when a key/provider/SDK is missing.

## 🚀 Quickstart

Three ways to run it — pick one.

### A. Hosted demo (Streamlit Community Cloud)
Deploy `app.py` straight from the GitHub repo on [share.streamlit.io](https://share.streamlit.io), then add your keys under **Settings → Secrets** (use [`.streamlit/secrets.toml.example`](.streamlit/secrets.toml.example) as a template). Zero local setup — the link to put on a CV.

### B. Docker (recommended for local real use)
```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then fill in your keys
docker compose up --build
# open http://localhost:8501
```
Keys can also be passed via a `.env` file or shell environment (see `docker-compose.yml`).

### C. Local (no Docker)
```bash
# Windows
run.bat

# macOS / Linux
./run.sh
```
The script creates a virtualenv, installs dependencies and launches the UI on `http://localhost:8501`.

<details>
<summary>Manual setup</summary>

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows: .venv\Scripts\activate.bat
pip install -r requirements.txt
streamlit run app.py
```
</details>

## 🔑 Configuration

Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` and fill in what you need. Everything is optional — scraping works without any key (AI features just stay off).

| Setting | Purpose |
|---|---|
| `AI_PROVIDER` | active LLM provider (default `cerebras`) |
| `APP_PASSWORD` | dashboard login password |
| `EBAY_APP_ID` / `EBAY_CERT_ID` | eBay Browse API (falls back to HTML scraping if absent) |

### AI providers

Set the key of any provider(s) you want; the sidebar lets you switch among the configured ones.

| Provider | Secret | Where to get a key |
|---|---|---|
| Cerebras (default) | `CEREBRAS_API_KEY` | https://cloud.cerebras.ai |
| Groq | `GROQ_API_KEY` | https://console.groq.com |
| OpenAI | `OPENAI_API_KEY` | https://platform.openai.com |
| OpenRouter | `OPENROUTER_API_KEY` | https://openrouter.ai |
| Anthropic (Claude) | `ANTHROPIC_API_KEY` | https://console.anthropic.com |
| Google Gemini | `GEMINI_API_KEY` | https://aistudio.google.com |

## 🧱 Architecture

```
offerte/      core engine — scrapers, orchestrator, AI, parsing, dedup, providers, cache, config
ui/           Streamlit UI — auth, search flow, cards, comparison, recommendation
app.py        entry point (page config, auth gate, render flow)
watchlist.py, price_history.py, search_history.py, knowledge_base.py   persistence
tests/        unit suite (monkeypatched) + Playwright E2E
```

The LLM backend is abstracted in [`offerte/providers.py`](offerte/providers.py): OpenAI-compatible providers share one client, while Anthropic and Gemini are wrapped in thin adapters exposing the same `chat.completions.create` / `models.list` shape — so the rest of the codebase is provider-agnostic. See [`CLAUDE.md`](CLAUDE.md) for the full module map.

## 🖥️ CLI

```bash
python offerte_tech.py -q "notebook 14 pollici 16gb" -b 800 --condizione nuovo -n 5
python offerte_tech.py -q "ssd 1tb" --export csv --output offerte.csv
```
`-q` query · `-b` budget · `-n` max results · `--condizione` (tutti/nuovo/usato) · `--fonti` source list · `--export csv`.

## ✅ Tests & CI

```bash
pytest tests/ -k "not playwright"     # fast offline unit suite
pytest tests/                          # full suite incl. Playwright E2E
```
GitHub Actions runs the offline suite on every push and pull request (`.github/workflows/ci.yml`).

## 🛠️ Tech stack

Python 3.11 · Streamlit · BeautifulSoup / requests · Playwright · ThreadPoolExecutor · Cerebras / OpenAI / Anthropic / Google SDKs · pytest.

## 📄 License

MIT — see [`LICENSE`](LICENSE).

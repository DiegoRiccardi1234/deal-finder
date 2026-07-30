# Architecture

Deal Finder is a Python price-comparison tool: it scrapes Italian e-commerce
sites in parallel, aggregates and de-duplicates the results, and layers an
LLM-driven conversational flow on top. Two packages carry the weight —
`offerte/` (engine) and `ui/` (Streamlit) — plus a handful of top-level feature
modules.

## Request flow

```
user free-text request
  └─ ui/presearch.py        LLM asks category-aware clarifying questions
       └─ offerte/ai.py     parse_search_intent → query + budget + hard specs
            └─ offerte/orchestrator.py   cerca_offerte()
                 ├─ ThreadPoolExecutor over offerte/scrapers/*     (I/O bound)
                 ├─ offerte/filters.py   is_relevant + hard spec filters
                 ├─ offerte/dedup.py     cross-source de-duplication
                 └─ offerte/ai.py        filtra_risultati_con_ai
                      └─ ui/cards.py     render + ui/recommendation.py chat
```

Results are cached on disk with a TTL (`offerte/cache.py`) so repeated searches
do not re-scrape and do not burn through rate limits.

## `offerte/` — core engine

| Module | Responsibility |
|---|---|
| `models.py` | `Offerta` dataclass — one product result |
| `orchestrator.py` | `cerca_offerte(query, budget, condizione, n)`: fan-out over every scraper, then dedup + filters |
| `scrapers/*.py` | One module per source; `scrapers/__init__.py` holds the `SCRAPERS` registry |
| `scrapers/_base.py` | Shared helpers (eBay OAuth token, endpoint constants) |
| `ai.py` | Prompting and parsing: `parse_search_intent`, `parse_comparison_query`, `detect_category_and_questions`, `fetch_specs_ai`, `filtra_risultati_con_ai`, `get_best_model` |
| `providers.py` | Multi-provider abstraction — see below |
| `parsing.py` | `parse_price` plus spec extractors (RAM, storage, inches, clothing sizes, shipping) and `tokenize_query` |
| `filters.py` | `is_relevant` and the hard spec filters |
| `dedup.py` | `_deduplica` — collapses the same product across sources, keeping the cheapest |
| `http.py` | `fetch_with_retry`, `get_headers`, `_random_delay` |
| `cache.py` | Disk-backed search cache with TTL (`data/search_cache.json`) |
| `config.py` | Tunable constants. Holds `VERSION`, the single source of truth also read by `pyproject.toml` |
| `export.py` | `print_results`, `export_to_csv` |
| `cli.py` | `argparse` entry point — installed as the `deal-finder` command |

`offerte_tech.py` at the repo root is a thin shim that re-exports `offerte` for
backwards compatibility with earlier import paths.

### Provider abstraction

`offerte/providers.py` is the seam that makes the engine LLM-agnostic. The
`PROVIDERS` registry maps a provider name to how its client is built:

- **OpenAI-compatible** (Cerebras, Groq, OpenAI, OpenRouter) share a single
  client implementation and differ only by base URL and key.
- **Anthropic** and **Google Gemini** are wrapped in thin adapters that expose
  the same call surface.

The active provider comes from the `AI_PROVIDER` env var (or `--provider` on the
CLI, or the sidebar selector). SDKs are imported lazily, so a provider you do
not use costs nothing and needs no dependency installed.

**No model is hardcoded.** `offerte.ai.get_best_model()` queries the provider's
model list at runtime and picks the best available by context window, minus a
blacklist. The candidates in `config.CEREBRAS_FALLBACK_MODELS` are used *only*
when that list cannot be fetched (no key, no network). A regression test asserts
that no model literal creeps back into the code.

## `ui/` — Streamlit interface

| Module | Responsibility |
|---|---|
| `auth.py` | Password gate + session-token persistence (`data/auth_sessions.json`) |
| `state.py` | Session-state init, price slider/number sync, formatting |
| `sources.py` | Per-source status display |
| `ai_client.py` | UI-side LLM client, deterministic mock client for tests |
| `presearch.py` | Pre-search chat that collects requirements before scraping |
| `recommendation.py` | Product payload + the final "recommend one" chat |
| `comparison.py` | "X vs Y" comparison board and matrix |
| `search.py` | `_run_search` — orchestrates a full search round-trip |
| `cards.py` | Result cards, results grid, specs grid |
| `export.py` | Copy-to-clipboard text, CSV bytes, spec summarising |
| `test_mode.py` | Mock results when `APP_TEST_MODE=1` |

`app.py` is the Streamlit entry point: page config, auth gate, and the
top-level render flow. `_shared.py` holds cross-page utilities (`load_css`,
navigation, theme toggle).

### Theming

`styles.css` is a single design system driven by CSS variables: exactly one
`:root` block (light) and one `[data-theme="dark"]` block that overrides the
same variables. Component rules never hardcode colours — they read `var(...)`,
so dark mode follows automatically. `_shared.load_css(theme)` swaps which block
is active. Adding a second `:root` or a literal colour breaks dark mode.

## Feature & persistence modules

| Module | Storage |
|---|---|
| `knowledge_base.py` | `data/knowledge_base.json` — product KB, refreshed in a background thread |
| `search_history.py` | `data/search_history.json` |
| `watchlist.py` | `data/watchlist.json` — favourites, de-duplicated by link |
| `price_history.py` | `data/price_history.json` — per-query minimum price, `is_new_low`, `below_threshold` |
| `updater.py` | Checks GitHub releases and self-updates local installs |

## Tests

- `tests/test_suite.py` — unit tests plus the Playwright E2E group. Scrapers are
  monkeypatched at `offerte.scrapers.<source>.<symbol>`.
- `tests/test_providers.py` — provider abstraction, including the
  no-hardcoded-model guard.
- `tests/test_features.py` — cache, price history, watchlist (all on `tmp_path`).
- `tests/test_updater.py` — release check and update path.
- `tests/conftest.py` — fixtures: the LLM mock, and a real Streamlit server for
  the E2E group. A marker is applied automatically based on the `page` fixture,
  so `-k "not playwright"` reliably excludes the browser tests.
- `tests/probe_*.py` — real-network diagnostics, deliberately **not** collected
  by pytest. `probe_scrapers.py` is the source-health probe behind the README's
  "last verified" table.

No test touches the network: every HTTP call is monkeypatched. That keeps CI
deterministic, and it is also why source breakage is caught by the probe rather
than by the suite.

## Configuration

Local runs read `.streamlit/secrets.toml` (template:
`.streamlit/secrets.toml.example`). Docker reads environment variables from
`.env` (template: `.env.example`) — `.streamlit/secrets.toml` is excluded from
the Docker build context, so the container cannot see it.

Every key is optional. Without any key, scraping still works and the AI
features stay off; eBay falls back from the Browse API to HTML scraping.

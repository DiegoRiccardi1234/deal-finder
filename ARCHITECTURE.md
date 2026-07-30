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
| `cache.py` | Search cache with TTL, persisted in SQLite |
| `db.py` | Shared SQLite connection, WAL, writer lock — see [Persistence](#persistence) |
| `migrations.py` | Schema versions plus the one-time import of legacy JSON stores |
| `log.py` | `configure_logging()` / `get_logger()` — see [Observability](#observability) |
| `source_status.py` | Per-search outcome of every source — see [Observability](#observability) |
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

## Observability

Two modules exist because a scraper returning an empty list is ambiguous, and
that ambiguity reached the user as a bare "0 results".

**`offerte/log.py`** — `configure_logging()` (called once by `app.py` and
`offerte/cli.py`, idempotent because Streamlit re-runs the script on every
interaction) plus `get_logger(__name__)` per module. Output goes to stderr, so
stdout stays clean for CLI results and `--export csv`; `LOG_LEVEL` tunes it.
Chatty third-party loggers are pinned to WARNING.

**`offerte/source_status.py`** — a lock-protected registry, reset at the start of
each search, recording one of five outcomes per source: `ok`, `empty` (responded,
nothing matched), `blocked` (403/429/503, CAPTCHA, JS challenge), `error`
(exception or search timeout), `disabled` (deliberately off). Scrapers report the
blocked/disabled cases, since only they see the HTTP status; the orchestrator's
wrapper fills in ok/empty/error from the result. A state that *explains* the
absence of results is never downgraded to `empty` — otherwise the `return []`
that follows a 403 would erase the reason.

It is a side registry rather than a return value because the 14 scrapers have six
different signatures across as many orchestrator branches; changing every return
type for an accessory piece of information would be a far more invasive refactor.
`ui/sources.py` renders it. It used to *infer* blockage by grepping captured log
text for strings like `f"{key} -> errore"` — a format nothing ever printed, so
the "blocked" state was unreachable.

### The sources canary

The test suite mocks every HTTP call, which is right — otherwise CI would be
flaky — but it means the suite can never notice a site changing its markup. That
gap is not theoretical: two sources sat at zero for weeks while CI stayed green.

`.github/workflows/sources-canary.yml` is the only thing that touches the real
network. It runs weekly, outside `ci.yml` so push/PR stays deterministic, and
reports through a GitHub issue rather than a red job — a job that goes red every
time a site throttles stops meaning anything within a month.

It compares against a versioned baseline, `tests/sources_baseline.json`, rather
than asserting that everything answers, because **several sources behave
differently from a CI runner than from a residential connection**: Amazon and
MediaWorld return 403 to cloud IPs by design, Expert comes back empty (likely
geo-restricted), and eBay without repo secrets falls back to HTML scraping and
gets 403. Only regressions are reported — a source expected `ok` that stops
answering — and `expected: "any"` silences the ones known to be intermittent.
One live issue, commented rather than duplicated, closed automatically on
recovery.

Reproduce it locally with:

```bash
python tests/probe_scrapers.py --query "iphone 15" --budget 900 --check-baseline
```

Expect deviations locally: the baseline describes the *runner*, and from an
Italian machine Amazon, MediaWorld and Expert all work. That asymmetry is the
reason the file exists.

Bounding the work: `AI_REQUEST_TIMEOUT` (default 60s) is applied when the
provider client is constructed, together with `max_retries=0` so the SDK's own
retries don't multiply with ours. `SEARCH_TOTAL_TIMEOUT` (default 90s) caps a
whole search; past it the orchestrator returns partial results and shuts the
executor down with `wait=False`, because `with ThreadPoolExecutor(...)` joins its
threads on exit and a hung source would otherwise still block the caller for its
full request timeout.

## Persistence

Everything mutable lives in one SQLite database, `data/deal_finder.db`, opened
through `offerte/db.py`. The connection uses `check_same_thread=False` because
Streamlit serves reruns from different threads and the knowledge-base updater runs
in a daemon thread; a `threading.RLock` serialises writes, and WAL journalling
lets readers proceed during a write. `offerte/migrations.py` owns the schema,
versioned via `PRAGMA user_version`.

This replaced six independent JSON files. The reason was not tidiness: none of
those writes was atomic (`open(w)` + `json.dump`, so a crash mid-write left
truncated JSON), the cache did read-modify-write with no lock — a benchmark of 24
concurrent writers lost 22 of them — and every reader swallowed
`JSONDecodeError` and returned an empty container, so a corrupted file looked
like "no data" and the next write destroyed the history for good. On first start
the legacy files are imported automatically and renamed `*.json.migrated`, so
upgrading does not lose a watchlist or a price history.

| Module | Table |
|---|---|
| `offerte/cache.py` | `search_cache` — keyed by the hash from `make_cache_key`, with TTL and `purge_expired` |
| `price_history.py` | `price_history` — append-only; `is_new_low`, `lowest_ever`, `below_threshold` |
| `watchlist.py` | `watchlist` — favourites; the link is the primary key, so de-duplication is a constraint |
| `search_history.py` | `search_history` — normalised query as primary key, capped at `MAX_ENTRIES` |
| `ui/auth.py` | `auth_sessions` — session fingerprint to expiry |
| `knowledge_base.py` | `kb_state`, `kb_unknown_items` — product KB refreshed in a background thread |

`data/knowledge_base.json` is still tracked in git, but only as a **read-only
seed** used when `kb_state` is empty. It used to be rewritten by the updater,
which meant every launch of the UI left the working tree dirty.

`updater.py` sits outside this: it checks GitHub releases and self-updates local
installs.

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
the Docker build context, so the container cannot see it. `docker-compose.yml`
mounts `./data`, so the SQLite database survives container restarts.

Every key is optional. Without any key, scraping still works and the AI
features stay off; eBay falls back from the Browse API to HTML scraping.

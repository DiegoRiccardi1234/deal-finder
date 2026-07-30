# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [3.0.0] - 2026-07-30

Major because two things break compatibility: the distributed package is renamed
from `trova-prezzi-mio` to `deal-finder`, and all persistent state moves from six
JSON files to a SQLite database. The migration is automatic and non-destructive —
existing watchlists and price histories are imported on first start and the old
files kept as `*.json.migrated` — but the on-disk format is no longer the same.

### Fixed
- **The release ZIP shipped the internal notes and 8 MB of cache.** The builder
  walked the filesystem instead of asking git, so it packaged everything the
  repository ignores: `.mypy_cache/` and — the part that matters —
  `HANDOFF.md`, `APPUNTI.md` and the `CLAUDE.md` files, the working notes
  deliberately untracked earlier in this release. They would have gone out in a
  public artifact. It now builds from `git ls-files`, so the archive contains by
  construction exactly what is versioned. Same change removes a crash on the
  Windows reserved device `nul`, which sits ignored in the repo root and made
  `os.path.relpath` abort mid-walk, silently truncating the archive to 51 KB.
  Added a floor check that refuses to produce an archive with too few files.
  Result: 182 KB and 75 files, verified to contain no secret, no internal note
  and every file needed to run.
- **Accessories in other languages slipped through the relevance filter.** Vinted
  and Wallapop are pan-European and listings arrive in the seller's language, so
  the Italian/English word list caught nothing: a search for `iphone 15` returned
  twelve €1.00 phone cases in a row — `coque` (FR), `Funda`/`Carcasa` (ES),
  `Handyhülle` (DE), `hoesje` (NL), `Capa` (PT). Added those languages, plus
  substring stems for German, where nouns compound (`Handyhülle`, `Hüllen`) and
  whole-word matching misses every form. Measured end to end: the top of the
  results went from twelve foreign-language cases to four actual iPhones out of
  six. The remaining two are documented limits — a `Display` spare part
  (deliberately not filtered, since displays are also products) and a €1.00 bait
  listing whose name genuinely is the product.

### Changed
- CI no longer pins `ruff`, `mypy` and `pip-audit` twice. The versions were
  written both in `requirements-test.txt` and by hand in the workflow, and
  Dependabot only updates the file — so after the first dependency PR the two
  would have drifted apart silently, and `ruff format` changes output between
  versions, meaning "formatted locally" and "CI happy" could stop agreeing. The
  jobs now read the version from the requirements file while still installing
  only the single tool.

### Added
- **Weekly sources canary** (`.github/workflows/sources-canary.yml`). The scrapers
  break on their own when sites change, and the test suite cannot notice: it mocks
  the whole network — correctly, or CI would be flaky. This is the only place that
  touches the real network, and it lives outside `ci.yml` so push/PR stays
  deterministic.

  It compares against a versioned baseline (`tests/sources_baseline.json`) rather
  than asserting "everything answers", because two sources *cannot* work from a CI
  runner: Amazon rejects cloud IPs with 503, and eBay without repo secrets falls
  back to HTML scraping and gets 403. A naive canary would cry wolf every week and
  be ignored within a month. Only regressions are reported — a source expected
  `ok` that stops answering — and `expected: "any"` silences the ones known to be
  intermittent. One live issue, updated rather than duplicated, closed
  automatically when things recover.
- `tests/probe_scrapers.py` gained `--json` and `--check-baseline`. It previously
  printed text and **always exited 0**, so nothing could act on its result. It now
  reports per-source state by reusing `offerte/source_status.py` instead of
  inventing a second vocabulary, and reads its optional keys from the environment
  as well as `.streamlit/secrets.toml` — the latter does not exist on a runner.
- CI: `pip-audit` step (it was pinned in `requirements-test.txt` but never
  invoked), a **Python 3.11 / 3.12 / 3.13 matrix** on the test job — `pyproject`
  declares `>=3.11` while only 3.11 was verified — a separate **non-blocking E2E
  job** that installs Chromium and runs the 7 Playwright tests, and coverage
  reporting.
- `.github/dependabot.yml` — weekly `pip` and `github-actions` updates, grouped so
  maintenance lands in one reviewable PR.

### Changed
- **Docker is documented but no longer recommended.** The image has never been
  built and is not exercised in CI; the tested path is `run.bat` / `run.sh`. The
  instructions themselves were fixed earlier in this release and verified with
  `docker compose config`, but a verified *config* is not a verified *build*, and
  the repo should not recommend what it does not check.
- Coverage is measured on `offerte/` only. Including `ui/` would report ~0% for
  it, which reads as "untested" and is wrong: the tests that exercise the UI are
  the E2E ones, and they launch Streamlit as a **subprocess** that `coverage`
  cannot instrument. Measured at the time of writing: `offerte/` 58%, with
  `db.py` 97%, `filters.py` 96%, `source_status.py` 94%; the scrapers pull the
  average down. No badge until the number means something.
- **Structured logging replaces `print`** across the engine (`offerte/log.py`,
  `get_logger(__name__)` per module, configured once by `app.py` and
  `offerte/cli.py`). Output goes to stderr so stdout stays clean for CLI results
  and `--export csv`; `LOG_LEVEL` tunes verbosity.
- **A source's outcome is now recorded, not guessed** (`offerte/source_status.py`).
  Every search reports one of `ok` / `empty` / `blocked` / `error` / `disabled`
  per source, and `ui/sources.py` renders it. Previously an empty result list
  meant three different things and the user saw a bare "0 results" whether Amazon
  had throttled us, the markup had changed, or nothing simply matched.
- Provider clients are built with an explicit `AI_REQUEST_TIMEOUT` (60s default)
  and `max_retries=0`. The timeout was missing entirely — the knowledge-base
  updater runs in a daemon thread and could hang forever — and leaving the SDK's
  own retries on multiplied them with ours: 4 × 2 = 8 calls per request.
- A whole search is now capped by `SEARCH_TOTAL_TIMEOUT` (90s default), returning
  partial results instead of nothing.

### Fixed
- **Accessories dominated the results for a device search.** Results are sorted
  by ascending price and an accessory costs a fraction of the device, so a search
  for `iphone 15` returned a €7.55 camera-lens cover, a screen protector and a
  protection kit as its top hits — all of which carry the model name and so passed
  the relevance filter. `looks_like_accessory` now drops covers, screen
  protectors, cables, chargers, accessory-only brands (Cellularline, SBS,
  OtterBox, …) and second-hand spare parts, unless the query itself asks for one:
  `custodia iphone 15` still returns cases. `display` and `batteria` are
  deliberately excluded from the list — they are also products in their own right,
  and filtering them would break a monitor or power-bank search.
- **Trovaprezzi was never called by the application.** `scrape_trovaprezzi` was
  neither imported nor submitted by `offerte/orchestrator.py` and was absent from
  the default source set, so the source the project is named after was dead code
  as far as the app was concerned — only the probe script exercised it. Now wired
  in, and it contributes ~40 offers per search.
- **A query made only of spec tokens returned zero results from every source.**
  With `strict_specs=False`, `is_relevant` skipped every technical token; for a
  two-token query like `ssd 1tb` that left the OR branch with nothing to check, so
  it fell through to `return False` and rejected 100% of products — the sources
  had found dozens. With three or more such tokens the AND branch did the
  opposite, accepting anything by vacuous truth, a blender included. Spec tokens
  are now evaluated when they are all there is. Measured: `deal-finder -q "ssd 1tb"
  -b 120` went from 0 results to 5.
- **The search timeout did not bound the perceived time.** Adding
  `as_completed(timeout=…)` stopped the *waiting* but `with ThreadPoolExecutor(...)`
  joins its threads on exit, so a hung source still blocked the caller for its
  full request timeout: measured 60s against a 4s budget. The executor is now shut
  down with `wait=False, cancel_futures=True`, and a source that misses the
  deadline is recorded as an error rather than left looking empty.
- Errors from AI providers are classified before retrying (`classify_ai_error`),
  preferring the SDK's `status_code` over substring matching — a prompt containing
  "429" is not a rate limit. Non-retryable failures (401/403 auth, 400/422 bad
  request) now fail fast: measured, an invalid API key previously cost 4 calls and
  6 seconds of fixed sleeps before surfacing unchanged. Retries use exponential
  backoff with jitter, and a 404 renegotiates the model without consuming an
  attempt (bounded, so a resolver stuck on a dead model cannot loop).

### Changed — persistence, packaging and rebrand
- **All mutable state moved from six JSON files to one SQLite database**
  (`data/deal_finder.db`, via the new `offerte/db.py` and `offerte/migrations.py`),
  following the same approach as the sibling `job-finder` project: WAL journalling,
  `check_same_thread=False` with an `RLock` serialising writes, schema versioned
  through `PRAGMA user_version`.

  This fixes a class of defects rather than patching them:
  - **No write was atomic.** Every store did `open(w)` + `json.dump`, so a crash
    mid-write left truncated JSON.
  - **The cache lost concurrent writes.** `read → mutate → write` of the whole
    file with no lock: a benchmark with 24 concurrent writers kept 2 of them.
    Every entry is now a row with an UPSERT.
  - **Corruption was indistinguishable from "empty".** Readers swallowed
    `JSONDecodeError` and returned `{}`/`[]`, so a damaged file looked like no
    data and the next write overwrote the history permanently. An unreadable
    database now raises.
  - **De-duplication was a race.** Watchlist links and search-history queries
    were filtered in Python between a non-atomic read and write; both are now
    primary keys, so the constraint lives in the database.

  Legacy files are imported automatically on first start and renamed
  `*.json.migrated` — upgrading does not lose a watchlist or a price history.
  Added `purge_expired()` for the cache, which previously grew without bound
  because nothing ever deleted stale entries.

- **`data/knowledge_base.json` is now a read-only seed.** The background updater
  used to rewrite it, and since the file is tracked, every launch of the UI left
  the working tree dirty with an `updated_at`/`version` diff. Runtime state lives
  in the `kb_state` table.
- **Renamed the project to "Deal Finder"** (repo `deal-finder`), aligning it with
  the sibling `job-finder`. The scraper for the *website* trovaprezzi.it keeps
  its name — only the product was renamed.
- `pyproject.toml` no longer duplicates the version: it is read from
  `offerte.config.VERSION` via `[tool.setuptools.dynamic]`. Previously three
  independent values (pyproject, `config.py`, README badge) had drifted apart —
  1.1.0 vs 2.0.0 vs 2.0.0. The README badge now reads the latest GitHub release.
- **The package is installable and the CLI is a real entry point.** Added
  `[build-system]` and `[project.scripts]`, so `pip install -e .` exposes
  `deal-finder`. `--help` no longer crashes on a Windows console: the emoji in
  the argparse description caused a `UnicodeEncodeError` under cp1252.
- `README.md` replaces the "14 scrapers" claim with a measured **Sources** table
  (live / throttled / blocked) plus a "last verified" date, reproducible with
  `tests/probe_scrapers.py`.
- Internal session notes and AI-agent instructions (`CLAUDE.md`, `TODO.md`,
  per-directory `CLAUDE.md`, `copilot-instructions.md`) are no longer tracked.
  The public architecture reference is the new
  [`ARCHITECTURE.md`](ARCHITECTURE.md), which the README links instead.

### Fixed — persistence, sources and UI
- `knowledge_base.track_unknown` read outside the lock and wrote inside it, so two
  items recorded concurrently for the same category overwrote each other. It is
  now a single `INSERT OR IGNORE` against a composite primary key.
- `load_kb` merged with the base schema *outside* the lock that guarded the read,
  so the background updater could write in between and the merge started from
  stale data.
- `_update_in_progress` was a module global tested and then set without a lock:
  two closely-spaced Streamlit reruns both passed the check and started two
  updaters. Claiming the flag is now atomic, under a lock deliberately separate
  from the KB I/O lock — reusing that one would deadlock, since `load_kb()` holds
  it while the decision is being made.
- **Trovaprezzi returned zero results.** The site was never blocking us — the
  markup had changed. Search now redirects to a product page whose offers live
  in `li.listing_item`, which works on both page types, unlike the previous
  `a.suggested_product`. The shop reported is now the actual merchant (eBay, Bpm
  power, …) instead of a flat "Trovaprezzi".
- **Trovaprezzi silently dropped JSON-LD results.** In the fallback branch the
  counter was incremented before being initialised, raising `UnboundLocalError`
  on the *first* item; the enclosing `except Exception: continue` swallowed it
  and skipped every remaining item in that script block.
- **Euronics returned zero results.** Cloudflare answers 403 on `/search?q=`
  while the home page stays 200, so the WAF rule targets the search path. The
  scraper now calls the Salesforce Commerce Cloud AJAX endpoint the product grid
  itself uses (`Search-UpdateGrid`).
- **Euronics reported the wrong price and hid discounted products.** The price
  selector picked `span.value` — the *recommended* list price — before
  `span.price-formatted`, the actual sale price. Because the inflated value was
  compared against the user's budget, discounted items were filtered out
  entirely rather than merely mispriced.
- **The Docker quickstart did not work as documented.** It told you to create
  `.streamlit/secrets.toml`, but that path is excluded by `.dockerignore` and
  compose only reads environment variables. Added the missing `.env.example`
  and corrected README and SETUP.txt.
- `tests/probe_scrapers.py` reported eBay as broken: running outside Streamlit it
  never loaded `.streamlit/secrets.toml`, so it only ever exercised the HTML
  fallback (403) instead of the working Browse API. It now loads the secrets and
  covers all 14 sources — four of them (trovaprezzi, wallapop, comet, expert)
  were missing from its list.
- A stray second module docstring in `app.py`, left over from the monolith split,
  was rendered by Streamlit's "magic" as visible body text at the top of every
  page. The heading also claimed "7 siti" next to 9 rendered chips; the count is
  now derived from the source list, and Trovaprezzi was missing from that list
  too.
- The card CTA rendered as a blue underlined link inside the terracotta button:
  Streamlit styles links in markdown containers with `.st-emotion-cache-<hash> a`,
  specificity (0,1,1), which beats `.card-cta` (0,1,0). Fixed by raising
  specificity with our own classes rather than chasing the generated hash, and
  without `!important`.

### Security
- `SECURITY.md` no longer claims "No credentials are ever committed", which was
  false as written. It now distinguishes *your* secrets — never committed — from
  two Algolia **search-only public keys** that Unieuro and Comet publish in their
  own JavaScript bundles, and which the scrapers need. Both are annotated at
  their definition with their provenance.
- Removed dead duplicate copies of those keys from `offerte/scrapers/_base.py`;
  every scraper already defined and used its own.
- The session-fingerprint salt in `ui/auth.py` was the old product slug inline.
  It is now a named constant documented as a salt, so a future rename cannot
  invalidate stored sessions by accident.

### Added
- `offerte/db.py` and `offerte/migrations.py` — shared SQLite layer and schema.
- `ARCHITECTURE.md` — public module map, request flow, provider abstraction,
  persistence model and test layout, in English.
- `.env.example` — template for the Docker path.

## [2.0.0] - 2026-07-05

### Security
- **Purged a leaked Google Maps API key from the entire git history.** It had
  been committed inside the test-output dump `tests/probe_siti_results.json`
  (since 2026-04). History was rewritten with `git-filter-repo`; the file and
  other probe dumps are now gitignored. See [SECURITY.md](SECURITY.md) for the
  current status of that incident.
- Hardened `.gitignore`: added `.env`, `.env.local`, and the regenerable probe
  artifacts (`tests/probe_siti_results.json`, `report_siti.md`).

### Added
- **Adaptive, category-aware pre-search chat.** The assistant now asks the
  right follow-up for the detected category — size for clothing, shoe size,
  main use for laptops, diagonal for TVs — via `detect_category_and_questions`,
  and offers clickable example chips. The brittle regex fallback was dropped
  from the conversation flow.
- **New all-time-low price alert.** Flags "🔻 nuovo minimo storico" when the
  current cheapest result beats the recorded price history for that query.
- **Multi-provider AI in the core.** `offerte/ai.py` and the knowledge-base
  updater now build their client via `offerte.providers` and honor
  `AI_PROVIDER`, so the CLI and the orchestrator's AI filtering work with any
  configured provider (Cerebras, Groq, OpenAI, OpenRouter, Anthropic, Gemini) —
  not just Cerebras. The UI was already multi-provider; the core caught up.
- **`--provider` flag on the CLI** (`offerte_tech.py --provider groq|openai|…`)
  to pick the AI backend without setting an env var.
- Code-quality tooling: **ruff** (lint + formatter, line-length 100), **mypy**
  (advisory / non-strict), and **pip-audit**, configured in `pyproject.toml`
  and pinned in `requirements-test.txt`.
- CI now runs three jobs: `lint` (ruff, blocking), `typecheck`
  (mypy, advisory / non-blocking) and `test` — the test job now runs the
  **full** suite (`test_suite`, `test_features`, `test_providers`,
  `test_updater`) instead of only `test_suite.py`.
- `SECURITY.md` (threat model, secret handling, reporting) and this `CHANGELOG.md`.

### Fixed
- **`offerte/export.py`:** restored a missing `import csv`. The pandas-absent
  fallback in `export_to_csv()` used `csv.DictWriter` / `csv.QUOTE_NONNUMERIC`
  and would raise `NameError` on machines without pandas (caught by ruff F821).
- `offerte/scrapers/mediaworld.py`: bound the loop constant `_RATE_KW` at
  def-time in a nested helper (ruff B023).
- `ui/comparison.py`: `zip(..., strict=True)` on equal-length columns (ruff B905).

### Changed
- **Rebrand: "Trova Prezzi Mio" → "Trova Prezzi".**
- **UI redesign — a single "editorial terracotta" design system.** Collapsed a
  stylesheet made of three competing, `!important`-fighting layers (~2050 lines,
  ~900 of them dead) into one variable-driven theme (~650 lines) with a
  **coherent dark mode** — previously light was terracotta while dark fell back
  to a leftover neon-lime theme.
- Removed fake telemetry ("System health / Uptime 99.9%") and the
  non-functional sidebar captions.
- Renamed the UI AI-client helpers `_get_cerebras_*` → `_get_ai_*`
  (provider-agnostic; historical aliases kept for compatibility).
- README: added a demo GIF plus light/dark/results screenshots. Hardened the
  flaky Playwright chat driver (event-synced + retry-on-reload); the full suite
  (78 tests, incl. E2E) is green.
- Removed ~650 dead "preamble" imports left over from the monolith→package
  split (star-import re-exports and test-monkeypatch targets preserved),
  modernized typing via pyupgrade, and applied `ruff format` across the
  codebase.
- Removed the dead, copy-pasted Cerebras AI preamble (the
  `CEREBRAS_MODEL`/`_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"` hardcoded
  constants — a model Cerebras dismissed — plus unused import guards) from
  ~35 files. The model is resolved dynamically; the only fallback list lives in
  `offerte/config.py`. A guard test asserts no hardcoded model literal remains.
- No behavior change — full offline suite (71 tests) stays green.

## [1.1.0] - 2026-06-12

### Added
- Multi-provider AI abstraction (`offerte/providers.py`): Cerebras, Groq,
  OpenAI, OpenRouter, Anthropic, Google Gemini, with a sidebar selector and
  dynamic best-model resolution.
- In-app auto-update for local installs (`updater.py`): update banner with
  `git pull` + reinstall for git clones, release link for ZIP installs,
  disabled on Streamlit Cloud.
- GitHub Action `release-asset.yml`: attaches a ready-to-run ZIP to every
  published release.

### Changed
- Bilingual README (EN/IT), plain-text `SETUP.txt`, three deployment tiers
  (Docker / launchers / Streamlit Cloud), MIT license.

### Fixed
- Dark-mode contrast and the collapse-arrow chevron glitch.

## [1.0.0] - 2026-06-12

### Added
- Initial release: Streamlit price-comparison UI over 14 Italian e-commerce
  scrapers, AI-powered search-intent parsing and recommendations, persistent
  search cache, watchlist and price history.

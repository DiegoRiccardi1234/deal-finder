# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
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

### Fixed
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
  now derived from the source list, and Trovaprezzi was missing from both that
  list and `ui/sources.py`'s labels.
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

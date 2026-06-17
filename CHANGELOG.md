# Changelog

All notable changes to this project are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- **Purged a leaked Google Maps API key from the entire git history.** It had
  been committed inside the test-output dump `tests/probe_siti_results.json`
  (since 2026-04). History was rewritten with `git-filter-repo`; the file and
  other probe dumps are now gitignored. The exposed key must be rotated.
- Hardened `.gitignore`: added `.env`, `.env.local`, and the regenerable probe
  artifacts (`tests/probe_siti_results.json`, `report_siti.md`).

### Added
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

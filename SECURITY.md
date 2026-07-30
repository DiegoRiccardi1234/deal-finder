# Security Policy

## Supported versions

Only the latest release on `main` is supported. See [CHANGELOG.md](CHANGELOG.md).

## Reporting a vulnerability

Please **do not** open a public issue for security problems.

- Use GitHub's **private vulnerability reporting** (repo → *Security* → *Report a vulnerability*), or
- Email the maintainer: `superdiego135@gmail.com`.

You'll get an acknowledgement within a few days. Once a fix is available it will
be released and credited (unless you prefer to stay anonymous).

## Threat model

Deal Finder is a personal price-comparison tool. It runs in two modes:

| Mode | Surface |
|------|---------|
| **Local** (`streamlit run app.py`, Docker, launcher scripts) | Binds to localhost; intended for a single trusted user. |
| **Streamlit Cloud** (hosted) | Public URL gated by `APP_PASSWORD`. |

The app performs **outbound** HTTP scraping of public e-commerce pages and
optional calls to AI providers. It does not accept untrusted inbound input
beyond the search box and the password gate.

## Secret handling

**No user credential is ever committed.** Every key that belongs to *you* —
provider API keys, `APP_PASSWORD`, eBay credentials — is read only from
environment variables or `st.secrets`:

- Local secrets live in `.streamlit/secrets.toml` — **gitignored**, never tracked.
  Copy `.streamlit/secrets.toml.example` and fill in your own values.
- Docker reads the same values from `.env` — also gitignored. Copy `.env.example`.
- On Streamlit Cloud, set secrets in the app's *Secrets* panel.
- All provider keys are **optional**; the app degrades gracefully when a key is
  absent (eBay falls back to HTML scraping, AI features turn off).

Relevant config: `offerte/providers.py` (`load_keys_from`, `get_api_key`),
`offerte/config.py`.

### Third-party public keys in source

Two Algolia **search-only public keys** *are* present in the source, for Unieuro
(`offerte/scrapers/unieuro.py`) and Comet (`offerte/scrapers/comet.py`). These
are not credentials of this project: each retailer publishes the key in its own
public JavaScript bundle, where any visitor's browser can read it, and the
scraper uses the same public search endpoint the site's own frontend calls. They
grant read-only catalogue search and nothing else.

They are kept in the repository deliberately — removing them would only break
the scrapers without protecting anything. Each is annotated at its definition
with where it came from, so it is not mistaken for a leak.

### History note — resolved

In June 2026 a Google Maps API key was accidentally committed to a test-output
dump (`tests/probe_siti_results.json`). It was **purged from the entire git
history** with `git-filter-repo`, **the key was rotated**, and probe-result dumps
are now gitignored.

Verified on 2026-07-30: scanning every commit reachable from every ref finds no
Google (`AIza…`) or provider key pattern anywhere in history. The incident is
closed — the old key is dead and nothing is outstanding.

If you forked or cloned the repository before that scrub, discard the old key.

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

Trova Prezzi Mio is a personal price-comparison tool. It runs in two modes:

| Mode | Surface |
|------|---------|
| **Local** (`streamlit run app.py`, Docker, launcher scripts) | Binds to localhost; intended for a single trusted user. |
| **Streamlit Cloud** (hosted) | Public URL gated by `APP_PASSWORD`. |

The app performs **outbound** HTTP scraping of public e-commerce pages and
optional calls to AI providers. It does not accept untrusted inbound input
beyond the search box and the password gate.

## Secret handling

**No credentials are ever committed.** Keys are read only from environment
variables or `st.secrets`:

- Local secrets live in `.streamlit/secrets.toml` — **gitignored**, never tracked.
  Copy `.streamlit/secrets.toml.example` and fill in your own values.
- On Streamlit Cloud, set secrets in the app's *Secrets* panel.
- All provider keys are **optional**; the app degrades gracefully when a key is
  absent (eBay falls back to HTML scraping, AI features turn off).

Relevant config: `offerte/providers.py` (`load_keys_from`, `get_api_key`),
`offerte/config.py`.

### History note

A Google Maps API key was accidentally committed to a test-output dump
(`tests/probe_siti_results.json`) and later **purged from the entire git
history** with `git-filter-repo`. Probe-result dumps are now gitignored. If you
forked or cloned the repository before that scrub, please discard the old key.

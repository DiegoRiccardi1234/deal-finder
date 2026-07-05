# TODO — Trova Prezzi

## Nuovi siti da aggiungere

### Negozi italiani — implementati
- [x] **Comet.it** — Algolia API (App ID: MVK2S77IYI / index: products)
- [x] **Expert.it** — JSON-LD ItemList scraping (`/it/it/exp/shop/search?terms=`)
- [x] **Wallapop** — API JSON due step (components → section), Roma lat/lon

### Negozi italiani — SPA, richiedono Playwright o API auth
- [ ] **Trony.it** — SPA con `nav-quick-search`, prodotti caricati via JS dopo page load
- [ ] **ePrice.it** — React SPA con autosuggest, URL di ricerca non standard
- [ ] **BuyDifferent / iRiparo** — ricondizionati Apple (niche, bassa priorità)


### Siti bloccati — richiedono Playwright
- [ ] **AliExpress** — funziona con Playwright headless (12 prodotti trovati nel probe)
  - Selettore funzionante: `a[href*="/item/"]`
  - NON funziona su Streamlit Cloud
  - Funziona su Render e in locale

### Siti non fattibili (bot-protection troppo aggressiva)
- ~~**Subito.it**~~ — Akamai CDN blocca HTTP 403 anche con Playwright headless
- ~~**Temu.com**~~ — CAPTCHA interattivo "Verifica di sicurezza"
- ~~**Alibaba.com**~~ — CAPTCHA interattivo "Captcha Interception"
- ~~**Facebook Marketplace**~~ — richiede login obbligatorio

---

## Deploy

### Opzioni cloud valutate
| Piattaforma | Playwright | Note |
|---|---|---|
| Streamlit Cloud | ❌ | Ambiente sandboxed, no browser headless |
| **Render** | ✅ | Free tier 750h/mese, Playwright funziona |

### Pro Render
- Deploy automatico da GitHub
- Playwright + Chromium installabili via build
- Variabili d'ambiente per secrets (sostituisce secrets.toml)

### Contro Render (free tier)
- Container si "spegne" dopo 15 min di inattività → cold start 30-60s
- RAM 512MB — Playwright + Chromium pesano ~300-400MB (vicino al limite)

### Conclusione deploy
Vale la pena solo se si vuole AliExpress. Per Subito/Temu/Alibaba nessuna
piattaforma cloud risolve il problema. Con i siti attuali (Amazon, eBay,
Euronics, Unieuro, MediaWorld, Vinted, Wallapop, Comet, Expert) Streamlit
Cloud funziona già bene — tutti usano HTTP plain senza Playwright.

---

## Fix già fatti in questa sessione

- [x] **Bug Amazon `condizione=nuovo`** — scartava tutte le 48 card perché
  `card.get_text()` catturava il cross-sell "Disponibile usato da €X" presente
  anche nelle card di prodotti nuovi. Fix: controlla solo `nome.lower()`
- [x] **Unieuro riscritto con Algolia** — era sempre 0 risultati (SPA Ionic/Angular).
  Ora usa l'API Algolia pubblica embedded nel frontend JS:
  `mnbcenyfii-dsn.algolia.net`, index `sgmproducts_prod`, key `977ed8d06b718d4929ca789c78c4107a`.
  Da 0 risultati in 6s → 13 risultati in 1.4s, nessun Playwright necessario
- [x] **Aggiunti scraper Subito/AliExpress/Temu/Alibaba** (non attivi di default,
  early-return con messaggio chiaro)
- [x] **Script `tests/probe_scrapers.py`** — probe HTTP reale su tutti i siti
- [x] **Script `tests/probe_playwright.py`** — probe Playwright headless sui siti bloccati
- [x] **Vinted sostituito con libreria `vinted-scraper`** — gestione cookie automatica, v3.0.0
- [x] **Wallapop aggiunto via API JSON** — due step: `/api/v3/search/components` → `/api/v3/search/section`
- [x] **Comet.it aggiunto via Algolia** — App ID: MVK2S77IYI / index: products / filter: visible=1
- [x] **Expert.it aggiunto via JSON-LD** — ItemList scraping da `/it/it/exp/shop/search?terms=`
- [x] **Fix `test_nuove_fonti_vuote`** — test flaky su rete reale, ora mocka anche `requests.post/get`

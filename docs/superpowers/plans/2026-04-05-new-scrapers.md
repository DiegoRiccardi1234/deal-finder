# New Scrapers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Wallapop, Comet, Expert scrapers + upgrade Vinted to use the `vinted-scraper` PyPI library.

**Architecture:** All new scrapers follow the existing `offerte_tech.py` pattern: a standalone function returning `list[Offerta]`, registered in `cerca_offerte()` via `ThreadPoolExecutor`. Wallapop uses a two-step JSON API; Comet uses Algolia (same as Unieuro); Expert uses JSON-LD embedded in the HTML response. Vinted replaces custom HTML parsing with the `vinted-scraper` library.

**Tech Stack:** Python, `requests`, `BeautifulSoup`, `vinted-scraper` (PyPI), Wallapop public API, Comet/Algolia API, Expert JSON-LD.

---

## Verified API details (do not re-investigate)

### vinted-scraper library
- Already installed via `pip install vinted-scraper`
- `VintedScraper("https://www.vinted.it").search({"search_text": q, "order": "price_low_to_high", "per_page": 48})`
- Item fields: `.title` (str), `.price` (float), `.url` (str), `.photo` (dict with key `"url"`)

### Wallapop API (two-step)
- Step 1: `GET https://api.wallapop.com/api/v3/search/components?keywords={q}&latitude=41.9028&longitude=12.4964&source=search_box`
  - Response: `components[].type_data.query_params.search_id` and `.category_id`
- Step 2: `GET https://api.wallapop.com/api/v3/search/section?keywords={q}&source=search_box&search_id={id}&category_id={cat}&latitude=41.9028&longitude=12.4964&order_by=most_relevance&section_type=organic_search_results`
  - Response: `data.section.items[]` where each item has `title`, `price.amount`, `web_slug`, `images[0].urls.small`, `shipping.user_allows_shipping`, `is_refurbished`
  - URL: `https://it.wallapop.com/item/{web_slug}`
- Required extra headers: `deviceos: 0`, `x-deviceos: 0`, `x-appversion: 818810`, `x-deviceid: {uuid}`, `mpid: -3421950124112390907`, `trackinguserid: -3421950124112390907`

### Comet Algolia
- `POST https://mvk2s77iyi-dsn.algolia.net/1/indexes/*/queries`
- Headers: `x-algolia-application-id: MVK2S77IYI`, `x-algolia-api-key: f7f4f516742fcb4597c1e71641f7d0ed`
- Body: `{"requests": [{"indexName": "products", "query": q, "hitsPerPage": 40, "page": 0, "filters": "visible=1"}]}`
- Hit fields: `name`, `pFinale` (price float), `url` (full URL), `image` (full URL), `isAcquistabile` (bool)

### Expert JSON-LD
- `GET https://www.expert.it/it/it/exp/shop/search?terms={query}`
- Parse `<script type="application/ld+json">` → `@type=ItemList` → `itemListElement[].item`
- Item fields: `name`, `offers.price` (str→float), `url`, `image`

---

## File map

| File | Change |
|---|---|
| `offerte_tech.py` | Replace `scrape_vinted`, add `scrape_wallapop`, `scrape_comet`, `scrape_expert`; update `cerca_offerte` + constants |
| `requirements.txt` | Add `vinted-scraper` |
| `tests/test_suite.py` | Add tests for 4 scrapers; update `_make_monkeypatch_cerca` |
| `TODO.md` | Mark Vinted/Wallapop/Comet/Expert as done; note Trony/ePrice as SPA |

---

## Task 1: Add vinted-scraper to requirements.txt

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add dependency**

In `requirements.txt`, add after `requests`:
```
vinted-scraper>=3.0.0
```

- [ ] **Step 2: Commit**
```bash
git add requirements.txt
git commit -m "chore: add vinted-scraper dependency"
```

---

## Task 2: Replace scrape_vinted with library-based implementation

**Files:**
- Modify: `offerte_tech.py` lines ~1425–1570 (the entire `scrape_vinted` function)

- [ ] **Step 1: Write failing test**

In `tests/test_suite.py`, add after the existing Amazon tests:

```python
def test_scrape_vinted_library_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """scrape_vinted deve usare VintedScraper e restituire Offerta objects."""
    from offerte_tech import scrape_vinted, Offerta

    class _FakeItem:
        title = "Notebook Lenovo usato"
        price = 150.0
        url = "https://www.vinted.it/items/123-notebook"
        photo = {"url": "https://images1.vinted.net/photo.jpg"}

    class _FakeScraper:
        def __init__(self, base_url): pass
        def search(self, params): return [_FakeItem()]

    monkeypatch.setattr("offerte_tech.VintedScraper", _FakeScraper)

    results = scrape_vinted("notebook", 0.0, 500.0, ["notebook"])
    assert len(results) == 1
    assert results[0].negozio == "Vinted"
    assert results[0].prezzo == 150.0
    assert results[0].immagine == "https://images1.vinted.net/photo.jpg"


def test_scrape_vinted_skips_nuovo_condizione(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_vinted
    results = scrape_vinted("notebook", 0.0, 500.0, ["notebook"], condizione="nuovo")
    assert results == []
```

- [ ] **Step 2: Run tests to confirm they fail**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_vinted_library_returns_results tests/test_suite.py::test_scrape_vinted_skips_nuovo_condizione -v
```
Expected: FAIL (ImportError or AttributeError — `VintedScraper` not imported in offerte_tech)

- [ ] **Step 3: Replace scrape_vinted in offerte_tech.py**

Near the top of `offerte_tech.py`, after the other optional imports (around line 89), add:
```python
try:
    from vinted_scraper import VintedScraper
except ImportError:
    VintedScraper = None  # type: ignore[assignment,misc]
```

Then replace the entire `scrape_vinted` function body (lines ~1428–1570) with:

```python
def scrape_vinted(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """Scraper per Vinted.it tramite libreria vinted-scraper (gestione cookie automatica)."""
    if condizione == "nuovo":
        print("\nℹ️ Vinted mostra solo articoli usati — salto per condizione=nuovo")
        return []
    print(f"\n🔍 Cerco su Vinted.it: \"{query}\"")
    risultati: list[Offerta] = []
    if VintedScraper is None:
        print("    ⚠️  Vinted.it: libreria vinted-scraper non installata.")
        return risultati
    try:
        scraper = VintedScraper("https://www.vinted.it")
        items = scraper.search({"search_text": query, "order": "price_low_to_high", "per_page": 48})
        print(f"    ✅ Vinted.it: {len(items)} risultati")
        for item in items:
            try:
                nome = str(item.title or "").strip()
                if not nome:
                    continue
                prezzo = float(item.price)
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                link = str(item.url)
                foto = item.photo
                img_url = foto.get("url", "") if isinstance(foto, dict) else ""
                risultati.append(Offerta(
                    nome=nome, prezzo=prezzo, negozio="Vinted",
                    link=link, fonte="vinted.it", spedizione="n.d.", immagine=img_url,
                ))
            except (AttributeError, TypeError, ValueError):
                continue
    except Exception as exc:
        print(f"    ❌ Vinted.it: errore → {exc}")
    return risultati
```

- [ ] **Step 4: Run tests**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_vinted_library_returns_results tests/test_suite.py::test_scrape_vinted_skips_nuovo_condizione -v
```
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add offerte_tech.py tests/test_suite.py
git commit -m "feat(scraper): replace vinted HTML scraper with vinted-scraper library"
```

---

## Task 3: Add scrape_wallapop

**Files:**
- Modify: `offerte_tech.py` — add constants near Unieuro constants, add function after `scrape_mediaworld`

- [ ] **Step 1: Write failing test**

```python
def test_scrape_wallapop_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_wallapop, Offerta

    comp_resp = {
        "components": [{"type": "search_results", "type_data": {"query_params": {
            "search_id": "abc-123", "category_id": "24200"
        }}}]
    }
    section_resp = {
        "data": {"section": {"items": [{
            "title": "Notebook HP 15 usato",
            "price": {"amount": 280.0, "currency": "EUR"},
            "web_slug": "notebook-hp-15-280",
            "images": [{"urls": {"small": "https://cdn.wallapop.com/img.jpg"}}],
            "shipping": {"user_allows_shipping": True},
            "is_refurbished": False,
        }]}}
    }

    call_count = {"n": 0}
    class _FakeResp:
        status_code = 200
        def __init__(self, data): self._data = data
        def json(self): return self._data
        def raise_for_status(self): pass

    def fake_get(url, **kwargs):
        call_count["n"] += 1
        if "components" in url:
            return _FakeResp(comp_resp)
        return _FakeResp(section_resp)

    monkeypatch.setattr("offerte_tech.requests.get", fake_get)

    results = scrape_wallapop("notebook", 0.0, 500.0, ["notebook"])
    assert call_count["n"] == 2
    assert len(results) == 1
    assert results[0].negozio == "Wallapop"
    assert results[0].prezzo == 280.0
    assert results[0].link == "https://it.wallapop.com/item/notebook-hp-15-280"
```

- [ ] **Step 2: Run test to confirm FAIL**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_wallapop_returns_results -v
```
Expected: FAIL (ImportError — `scrape_wallapop` not defined)

- [ ] **Step 3: Add constants and scrape_wallapop to offerte_tech.py**

Near the `_UNIEURO_ALGOLIA_URL` constant, add:
```python
_WALLAPOP_COMPONENTS_URL = "https://api.wallapop.com/api/v3/search/components"
_WALLAPOP_SECTION_URL = "https://api.wallapop.com/api/v3/search/section"
```

After `scrape_mediaworld` (around line 2785, before `scrape_subito`), add:

```python
# ===========================================================================
# SCRAPER — wallapop.com
# ===========================================================================

def scrape_wallapop(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
    condizione: str = "tutti",
) -> list[Offerta]:
    """
    Scraper per Wallapop.it tramite API pubblica (due step: components → section).

    NOTE API (valide ad aprile 2026):
        Step 1: GET /api/v3/search/components → estrae search_id e category_id
        Step 2: GET /api/v3/search/section con search_id → items[]
        Campi: title, price.amount, web_slug, images[0].urls.small
        URL articolo: https://it.wallapop.com/item/{web_slug}
        Richiede header x-deviceid (UUID random), x-appversion, mpid.
    """
    import uuid as _uuid
    print(f"\n🔍 Cerco su Wallapop: \"{query}\"")
    risultati: list[Offerta] = []
    _headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "it,it-IT;q=0.9",
        "Referer": "https://it.wallapop.com/",
        "User-Agent": get_headers().get("User-Agent", "Mozilla/5.0"),
        "deviceos": "0",
        "x-deviceos": "0",
        "x-appversion": "818810",
        "x-deviceid": str(_uuid.uuid4()),
        "mpid": "-3421950124112390907",
        "trackinguserid": "-3421950124112390907",
    }
    try:
        r1 = requests.get(_WALLAPOP_COMPONENTS_URL, params={
            "keywords": query, "latitude": 41.9028, "longitude": 12.4964, "source": "search_box",
        }, headers=_headers, timeout=TIMEOUT)
        r1.raise_for_status()

        search_id = category_id = None
        for comp in r1.json().get("components", []):
            qp = (comp.get("type_data") or {}).get("query_params") or {}
            if qp.get("search_id"):
                search_id = qp["search_id"]
                category_id = qp.get("category_id")
                break
        if not search_id:
            print("    ⚠️  Wallapop: search_id non trovato nella risposta components.")
            return risultati

        params2: dict = {
            "keywords": query, "source": "search_box", "search_id": search_id,
            "latitude": 41.9028, "longitude": 12.4964,
            "order_by": "most_relevance", "section_type": "organic_search_results",
        }
        if category_id:
            params2["category_id"] = category_id

        r2 = requests.get(_WALLAPOP_SECTION_URL, params=params2, headers=_headers, timeout=TIMEOUT)
        r2.raise_for_status()
        items = r2.json().get("data", {}).get("section", {}).get("items", [])
        print(f"    ✅ Wallapop: {len(items)} risultati")

        for item in items:
            try:
                nome = str(item.get("title") or "").strip()
                if not nome:
                    continue
                price_data = item.get("price") or {}
                prezzo = float(price_data.get("amount", 0))
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                web_slug = str(item.get("web_slug") or "").strip()
                if not web_slug:
                    continue
                link = f"https://it.wallapop.com/item/{web_slug}"
                imgs = item.get("images") or []
                img_url = imgs[0].get("urls", {}).get("small", "") if imgs else ""
                ship = item.get("shipping") or {}
                spedizione = "Spedizione disponibile" if ship.get("user_allows_shipping") else "Solo ritiro"
                if condizione == "nuovo" and item.get("is_refurbished"):
                    continue
                risultati.append(Offerta(
                    nome=nome, prezzo=prezzo, negozio="Wallapop",
                    link=link, fonte="wallapop.com", spedizione=spedizione, immagine=img_url,
                ))
            except (AttributeError, TypeError, ValueError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Wallapop: errore HTTP {status}.")
    except Exception as exc:
        print(f"    ❌ Wallapop: errore inatteso → {exc}")
    return risultati
```

- [ ] **Step 4: Run test**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_wallapop_returns_results -v
```
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add offerte_tech.py tests/test_suite.py
git commit -m "feat(scraper): add Wallapop scraper via two-step JSON API"
```

---

## Task 4: Add scrape_comet

**Files:**
- Modify: `offerte_tech.py` — add constants, add function after `scrape_wallapop`

- [ ] **Step 1: Write failing test**

```python
def test_scrape_comet_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_comet, Offerta

    algolia_resp = {"results": [{"hits": [{
        "name": "Lenovo IdeaPad 3 15 - 16GB/512GB",
        "pFinale": 549.0,
        "url": "https://www.comet.it/lenovo-ideapad-3-LEN001",
        "image": "https://static.comet.it/img/LEN001.jpg",
        "isAcquistabile": True,
    }]}]}

    class _FakeResp:
        status_code = 200
        def json(self): return algolia_resp
        def raise_for_status(self): pass

    monkeypatch.setattr("offerte_tech.requests.post", lambda *a, **k: _FakeResp())

    results = scrape_comet("notebook", 0.0, 800.0, ["notebook", "lenovo"])
    assert len(results) == 1
    assert results[0].negozio == "Comet"
    assert results[0].prezzo == 549.0
    assert "comet.it" in results[0].link
```

- [ ] **Step 2: Run test to confirm FAIL**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_comet_returns_results -v
```
Expected: FAIL

- [ ] **Step 3: Add constants and scrape_comet to offerte_tech.py**

Add constants near Unieuro ones:
```python
_COMET_ALGOLIA_URL = "https://mvk2s77iyi-dsn.algolia.net/1/indexes/*/queries"
_COMET_ALGOLIA_APP_ID = "MVK2S77IYI"
_COMET_ALGOLIA_API_KEY = "f7f4f516742fcb4597c1e71641f7d0ed"
```

After `scrape_wallapop`, add:

```python
# ===========================================================================
# SCRAPER — comet.it
# ===========================================================================

def scrape_comet(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per Comet.it tramite Algolia (API pubblica embedded nel frontend).

    NOTE API (valide ad aprile 2026):
        App ID: MVK2S77IYI  |  Index: products  |  Filter: visible=1
        Hit fields: name, pFinale (prezzo finale), url (assoluto), image (assoluto)
    """
    print(f"\n🔍 Cerco su Comet.it: \"{query}\"")
    risultati: list[Offerta] = []
    try:
        payload = json.dumps({"requests": [{
            "indexName": "products",
            "query": query,
            "hitsPerPage": 40,
            "page": 0,
            "filters": "visible=1",
        }]})
        headers = {
            "Content-Type": "application/json",
            "x-algolia-api-key": _COMET_ALGOLIA_API_KEY,
            "x-algolia-application-id": _COMET_ALGOLIA_APP_ID,
            "Origin": "https://www.comet.it",
            "Referer": "https://www.comet.it/",
        }
        resp = requests.post(_COMET_ALGOLIA_URL, data=payload, headers=headers, timeout=TIMEOUT)
        resp.raise_for_status()
        hits = resp.json().get("results", [{}])[0].get("hits", [])
        if not hits:
            print("    ⚠️  Comet.it: nessun prodotto trovato.")
            return risultati
        print(f"    ✅ Comet.it (Algolia): {len(hits)} risultati")
        for hit in hits:
            try:
                nome = str(hit.get("name") or "").strip()
                if not nome:
                    continue
                prezzo = float(hit.get("pFinale") or hit.get("pListino") or 0)
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                if not hit.get("isAcquistabile", True):
                    continue
                link = str(hit.get("url") or "").strip()
                if not link:
                    continue
                img_url = str(hit.get("image") or "").strip()
                risultati.append(Offerta(
                    nome=nome, prezzo=prezzo, negozio="Comet",
                    link=link, fonte="comet.it", spedizione="n.d.", immagine=img_url,
                ))
            except (AttributeError, TypeError, ValueError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Comet.it: errore HTTP {status}.")
    except Exception as exc:
        print(f"    ❌ Comet.it: errore inatteso → {exc}")
    return risultati
```

- [ ] **Step 4: Run test**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_comet_returns_results -v
```
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add offerte_tech.py tests/test_suite.py
git commit -m "feat(scraper): add Comet.it scraper via Algolia API"
```

---

## Task 5: Add scrape_expert

**Files:**
- Modify: `offerte_tech.py` — add function after `scrape_comet`

- [ ] **Step 1: Write failing test**

```python
def test_scrape_expert_returns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    from offerte_tech import scrape_expert, Offerta

    json_ld = json.dumps({
        "@context": "https://schema.org",
        "@type": "ItemList",
        "numberOfItems": "1",
        "itemListElement": [{"@type": "ListItem", "position": 1, "item": {
            "@type": "Product",
            "name": "NOTEBOOK ACER ASPIRE 15 - Intel Ultra 5",
            "url": "https://www.expert.it/it/it/exp/shop/product/notebook-acer/exp123456",
            "image": "https://d3s2y7lmzr67yx.cloudfront.net/IMG/EXPERT/EXP123456.jpg",
            "offers": {"@type": "Offer", "price": "699", "priceCurrency": "EUR"},
        }}],
    })
    html = f'<html><head></head><body><script type="application/ld+json">{json_ld}</script></body></html>'

    monkeypatch.setattr("offerte_tech.fetch_with_retry", lambda *a, **k: _FakeResponse(html, 200))

    results = scrape_expert("notebook", 0.0, 800.0, ["notebook", "acer"])
    assert len(results) == 1
    assert results[0].negozio == "Expert"
    assert results[0].prezzo == 699.0
    assert "expert.it" in results[0].link
```

Note: this test uses the existing `_FakeResponse` class already in `tests/test_suite.py`.

- [ ] **Step 2: Run test to confirm FAIL**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_expert_returns_results -v
```
Expected: FAIL

- [ ] **Step 3: Add scrape_expert to offerte_tech.py**

After `scrape_comet`, add:

```python
# ===========================================================================
# SCRAPER — expert.it
# ===========================================================================

def scrape_expert(
    query: str,
    prezzo_min: float,
    budget_max: Optional[float],
    query_tokens: list[str],
) -> list[Offerta]:
    """
    Scraper per Expert.it tramite JSON-LD ItemList nella pagina di ricerca.

    NOTE SELETTORI (validi ad aprile 2026):
        URL ricerca: /it/it/exp/shop/search?terms={query}
        Data: <script type="application/ld+json"> @type=ItemList
        Campi item: name, offers.price (str), url (assoluto), image (assoluto)
    """
    url = f"https://www.expert.it/it/it/exp/shop/search?terms={quote_plus(query)}"
    print(f"\n🔍 Cerco su Expert.it: \"{query}\"")
    risultati: list[Offerta] = []
    try:
        headers = get_headers()
        headers["Referer"] = "https://www.expert.it/"
        resp = fetch_with_retry(url, headers)
        if resp.status_code in (401, 403, 429, 503):
            print(f"    ⚠️  Expert.it: accesso bloccato (HTTP {resp.status_code}).")
            return risultati
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        ld_tags = soup.select('script[type="application/ld+json"]')
        items: list = []
        for tag in ld_tags:
            try:
                data = json.loads(tag.string or "")
                if data.get("@type") == "ItemList":
                    items = data.get("itemListElement", [])
                    break
            except (json.JSONDecodeError, AttributeError):
                continue

        if not items:
            print("    ⚠️  Expert.it: nessun prodotto trovato (JSON-LD ItemList assente).")
            return risultati
        print(f"    ✅ Expert.it: {len(items)} risultati")

        for el in items:
            try:
                item = el.get("item", {})
                nome = str(item.get("name") or "").strip()
                if not nome:
                    continue
                prezzo = float(item.get("offers", {}).get("price", 0))
                if not math.isfinite(prezzo) or prezzo <= 0:
                    continue
                if not _within_price_range(prezzo, prezzo_min, budget_max):
                    continue
                if not is_relevant(nome, query_tokens, strict_specs=False):
                    continue
                link = str(item.get("url") or "").strip()
                if not link:
                    continue
                img_url = str(item.get("image") or "").strip()
                risultati.append(Offerta(
                    nome=nome, prezzo=prezzo, negozio="Expert",
                    link=link, fonte="expert.it", spedizione="n.d.", immagine=img_url,
                ))
            except (AttributeError, TypeError, ValueError, KeyError):
                continue
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        print(f"    ⚠️  Expert.it: errore HTTP {status}.")
    except Exception as exc:
        print(f"    ❌ Expert.it: errore inatteso → {exc}")
    return risultati
```

- [ ] **Step 4: Run test**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py::test_scrape_expert_returns_results -v
```
Expected: PASS

- [ ] **Step 5: Commit**
```bash
git add offerte_tech.py tests/test_suite.py
git commit -m "feat(scraper): add Expert.it scraper via JSON-LD ItemList"
```

---

## Task 6: Register all new scrapers in cerca_offerte + CLI

**Files:**
- Modify: `offerte_tech.py` lines ~3254–3300 (`cerca_offerte` function) and ~3418 (CLI choices)

- [ ] **Step 1: Update fonti_norm default set** (line ~3257)

Change:
```python
fonti_norm = {"amazon", "ebay", "vinted", "euronics", "unieuro", "mediaworld"}
```
To:
```python
fonti_norm = {"amazon", "ebay", "vinted", "euronics", "unieuro", "mediaworld", "wallapop", "comet", "expert"}
```

- [ ] **Step 2: Add executor.submit calls** (after the mediaworld block, before the subito block)

Add after the `if "mediaworld" in fonti_norm:` block:
```python
        if "wallapop" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_wallapop, "Wallapop", query, prezzo_min, budget_max, query_tokens, condizione)] = "Wallapop"
        if "comet" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_comet, "Comet.it", query, prezzo_min, budget_max, query_tokens)] = "Comet.it"
        if "expert" in fonti_norm:
            future_to_label[executor.submit(_timed_call, scrape_expert, "Expert.it", query, prezzo_min, budget_max, query_tokens)] = "Expert.it"
```

- [ ] **Step 3: Update CLI choices** (line ~3418)

Change:
```python
choices=["amazon", "ebay", "vinted", "euronics", "unieuro", "mediaworld"],
```
To:
```python
choices=["amazon", "ebay", "vinted", "euronics", "unieuro", "mediaworld", "wallapop", "comet", "expert"],
```

- [ ] **Step 4: Update _make_monkeypatch_cerca in tests** (line ~189)

Add to `_make_monkeypatch_cerca`:
```python
    monkeypatch.setattr("offerte_tech.scrape_wallapop", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_comet", lambda *a, **kw: [])
    monkeypatch.setattr("offerte_tech.scrape_expert", lambda *a, **kw: [])
```

- [ ] **Step 5: Run full test suite (excluding Playwright)**
```bash
cd "d:/DiegoD/Trova Prezzi Mio" && .venv/Scripts/pytest tests/test_suite.py -k "not playwright" -v
```
Expected: all PASS

- [ ] **Step 6: Commit**
```bash
git add offerte_tech.py tests/test_suite.py
git commit -m "feat(scraper): register wallapop/comet/expert in cerca_offerte and CLI"
```

---

## Task 7: Update TODO.md

**Files:**
- Modify: `TODO.md`

- [ ] **Step 1: Mark done items, note SPA sites**

Mark Comet, Expert, Wallapop as done `[x]`.
Add Trony and ePrice under a new section "Siti SPA — richiedono Playwright o API auth" with notes:
- Trony: SPA con `nav-quick-search`, risultati caricati via JS, necessita Playwright
- ePrice: React SPA con autosuggest, URL di ricerca non standard, necessita Playwright

Also add under "Fix già fatti":
- `[x]` Vinted sostituito con libreria `vinted-scraper` (cookie management automatico)
- `[x]` Wallapop aggiunto via API JSON (due step: components + section)
- `[x]` Comet aggiunto via Algolia (MVK2S77IYI / index: products)
- `[x]` Expert aggiunto via JSON-LD ItemList

- [ ] **Step 2: Commit**
```bash
git add TODO.md
git commit -m "docs: update TODO.md with completed scrapers and SPA notes"
```

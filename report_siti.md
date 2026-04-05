# Report test scraping siti e-commerce

Data test: 2026-04-05
Ambiente: Windows + Python 3.11 + Playwright Chromium headless (IP cloud/datacenter)

## Aggiornamento Deploy Render (2026-04-05)

Validazione fatta direttamente su `https://trova-prezzi.onrender.com/` con Playwright end-to-end (login + presearch + ricerca):

- Accesso: presente gate password (`Accesso riservato`) nel deploy.
- Ricerca completata in app: `✅ Ricerca completata — 20 offerte trovate`.
- Card renderizzate nella griglia risultati: `26`.
- Evidenza eBay nei log app: `eBay Browse API: 44 risultati`.

Conclusione pratica:
- eBay nel deploy app funziona tramite Browse API.
- Uno zero su `tests/probe_scrapers.py` non invalida eBay in produzione, perche' quel probe usa fallback HTML senza API key.
- Per certificare eBay usare sempre i log di `cerca_offerte` in app (Render/Streamlit), non il fallback HTML.

### trovaprezzi.it
- **URL testato:** https://www.trovaprezzi.it/cerca.aspx?nome=notebook
- **Metodo che funziona:** nessuno (blocco 403 su requests e Playwright)
- **Selettori trovati:** non affidabili (pagina challenge)
- **API interna trovata:** no REST chiara dal network; solo pattern interni/blocco
- **Fattibilita:** DIFFICILE
- **Note:**
  - Tutti gli URL testati (`/cerca.aspx`, `/categoria.aspx?libera=...`, `/prezzi/...`) hanno risposto 403 in questa sessione.
  - In una prima risposta non bloccata (transiente) sono emersi indizi di aggregazione multi-shop e link esterni verso negozi.
  - Pattern URL semplice di ricerca esiste, ma non stabile da IP bot-like.
  - Verifica richiesta specifica:
    - filtro prezzo: pattern URL/parametri presenti, non validabile end-to-end sotto blocco 403.
    - link a shop originali: evidenze parziali dai link esterni rilevati prima del blocco.
    - numero shop aggregati: stima preliminare ~8 domini in una run transiente, non confermabile in modo robusto per via del blocco.
- **Codice di esempio** (Python, max 20 righe):
```python
import requests
from bs4 import BeautifulSoup
url = "https://www.trovaprezzi.it/cerca.aspx?nome=notebook"
headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "it-IT,it;q=0.9"}
r = requests.get(url, headers=headers, timeout=20)
print(r.status_code, r.url)
if r.status_code == 200:
    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("a[href]")[:10]:
        print(a.get("href"))
```

### subito.it
- **URL testato:** https://www.subito.it/annunci-italia/vendita/usato/?q=iphone+14
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili (403)
- **API interna trovata:** no
- **Fattibilita:** IMPOSSIBILE
- **Note:** requests=403, Playwright=403 (Access Denied) anche dopo wait 5s.
- **Codice di esempio** (Python, max 20 righe):
```python
import requests
url = "https://www.subito.it/annunci-italia/vendita/usato/?q=iphone+14"
r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
print(r.status_code, r.text[:120])
```

### eprice.it
- **URL testato:** https://www.eprice.it/s/cerca/notebook
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili (403 su home e search)
- **API interna trovata:** no (non ispezionabile per blocco a monte)
- **Fattibilita:** IMPOSSIBILE
- **Note:**
  - 403 su `https://www.eprice.it/`, `https://www.eprice.it/s/cerca/notebook`, `https://www.eprice.it/search?q=notebook`.
  - Non possibile estrarre endpoint SPA/API da questo ambiente.
- **Codice di esempio** (Python, max 20 righe):
```python
import requests
for url in [
    "https://www.eprice.it/",
    "https://www.eprice.it/s/cerca/notebook",
]:
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    print(url, r.status_code)
```

### trony.it
- **URL testato:** https://www.trony.it/catalogsearch/result/?q=notebook
- **Metodo che funziona:** API diretta parziale (GraphQL rilevata ma autenticazione richiesta)
- **Selettori trovati:** non affidabili per listing (pagina 404 Not Found)
- **API interna trovata:** si - endpoint: https://www.trony.it/graphql (POST senza token -> 401), endpoint EmmaSuite (`trony-integration.emmasuite.com/api/...`)
- **Fattibilita:** DIFFICILE
- **Note:**
  - URL search HTML restituisce 404 (sia requests che Playwright).
  - `/api/search` restituisce 404, `/graphql` esiste ma richiede token/credenziali.
- **Codice di esempio** (Python, max 20 righe):
```python
import requests
url = "https://www.trony.it/graphql"
payload = {"query": "{ __typename }"}
r = requests.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
print(r.status_code)
print(r.text[:200])
```

### mediamarkt.it
- **URL testato:** https://www.mediamarkt.it/it/search.html?query=notebook
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili
- **API interna trovata:** no (connessione fallisce prima)
- **Fattibilita:** DIFFICILE
- **Note:** SSL handshake/cipher mismatch sia con requests sia con Playwright (`ERR_SSL_VERSION_OR_CIPHER_MISMATCH`).
- **Codice di esempio** (Python, max 20 righe):
```python
import requests
url = "https://www.mediamarkt.it/it/search.html?query=notebook"
try:
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
    print(r.status_code)
except Exception as e:
    print("Errore SSL:", e)
```

### unieuro.it
- **URL testato:** https://www.unieuro.it/online/search?text=notebook
- **Metodo che funziona:** API diretta (Algolia)
- **Selettori trovati:** non necessari; usare campi JSON (`title_it`, `discountedPrice`, `productUrl_it`)
- **API interna trovata:** si - endpoint: `https://mnbcenyfii-dsn.algolia.net/1/indexes/*/queries` con App ID `MNBCENYFII` e key `977ed8d06b718d4929ca789c78c4107a`
- **Fattibilita:** FACILE
- **Note:** credenziali fornite sono ancora valide (HTTP 200, hits presenti su index `sgmproducts_prod`).
- **Codice di esempio** (Python, max 20 righe):
```python
import requests, json
url = "https://mnbcenyfii-dsn.algolia.net/1/indexes/*/queries?x-algolia-api-key=977ed8d06b718d4929ca789c78c4107a&x-algolia-application-id=MNBCENYFII"
payload = {"requests": [{"indexName": "sgmproducts_prod", "query": "notebook", "hitsPerPage": 3}]}
headers = {
    "x-algolia-application-id": "MNBCENYFII",
    "x-algolia-api-key": "977ed8d06b718d4929ca789c78c4107a",
    "content-type": "application/x-www-form-urlencoded",
}
r = requests.post(url, data=json.dumps(payload), headers=headers, timeout=20)
for h in r.json()["results"][0]["hits"]:
    print(h.get("title_it"), h.get("discountedPrice"), h.get("productUrl_it"))
```

### zalando.it
- **URL testato:** https://www.zalando.it/catalogo/?q=scarpe+nike
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili (pagina bloccata)
- **API interna trovata:** no (traffico utile non emerso per blocco iniziale)
- **Fattibilita:** DIFFICILE
- **Note:** requests timeout (anche a 60s), Playwright 403.
- **Codice di esempio** (Python, max 20 righe):
```python
from playwright.sync_api import sync_playwright
url = "https://www.zalando.it/catalogo/?q=scarpe+nike"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    p = b.new_page()
    resp = p.goto(url, wait_until="domcontentloaded", timeout=60000)
    print(resp.status if resp else None, p.url)
    b.close()
```

### asos.com
- **URL testato:** https://www.asos.com/it/search/?q=t-shirt
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili
- **API interna trovata:** no utile (blocco prima del listing)
- **Fattibilita:** IMPOSSIBILE
- **Note:** requests timeout; Playwright 403 (Access Denied).
- **Codice di esempio** (Python, max 20 righe):
```python
from playwright.sync_api import sync_playwright
url = "https://www.asos.com/it/search/?q=t-shirt"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    p = b.new_page()
    resp = p.goto(url, wait_until="domcontentloaded", timeout=60000)
    print(resp.status if resp else None, p.title())
    b.close()
```

### aliexpress.it
- **URL testato:** https://it.aliexpress.com/wholesale?SearchText=notebook
- **Metodo che funziona:** nessuno in questa sessione
- **Selettori trovati:** non affidabili (pagina Captcha Interception)
- **API interna trovata:** si - endpoint osservati: `https://acs.aliexpress.com/h5/mtop...` (XHR/fetch)
- **Fattibilita:** DIFFICILE
- **Note:**
  - Retest Playwright mirato: status 200 ma titolo `Captcha Interception`, 0 prodotti.
  - Quindi il selettore storico `a[href*="/item/"]` qui non e` risultato utilizzabile.
- **Codice di esempio** (Python, max 20 righe):
```python
from playwright.sync_api import sync_playwright
url = "https://it.aliexpress.com/wholesale?SearchText=notebook"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    p = b.new_page(locale="it-IT")
    p.goto(url, wait_until="domcontentloaded", timeout=20000)
    p.wait_for_timeout(5000)
    print(p.title())
    print(len(p.query_selector_all('a[href*="/item/"]')))
    b.close()
```

### shein.com
- **URL testato:** https://it.shein.com/pdsearch/vestito/
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili (redirect a risk/challenge)
- **API interna trovata:** si - endpoint app rilevati (`/api/common/ugidInit`, `/api/common/i18nBatch/get`, `/bff-api/abt/merge/...`)
- **Fattibilita:** IMPOSSIBILE
- **Note:** requests e Playwright finiscono su URL challenge (`/risk/challenge?...`).
- **Codice di esempio** (Python, max 20 righe):
```python
from playwright.sync_api import sync_playwright
url = "https://it.shein.com/pdsearch/vestito/"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    p = b.new_page(locale="it-IT")
    p.goto(url, wait_until="domcontentloaded", timeout=25000)
    p.wait_for_timeout(5000)
    print(p.url)
    b.close()
```

### temu.com
- **URL testato:** https://www.temu.com/it/search_result.html?search_key=notebook
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili
- **API interna trovata:** si - endpoint: `/api/bg/sigerus/*`, `/api/passport/token/touch`, `/api/phantom/*`
- **Fattibilita:** IMPOSSIBILE
- **Note:** Playwright rediretto a login (`Temu | Login`), nessun prodotto parsabile.
- **Codice di esempio** (Python, max 20 righe):
```python
from playwright.sync_api import sync_playwright
url = "https://www.temu.com/it/search_result.html?search_key=notebook"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    p = b.new_page(locale="it-IT")
    p.goto(url, wait_until="domcontentloaded", timeout=25000)
    p.wait_for_timeout(5000)
    print(p.title(), p.url)
    b.close()
```

### alibaba.com
- **URL testato:** https://www.alibaba.com/trade/search?SearchText=notebook
- **Metodo che funziona:** nessuno
- **Selettori trovati:** non disponibili (Captcha Interception)
- **API interna trovata:** si - endpoint anti-bot: `cf.aliyun.com/nocaptcha/initialize.jsonp`, `/_____tmd_____/punishTextFetch`
- **Fattibilita:** IMPOSSIBILE
- **Note:** requests 200 ma payload anti-bot; Playwright titolo `Captcha Interception`.
- **Codice di esempio** (Python, max 20 righe):
```python
from playwright.sync_api import sync_playwright
url = "https://www.alibaba.com/trade/search?SearchText=notebook"
with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True, args=["--no-sandbox"])
    p = b.new_page(locale="it-IT")
    p.goto(url, wait_until="domcontentloaded", timeout=25000)
    p.wait_for_timeout(5000)
    print(p.title(), p.url)
    b.close()
```

## Sintesi operativa
- Distinzione importante: il risultato dipende dal contesto di test (probe diretto vs app deploy con credenziali/config).
- Confermato in app deploy Render: **eBay Browse API operativo** (log: `eBay Browse API: 44 risultati`).
- Confermato da probe diretto: **Unieuro (Algolia API)** molto stabile.
- Fonti con blocchi aggressivi in probe diretto: **Subito, ePrice, Zalando, ASOS, Shein, Temu, Alibaba**.
- **AliExpress**: comportamento variabile (query/IP dipendente). In una sessione puo' mostrare CAPTCHA, in un'altra restituire risultati validi con Playwright.

## Note operative per Claude Code
- Non usare `tests/probe_scrapers.py` per giudicare lo stato di eBay API: il probe eBay e' fallback HTML (`_scrape_ebay_html`) senza OAuth.
- Per eBay, la fonte di verita' e' il log runtime di `cerca_offerte` quando sono presenti `EBAY_APP_ID` e `EBAY_CERT_ID`.
- Quando valuti AliExpress con Playwright, testare almeno 2 query diverse prima di classificarlo come "non utilizzabile".

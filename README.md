# Offerte Tech Italia

Tool Python per cercare offerte di prodotti tech in Italia, con:
- scraper multi-fonte (`trovaprezzi.it` + `amazon.it`),
- filtro per query e budget,
- ordinamento per prezzo,
- interfaccia web Streamlit,
- esportazione CSV.

## Requisiti
- Python `3.10+` (consigliato `3.11`)
- Sistema operativo: Windows/macOS/Linux (istruzioni venv qui sotto includono Windows PowerShell)

Dipendenze Python:
- `requests`
- `beautifulsoup4`
- `fake-useragent`
- `streamlit`
- `groq`

## Installazione dipendenze
Dalla root del progetto:

```powershell
cd "d:\DiegoD\Trova Prezzi Mio"
python -m venv .venv
```

### Attivazione venv su Windows PowerShell
```powershell
.\.venv\Scripts\Activate.ps1
```

Se PowerShell blocca gli script (`execution of scripts is disabled`), usa uno di questi fix:

1. Solo per la sessione corrente (consigliato per test rapidi):
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

2. Permanente per il tuo utente (piu comodo):
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
```

Dopo l'attivazione:

```powershell
pip install requests beautifulsoup4 fake-useragent streamlit
```

## Avvio UI Streamlit
```powershell
streamlit run app.py
```

Poi apri:
- `http://localhost:8501`

## Configurazione AI (Groq)
Per abilitare l'assistente AI in `app.py`:

1. Crea una API key gratuita su:
	- `https://console.groq.com`

2. Crea il file `.streamlit/secrets.toml` nella root progetto con:

```toml
# Esempio (non committare mai chiavi reali)
GROQ_API_KEY = "gsk_..."
```

3. Assicurati che `groq` sia installato:

```powershell
pip install groq
```

Nota sicurezza:
- `secrets.toml` non va mai committato su git.
- Il progetto include `.gitignore` con la regola `.streamlit/secrets.toml`.

Comportamento chat AI:
- Dopo una ricerca con risultati, l'assistente genera automaticamente una prima analisi.
- Il contesto iniziale (lista completa prodotti) viene inviato in modo interno al modello.
- In UI non appare il bubble utente iniziale: viene mostrata la caption `🔍 Analisi completata su X prodotti trovati`.

## Uso CLI
Esempio base:

```powershell
python offerte_tech.py -q "notebook 14 pollici 16gb" -b 800 -n 10
```

Con export CSV:

```powershell
python offerte_tech.py -q "ssd 1tb" --export csv --output offerte_ssd.csv
```

Parametri principali:
- `-q, --query` (obbligatorio): testo di ricerca
- `-b, --budget` (opzionale): budget massimo in euro
- `-n, --top` (opzionale): numero massimo risultati (default 10)
- `--condizione` (opzionale): filtro condizione Amazon (`tutti`, `nuovo`, `usato`; default `tutti`)
- `--export csv` (opzionale): abilita export CSV
- `--output` (opzionale): nome file CSV

Esempi con condizione:

```powershell
python offerte_tech.py -q "notebook 14 pollici 16gb" -b 800 --condizione nuovo -n 5
python offerte_tech.py -q "iphone 17" --condizione usato -n 10
```

## Categorie trovaprezzi supportate (auto-mapping da query)
- `notebook`, `laptop` -> `notebook/offerte/notebook`
- `ssd` -> `ssd/offerte/ssd`
- `smartphone`, `telefono`, `iphone`, `samsung`, `xiaomi`, `pixel`, `android` -> `smartphone/offerte/smartphone`
- `monitor` -> `monitor/offerte/monitor`
- `gpu`, `scheda` -> `schede-video/offerte/schede-video`
- `ram` -> `memorie-ram/offerte/memorie-ram`
- `router`, `wifi` -> `router/offerte/router`
- `smartwatch` -> `smartwatch/offerte/smartwatch`
- `cuffie`, `auricolari`, `airpods`, `earbuds` -> `cuffie/offerte/cuffie`
- `mouse` -> `mouse/offerte/mouse`
- `tastiera` -> `tastiere/offerte/tastiere`
- `webcam` -> `webcam/offerte/webcam`
- `stampante` -> `stampanti/offerte/stampanti`
- `hard`, `hdd`, `disco` -> `hard-disk-esterni/offerte/hard-disk-esterni`
- `tablet` -> `tablet/offerte/tablet`
- `tv` -> `televisori-lcd-plasma/offerte/televisori`

## Troubleshooting

### 1) `trovaprezzi.it` non mostra risultati / errore 403 o 404
Il progetto usa pagine categoria statiche (es. `.../notebook/offerte/notebook`) e filtra lato codice con i token query.

Se trovi 403/404 o zero risultati:
- riprova dopo qualche minuto (possibile blocco anti-bot/rate limit),
- usa query con token categoria supportati (vedi sezione categorie),
- prova una query piu semplice,
- controlla se la struttura HTML e cambiata e richiede aggiornamento selettori.

### 2) Amazon mostra CAPTCHA / robot check
Sintomi: pochi/zero risultati o pagina anti-bot.

Cosa fare:
- attendi e riprova,
- riduci frequenza richieste,
- usa query meno aggressive,
- verifica connessione/rete/IP.

Nota: il codice include User-Agent realistico, delay random e retry automatico.

### 3) Errore PowerShell su attivazione venv
Errore tipico: `running scripts is disabled on this system`.

Risoluzione rapida:
```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

Risoluzione persistente per utente:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

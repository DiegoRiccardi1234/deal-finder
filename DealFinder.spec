# -*- mode: python ; coding: utf-8 -*-
"""Bundle Windows di Deal Finder: un eseguibile, nessun terminale.

Si costruisce con `python scripts/build_exe.py`, non invocando PyInstaller a
mano: quello script prepara la cartella e confeziona lo zip.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

block_cipher = None

# File **spediti**, di sola lettura, che finiscono in `_internal/`.
#
# `app.py` sta qui e non fra i moduli congelati per una ragione precisa:
# Streamlit non importa lo script principale, lo **riesegue** a ogni
# interazione leggendolo da disco. Congelato e basta non esisterebbe come file
# e il server non partirebbe.
datas = [
    ("app.py", "."),
    ("styles.css", "."),
    (".streamlit/config.toml", ".streamlit"),
    # Seed della knowledge base: `knowledge_base.py` lo legge da qui e non lo
    # riscrive mai (lo stato vivo sta in SQLite, accanto all'eseguibile).
    ("data/knowledge_base.json", "data"),
]

# Il frontend compilato di Streamlit (`streamlit/static`) e i file di
# configurazione: senza, il server risponde ma la pagina resta bianca.
datas += collect_data_files("streamlit")

# Streamlit legge la propria versione da `importlib.metadata`, che in un bundle
# funziona solo se ci si porta dietro la cartella `.dist-info`. Senza, l'avvio
# muore con `PackageNotFoundError` prima di aprire la porta.
for _pacchetto in ("streamlit", "openai", "anthropic", "cerebras_cloud_sdk"):
    try:
        datas += copy_metadata(_pacchetto)
    except Exception:
        # Un provider non installato non deve impedire il build.
        pass

hiddenimports = [
    # L'icona nell'area di notifica: `pystray` sceglie il backend a runtime,
    # quindi quello di Windows va nominato o non viene congelato.
    "pystray",
    "pystray._win32",
    "PIL",
    "PIL.Image",
    "PIL.ImageDraw",
]

# Streamlit si importa dei propri moduli a runtime, e l'analisi statica non li
# vede: `magic_funcs` per esempio viene importato dal codice che Streamlit
# **inietta** nello script dell'utente. Il difetto non si presenta come un
# errore di build né come un server che non parte — la porta risponde `ok` e
# l'errore compare solo a schermo, alla prima pagina. Elencarli tutti costa
# poco (sono moduli Python, non binari) ed elimina la categoria.
hiddenimports += collect_submodules("streamlit")

# L'applicazione vera e propria. Va nominata, e la ragione è la stessa per cui
# `app.py` sta fra i `datas`: l'analisi parte dal launcher, che *non* importa
# `app.py` — lo passa a Streamlit come percorso. Da lì in giù nessun modulo
# dell'applicazione verrebbe congelato, e ce ne accorgeremmo solo a schermo,
# uno alla volta. L'elenco è lo stesso di `[tool.setuptools]` in pyproject.toml.
hiddenimports += collect_submodules("offerte")
hiddenimports += collect_submodules("ui")
hiddenimports += [
    "app",
    "_shared",
    "offerte_tech",
    "knowledge_base",
    "price_history",
    "search_history",
    "watchlist",
]

# Gli scraper non servono qui: `offerte/scrapers/__init__.py` li importa tutti
# staticamente, quindi l'analisi li segue da sola.

excludes = [
    # Nessun grafico e nessuna tabella in tutta l'interfaccia (zero occorrenze
    # di st.dataframe / st.map / st.*_chart): sono dipendenze di Streamlit che
    # noi non attraversiamo mai. Valgono da sole più dell'intero resto.
    "altair",
    "pydeck",
    "pyarrow",
    "pandas",
    "numpy",
    # Roba da sviluppo o interfacce grafiche che non usiamo.
    "tests",
    "matplotlib",
    "tkinter",
    "PyQt5",
    "PySide2",
    "PyQt6",
    "PySide6",
    "mypy",
    "pytest",
    "ruff",
]

analisi_app = Analysis(
    ["scripts/launch_tray.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

analisi_agg = Analysis(
    ["scripts/updater.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["update_sync"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

# `MERGE` mette in comune le dipendenze fra i due eseguibili: senza,
# `Aggiorna.exe` si porterebbe dietro una seconda copia di tutto e lo zip
# raddoppierebbe. L'ordine conta — il primo della lista è quello che *possiede*
# le dipendenze condivise, e l'altro le cerca lì accanto.
MERGE((analisi_app, "DealFinder", "DealFinder"), (analisi_agg, "Aggiorna", "Aggiorna"))

pyz_app = PYZ(analisi_app.pure, analisi_app.zipped_data, cipher=block_cipher)
pyz_agg = PYZ(analisi_agg.pure, analisi_agg.zipped_data, cipher=block_cipher)

exe_app = EXE(
    pyz_app,
    analisi_app.scripts,
    [],
    exclude_binaries=True,
    name="DealFinder",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Niente finestra nera, mai. Gli errori d'avvio finiscono in
    # `data/logs/deal-finder.log`: con `console=True` un eseguibile rilanciato
    # dall'aggiornatore muore alla prima `print`, perché lo stdout ereditato
    # non è più valido.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

exe_agg = EXE(
    pyz_agg,
    analisi_agg.scripts,
    [],
    exclude_binaries=True,
    name="Aggiorna",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Anche l'aggiornatore è senza finestra: nessuno lo guarda mentre lavora, e
    # tutto quello che ha da dire finisce in `data/logs/aggiornamento.log`.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe_app,
    analisi_app.binaries,
    analisi_app.zipfiles,
    analisi_app.datas,
    exe_agg,
    analisi_agg.binaries,
    analisi_agg.zipfiles,
    analisi_agg.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="DealFinder",
)

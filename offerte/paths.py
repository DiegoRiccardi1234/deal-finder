"""Dove vivono i file, in sviluppo e dentro il bundle Windows.

Serve perché nell'exe congelato da PyInstaller ci sono **due** cartelle, e
confonderle rompe l'applicazione in due modi opposti:

- `sys._MEIPASS` è dove PyInstaller scompatta il bundle. È temporanea e di sola
  lettura: un database scritto lì sparisce alla chiusura, e nel caso onedir la
  scrittura fallisce del tutto. Ci vivono solo i file spediti (seed, asset).
- La cartella **accanto all'exe** è l'unica che sopravvive e che l'aggiornatore
  preserva (`data` è in `DA_NON_TOCCARE`). Ci vive tutto ciò che l'utente crea:
  database, chiavi, log, cache.

Da sorgente le due coincidono con la radice del repository, quindi lo sviluppo
non cambia di una virgola.

Solo stdlib e nessun import interno, come `offerte/config.py` e `offerte/db.py`:
questo modulo sta sotto a tutti gli altri e non deve poter creare cicli.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Chi lancia l'applicazione può imporre la cartella di lavoro. Il launcher del
#: bundle la dichiara **prima** di importare qualsiasi modulo dell'applicazione:
#: i percorsi qui sotto sono costanti di modulo nei consumatori, quindi si
#: risolvono una volta sola, al primo import. Dichiararla dopo non avrebbe
#: nessun effetto e il difetto sarebbe silenzioso.
WORKSPACE_ENV = "DEAL_FINDER_WORKSPACE"


def is_frozen() -> bool:
    """True quando giriamo dentro l'exe PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Radice dei file **spediti**, di sola lettura.

    Congelato: la cartella di scompattazione (`sys._MEIPASS`). Da sorgente: la
    radice del repository. Qui dentro stanno `app.py`, `styles.css` e i seed —
    niente che l'applicazione debba riscrivere.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parent.parent


def workspace() -> Path:
    """Radice dei dati **dell'utente**, scrivibile e persistente."""
    forced = os.environ.get(WORKSPACE_ENV, "").strip()
    if forced:
        return Path(forced).expanduser().resolve()
    if is_frozen():
        # Accanto all'exe, non accanto a questo file: da congelato `__file__`
        # punta dentro `_MEIPASS`, che è proprio la cartella da evitare.
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """`<workspace>/data`, creata se manca."""
    path = workspace() / "data"
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    """`<workspace>/data/logs`, creata se manca."""
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path

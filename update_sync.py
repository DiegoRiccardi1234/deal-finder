"""Copia una cartella sopra l'altra, senza toccare quello che è dell'utente.

Sta in un modulo suo, senza effetti all'import, per due motivi: lo importa
`scripts/updater.py`, che gira come programma a parte, e così si può provare
senza costruire un eseguibile.

E sta in cima al progetto, **fuori** dal package `offerte`, per un motivo
misurato: `offerte/__init__.py` ri-esporta tutta l'applicazione, quindi
importare `offerte.qualcosa` da dentro `Aggiorna.exe` si trascinava dietro
scraper, provider AI e Streamlit — 20 MB di eseguibile invece di due, e 18 MB
in più su ogni aggiornamento scaricato. L'aggiornatore ha bisogno solo della
libreria standard, e da qui è quello che prende.

Le regole scritte qui vengono da un aggiornamento fallito davvero su Trip
Finder, il 2026-08-01: `PermissionError [WinError 32] Il file è utilizzato da un
altro processo`. Su Windows i file restano bloccati per qualche secondo dopo che
il processo che li teneva è uscito, e l'antivirus ne blocca altri mentre
scansiona un archivio appena scompattato. Un aggiornatore che si arrende al
primo file lascia l'installazione a metà, che è peggio di non aver aggiornato.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

#: Roba dell'utente, mai sovrascritta: il database, lo storico, i preferiti, le
#: chiavi, i log. Il confronto è sulla prima parte del percorso, quindi
#: `data/qualunque/cosa` è protetto perché lo è `data`.
#:
#: `.streamlit` è nostro e non di Trip Finder: lì vive `secrets.toml` di chi
#: usa Deal Finder da sorgente o in Docker, e sovrascriverlo vorrebbe dire
#: cancellargli le chiavi durante un aggiornamento.
DA_NON_TOCCARE = ("data", ".env", ".env.local", ".streamlit")

#: Quanto riprovare prima di arrendersi su un file bloccato. In tutto una
#: trentina di secondi: Defender su un archivio appena scompattato può
#: tenerselo dieci o venti secondi, e arrendersi prima vuol dire fallire per
#: una scansione.
ATTESE = (1.0, 2.0, 4.0, 8.0, 16.0)


def _protetto(relativo: Path) -> bool:
    parti = relativo.parts
    return bool(parti) and parti[0] in DA_NON_TOCCARE


def _e_questo_programma(destinazione: Path) -> bool:
    """Vero se il file di arrivo è l'eseguibile che sta girando adesso.

    Cintura e bretelle: chi lancia l'aggiornatore ne mette una copia nel
    temporaneo proprio perché quello installato resti libero, ma se quella
    copia non riesce non si deve comunque provare a riscrivere sé stessi
    mentre si è in esecuzione — che è il primo modo in cui questo aggiornamento
    è fallito.
    """
    try:
        return destinazione.resolve() == Path(sys.executable).resolve()
    except OSError:
        return False


def _copia_insistendo(origine: Path, destinazione: Path) -> None:
    for attesa in ATTESE:
        try:
            shutil.copy2(origine, destinazione)
            return
        except PermissionError:
            time.sleep(attesa)
    try:
        shutil.copy2(origine, destinazione)
    except PermissionError as exc:
        # Il nome del file che è rimasto bloccato vale più del messaggio
        # generico: dice se è l'eseguibile, una libreria o roba dell'antivirus.
        raise PermissionError(
            exc.errno,
            f"{exc.strerror} (ancora bloccato dopo {len(ATTESE)} tentativi): {destinazione}",
        ) from exc


def sincronizza(*, origine: Path, destinazione: Path) -> int:
    """Copia tutto quello che sta in `origine` dentro `destinazione`.

    Restituisce quanti file ha scritto. Non cancella niente: quello che c'era
    e non c'è più resta, ed è la scelta prudente — un file di troppo non ha
    mai rotto niente, uno mancante sì.
    """
    if not origine.exists():
        raise FileNotFoundError(f"non trovo la cartella da installare: {origine}")
    destinazione.mkdir(parents=True, exist_ok=True)

    scritti = 0
    for file in origine.rglob("*"):
        if not file.is_file():
            continue
        relativo = file.relative_to(origine)
        if _protetto(relativo):
            continue
        arrivo = destinazione / relativo
        if _e_questo_programma(arrivo):
            continue
        arrivo.parent.mkdir(parents=True, exist_ok=True)
        _copia_insistendo(file, arrivo)
        scritti += 1
    return scritti

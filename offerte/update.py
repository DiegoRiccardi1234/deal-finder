"""Controllo e installazione degli aggiornamenti.

Tre modi di essere installati, tre comportamenti:

- **bundle Windows** (`DealFinder.exe`): si scarica lo zip della release, si
  passa la mano ad `Aggiorna.exe` e ci si spegne. È il caso per cui esiste
  questo modulo, ed è quello che prima non funzionava: chi scaricava lo ZIP
  vedeva solo un link e doveva rifare tutto a mano, perdendo `data/`.
- **copia git**: `git pull --ff-only` più le dipendenze. Proporre una
  sovrascrittura di file su una copia di lavoro sarebbe un ottimo modo per far
  perdere del lavoro a qualcuno, quindi qui non si tocca niente d'altro.
- **Streamlit Cloud**: niente, si aggiorna da sé.

La sequenza del bundle è obbligata dal fatto che su Windows un eseguibile in
esecuzione è bloccato dal sistema: si scarica lo zip, si scrive un lucchetto,
si lancia `Aggiorna.exe` e si esce. È lui che sostituisce i file e riapre.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from offerte import paths
from offerte.config import VERSION

logger = logging.getLogger(__name__)

_REPO = "DiegoRiccardi1234/deal-finder"
GITHUB_API = f"https://api.github.com/repos/{_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{_REPO}/releases/latest"

#: Il nome dell'asset costruito da `scripts/build_exe.py`. Se non combaciano,
#: l'aggiornamento non parte e lo dice: meglio di scaricare l'archivio sbagliato.
ASSET = "DealFinder-windows.zip"

#: Quanto un lucchetto è da prendere sul serio. Deve superare il caso peggiore
#: dell'aggiornatore — attesa dell'uscita (60 s), respiro (3 s), copia con i
#: suoi tentativi (~31 s) — altrimenti scade a metà lavoro e ne parte un altro.
DURATA_LUCCHETTO = 180.0

#: Il lanciatore lo registra all'avvio: serve a chiudere SQLite prima di morire,
#: o sarebbe il `-wal` rimasto aperto a far fallire la sostituzione dei file.
_spegnimento: Callable[[], None] | None = None


def register_shutdown(callback: Callable[[], None]) -> None:
    global _spegnimento
    _spegnimento = callback


def is_frozen() -> bool:
    return paths.is_frozen()


def current_version() -> str:
    return VERSION


def _parse_version(s: str) -> tuple[int, ...]:
    """`v0.10.2` → (0, 10, 2). Confrontare le stringhe direbbe che 0.9 > 0.10."""
    nums = re.findall(r"\d+", str(s or ""))
    return tuple(int(n) for n in nums) if nums else (0,)


def is_newer(latest: str, current: str) -> bool:
    return _parse_version(latest) > _parse_version(current)


def _default_fetch(url: str, headers: dict):
    from offerte.http import fetch_with_retry, get_headers

    h = get_headers()
    h.update(headers)
    return fetch_with_retry(url, h)


def _release_data(*, fetch: Callable | None = None) -> dict[str, Any] | None:
    """Il JSON dell'ultima release, o None se GitHub non risponde.

    L'endpoint è pubblico e non serve autenticazione. Se non risponde non è un
    errore dell'applicazione: si tace e si va avanti.
    """
    fetch = fetch or _default_fetch
    try:
        resp = fetch(GITHUB_API, {"Accept": "application/vnd.github+json"})
        return resp.json() or {}
    except Exception:
        return None


def latest_release(*, fetch: Callable | None = None) -> str | None:
    """Tag dell'ultima release GitHub (es. 'v1.2.0'), o None su errore."""
    dati = _release_data(fetch=fetch)
    tag = (dati or {}).get("tag_name")
    return str(tag) if tag else None


def update_available(*, fetch: Callable | None = None, current: str | None = None) -> str | None:
    """Ritorna il tag remoto se più recente della versione locale, altrimenti None."""
    current = current or current_version()
    latest = latest_release(fetch=fetch)
    if latest and is_newer(latest, current):
        return latest
    return None


def _project_root() -> str:
    return str(paths.workspace())


def is_git_clone(root: str | None = None) -> bool:
    return os.path.isdir(os.path.join(root or _project_root(), ".git"))


def is_cloud() -> bool:
    """True su Streamlit Cloud o se l'update è disabilitato via env."""
    if os.environ.get("DISABLE_AUTO_UPDATE", "").strip() == "1":
        return True
    return os.path.isdir("/mount/src")


def _default_runner(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


# --------------------------------------------------------------------------- #
# Bundle Windows
# --------------------------------------------------------------------------- #


def _scarica_asset(url: str, destinazione: Path, *, fetch: Callable | None = None) -> None:
    fetch = fetch or _default_fetch
    risposta = fetch(url, {"Accept": "application/octet-stream"})
    destinazione.write_bytes(risposta.content)


def _installa_bundle(*, fetch: Callable | None = None) -> dict:
    """Scarica la versione nuova e passa la mano ad `Aggiorna.exe`."""
    dati = _release_data(fetch=fetch)
    if dati is None:
        return {"ok": False, "mode": "bundle", "error": "release non raggiungibile"}

    asset = next((a for a in (dati.get("assets") or []) if a.get("name") == ASSET), None)
    if not asset:
        return {"ok": False, "mode": "bundle", "error": f"nessun {ASSET} nella release"}

    cartella = paths.data_dir() / "aggiornamenti"
    cartella.mkdir(parents=True, exist_ok=True)
    archivio = cartella / ASSET
    try:
        _scarica_asset(str(asset["browser_download_url"]), archivio, fetch=fetch)
    except Exception as exc:
        return {"ok": False, "mode": "bundle", "error": f"scaricamento fallito: {exc}"}

    dest = Path(sys.executable).resolve().parent
    aggiornatore = dest / "Aggiorna.exe"
    if not aggiornatore.exists():
        return {
            "ok": False,
            "mode": "bundle",
            "error": "Aggiorna.exe non c'è: reinstalla il bundle",
        }

    # Un aggiornamento già in corso non se ne fa partire un secondo: due
    # aggiornatori che copiano sugli stessi file si bloccano a vicenda.
    lucchetto = paths.data_dir() / "aggiornamento.lock"
    if lucchetto.exists():
        try:
            eta = time.time() - lucchetto.stat().st_mtime
        except OSError:
            eta = DURATA_LUCCHETTO + 1
        if eta < DURATA_LUCCHETTO:
            return {
                "ok": False,
                "mode": "bundle",
                "error": f"un aggiornamento è già in corso da {int(eta)} secondi",
            }

    # L'aggiornatore va lanciato da **una copia**, non da dove sta installato:
    # fra i file da sostituire c'è anche lui, e su Windows un eseguibile in
    # esecuzione è bloccato. La prima versione di Trip Finder si riscriveva
    # addosso e moriva con «Il file è utilizzato da un altro processo».
    try:
        temporaneo = Path(tempfile.mkdtemp(prefix="dealfinder-agg-"))
        copia = temporaneo / aggiornatore.name
        shutil.copy2(aggiornatore, copia)
        # E insieme a lui `_internal`. Il caricatore di PyInstaller cerca lì
        # `python311.dll` **prima** che Python parta: copiando il solo
        # eseguibile muore con «Failed to load Python DLL» e non arriva nemmeno
        # alla prima riga di codice, quindi non lascia traccia nemmeno nel log.
        interno = aggiornatore.parent / "_internal"
        if interno.is_dir():
            shutil.copytree(interno, temporaneo / "_internal")
    except OSError as exc:
        return {
            "ok": False,
            "mode": "bundle",
            "error": f"non riesco a preparare l'aggiornatore: {exc}",
        }

    # Il lucchetto dice al programma «non partire, sto lavorando»: senza, un
    # doppio click durante l'aggiornamento riaprirebbe l'eseguibile vecchio e
    # ne bloccherebbe la sostituzione a metà.
    lucchetto.write_text(str(os.getpid()), encoding="utf-8")

    subprocess.Popen(
        [
            str(copia),
            "--zip",
            str(archivio),
            "--dest",
            str(dest),
            "--exe",
            str(Path(sys.executable).resolve()),
            "--pid",
            str(os.getpid()),
            "--temporaneo",
            str(temporaneo),
        ],
        cwd=str(temporaneo),
        close_fds=True,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    logger.info("aggiornatore avviato, mi spengo")
    # Un istante di respiro perché la pagina riceva la risposta prima che il
    # server chiuda: altrimenti il browser vede la connessione cadere e scrive
    # "errore" proprio mentre tutto sta andando bene.
    threading.Timer(1.5, _esci).start()
    return {"ok": True, "mode": "bundle", "version": dati.get("tag_name")}


def _esci() -> None:
    if _spegnimento is not None:
        _spegnimento()
    else:  # da sorgente non c'è nessun lanciatore che ci ascolti
        os._exit(0)


# --------------------------------------------------------------------------- #
# Punto d'ingresso unico
# --------------------------------------------------------------------------- #


def do_update(
    *, root: str | None = None, runner: Callable | None = None, fetch: Callable | None = None
) -> dict:
    """Aggiorna l'installazione, nel modo giusto per come è installata."""
    if is_frozen():
        return _installa_bundle(fetch=fetch)

    root = root or _project_root()
    if not is_git_clone(root):
        return {
            "ok": False,
            "mode": "zip",
            "url": RELEASES_URL,
            "message": "Installazione senza git: scarica l'ultima release dal link.",
        }
    runner = runner or _default_runner
    try:
        out1 = runner(["git", "-C", root, "pull", "--ff-only"])
        out2 = runner(
            [sys.executable, "-m", "pip", "install", "-r", os.path.join(root, "requirements.txt")]
        )
        return {"ok": True, "mode": "git", "log": f"{out1}\n{out2}".strip()}
    except Exception as e:
        return {"ok": False, "mode": "git", "error": str(e)}

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

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

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
    return bool(parti) and parti[0].casefold() in DA_NON_TOCCARE


def valida_archivio(percorso: Path) -> str:
    """Controlla percorsi Windows, struttura e CRC prima di fermare l'app.

    Restituisce il prefisso della cartella del bundle (oppure stringa vuota).
    Non estrae e non modifica l'installazione.
    """
    with zipfile.ZipFile(percorso) as archivio:
        nomi: set[str] = set()
        file: dict[str, zipfile.ZipInfo] = {}
        riservati = {"con", "prn", "aux", "nul"} | {
            f"{tipo}{n}" for tipo in ("com", "lpt") for n in range(1, 10)
        }
        for voce in archivio.infolist():
            nome = voce.filename
            parti = nome.rstrip("/").split("/")
            if (
                not nome
                or "\\" in nome
                or "\x00" in nome
                or nome.startswith("/")
                or any(
                    p in ("", ".", "..")
                    or ":" in p
                    or p.rstrip(" .") != p
                    or p.split(".")[0].casefold() in riservati
                    for p in parti
                )
                or stat.S_ISLNK(voce.external_attr >> 16)
            ):
                raise ValueError(f"percorso ZIP non sicuro: {nome!r}")
            normalizzato = "/".join(parti).casefold()
            if normalizzato in nomi:
                raise ValueError(f"percorso ZIP duplicato: {nome!r}")
            nomi.add(normalizzato)
            if not voce.is_dir():
                file[normalizzato] = voce
        prefisso = "dealfinder/" if "dealfinder/dealfinder.exe" in file else ""
        richiesti = ("dealfinder.exe", "aggiorna.exe", "leggimi.txt")
        if any(
            prefisso + nome not in file or file[prefisso + nome].file_size == 0
            for nome in richiesti
        ) or not any(
            nome.startswith(prefisso + "_internal/") and voce.file_size > 0
            for nome, voce in file.items()
        ):
            raise ValueError(
                "bundle incompleto: servono DealFinder.exe, Aggiorna.exe, LEGGIMI.txt e _internal"
            )
        if prefisso and any(not nome.startswith(prefisso) for nome in file):
            raise ValueError("file esterni alla cartella DealFinder nel bundle")
        # Un file non può essere anche il genitore di un altro percorso.
        for nome in nomi:
            if any(
                str(genitore) in file
                for genitore in PurePosixPath(nome).parents
                if str(genitore) != "."
            ):
                raise ValueError(f"conflitto fra file e cartella nello ZIP: {nome}")
        corrotto = archivio.testzip()
        if corrotto:
            raise ValueError(f"CRC non valido nello ZIP: {corrotto}")
        if prefisso:
            return next(
                voce.filename.split("/")[0]
                for voce in file.values()
                if voce.filename.casefold().startswith(prefisso)
            )
        return ""


def estrai_bundle(archivio: Path, destinazione: Path) -> Path:
    prefisso = valida_archivio(archivio)
    destinazione.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archivio) as zip_bundle:
        zip_bundle.extractall(destinazione)
    return destinazione / prefisso if prefisso else destinazione


class ErroreSincronizzazione(RuntimeError):
    """Fallimento con esito esplicito del ripristino e backup recuperabile."""

    def __init__(self, causa: Exception, *, ripristinato: bool, backup: Path | None):
        self.ripristinato = ripristinato
        self.backup = backup
        dettaglio = (
            "versione precedente ripristinata"
            if ripristinato
            else f"ripristino incompleto; backup: {backup}"
        )
        super().__init__(f"{causa}; {dettaglio}")


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

    Salva tutti i file da sostituire prima della prima scrittura. Se una copia
    fallisce, ripristina anche il file parzialmente scritto e rimuove soltanto
    i nuovi file della transazione. I file estranei e i dati restano intatti.
    Il backup con registro viene conservato se il ripristino non riesce.
    """
    if not origine.exists():
        raise FileNotFoundError(f"non trovo la cartella da installare: {origine}")
    destinazione.mkdir(parents=True, exist_ok=True)

    origine = origine.resolve()
    destinazione = destinazione.resolve()
    piano: list[tuple[Path, Path, Path, bool]] = []
    for file in sorted(origine.rglob("*")):
        if file.is_symlink():
            raise ValueError(f"link non ammesso nel bundle: {file}")
        if not file.is_file():
            continue
        relativo = file.relative_to(origine)
        if _protetto(relativo):
            continue
        arrivo = destinazione / relativo
        if not arrivo.resolve().is_relative_to(destinazione) or arrivo.is_symlink():
            raise ValueError(f"percorso di installazione non sicuro: {arrivo}")
        if _e_questo_programma(arrivo):
            continue
        if arrivo.exists() and not arrivo.is_file():
            raise ValueError(f"un file del bundle incontra una cartella: {arrivo}")
        piano.append((file, arrivo, relativo, arrivo.exists()))

    backup = Path(tempfile.mkdtemp(prefix=f".{destinazione.name}-backup-", dir=destinazione.parent))
    if backup.resolve().parent != destinazione.parent:
        raise ValueError(f"backup fuori dalla cartella prevista: {backup}")
    toccati: list[tuple[Path, Path, bool]] = []
    cartelle_nuove: set[Path] = set()
    try:
        registro = {
            "destinazione": str(destinazione),
            "file": [{"percorso": str(rel), "esisteva": esisteva} for _, _, rel, esisteva in piano],
        }
        (backup / "registro.json").write_text(json.dumps(registro, indent=2), encoding="utf-8")
        # Nessuna sostituzione finché TUTTI i backup non sono disponibili.
        for _, arrivo, relativo, esisteva in piano:
            if esisteva:
                salvato = backup / "file" / relativo
                salvato.parent.mkdir(parents=True, exist_ok=True)
                _copia_insistendo(arrivo, salvato)
        for file, arrivo, relativo, esisteva in piano:
            genitore = arrivo.parent
            while not genitore.exists() and genitore != destinazione:
                cartelle_nuove.add(genitore)
                genitore = genitore.parent
            arrivo.parent.mkdir(parents=True, exist_ok=True)
            # Registrare PRIMA della copia: copy2 può scrivere e poi fallire.
            toccati.append((arrivo, relativo, esisteva))
            with (backup / "toccati.jsonl").open("a", encoding="utf-8") as registro_toccati:
                registro_toccati.write(
                    json.dumps({"percorso": str(relativo), "esisteva": esisteva}) + "\n"
                )
                registro_toccati.flush()
                os.fsync(registro_toccati.fileno())
            _copia_insistendo(file, arrivo)
    except Exception as exc:
        errori = []
        for arrivo, relativo, esisteva in reversed(toccati):
            try:
                if esisteva:
                    _copia_insistendo(backup / "file" / relativo, arrivo)
                else:
                    for attesa in (*ATTESE, None):
                        try:
                            arrivo.unlink(missing_ok=True)
                            break
                        except PermissionError:
                            if attesa is None:
                                raise
                            time.sleep(attesa)
            except OSError as errore:
                errori.append(f"{arrivo}: {errore}")
        for cartella in sorted(cartelle_nuove, key=lambda p: len(p.parts), reverse=True):
            try:
                cartella.rmdir()
            except OSError:
                pass  # una cartella non vuota non va cancellata
        if errori:
            try:
                (backup / "RIPRISTINO_FALLITO.txt").write_text(
                    "Non avviare questa installazione prima del recupero.\n"
                    "Conservare tutta questa cartella. registro.json elenca i file previsti; "
                    "toccati.jsonl elenca quelli interessati dalle copie.\n"
                    "file/ contiene le copie originali da ripristinare nella destinazione "
                    "indicata dal registro. I file toccati con esisteva=false erano assenti "
                    "e vanno rimossi. Non cancellare altri file o dati.\n\n" + "\n".join(errori),
                    encoding="utf-8",
                )
            except OSError:
                pass  # il disco può essere pieno: preservare comunque backup e registro
        else:
            shutil.rmtree(backup, ignore_errors=True)
        raise ErroreSincronizzazione(
            exc, ripristinato=not errori, backup=backup if errori else None
        ) from exc
    shutil.rmtree(backup, ignore_errors=True)
    return len(piano)

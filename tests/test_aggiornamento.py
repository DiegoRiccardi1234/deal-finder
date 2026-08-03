"""La copia che sostituisce l'installazione, e cosa deve non toccare.

Le regole qui sotto vengono da un aggiornamento fallito davvero su Trip Finder,
il 2026-08-01: l'aggiornatore stava riscrivendo sé stesso mentre girava, e
Windows glielo ha impedito con `PermissionError [WinError 32]` lasciando
l'installazione a metà. Un aggiornamento che fallisce a metà è peggio di uno
che non parte.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

import update_sync


def _albero(radice: Path, file: dict[str, str]) -> None:
    for percorso, contenuto in file.items():
        destinazione = radice / percorso
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        destinazione.write_text(contenuto, encoding="utf-8")


def test_i_dati_dell_utente_non_si_toccano(tmp_path) -> None:
    """Il database, i preferiti e le chiavi sono suoi. Un aggiornamento che li
    sovrascrive gli fa perdere lo storico per aggiungere una funzione."""
    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(
        nuovo,
        {
            "DealFinder.exe": "versione nuova",
            "_internal/libreria.dll": "nuova",
            "data/deal_finder.db": "QUESTO NON DEVE ARRIVARE",
        },
    )
    _albero(
        installato,
        {
            "DealFinder.exe": "versione vecchia",
            "data/deal_finder.db": "il database dell'utente",
            "data/local_secrets.json": "le sue chiavi",
            ".streamlit/secrets.toml": "i suoi secrets",
        },
    )

    scritti = update_sync.sincronizza(origine=nuovo, destinazione=installato)

    assert scritti == 2  # l'eseguibile e la libreria, non il database
    assert (installato / "DealFinder.exe").read_text(encoding="utf-8") == "versione nuova"
    assert (installato / "data" / "deal_finder.db").read_text(
        encoding="utf-8"
    ) == "il database dell'utente"
    assert (installato / "data" / "local_secrets.json").exists()
    assert (installato / ".streamlit" / "secrets.toml").read_text(
        encoding="utf-8"
    ) == "i suoi secrets"


def test_non_si_riscrive_addosso(tmp_path, monkeypatch) -> None:
    """È il difetto che ha fatto fallire l'aggiornamento del 2026-08-01: fra i
    file da sostituire c'è l'aggiornatore stesso, e su Windows un eseguibile in
    esecuzione è bloccato."""
    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(nuovo, {"Aggiorna.exe": "nuovo", "DealFinder.exe": "nuovo"})
    _albero(installato, {"Aggiorna.exe": "quello che sta girando"})
    monkeypatch.setattr(sys, "executable", str(installato / "Aggiorna.exe"))

    update_sync.sincronizza(origine=nuovo, destinazione=installato)

    assert (installato / "Aggiorna.exe").read_text(encoding="utf-8") == "quello che sta girando"
    assert (installato / "DealFinder.exe").read_text(encoding="utf-8") == "nuovo"


def test_un_file_bloccato_si_riprova_prima_di_arrendersi(tmp_path, monkeypatch) -> None:
    """Defender su un archivio appena scompattato può tenersi un file dieci o
    venti secondi. Arrendersi al primo tentativo vuol dire fallire per una
    scansione."""
    tentativi = {"n": 0}
    vero = update_sync.shutil.copy2

    def a_volte(origine, destinazione):
        tentativi["n"] += 1
        if tentativi["n"] < 3:
            raise PermissionError(13, "occupato")
        return vero(origine, destinazione)

    monkeypatch.setattr(update_sync.shutil, "copy2", a_volte)
    monkeypatch.setattr(update_sync.time, "sleep", lambda _s: None)

    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(nuovo, {"DealFinder.exe": "nuovo"})
    installato.mkdir()

    assert update_sync.sincronizza(origine=nuovo, destinazione=installato) == 1
    assert tentativi["n"] == 3


def test_se_resta_bloccato_lo_dice_col_nome_del_file(tmp_path, monkeypatch) -> None:
    """Il messaggio generico non serve a nessuno: sapere quale file è rimasto
    bloccato dice se è l'eseguibile, una libreria o l'antivirus."""

    def sempre_occupato(origine, destinazione):
        raise PermissionError(13, "occupato")

    monkeypatch.setattr(update_sync.shutil, "copy2", sempre_occupato)
    monkeypatch.setattr(update_sync.time, "sleep", lambda _s: None)

    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(nuovo, {"_internal/bloccata.dll": "x"})
    installato.mkdir()

    with pytest.raises(PermissionError, match="bloccata.dll"):
        update_sync.sincronizza(origine=nuovo, destinazione=installato)


def test_senza_la_cartella_di_partenza_non_si_finge_di_aggiornare(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        update_sync.sincronizza(origine=tmp_path / "manca", destinazione=tmp_path / "dove")


def test_da_sorgente_il_bundle_non_si_installa(monkeypatch) -> None:
    """Fuori dal bundle `do_update` non deve prendere la strada dell'exe.

    È la stessa distinzione che prima non c'era: chi ha una copia git si
    aggiorna con `git pull`, e sovrascrivergli i file sarebbe un ottimo modo per
    fargli perdere del lavoro.
    """
    from offerte import update

    monkeypatch.setattr(update.paths, "is_frozen", lambda: False)
    monkeypatch.setattr(update, "is_git_clone", lambda root=None: False)

    esito = update.do_update()

    assert esito["mode"] == "zip"
    assert esito["ok"] is False


def test_il_bundle_stagia_l_aggiornatore_in_temp(tmp_path, monkeypatch) -> None:
    """La parte che a mano non si vede: cosa viene copiato in `%TEMP%`.

    `Aggiorna.exe` non si lancia da dove è installato — è uno dei file da
    sostituire, e su Windows un eseguibile in esecuzione è bloccato. E non basta
    copiare lui: il caricatore di PyInstaller cerca `python311.dll` in
    `_internal` **prima** che Python parta, quindi senza quella cartella muore
    con «Failed to load Python DLL» senza lasciare traccia nemmeno nel log.
    """
    from offerte import update

    installato = tmp_path / "installato"
    (installato / "_internal").mkdir(parents=True)
    (installato / "_internal" / "python311.dll").write_text("dll", encoding="utf-8")
    (installato / "Aggiorna.exe").write_text("aggiornatore", encoding="utf-8")
    (installato / "DealFinder.exe").write_text("app", encoding="utf-8")

    dati_utente = tmp_path / "dati"
    dati_utente.mkdir()

    monkeypatch.setattr(update.paths, "is_frozen", lambda: True)
    monkeypatch.setattr(update.paths, "data_dir", lambda: dati_utente)
    monkeypatch.setattr(update.sys, "executable", str(installato / "DealFinder.exe"))

    class _Risposta:
        content = b"zip finto"

        @staticmethod
        def json():
            return {
                "tag_name": "v9.9.9",
                "assets": [{"name": update.ASSET, "browser_download_url": "http://esempio/z.zip"}],
            }

    lanciato: dict = {}
    monkeypatch.setattr(
        update.subprocess, "Popen", lambda cmd, **kw: lanciato.setdefault("cmd", cmd)
    )
    monkeypatch.setattr(
        update.threading, "Timer", lambda *a, **k: type("T", (), {"start": lambda s: None})()
    )

    esito = update.do_update(fetch=lambda url, headers: _Risposta())

    assert esito["ok"] is True and esito["version"] == "v9.9.9"
    assert (dati_utente / "aggiornamento.lock").exists(), (
        "senza lucchetto un doppio click riapre il vecchio"
    )

    argomenti = lanciato["cmd"]
    temporaneo = Path(argomenti[argomenti.index("--temporaneo") + 1])
    assert (temporaneo / "Aggiorna.exe").exists()
    assert (temporaneo / "_internal" / "python311.dll").exists(), (
        "senza _internal l'aggiornatore muore prima di partire, e in silenzio"
    )
    assert argomenti[argomenti.index("--dest") + 1] == str(installato)
    assert argomenti[argomenti.index("--pid") + 1] == str(os.getpid())

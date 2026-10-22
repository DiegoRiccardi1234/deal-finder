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
import io
import zipfile
from pathlib import Path

import pytest

import update_sync


def _albero(radice: Path, file: dict[str, str]) -> None:
    for percorso, contenuto in file.items():
        destinazione = radice / percorso
        destinazione.parent.mkdir(parents=True, exist_ok=True)
        destinazione.write_text(contenuto, encoding="utf-8")


def _zip_bundle_bytes(extra: dict[str, str] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archivio:
        for nome, contenuto in {
            "DealFinder/DealFinder.exe": "eseguibile nuovo",
            "DealFinder/Aggiorna.exe": "updater nuovo",
            "DealFinder/LEGGIMI.txt": "istruzioni",
            "DealFinder/_internal/python311.dll": "runtime nuovo",
            **(extra or {}),
        }.items():
            archivio.writestr(nome, contenuto)
    return buffer.getvalue()


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

    with pytest.raises(update_sync.ErroreSincronizzazione, match="bloccata.dll"):
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
        content = _zip_bundle_bytes()

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


def test_rollback_dopo_prima_sostituzione_e_seconda_copia_parziale(tmp_path, monkeypatch):
    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(nuovo, {"a.exe": "nuovo a", "b.dll": "nuovo b", "z.dll": "nuova aggiunta"})
    _albero(installato, {"a.exe": "vecchio a", "b.dll": "vecchio b", "estraneo.txt": "mio"})
    copia = update_sync.shutil.copy2
    sostituiti = []

    def guasto(origine, destinazione):
        if origine.parent == nuovo:
            sostituiti.append(origine.name)
            if origine.name == "b.dll":
                destinazione.write_text("copia troncata", encoding="utf-8")
                raise OSError("disco interrotto")
        return copia(origine, destinazione)

    monkeypatch.setattr(update_sync.shutil, "copy2", guasto)
    with pytest.raises(update_sync.ErroreSincronizzazione) as errore:
        update_sync.sincronizza(origine=nuovo, destinazione=installato)
    assert sostituiti == ["a.exe", "b.dll"]
    assert errore.value.ripristinato is True
    assert errore.value.backup is None
    assert (installato / "a.exe").read_text() == "vecchio a"
    assert (installato / "b.dll").read_text() == "vecchio b"
    assert (installato / "estraneo.txt").read_text() == "mio"
    assert not (installato / "z.dll").exists()
    assert not list(tmp_path.glob(".installato-backup-*"))


def test_rollback_rimuove_solo_i_file_appena_aggiunti(tmp_path, monkeypatch):
    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(nuovo, {"aaa/nuovo.dll": "nuovo", "z.exe": "nuovo"})
    _albero(installato, {"estraneo.txt": "mio"})
    copia = update_sync.shutil.copy2

    def guasto(origine, destinazione):
        if origine.name == "z.exe":
            raise OSError("fallimento")
        return copia(origine, destinazione)

    monkeypatch.setattr(update_sync.shutil, "copy2", guasto)
    with pytest.raises(update_sync.ErroreSincronizzazione) as errore:
        update_sync.sincronizza(origine=nuovo, destinazione=installato)
    assert errore.value.ripristinato
    assert {p.name for p in installato.iterdir()} == {"estraneo.txt"}


def test_backup_fallito_non_modifica_nessun_file_installato(tmp_path, monkeypatch):
    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(nuovo, {"a.exe": "nuovo a", "b.dll": "nuovo b"})
    _albero(installato, {"a.exe": "vecchio a", "b.dll": "vecchio b"})
    copia = update_sync.shutil.copy2
    sostituzioni = []

    def guasto(origine, destinazione):
        if origine.parent == nuovo:
            sostituzioni.append(origine.name)
        if origine == installato / "b.dll":
            raise OSError("backup non scrivibile")
        return copia(origine, destinazione)

    monkeypatch.setattr(update_sync.shutil, "copy2", guasto)
    with pytest.raises(update_sync.ErroreSincronizzazione) as errore:
        update_sync.sincronizza(origine=nuovo, destinazione=installato)
    assert errore.value.ripristinato
    assert sostituzioni == []
    assert (installato / "a.exe").read_text() == "vecchio a"
    assert (installato / "b.dll").read_text() == "vecchio b"


def test_ripristino_fallito_conserva_backup_e_registro(tmp_path, monkeypatch):
    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    _albero(nuovo, {"a.exe": "nuovo a", "b.dll": "nuovo b"})
    _albero(installato, {"a.exe": "vecchio a", "b.dll": "vecchio b"})
    copia = update_sync.shutil.copy2

    def guasto(origine, destinazione):
        if origine == nuovo / "b.dll" or (
            "-backup-" in str(origine) and destinazione == installato / "a.exe"
        ):
            raise OSError("fallita copia o ripristino")
        return copia(origine, destinazione)

    monkeypatch.setattr(update_sync.shutil, "copy2", guasto)
    with pytest.raises(update_sync.ErroreSincronizzazione) as errore:
        update_sync.sincronizza(origine=nuovo, destinazione=installato)
    assert not errore.value.ripristinato
    backup = errore.value.backup
    assert backup and (backup / "registro.json").is_file()
    assert (backup / "file/a.exe").read_text() == "vecchio a"
    assert (backup / "file/b.dll").read_text() == "vecchio b"
    assert "a.exe" in (backup / "RIPRISTINO_FALLITO.txt").read_text()


def test_successo_preserva_segreti_e_file_estranei_e_rimuove_backup(tmp_path):
    nuovo, installato = tmp_path / "nuovo", tmp_path / "installato"
    protetti = {
        "data/db": "db",
        ".env": "chiave",
        ".env.local": "locale",
        ".streamlit/secrets.toml": "segreto",
    }
    _albero(nuovo, {"app.exe": "nuova", **dict.fromkeys(protetti, "sovrascrittura vietata")})
    _albero(installato, {"app.exe": "vecchia", "estraneo.txt": "mio", **protetti})
    assert update_sync.sincronizza(origine=nuovo, destinazione=installato) == 1
    for nome, contenuto in protetti.items():
        assert (installato / nome).read_text() == contenuto
    assert (installato / "estraneo.txt").read_text() == "mio"
    assert not list(tmp_path.glob(".installato-backup-*"))


@pytest.mark.parametrize(
    "malevolo",
    [
        "../fuori.txt",
        "DealFinder/../../fuori.txt",
        "C:/fuori.txt",
        "/fuori.txt",
        "DealFinder/..\\fuori.txt",
        "DealFinder/app.exe:stream",
        "DealFinder/NUL.txt",
        "DealFinder/trailing. /file",
        "DealFinder/./file",
        "DealFinder//file",
        "DealFinder/dealfinder.EXE",
        "estraneo.txt",
        "DealFinder/LEGGIMI.txt/figlio",
    ],
)
def test_archivi_con_percorsi_non_sicuri_vengono_rifiutati(tmp_path, malevolo):
    archivio = tmp_path / "bundle.zip"
    archivio.write_bytes(_zip_bundle_bytes({malevolo: "malevolo"}))
    with pytest.raises(ValueError):
        update_sync.estrai_bundle(archivio, tmp_path / "estratto")
    assert not (tmp_path / "estratto").exists()


def test_archivio_crc_non_valido_non_viene_estratto(tmp_path):
    archivio = tmp_path / "bundle.zip"
    archivio.write_bytes(_zip_bundle_bytes().replace(b"runtime nuovo", b"runtime rotto"))
    with pytest.raises(ValueError, match="CRC"):
        update_sync.estrai_bundle(archivio, tmp_path / "estratto")
    assert not (tmp_path / "estratto").exists()


@pytest.mark.parametrize("contenuto", [b"HTML di errore", b"PK\x03\x04ZIP troncato"])
def test_archivio_non_zip_o_troncato_viene_rifiutato(tmp_path, contenuto):
    archivio = tmp_path / "bundle.zip"
    archivio.write_bytes(contenuto)
    with pytest.raises(zipfile.BadZipFile):
        update_sync.valida_archivio(archivio)


def test_bundle_incompleto_viene_rifiutato(tmp_path):
    archivio = tmp_path / "bundle.zip"
    with zipfile.ZipFile(archivio, "w") as zip_bundle:
        zip_bundle.writestr("DealFinder/DealFinder.exe", "solo exe")
    with pytest.raises(ValueError, match="incompleto"):
        update_sync.valida_archivio(archivio)


def test_launcher_non_ferma_app_se_lo_zip_non_e_valido(tmp_path, monkeypatch):
    from offerte import update

    _albero(tmp_path / "installato", {"Aggiorna.exe": "updater", "DealFinder.exe": "app"})
    monkeypatch.setattr(sys, "executable", str(tmp_path / "installato/DealFinder.exe"))
    monkeypatch.setattr(update.paths, "data_dir", lambda: tmp_path)
    lanci = []
    monkeypatch.setattr(update.subprocess, "Popen", lambda *a, **k: lanci.append(a))
    monkeypatch.setattr(
        update.threading, "Timer", lambda *a, **k: pytest.fail("shutdown non autorizzato")
    )

    class Risposta:
        content = b"non uno ZIP"

        def json(self):
            return {
                "assets": [{"name": update.ASSET, "browser_download_url": "http://esempio/zip"}]
            }

    esito = update._installa_bundle(fetch=lambda *a: Risposta())
    assert not esito["ok"] and "verifica" in esito["error"]
    assert not lanci
    assert not (tmp_path / "aggiornamento.lock").exists()


@pytest.mark.parametrize("ripristinato", [True, False])
def test_updater_non_riavvia_dopo_errore_e_registra_esito_reale(
    tmp_path, monkeypatch, ripristinato
):
    from scripts import updater

    installato = tmp_path / "installato"
    _albero(installato, {"DealFinder.exe": "vecchio", "data/aggiornamento.lock": "1"})
    archivio = tmp_path / "bundle.zip"
    archivio.write_bytes(_zip_bundle_bytes())
    monkeypatch.setattr(sys, "argv", ["updater", "--zip", str(archivio), "--dest", str(installato)])
    monkeypatch.setattr(updater, "_aspetta", lambda *a: None)
    monkeypatch.setattr(updater.time, "sleep", lambda *a: None)
    monkeypatch.setattr(
        updater.subprocess, "Popen", lambda *a, **k: pytest.fail("non riavviare app")
    )
    backup = tmp_path / "backup-da-conservare" if not ripristinato else None

    def fallisce(**kwargs):
        raise update_sync.ErroreSincronizzazione(
            OSError("disco"), ripristinato=ripristinato, backup=backup
        )

    monkeypatch.setattr(updater, "sincronizza", fallisce)
    assert updater.main() == 1
    log = (installato / "data/logs/aggiornamento.log").read_text(encoding="utf-8")
    assert ("resta installata la versione di prima" in log) == ripristinato
    assert ("RIPRISTINO INCOMPLETO" in log) != ripristinato
    assert archivio.exists(), "archivio conservato per il recupero"
    assert not (installato / "data/aggiornamento.lock").exists()


def test_updater_controlla_zip_prima_di_attendere_il_processo(tmp_path, monkeypatch):
    from scripts import updater

    archivio = tmp_path / "bundle.zip"
    archivio.write_bytes(b"non zip")
    installato = tmp_path / "installato"
    monkeypatch.setattr(sys, "argv", ["updater", "--zip", str(archivio), "--dest", str(installato)])
    monkeypatch.setattr(
        updater, "_aspetta", lambda *a: pytest.fail("non attendere su zip invalido")
    )
    monkeypatch.setattr(
        updater.subprocess, "Popen", lambda *a, **k: pytest.fail("non riavviare app")
    )
    assert updater.main() == 1


def test_updater_successo_rimuove_zip_e_lock_e_riavvia(tmp_path, monkeypatch):
    from scripts import updater

    installato = tmp_path / "installato"
    _albero(installato, {"DealFinder.exe": "vecchio", "data/aggiornamento.lock": "1"})
    archivio = tmp_path / "bundle.zip"
    archivio.write_bytes(_zip_bundle_bytes())
    monkeypatch.setattr(sys, "argv", ["updater", "--zip", str(archivio), "--dest", str(installato)])
    monkeypatch.setattr(updater, "_aspetta", lambda *a: None)
    monkeypatch.setattr(updater.time, "sleep", lambda *a: None)
    lanci = []
    monkeypatch.setattr(updater.subprocess, "Popen", lambda *a, **k: lanci.append((a, k)))
    assert updater.main() == 0
    assert len(lanci) == 1
    assert lanci[0][0][0] == [str(installato / "DealFinder.exe")]
    assert not archivio.exists()
    assert not (installato / "data/aggiornamento.lock").exists()
    assert (installato / "DealFinder.exe").read_text() == "eseguibile nuovo"
    assert not list(tmp_path.glob(".installato-backup-*"))

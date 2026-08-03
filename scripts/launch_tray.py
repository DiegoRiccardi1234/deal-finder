"""Avvia Deal Finder senza finestra, con un'icona nell'area di notifica.

Il server è un processo lungo, ma la finestra nera del terminale non serve a
niente: non ci si legge nulla di utile e l'unico modo di fermare il programma
era chiuderla. Qui Streamlit gira su un thread e il thread principale viene
ceduto a `pystray`.

    .venv\\Scripts\\pythonw.exe scripts\\launch_tray.py        senza console
    .venv\\Scripts\\python.exe  scripts\\launch_tray.py --port 9000

Con `pythonw.exe` — e nell'eseguibile costruito con `console=False` — non
esiste nessuna console: `sys.stdout` e `sys.stderr` sono `None` e la prima riga
stampata da una libreria qualunque farebbe morire il processo senza lasciare
traccia. Per questo la prima cosa che accade qui è riparare i due flussi, e per
questo i log vanno su file: un errore d'avvio altrimenti sarebbe invisibile.

Differenza rispetto agli altri Finder, che montano FastAPI: Streamlit non si
avvia passando un oggetto applicazione, ma **rieseguendo uno script**. Da lì
discendono i due vincoli commentati più sotto — `app.py` dev'essere un file
vero su disco, e i gestori di segnale vanno neutralizzati.
"""

from __future__ import annotations

import os
import sys


def _harden_stdio() -> None:
    """Sostituisce con /dev/null i flussi che non si possono usare.

    Non basta che esistano. Un processo riavviato dall'aggiornatore può
    ereditare un descrittore che **sembra** valido — `fileno()` risponde — ma il
    cui handle non lo è più: la prima riga scritta lo fa morire prima ancora
    che apra la porta, e da fuori sembra un aggiornamento che non finisce mai.
    L'unico modo di saperlo è chiedere al sistema con `fstat`.
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        usable = stream is not None
        if usable:
            try:
                descrittore = stream.fileno()
            except Exception:
                # StringIO e simili non hanno un descrittore ma sanno scrivere:
                # vanno lasciati stare (succede sotto pytest).
                usable = hasattr(stream, "write")
            else:
                try:
                    os.fstat(descrittore)
                except OSError:
                    usable = False
        if not usable:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


_harden_stdio()


def _declare_workspace() -> str:
    """Dichiara dove stanno `data/`, il database e i log, **prima** di importare l'app.

    Da sorgente è la radice del progetto. Dentro l'eseguibile PyInstaller no:
    lì i moduli stanno in `_MEIPASS`, una cartella temporanea di sola lettura
    che sparisce alla chiusura, e il database finirebbe dentro quella. Il
    workspace vero è la cartella accanto a `DealFinder.exe`, così chi scompatta
    lo zip trova i suoi dati dove si aspetta di trovarli.

    Va fatto qui e non in `offerte.paths`, che la legge: i consumatori si
    risolvono i percorsi a tempo di import, quindi quando importano la
    variabile deve esserci già.
    """
    from pathlib import Path as _Path

    if os.environ.get("DEAL_FINDER_WORKSPACE"):
        return os.environ["DEAL_FINDER_WORKSPACE"]
    if getattr(sys, "frozen", False):
        workspace = _Path(sys.executable).resolve().parent
    else:
        workspace = _Path(__file__).resolve().parent.parent
    os.environ["DEAL_FINDER_WORKSPACE"] = str(workspace)
    return str(workspace)


_declare_workspace()

if not getattr(sys, "frozen", False):
    # Da sorgente questo file sta in `scripts/`, che non è la radice: senza
    # questo `import offerte` fallisce. Nel bundle i pacchetti sono già
    # importabili e la radice come cartella non esiste.
    sys.path.insert(0, os.environ["DEAL_FINDER_WORKSPACE"])

import argparse  # noqa: E402
import logging  # noqa: E402
import socket  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.error  # noqa: E402
import urllib.request  # noqa: E402
import webbrowser  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(os.environ["DEAL_FINDER_WORKSPACE"])
LOG_FILE = ROOT / "data" / "logs" / "deal-finder.log"

#: Porta di partenza: quella di default di Streamlit, così l'indirizzo è quello
#: che l'utente ha già visto in sviluppo e nella documentazione.
DEFAULT_PORT = 8501
#: Quante provarne prima di arrendersi.
PORT_SPAN = 20
#: Percorso di salute di Streamlit: risponde `ok` e non richiede una sessione.
#: Serve a distinguere «c'è già un Deal Finder» da «c'è un altro server».
PROBE_PATH = "/_stcore/health"


def _setup_logging() -> None:
    """Log su file, deciso **prima** di importare l'app.

    Streamlit chiama `logging.basicConfig`, che non fa niente se la radice ha
    già un handler: registrando il nostro per primo i log finiscono nel file
    invece che in un flusso che qui non esiste.
    """
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def is_free(port: int) -> bool:
    """Vero se qualcuno può ancora mettersi in ascolto su questa porta."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def pick_port(start: int = DEFAULT_PORT, span: int = PORT_SPAN) -> int:
    """La prima porta libera da `start` in su.

    La porta può essere occupata da un altro programma o da un Deal Finder
    rimasto aperto: meglio spostarsi che morire con "address already in use".
    Se sono tutte occupate si torna alla prima e sarà Streamlit a spiegare
    perché non parte, nel file di log.
    """
    for port in range(start, start + span + 1):
        if is_free(port):
            return port
    return start


def deal_finder_at(port: int, timeout: float = 1.5) -> bool:
    """Vero se su questa porta c'è già un Deal Finder vivo (non un altro server)."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}{PROBE_PATH}", timeout=timeout
        ) as answer:
            return answer.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def open_when_ready(url: str, port: int, wait: float = 90.0) -> None:
    """Apre il browser quando il server risponde davvero.

    Al primo avvio Streamlit ci mette qualche secondo a caricare i propri
    moduli, e una pagina aperta troppo presto mostra un errore di connessione
    che sembra un guasto e non lo è.
    """
    scadenza = time.monotonic() + wait
    while time.monotonic() < scadenza:
        if deal_finder_at(port, timeout=3.0):
            webbrowser.open(url)
            return
        time.sleep(0.7)
    logging.getLogger(__name__).error("il server non ha risposto entro %.0f s", wait)


def copy_to_clipboard(text: str) -> None:
    """Appunti di Windows via `clip.exe`, senza dipendenze e senza finestre."""
    try:
        subprocess.run(
            ["clip"],
            input=text,
            text=True,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError:
        logging.getLogger(__name__).warning("appunti non disponibili", exc_info=True)


def _icon_image():
    """Il cartellino del prezzo, disegnato a runtime: nessun .ico da versionare.

    Terracotta su crema, gli stessi due colori del design system in
    `styles.css`: nell'area di notifica l'icona dev'essere riconoscibile come
    questa applicazione anche a 16 pixel.
    """
    from PIL import Image, ImageDraw

    size = 64
    terracotta = (160, 63, 40, 255)
    crema = (250, 249, 246, 255)
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    # Il cartellino: un quadrato con l'angolo in alto a destra tagliato.
    draw.polygon(
        [(6, 22), (22, 6), (58, 6), (58, 42), (42, 58), (6, 58)],
        fill=terracotta,
    )
    # Il foro per il filo.
    draw.ellipse((42, 12, 54, 24), fill=crema)
    # Un taglio di prezzo stilizzato: la barra diagonale.
    draw.line([(16, 46), (40, 22)], fill=crema, width=5)
    return image


def _spegni_ordinato() -> None:
    """Chiude le connessioni SQLite e poi il processo.

    Streamlit non offre uno spegnimento pulito paragonabile a
    `Server.should_exit` di uvicorn: il suo server vive dentro un event loop su
    un altro thread e fermarlo da fuori è più fragile che uscire. Quello che
    **non** si può saltare è il rilascio di SQLite: senza, il `-wal` resta sul
    disco e sarà lui a far fallire la sostituzione dei file al prossimo
    aggiornamento.
    """
    try:
        from offerte import db

        db.close_all()
    except Exception:
        logging.getLogger(__name__).warning("chiusura del database non riuscita", exc_info=True)
    os._exit(0)


def run_tray(url: str) -> None:
    """Icona e menu. Se `pystray` manca, il server resta comunque in servizio."""
    log = logging.getLogger(__name__)
    try:
        import pystray
    except Exception:
        # Senza console un'eccezione qui non si vedrebbe: meglio restare vivi e
        # servire il sito, che è il motivo per cui il programma esiste.
        log.exception("pystray non disponibile: niente icona, il server resta acceso")
        threading.Event().wait()
        return

    def on_open(_icon=None, _item=None) -> None:
        webbrowser.open(url)

    def on_copy(_icon=None, _item=None) -> None:
        copy_to_clipboard(url)

    def on_quit(icon, _item=None) -> None:
        icon.stop()
        _spegni_ordinato()

    menu = pystray.Menu(
        pystray.MenuItem("Apri Deal Finder", on_open, default=True),
        pystray.MenuItem("Copia indirizzo", on_copy),
        pystray.MenuItem("Esci", on_quit),
    )
    try:
        icon = pystray.Icon("DealFinder", _icon_image(), f"Deal Finder — {url}", menu)
        icon.run()
    except Exception:
        log.exception("l'icona non è partita: il server resta acceso")
        threading.Event().wait()


def _avvia_streamlit(port: int) -> threading.Thread:
    """Streamlit su un thread, con i due aggiustamenti che il thread impone.

    1. `bootstrap.run` installa gestori per SIGINT/SIGTERM, e `signal.signal`
       **funziona solo sul thread principale**: chiamato altrove solleva
       `ValueError` e il server muore appena avviato. Qui il thread principale
       serve all'icona, quindi il gestore va neutralizzato — e possiamo
       permettercelo proprio perché lo spegnimento lo governiamo noi dal menu.
    2. `main_script_path` dev'essere un file `.py` **vero su disco**: Streamlit
       non importa un modulo, riesegue lo script a ogni interazione. Nel bundle
       `app.py` è quindi spedito come file, non congelato dentro l'eseguibile.
    3. `bootstrap.run` **non applica** le opzioni che gli passi: le sorveglia
       soltanto. Ad applicarle è `load_config_options`, che la riga di comando
       chiama un attimo prima. Saltarla non dà nessun errore — il server parte
       e basta, sulla porta di default e con le impostazioni sbagliate. È il
       genere di difetto che si vede solo guardando l'indirizzo.
    """
    from streamlit import config as st_config
    from streamlit.web import bootstrap

    bootstrap._set_up_signal_handler = lambda _server: None  # type: ignore[attr-defined]

    from offerte import paths

    script = paths.bundle_dir() / "app.py"
    if not script.is_file():
        raise SystemExit(f"app.py non trovato in {script.parent}")

    flag_options = {
        "server.port": port,
        "server.address": "127.0.0.1",
        # Il browser lo apriamo noi, quando il server risponde davvero.
        "server.headless": True,
        # Nel bundle gli script stanno in una cartella temporanea che non
        # cambia mai: sorvegliarla costa e non serve.
        "server.fileWatcherType": "none",
        "browser.gatherUsageStats": False,
        "global.developmentMode": False,
    }

    # Come fa `streamlit run`: prima si dichiara qual è lo script principale
    # (serve a trovare `.streamlit/config.toml` e i secrets accanto a lui), poi
    # si applicano le opzioni.
    st_config._main_script_path = str(script)
    bootstrap.load_config_options(flag_options=flag_options)

    thread = threading.Thread(
        target=bootstrap.run,
        args=(str(script), False, [], flag_options),
        name="streamlit",
        daemon=True,
    )
    thread.start()
    return thread


def main() -> int:
    parser = argparse.ArgumentParser(description="Deal Finder con icona nell'area di notifica")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help=f"porta fissa (default: prima libera da {DEFAULT_PORT})",
    )
    parser.add_argument("--no-browser", action="store_true", help="non aprire il browser all'avvio")
    args = parser.parse_args()

    _setup_logging()
    log = logging.getLogger(__name__)

    # Aggiornamento in corso: l'eseguibile vecchio non deve ripartire, o
    # riblocca i file che l'aggiornatore sta sostituendo e la copia muore a
    # metà. Il lucchetto vecchio non conta: vorrebbe dire che l'aggiornatore è
    # morto, e restare chiusi per sempre sarebbe peggio.
    lucchetto = ROOT / "data" / "aggiornamento.lock"
    if not os.environ.get("DEAL_FINDER_AGGIORNATO"):
        try:
            eta = time.time() - lucchetto.stat().st_mtime
        except OSError:
            eta = None
        if eta is not None and eta < 900:
            log.info("aggiornamento in corso da %.0fs: non parto", eta)
            return 0

    # Se Deal Finder gira già, non se ne avvia un secondo: si apre quello. È
    # anche la guardia contro il doppio click ripetuto sull'icona di avvio.
    # Chi chiede una porta esplicita guarda solo quella.
    da_controllare = [args.port] if args.port else range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN + 1)
    for port in da_controllare:
        if is_free(port):
            continue
        if deal_finder_at(port):
            log.info("Deal Finder è già in ascolto sulla %d: apro il browser", port)
            webbrowser.open(f"http://127.0.0.1:{port}/")
            return 0

    port = args.port if args.port else pick_port()
    url = f"http://127.0.0.1:{port}/"

    _avvia_streamlit(port)

    # L'aggiornatore ha bisogno che questo processo esca **e molli SQLite**
    # prima di sostituire i file. Senza questa registrazione userebbe un
    # `os._exit` a freddo, lasciando il `-wal` sul disco: e sarebbe poi quel
    # file bloccato a far fallire la copia, con l'aggiornamento che «parte e non
    # finisce» invece di dare un errore.
    from offerte import update

    update.register_shutdown(_spegni_ordinato)
    log.info("Deal Finder su %s", url)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()

    run_tray(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

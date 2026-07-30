"""Logging centralizzato.

Usa `get_logger(__name__)` nei moduli invece di `print`. `configure_logging()` va
chiamata una volta all'avvio (`app.py` per la UI, `offerte/cli.py` per la CLI).

Perché non `print`: il motore stampava tutto su stdout senza livelli né nome del
modulo, quindi era impossibile distinguere una fonte *bloccata* da una fonte
*senza risultati*, filtrare il rumore, o dirottare la diagnostica su file. In più
`print` da un thread dello `ThreadPoolExecutor` si mescola all'output degli altri
tredici scraper.

Solo stdlib, nessun import interno: importabile da qualunque modulo senza rischio
di cicli.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_CONFIGURED = False

LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%H:%M:%S"


def configure_logging(log_dir: Path | str | None = None, level: str | int | None = None) -> None:
    """Configura il root logger. Idempotente: chiamarla più volte è sicuro.

    Streamlit ri-esegue lo script a ogni interazione, quindi la guardia non è un
    vezzo: senza, ogni rerun aggiungerebbe un handler e le righe verrebbero
    stampate N volte.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    resolved = _resolve_level(level)
    root = logging.getLogger()
    root.setLevel(resolved)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    # stderr, non stdout: su Streamlit Cloud stdout è già usato per il protocollo
    # e in CLI lascia stdout libero per i risultati (utile con `--export csv`).
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.setLevel(resolved)
    root.addHandler(stream)

    if log_dir is not None:
        try:
            path = Path(log_dir)
            path.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                path / "deal-finder.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            file_handler.setLevel(resolved)
            root.addHandler(file_handler)
        except OSError:
            # Un filesystem in sola lettura (container) non deve impedire l'avvio.
            pass

    # Librerie verbose: il loro per-request chatter sommergerebbe il nostro.
    for noisy in ("httpx", "httpcore", "urllib3", "openai", "anthropic", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def _resolve_level(level: str | int | None) -> int:
    if level is None:
        level = os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(level, int):
        return level
    return getattr(logging, str(level).upper(), logging.INFO)


def get_logger(name: str) -> logging.Logger:
    """Logger con scope di modulo. `configure_logging()` va chiamata una volta."""
    return logging.getLogger(name)

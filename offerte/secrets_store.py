"""Le chiavi che l'utente incolla nell'interfaccia, salvate su questo computer.

Esiste perché nel bundle Windows non c'è nessun `secrets.toml` da editare, e
chiedere a chi ha appena scompattato uno zip di aprire un file TOML dentro una
cartella nascosta era l'ostacolo più grosso fra lui e l'IA accesa.

Tre regole, tutte e tre volute:

- **Il file vince sulle variabili d'ambiente e su `secrets.toml`.** Se l'utente
  ha appena scritto una chiave nel pannello, quella è la sua ultima parola: un
  `.env` dimenticato che la scavalca è un difetto che non si spiega.
- **Stringa vuota = cancella.** Serve un modo per togliere una chiave dal
  pannello, e «salva il campo vuoto» è quello che chiunque prova per primo.
- **Non si restituisce mai una chiave.** `status()` dà booleani. Una chiave
  rimessa a schermo, anche mascherata, è una chiave che finisce in uno
  screenshot.

Vive accanto all'eseguibile (`data/local_secrets.json`), quindi l'aggiornatore
non la tocca: `data` è in `DA_NON_TOCCARE`.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from offerte import paths
from offerte.providers import PROVIDERS

logger = logging.getLogger(__name__)

NOME_FILE = "local_secrets.json"

#: Cosa si può scrivere da qui, elencato apposta invece di accettare qualunque
#: nome: il pannello parla con un file su disco, e una lista aperta vorrebbe
#: dire che una chiave sbagliata nel payload diventa una variabile d'ambiente.
CAMPI_AMMESSI: tuple[str, ...] = tuple(
    dict.fromkeys(
        [cfg.key_env for cfg in PROVIDERS.values()] + ["AI_PROVIDER", "EBAY_APP_ID", "EBAY_CERT_ID"]
    )
)


def percorso():
    return paths.data_dir() / NOME_FILE


def carica() -> dict[str, str]:
    """Il contenuto del file, o vuoto se non c'è o è illeggibile."""
    file = percorso()
    if not file.is_file():
        return {}
    try:
        dati = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("local_secrets.json illeggibile: lo ignoro", exc_info=True)
        return {}
    if not isinstance(dati, dict):
        return {}
    return {k: str(v) for k, v in dati.items() if k in CAMPI_AMMESSI and v}


def salva(campo: str, valore: str) -> bool:
    """Scrive (o cancella, se `valore` è vuoto) un campo. False se non ammesso."""
    if campo not in CAMPI_AMMESSI:
        return False
    dati = carica()
    pulito = str(valore or "").strip()
    if pulito:
        dati[campo] = pulito
    else:
        dati.pop(campo, None)
    file = percorso()
    file.parent.mkdir(parents=True, exist_ok=True)
    # Scrittura non atomica ma su un file piccolo e riscritto raramente: qui il
    # danno di un troncamento è «rimetti la chiave», non «hai perso lo storico».
    file.write_text(json.dumps(dati, indent=2, ensure_ascii=False), encoding="utf-8")
    applica_all_ambiente()
    return True


def applica_all_ambiente() -> None:
    """Riversa il file nelle variabili d'ambiente, **sovrascrivendo**.

    Sovrascrivere è il punto: `providers.load_keys_from` di proposito non tocca
    una variabile già impostata, perché legge da `secrets.toml`. Qui è il
    contrario — questo file è l'ultima cosa che l'utente ha deciso.
    """
    import os

    for campo, valore in carica().items():
        os.environ[campo] = valore


def status() -> dict[str, bool]:
    """Quali campi sono valorizzati. **Solo booleani**, mai i valori."""
    dati = carica()
    return {campo: bool(dati.get(campo)) for campo in CAMPI_AMMESSI}


def riepilogo_provider() -> dict[str, dict[str, Any]]:
    """Per ogni provider: se ha una chiave, e da dove arriva.

    La provenienza serve a spiegare all'utente perché un provider risulta
    configurato anche se lui qui non ha scritto niente (una env var, o un
    `secrets.toml` di chi lavora da sorgente).
    """
    import os

    salvate = carica()
    fuori: dict[str, dict[str, Any]] = {}
    for nome, cfg in PROVIDERS.items():
        da_file = bool(salvate.get(cfg.key_env))
        da_ambiente = bool(os.environ.get(cfg.key_env, "").strip()) and not da_file
        fuori[nome] = {
            "label": cfg.label,
            "campo": cfg.key_env,
            "configurato": da_file or da_ambiente,
            "origine": "pannello" if da_file else ("ambiente" if da_ambiente else ""),
        }
    return fuori

"""Stato per-fonte dell'ultima ricerca.

Il problema che risolve: ogni scraper ritorna `list[Offerta]`, quindi una lista
vuota poteva significare tre cose molto diverse — la fonte ha risposto e non ha
prodotti che combaciano, la fonte ci ha bloccati (403/503/CAPTCHA), oppure è
esplosa. L'unica traccia era una `print` che finiva mescolata a quella degli altri
tredici scraper. Per l'utente diventava indistintamente «0 risultati», che si
legge come "il software è rotto" invece di "Amazon ci sta throttlando".

Perché un registro a parte e non un valore di ritorno: i 14 scraper hanno sei
firme diverse e sono chiamati da altrettanti rami di `orchestrator`; cambiare
tutti i tipi di ritorno sarebbe un refactor invasivo per un'informazione
accessoria. Il registro è scritto dai thread dello `ThreadPoolExecutor` e letto
dal chiamante quando hanno finito, quindi basta un lock.

Solo stdlib, nessun import interno.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

#: La fonte ha risposto e ha prodotto risultati.
OK = "ok"
#: La fonte ha risposto correttamente ma nessun prodotto combacia.
EMPTY = "empty"
#: La fonte ci ha rifiutati (403/429/503, CAPTCHA, challenge JS).
BLOCKED = "blocked"
#: Errore imprevisto: eccezione, timeout, parsing andato male.
ERROR = "error"
#: Disattivata per scelta (bot-protection non aggirabile): early-return volontario.
DISABLED = "disabled"


@dataclass(frozen=True)
class SourceStatus:
    """Esito di una fonte in una ricerca."""

    fonte: str
    state: str
    detail: str = ""
    results: int = 0

    @property
    def is_problem(self) -> bool:
        """True se l'assenza di risultati NON è colpa della query."""
        return self.state in (BLOCKED, ERROR)

    def describe(self) -> str:
        """Etichetta breve, pensata per la UI."""
        if self.state == OK:
            return f"{self.results} offerte"
        if self.state == EMPTY:
            return "nessun risultato per questa ricerca"
        if self.state == BLOCKED:
            return f"bloccata dalla fonte{f' ({self.detail})' if self.detail else ''}"
        if self.state == ERROR:
            return f"errore{f' ({self.detail})' if self.detail else ''}"
        if self.state == DISABLED:
            return self.detail or "disattivata"
        return self.state


@dataclass
class _Registry:
    lock: threading.Lock = field(default_factory=threading.Lock)
    entries: dict[str, SourceStatus] = field(default_factory=dict)


_registry = _Registry()


def reset() -> None:
    """Azzera il registro. Va chiamata all'inizio di ogni ricerca."""
    with _registry.lock:
        _registry.entries.clear()


#: Stati che spiegano *perché* non ci sono risultati e che quindi non vanno
#: sovrascritti da un generico `empty`.
_EXPLANATORY = (BLOCKED, ERROR, DISABLED)


def report(fonte: str, state: str, detail: str = "", results: int = 0) -> None:
    """Registra l'esito di `fonte`.

    Uno stato che *spiega* l'assenza di risultati (bloccata / errore /
    disattivata) non viene declassato a `empty`. Serve perché chi chiama gli
    scraper li avvolge e riporta l'esito in base alla lista restituita: senza
    questa regola, un `return []` dopo un 403 cancellerebbe il motivo appena
    registrato e l'utente rivedrebbe «0 risultati».
    """
    key = str(fonte or "").strip().lower()
    if not key:
        return
    with _registry.lock:
        prev = _registry.entries.get(key)
        if prev is not None and prev.state in _EXPLANATORY and state == EMPTY:
            return
        _registry.entries[key] = SourceStatus(key, state, str(detail or ""), int(results))


def report_blocked(fonte: str, status_code: int | str | None = None) -> None:
    detail = f"HTTP {status_code}" if status_code else ""
    report(fonte, BLOCKED, detail)


def report_error(fonte: str, exc: BaseException | str) -> None:
    detail = f"{type(exc).__name__}" if isinstance(exc, BaseException) else str(exc)
    report(fonte, ERROR, detail)


def report_empty(fonte: str) -> None:
    report(fonte, EMPTY)


def report_disabled(fonte: str, reason: str = "") -> None:
    report(fonte, DISABLED, reason)


def report_ok(fonte: str, results: int) -> None:
    report(fonte, OK, results=results)


def get(fonte: str) -> SourceStatus | None:
    with _registry.lock:
        return _registry.entries.get(str(fonte or "").strip().lower())


def snapshot() -> dict[str, SourceStatus]:
    """Copia dello stato corrente, sicura da leggere dopo la ricerca."""
    with _registry.lock:
        return dict(_registry.entries)


def problems() -> list[SourceStatus]:
    """Fonti la cui assenza di risultati non dipende dalla query."""
    return [s for s in snapshot().values() if s.is_problem]

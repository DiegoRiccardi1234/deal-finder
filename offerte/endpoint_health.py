"""Salute live degli endpoint OpenRouter, prima di scegliere un modello.

Sul piano gratuito i modelli spariscono, si degradano o restano in lista pur non
avendo più nessun provider dietro. Interrogarli è tempo perso e, peggio, consuma
tentativi di una quota condivisa.

`GET /api/v1/models/{slug}/endpoints` risponde **senza autenticazione**, non
esegue inferenza e quindi non intacca la quota giornaliera. Costa una richiesta
HTTP e dice tutto quello che serve:

  - `endpoints: []`   → modello morto, nessun provider lo serve più
  - `status`          → 0 significa operativo
  - `uptime_last_5m`  → affidabilità nell'immediato

Nota su cosa **non** si può usare: `latency_last_30m` è uno scalare in
millisecondi oppure `null`, e sui modelli `:free` è quasi sempre `null`. Ordinare
per latenza sembra sensato e non funziona.

Se la verifica fallisce non si blocca nulla: il modello resta in gioco e sarà il
failover a valle a scartarlo. Un'informazione mancante non è una bocciatura.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{slug}/endpoints"

#: Sotto questa disponibilità nei cinque minuti precedenti il modello è da
#: evitare: risponderebbe a intermittenza e ogni fallimento costa un giro di
#: failover.
MIN_UPTIME_5M = 80.0

#: Ampiezza della fascia di merito. Due modelli entro due punti di uptime sono
#: equivalenti nei fatti: a quel punto deve vincere la qualità, non un decimale
#: di disponibilità, altrimenti un modello valido viene scavalcato per niente.
#: La fascia si calcola **rispetto al migliore del gruppo**, non su valori
#: assoluti: con soglie fisse un 99,8% e un 100% finirebbero in fasce diverse
#: solo perché cade lì il confine, che è esattamente il difetto da evitare.
UPTIME_BUCKET = 2.0

#: Quante verifiche in parallelo. Serve un limite: sono richieste gratuite ma
#: restano richieste.
MAX_CONCURRENT_CHECKS = 6

#: Quanto fidarsi di una risposta già ottenuta. Cinque minuti è la finestra
#: stessa di `uptime_last_5m`: tenerla più lunga vorrebbe dire decidere su un
#: dato che descrive un momento diverso da quello in cui si sta scegliendo.
TTL_CACHE = 300.0


@dataclass(frozen=True)
class Health:
    slug: str
    alive: bool
    uptime_5m: float = 0.0
    uptime_1d: float = 0.0
    providers: tuple[str, ...] = ()
    detail: str = ""


_cache: dict[str, tuple[float, Health]] = {}


def tiers(values: list[float]) -> list[int]:
    """Fascia di ciascun valore rispetto al massimo: 0 è la fascia migliore."""
    if not values:
        return []
    best = max(values)
    return [int((best - value) // UPTIME_BUCKET) for value in values]


def _parse(slug: str, payload: object) -> Health:
    data = payload.get("data") if isinstance(payload, dict) else None
    endpoints = data.get("endpoints") if isinstance(data, dict) else None

    if not isinstance(endpoints, list) or not endpoints:
        return Health(slug, False, detail="nessun provider serve più il modello")

    sani = [e for e in endpoints if isinstance(e, dict) and e.get("status") in (0, None)]
    if not sani:
        return Health(slug, False, detail="tutti gli endpoint sono in errore")

    def uptime(endpoint: dict, chiave: str) -> float:
        valore = endpoint.get(chiave)
        return float(valore) if isinstance(valore, (int, float)) else 0.0

    # Conta il provider migliore: OpenRouter instrada lì per primo.
    best_5m = max(uptime(e, "uptime_last_5m") for e in sani)
    best_1d = max(uptime(e, "uptime_last_1d") for e in sani)
    nomi = tuple(str(e.get("provider_name")) for e in sani if e.get("provider_name"))

    if best_5m and best_5m < MIN_UPTIME_5M:
        return Health(
            slug,
            False,
            best_5m,
            best_1d,
            nomi,
            detail=f"disponibilità al {best_5m:.0f}% negli ultimi 5 minuti",
        )
    return Health(slug, True, best_5m, best_1d, nomi)


def _fetch(url: str):
    from offerte.http import fetch_with_retry, get_headers

    intestazioni = get_headers()
    intestazioni["Accept"] = "application/json"
    return fetch_with_retry(url, intestazioni)


def check(slug: str, *, fetch=None, now: float | None = None) -> Health:
    """Salute di un singolo modello, con cache breve."""
    adesso = time.time() if now is None else now
    voce = _cache.get(slug)
    if voce and adesso - voce[0] < TTL_CACHE:
        return voce[1]

    try:
        risposta = (fetch or _fetch)(ENDPOINTS_URL.format(slug=slug))
        salute = _parse(slug, risposta.json())
    except Exception as exc:
        # Nessuna informazione non è una bocciatura: si lascia il modello in
        # gioco e sarà il failover a scartarlo se davvero non risponde.
        logger.debug("salute di %s non verificabile: %s", slug, exc)
        return Health(slug, True, detail="verifica non riuscita")

    _cache[slug] = (adesso, salute)
    return salute


def check_many(slugs: list[str], *, fetch=None) -> dict[str, Health]:
    """Le verifiche in parallelo, con un tetto ai thread."""
    if not slugs:
        return {}
    with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_CHECKS, len(slugs))) as pool:
        esiti = list(pool.map(lambda s: (s, check(s, fetch=fetch)), slugs))
    return dict(esiti)


def svuota_cache() -> None:
    _cache.clear()

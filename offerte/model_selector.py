"""Quale modello usare, fra quelli che il provider dichiara disponibili.

Prima la scelta era: il primo candidato del registry che compare nel catalogo,
altrimenti quello con la finestra di contesto più larga. Due difetti concreti.

**La finestra di contesto non misura la qualità.** Su OpenRouter il modello con
il contesto più largo è spesso un reasoning-model, che sui compiti a JSON brucia
il budget in ragionamento nascosto e tronca la risposta. Il segnale è
`finish_reason == "length"`: il JSON esterno si chiude, l'array dentro è corto, e
`json.loads` passa. Il fallimento è **silenzioso**, ed è il peggiore.

**Il nome non dice se un modello è un reasoning-model.** `nemotron-super`,
`gpt-oss`, `hy3` non hanno "reason" nello slug. Per questo qui c'è sia
un'euristica sul nome, sia — e conta di più — una memoria di quello che è
successo davvero: chi tronca, chi risponde vuoto, chi dà 429 viene de-rankato
per il resto della sessione.

Le penalità sono per **(provider, modello)** e non per il solo modello: lo stesso
slug può troncare su un host e andare benissimo su un altro.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

#: Sotto questa taglia i modelli scorano male sui compiti strutturati: sono
#: "giocattoli" che sbagliano lo schema. Sopra i 40 miliardi invece si escludono
#: i mid-model puliti e restano i giganti che troncano — per questo il bonus
#: taglia si ferma, non cresce all'infinito.
QUALITY_FLOOR_B = 26.0

#: Indizi che il modello è addestrato a seguire istruzioni: è quello che serve
#: per produrre JSON.
INSTRUCT_HINTS = ("instruct", "-it", "chat")

#: Indizi di ragionamento esplicito. Su un compito a JSON è una penalità, non un
#: pregio: il budget se ne va in catena di pensiero e la risposta si tronca.
REASONING_HINTS = ("reasoning", "-r1", "think", "-o1", "-o3", "qwq")

#: Quanto dura una penalità. Trenta minuti perché un modello che ha troncato una
#: volta tende a rifarlo finché non cambia qualcosa dall'altra parte.
TTL_PENALITA = 1800.0

#: Tranne il 429, che dura poco: su free tier è un throttle condiviso e passa.
#: Tenerlo mezz'ora spegnerebbe modelli sani per un burst altrui.
TTL_RATE_LIMIT = 180.0

PESI = {
    "troncato": 6.0,
    "vuoto": 4.0,
    "errore": 3.0,
    "rate_limit": 2.0,
}


@dataclass
class _Penalita:
    peso: float
    scadenza: float


_penalita: dict[tuple[str, str], _Penalita] = {}


def registra_penalita(
    provider: str, modello: str, motivo: str, *, now: float | None = None
) -> None:
    """Ricorda che questa coppia si è comportata male."""
    peso = PESI.get(motivo)
    if not peso or not modello:
        return
    adesso = time.time() if now is None else now
    ttl = TTL_RATE_LIMIT if motivo == "rate_limit" else TTL_PENALITA
    chiave = (provider, modello)
    corrente = _penalita.get(chiave)
    accumulato = peso + (corrente.peso if corrente and corrente.scadenza > adesso else 0.0)
    _penalita[chiave] = _Penalita(accumulato, adesso + ttl)


def penalita_di(provider: str, modello: str, *, now: float | None = None) -> float:
    adesso = time.time() if now is None else now
    voce = _penalita.get((provider, modello))
    if voce is None or voce.scadenza <= adesso:
        return 0.0
    return voce.peso


def dimentica_penalita() -> None:
    _penalita.clear()


def taglia_miliardi(slug: str) -> float:
    """`llama-3.3-70b` → 70.0. Zero se il nome non la dichiara."""
    trovati = re.findall(r"(\d+(?:\.\d+)?)\s*[bB](?![a-zA-Z0-9])", slug or "")
    return max((float(t) for t in trovati), default=0.0)


def qualita_dal_nome(slug: str, *, compito: str = "json") -> float:
    """Punteggio euristico. Non è una verità, è un ordinamento di partenza."""
    nome = (slug or "").lower()
    punti = 0.0

    taglia = taglia_miliardi(nome)
    if taglia:
        if taglia < QUALITY_FLOOR_B:
            punti -= 4.0
        else:
            # Oltre la soglia la taglia aiuta poco e presto smette: su JSON un
            # 26B pulito batte un 120B che ragiona ad alta voce e tronca.
            punti += min(taglia, 40.0) / 20.0

    if any(h in nome for h in INSTRUCT_HINTS):
        punti += 1.5
    if compito == "json" and any(h in nome for h in REASONING_HINTS):
        punti -= 3.0
    if nome.endswith(":free") or ":free" in nome:
        punti += 0.5
    return punti


def ordina(
    modelli: list[str],
    *,
    provider: str,
    preferiti: tuple[str, ...] = (),
    compito: str = "json",
    salute: dict | None = None,
    now: float | None = None,
) -> list[str]:
    """I candidati dal migliore al peggiore.

    L'ordine è: fascia di uptime (se la salute è nota), poi qualità meno
    penalità. La fascia viene prima perché un modello irraggiungibile non ha
    qualità; dentro la stessa fascia decide il merito, non un decimale di
    disponibilità.
    """
    if not modelli:
        return []

    # I modelli che la verifica dà per morti finiscono **in fondo**, non fuori.
    # Toglierli sembrava più pulito e non lo è: la verifica può sbagliarsi (un
    # 404 su uno slug che non è di OpenRouter la fa fallire), e un candidato
    # perso qui è un candidato che il failover non può più raggiungere. In coda
    # non dà fastidio a nessuno e c'è se serve.
    morti = {m for m in modelli if salute is not None and not getattr(salute.get(m), "alive", True)}

    uptime = [float(getattr((salute or {}).get(m, None), "uptime_5m", 0.0)) for m in modelli]
    fasce = tiers_o_zero(uptime)

    def merito(indice: int, modello: str) -> tuple[int, int, float]:
        punti = qualita_dal_nome(modello, compito=compito)
        if modello in preferiti:
            # Un candidato del registry batte qualunque punteggio ricavato dal
            # nome (che al massimo arriva a 4): quella lista è conoscenza
            # verificata sul campo, l'euristica è una congettura su una stringa.
            # Ma **non** batte le penalità: se quel modello ha appena troncato
            # due volte (6+6) scende comunque, ed è giusto così — quello che è
            # successo davvero vale più di quello che credevamo.
            punti += 5.0 + (len(preferiti) - preferiti.index(modello)) * 0.1
        punti -= penalita_di(provider, modello, now=now)
        return (1 if modello in morti else 0, fasce[indice], -punti)

    return [m for _, m in sorted(enumerate(modelli), key=lambda p: merito(p[0], p[1]))]


def tiers_o_zero(valori: list[float]) -> list[int]:
    """Le fasce di uptime, o tutti zero se nessuno ha un dato utile.

    Serve la distinzione: con tutti gli uptime a zero — il caso di un provider
    che non è OpenRouter — le fasce sarebbero tutte 0 comunque, ma passare dal
    modulo della salute per scoprirlo costerebbe un import inutile.
    """
    if not valori or not any(valori):
        return [0] * len(valori)
    from offerte.endpoint_health import tiers

    return tiers(valori)

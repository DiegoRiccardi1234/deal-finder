"""offerte: offerte/http.py"""
from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

try:
    from offerte.ai import (
        get_best_model as _get_best_model,
        cerebras_chat_with_retry as _cerebras_chat_lib,
    )
except Exception:
    _get_best_model = None  # type: ignore[assignment]
    _cerebras_chat_lib = None  # type: ignore[assignment]

_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"
from offerte._constants import *  # noqa: F401,F403

def get_headers() -> dict[str, str]:
    """Genera headers HTTP realistici per evitare blocchi."""
    return {
        "User-Agent":      _random_ua(),
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.7,en;q=0.5",
        # Brotli (br) rimosso: requests non decodifica Brotli senza il pacchetto brotli,
        # restituendo contenuto binario illeggibile.
        "Accept-Encoding": "gzip, deflate",
        "DNT":             "1",
        "Connection":      "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _random_delay() -> None:
    """Attesa casuale tra DELAY_MIN e DELAY_MAX secondi."""
    time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))


def fetch_with_retry(
    url: str,
    headers: dict[str, str],
    *,
    max_retries: int = MAX_RETRIES,
    backoff_base: float = BACKOFF_BASE,
    timeout: int = TIMEOUT,
    session: Optional[requests.Session] = None,
) -> requests.Response:
    """
    Esegue una GET con retry automatico in caso di:
      - requests.Timeout
      - requests.ConnectionError
      - HTTP status 429 (Too Many Requests)
      - HTTP status 503 (Service Unavailable)

    Retry policy:
      - Massimo `max_retries` tentativi extra (totale: 1 + max_retries)
      - Backoff esponenziale: attende backoff_base^tentativo secondi tra i retry
        più un jitter casuale (±20%) per evitare thundering herd
      - Alla prima risposta valida (qualunque status != 429/503) ritorna subito
      - Se tutti i tentativi falliscono, rilancia l'ultima eccezione

    Args:
        url:          URL da richiedere.
        headers:      Headers HTTP da inviare.
        max_retries:  Numero massimo di tentativi aggiuntivi (default: 2).
        backoff_base: Base per il calcolo del backoff (default: 2.0 secondi).
        timeout:      Timeout per richiesta (default: TIMEOUT costante).
        session:      requests.Session opzionale; ne crea una usa-e-getta se assente.

    Returns:
        requests.Response dell'ultima risposta riuscita.

    Raises:
        requests.Timeout:         Se tutti i tentativi scadono per timeout.
        requests.ConnectionError: Se tutti i tentativi falliscono per connessione.
        requests.HTTPError:       Se riceve un errore HTTP non recuperabile.
    """
    requester = session or requests
    last_exc: Optional[BaseException] = None

    for attempt in range(1 + max_retries):          # tentativo 0, 1, 2 …
        try:
            resp = requester.get(url, headers=headers, timeout=timeout)

            # 429 Too Many Requests o 503 Service Unavailable → vale la pena riprovare
            if resp.status_code in (429, 503):
                # Rispetta l'header Retry-After se presente (in secondi)
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    suggested = float(retry_after)
                else:
                    # Backoff esponenziale: 2^attempt con jitter ±20%
                    suggested = backoff_base ** (attempt + 1)

                jitter    = suggested * random.uniform(-0.2, 0.2)
                wait_time = max(1.0, suggested + jitter)

                if attempt < max_retries:
                    print(
                        f"    ⚠️  HTTP {resp.status_code} — "
                        f"tentativo {attempt + 1}/{1 + max_retries}, "
                        f"attendo {wait_time:.1f}s…"
                    )
                    time.sleep(wait_time)
                    headers = get_headers()          # ruota lo User-Agent
                    continue
                else:
                    # Ultimo tentativo: ritorna la risposta così com'è
                    return resp

            # Risposta valida (anche 4xx/5xx non gestiti sopra)
            return resp

        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < max_retries:
                wait_time = backoff_base ** (attempt + 1)
                jitter    = wait_time * random.uniform(-0.2, 0.2)
                wait_time = max(1.0, wait_time + jitter)
                exc_tipo  = "Timeout" if isinstance(exc, requests.Timeout) else "Errore di connessione"
                print(
                    f"    ⚠️  {exc_tipo} — "
                    f"tentativo {attempt + 1}/{1 + max_retries}, "
                    f"attendo {wait_time:.1f}s…"
                )
                time.sleep(wait_time)
                headers = get_headers()              # ruota lo User-Agent

    # Tutti i tentativi esauriti → rilancia l'ultima eccezione
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"fetch_with_retry: tutti i tentativi ({1 + max_retries}) verso {url} esauriti.")



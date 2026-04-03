"""
cerebras_model.py — Selezione automatica del miglior modello Cerebras disponibile
con retry automatico per rate limit (429) e modelli non più disponibili (404).
"""

import os
import time

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

# Modelli da escludere (troppo piccoli o lenti)
BLACKLIST = {"llama3.1-8b"}

# Fallback statico se l'API non è raggiungibile
FALLBACK_MODEL = "llama-3.3-70b"

_cached_model: str | None = None


def get_best_model(client=None, force_refresh: bool = False) -> str:
    """
    Restituisce l'ID del miglior modello Cerebras disponibile.
    Interroga /v1/models, esclude la BLACKLIST, sceglie quello con
    context_window più grande. Risultato cachato in memoria.
    """
    global _cached_model

    if _cached_model and not force_refresh:
        return _cached_model

    try:
        if client is None:
            if Cerebras is None:
                return FALLBACK_MODEL
            api_key = os.environ.get("CEREBRAS_API_KEY", "")
            if not api_key:
                return FALLBACK_MODEL
            client = Cerebras(api_key=api_key)

        models = client.models.list()
        available = [
            m for m in (models.data or [])
            if getattr(m, "id", None) and m.id not in BLACKLIST
        ]

        if not available:
            _cached_model = FALLBACK_MODEL
            return _cached_model

        available.sort(key=lambda m: getattr(m, "context_window", 0), reverse=True)
        _cached_model = available[0].id

    except Exception:
        _cached_model = FALLBACK_MODEL

    return _cached_model


def invalidate_model() -> None:
    """Svuota la cache del modello (es. dopo un 404)."""
    global _cached_model
    _cached_model = None


def cerebras_chat_with_retry(
    client,
    messages: list,
    model: str | None = None,
    max_retries: int = 4,
    base_delay: float = 2.0,
    **kwargs,
):
    """
    Chiama client.chat.completions.create() con retry automatico.

    - Su 404 (modello non trovato): invalida la cache, recupera il nuovo
      miglior modello e riprova.
    - Su 429 (rate limit): attende con backoff esponenziale e riprova.
    - Su altri errori: riprova fino a max_retries volte.

    Args:
        client:      Istanza Cerebras già inizializzata.
        messages:    Lista messaggi per la chat.
        model:       Modello da usare. Se None usa get_best_model().
        max_retries: Numero massimo di tentativi (default 4).
        base_delay:  Secondi di attesa base per il backoff (default 2.0).
        **kwargs:    Parametri extra passati a chat.completions.create().

    Returns:
        Risposta dell'API Cerebras.

    Raises:
        Exception: se tutti i tentativi falliscono.
    """
    if model is None:
        model = get_best_model(client=client)

    last_exc = None
    for attempt in range(max_retries):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                **kwargs,
            )
        except Exception as exc:
            last_exc = exc
            exc_str = str(exc)

            # 404 — modello non più disponibile: aggiorna e riprova subito
            if "404" in exc_str or "model_not_found" in exc_str or "does not exist" in exc_str:
                invalidate_model()
                model = get_best_model(client=client, force_refresh=True)
                # Piccola pausa prima di riprovare
                time.sleep(1.0)
                continue

            # 429 — rate limit: backoff esponenziale
            if "429" in exc_str or "rate_limit" in exc_str or "too many" in exc_str.lower():
                wait = base_delay * (2 ** attempt)
                time.sleep(wait)
                continue

            # Altri errori: backoff lineare leggero
            if attempt < max_retries - 1:
                time.sleep(base_delay)

    raise last_exc  # type: ignore[misc]

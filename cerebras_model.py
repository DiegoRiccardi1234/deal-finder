"""
cerebras_model.py — Selezione automatica del miglior modello Cerebras disponibile.

Chiama /v1/models una sola volta per sessione (cache in-memory) e restituisce
il modello con la context window più grande, escludendo quelli nella BLACKLIST.
"""

import os

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

    Criteri di selezione: context_window più grande = modello più capace.
    Il risultato è cachato in memoria per non chiamare l'API ad ogni richiesta.

    Args:
        client:        Istanza Cerebras opzionale. Se None viene creata internamente.
        force_refresh: Se True, ignora la cache e ri-interroga l'API.

    Returns:
        ID del modello (str), es. "llama-3.3-70b".
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

        # Ordina per context_window decrescente (più capace prima)
        available.sort(key=lambda m: getattr(m, "context_window", 0), reverse=True)
        _cached_model = available[0].id
    except Exception:
        _cached_model = FALLBACK_MODEL

    return _cached_model

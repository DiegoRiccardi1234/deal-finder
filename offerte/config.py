"""Configurazione centralizzata.

Sorgente unica per le costanti tunabili condivise. Volutamente senza import
interni (solo stdlib) per poter essere importato da qualsiasi modulo di
`offerte/` e `ui/` senza rischio di import circolari.
"""

from __future__ import annotations

import os

# Versione del progetto (sorgente unica; usata dall'auto-update per il confronto).
VERSION: str = "4.2.1"

# Fallback testuali di Google; i pool per compito sono in offerte.providers.
# Il catalogo limita la scelta ai modelli ammessi, senza passaggi a pagamento.
AI_FALLBACK_MODELS: tuple[str, ...] = ("gemini-3.1-flash-lite", "gemini-3.5-flash-lite")
DEFAULT_AI_MODEL: str = AI_FALLBACK_MODELS[0]
AI_MODEL_BLACKLIST: frozenset[str] = frozenset({"llama3.1-8b"})

# Alias di import per le integrazioni precedenti; non abilitano Cerebras.
CEREBRAS_FALLBACK_MODELS = AI_FALLBACK_MODELS
DEFAULT_CEREBRAS_MODEL = DEFAULT_AI_MODEL
CEREBRAS_MODEL_BLACKLIST = AI_MODEL_BLACKLIST


def _env_float(name: str, default: float) -> float:
    try:
        val = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return val if val > 0 else default


# Timeout (secondi) delle richieste ai provider AI. Senza questo gli SDK usano
# il proprio default (o nessuno), e una chiamata lenta blocca il rerun
# di Streamlit.
AI_REQUEST_TIMEOUT: float = _env_float("AI_REQUEST_TIMEOUT", 60.0)

# Tetto (secondi) al tempo totale di una ricerca: oltre questo l'orchestrator
# smette di attendere gli scraper ancora in corso e restituisce i parziali.
SEARCH_TOTAL_TIMEOUT: float = _env_float("SEARCH_TOTAL_TIMEOUT", 90.0)

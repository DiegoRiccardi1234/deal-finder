"""Configurazione centralizzata.

Sorgente unica per le costanti tunabili condivise. Volutamente senza import
interni (solo stdlib) per poter essere importato da qualsiasi modulo di
`offerte/` e `ui/` senza rischio di import circolari.
"""

from __future__ import annotations

import os

# Versione del progetto (sorgente unica; usata dall'auto-update per il confronto).
VERSION: str = "2.0.0"

# La scelta del modello è DINAMICA: `offerte.ai.get_best_model()` interroga
# `client.models.list()` e seleziona il migliore disponibile (per context_window,
# esclusi i blacklistati). Nessun modello è hardcodato come "il" modello.
#
# Questi sono solo CANDIDATI DI FALLBACK in ordine di preferenza, usati quando
# l'API non è raggiungibile (client assente, niente API key, errore di rete) e
# quindi non si può interrogare la lista. Override del primo via env
# CEREBRAS_FALLBACK_MODEL. NB: llama-3.3-70b è stato dismesso da Cerebras (2026-06).
CEREBRAS_FALLBACK_MODELS: tuple[str, ...] = tuple(
    dict.fromkeys(
        m
        for m in (
            os.environ.get("CEREBRAS_FALLBACK_MODEL", "").strip(),
            "zai-glm-4.7",
            "gpt-oss-120b",
        )
        if m
    )
)

# Compat: primo candidato come singolo default per i consumatori legacy.
DEFAULT_CEREBRAS_MODEL: str = CEREBRAS_FALLBACK_MODELS[0]

# Modelli da escludere dal resolver (troppo piccoli / deprecati).
CEREBRAS_MODEL_BLACKLIST: frozenset[str] = frozenset({"llama3.1-8b"})

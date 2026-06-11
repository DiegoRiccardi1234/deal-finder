"""Configurazione centralizzata.

Sorgente unica per le costanti tunabili condivise. Volutamente senza import
interni (solo stdlib) per poter essere importato da qualsiasi modulo di
`offerte/` e `ui/` senza rischio di import circolari.
"""
from __future__ import annotations

import os

# Modello Cerebras usato come fallback statico quando il resolver dinamico
# `offerte.ai.get_best_model()` non riesce a interrogare l'API (client assente,
# nessuna API key, errore di rete). Override via env var CEREBRAS_FALLBACK_MODEL.
DEFAULT_CEREBRAS_MODEL: str = os.environ.get("CEREBRAS_FALLBACK_MODEL", "llama-3.3-70b")

# Modelli da escludere dal resolver (troppo piccoli / deprecati).
CEREBRAS_MODEL_BLACKLIST: frozenset[str] = frozenset({"llama3.1-8b"})

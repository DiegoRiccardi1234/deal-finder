"""offerte: offerte/dedup.py"""

from __future__ import annotations

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta


def _deduplica(offerte: list[Offerta], soglia_pct: float = 0.05) -> list[Offerta]:
    """
    Rimuove duplicati dove nome identico (case-insensitive) e prezzo entro
    `soglia_pct` percentuale di differenza → mantiene l'offerta più economica.
    """
    uniche: list[Offerta] = []
    for offerta in offerte:
        duplicato = False
        for esistente in uniche:
            nome_simile = offerta.nome.lower() == esistente.nome.lower()
            if nome_simile:
                diff_rel = abs(offerta.prezzo - esistente.prezzo) / max(esistente.prezzo, 1)
                if diff_rel <= soglia_pct:
                    # Tiene la più economica
                    if offerta.prezzo < esistente.prezzo:
                        uniche.remove(esistente)
                        uniche.append(offerta)
                    duplicato = True
                    break
        if not duplicato:
            uniche.append(offerta)
    return uniche

"""offerte: offerte/filters.py"""

from __future__ import annotations

import re

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte.parsing import *  # noqa: F401,F403


def _passes_hard_spec_filters(offerta: Offerta, filtri: dict[str, str]) -> bool:
    """Applica vincoli tecnici hard per ridurre falsi positivi su notebook/smartphone."""
    return len(_hard_spec_mismatch_reasons(offerta, filtri)) == 0


def _hard_spec_mismatch_reasons(offerta: Offerta, filtri: dict[str, str]) -> list[str]:
    """Restituisce i motivi di mismatch hard (RAM/storage/display), lista vuota se passa."""
    if not filtri:
        return []

    reasons: list[str] = []

    search_text = f"{offerta.nome} " + " ".join(
        str(v) for v in (offerta.specs or {}).values() if v not in (None, "", [], {})
    )
    search_lower = search_text.lower()

    ram_target = filtri.get("ram_gb") or filtri.get("ram")
    if ram_target:
        m = re.search(r"(\d{1,3})", str(ram_target))
        if m:
            target = int(m.group(1))
            gb_vals = _extract_ram_gb_values(search_lower)
            if not gb_vals or max(gb_vals) < target:
                found = f"trovato={max(gb_vals)}GB" if gb_vals else "trovato=assente"
                reasons.append(f"ram<{target}GB ({found})")

    storage_target = filtri.get("storage_gb") or filtri.get("storage")
    if storage_target:
        m = re.search(r"(\d{2,4})", str(storage_target))
        if m:
            target = int(m.group(1))
            gb_vals = _extract_storage_gb_values(search_lower)
            if not gb_vals:
                gb_vals = _extract_gb_values(search_lower)
            if not gb_vals or max(gb_vals) < target:
                found = f"trovato={max(gb_vals)}GB" if gb_vals else "trovato=assente"
                reasons.append(f"storage<{target}GB ({found})")

    size_target = filtri.get("size_inches") or filtri.get("display")
    if size_target:
        parsed_range = _parse_target_range(str(size_target))
        if parsed_range is not None:
            low, high = parsed_range
            inches_vals = _extract_inches_values(search_lower)
            if not inches_vals or not any(low <= v <= high for v in inches_vals):
                found = ",".join(f'{v:.1f}"' for v in inches_vals) if inches_vals else "assente"
                reasons.append(f'display fuori range {low:.1f}-{high:.1f}" (trovato={found})')

    return reasons


def is_relevant(nome: str, query_tokens: list[str], strict_specs: bool = True) -> bool:
    """
    Filtro di rilevanza: i token della query devono essere presenti nel nome
    del prodotto (o in uno dei loro alias normalizzati).

    Con strict_specs=False, i token di specifica tecnica (es. '16gb', 'ram')
    vengono saltati — le specs verranno verificate tramite AI enrichment.

    Per query corte (<=2 token), basta che almeno 1 token sia presente (OR logic)
    per supportare query generiche tipo 'scarpe', 'libro', ecc.
    """
    nome_lower = nome.lower()
    brand_tokens = [token for token in query_tokens if token in _TECH_BRANDS]
    # Per query corte senza brand tech specifico, applica logica OR:
    # basta un token per considerare rilevante (supporta query generiche come "scarpe", "libro")
    if len(query_tokens) <= 2 and not brand_tokens:
        for token in query_tokens:
            if not strict_specs and _is_spec_token(token):
                continue
            varianti = _ALIASES.get(token, {token})
            varianti.add(token)
            if any(v in nome_lower for v in varianti):
                return True
        return False
    for token in query_tokens:
        if not strict_specs and _is_spec_token(token):
            continue
        if token.isdigit() and len(token) <= 2 and brand_tokens:
            if not any(
                re.search(rf"\b{re.escape(brand)}\s*{re.escape(token)}\b", nome_lower)
                for brand in brand_tokens
            ):
                return False
            continue

        # Espande il token con gli alias conosciuti
        varianti = _ALIASES.get(token, {token})
        varianti.add(token)
        if not any(v in nome_lower for v in varianti):
            return False
    return True

"""ui: ui/export.py"""

from __future__ import annotations

import csv
import io
import re
from typing import Any


try:
    import knowledge_base as kb_manager
except Exception:
    kb_manager = None  # type: ignore[assignment]

from offerte_tech import Offerta

try:
    from search_history import load_history, save_search as _save_search
except ImportError:

    def load_history() -> list[dict[str, Any]]:
        return []

    def _save_search(**kw: Any) -> None:
        return None


def _offerte_to_copy_text(offerte: list[Offerta], query: str = "") -> str:
    lines = []
    if query:
        lines.append(f"Ricerca: {query}")
        lines.append("")
    lines.append(f"Ho trovato {len(offerte)} risultati:")
    lines.append("")
    for i, o in enumerate(offerte, 1):
        lines.append(f"{i}. {o.nome}")
        lines.append(f"   Prezzo: €{o.prezzo:.2f}")
        if o.spedizione and o.spedizione not in ("n.d.", ""):
            lines.append(f"   Spedizione: {o.spedizione}")
        condizione = str(getattr(o, "condizione", "") or "").strip()
        if condizione:
            lines.append(f"   Condizione: {condizione}")
        lines.append(f"   Fonte: {o.fonte}")
        lines.append(f"   Link: {o.link}")
        lines.append("")
    lines.append(
        "Analizza questi risultati: sono buoni per la mia ricerca? Quali consiglieresti e perché?"
    )
    return "\n".join(lines)


def _offerte_to_csv_bytes(offerte: list[Offerta]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf,
        fieldnames=["posizione", "nome", "prezzo_eur", "spedizione", "negozio", "fonte", "link"],
        lineterminator="\n",
    )
    writer.writeheader()
    for i, offerta in enumerate(offerte, start=1):
        writer.writerow(
            {
                "posizione": i,
                "nome": offerta.nome,
                "prezzo_eur": f"{offerta.prezzo:.2f}",
                "spedizione": offerta.spedizione,
                "negozio": offerta.negozio,
                "fonte": offerta.fonte,
                "link": offerta.link,
            }
        )
    return ("\ufeff" + buf.getvalue()).encode("utf-8")


def _specs_from_name(nome: str) -> str:
    """Estrae specs di base dal nome prodotto tramite regex (fallback quando specs strutturate sono assenti)."""
    parts: list[str] = []
    text = nome.lower()
    m = re.search(r'(\d{1,2}[,.]?\d*)\s*(?:"|\'\'{2}|pollici?\b)', text)
    if m:
        parts.append(f'Display: {m.group(1)}"')
    m = re.search(r"(\d{1,3})\s*gb\s*(?:di\s*)?(?:ram|lpddr|ddr)", text)
    if m:
        parts.append(f"Ram: {m.group(1)}GB")
    m = re.search(r"(\d{1,4})\s*(tb|gb)\s*(?:ssd|nvme|m\.2|emmc)", text)
    if m:
        unit = m.group(2).upper()
        parts.append(f"SSD: {m.group(1)}{unit}")
    m = re.search(
        r"\b(i[357]-\d{4,5}[a-z]*|i[357]\s+\d{4,5}[a-z]*|ryzen\s*[357]\s*\d{4}[a-z]*|core\s*ultra\s*[57]\s*\d{3}|celeron\s*n\d+)\b",
        text,
    )
    if m:
        parts.append(f"CPU: {m.group(1).title()}")
    return " · ".join(parts)


def _summarize_specs(specs: dict[str, Any], nome: str = "") -> str:
    if not specs:
        return _specs_from_name(nome) if nome else ""
    parts = []
    for key, value in specs.items():
        if value in (None, "", [], {}):
            continue
        label = str(key).replace("_", " ").capitalize()
        parts.append(f"{label}: {value}")
    if parts:
        return " · ".join(parts)
    return _specs_from_name(nome) if nome else ""


def _offerte_to_records(offerte: list[Offerta]) -> list[dict[str, Any]]:
    return [
        {
            "#": i,
            "Prodotto": offerta.nome,
            "Prezzo €": round(offerta.prezzo, 2),
            "Spedizione": offerta.spedizione,
            "Negozio": offerta.negozio,
            "Fonte": offerta.fonte,
            "Specs": _summarize_specs(offerta.specs, offerta.nome),
            "Link": offerta.link,
        }
        for i, offerta in enumerate(offerte, start=1)
    ]

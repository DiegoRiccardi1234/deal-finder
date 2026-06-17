"""offerte: offerte/models.py"""

from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass(order=False)
class Offerta:
    """Rappresenta un'offerta raccolta da una fonte."""

    nome: str
    prezzo: float
    negozio: str
    link: str
    fonte: str = field(default="")
    spedizione: str = field(default="n.d.")
    alternativa: str = field(default="")
    specs: dict[str, object] = field(default_factory=dict)
    immagine: str = field(default="")

    def __str__(self) -> str:
        nome_corto = self.nome[:62] + "…" if len(self.nome) > 63 else self.nome
        prezzo_fmt = f"€ {self.prezzo:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return (
            f"  💰 {prezzo_fmt:<12}  🏪 {self.negozio:<18}  📦 {nome_corto}\n"
            f"     📦 Spedizione: {self.spedizione}\n"
            f"     🔗 {self.link}"
        )

"""offerte: offerte/models.py"""

from __future__ import annotations

from dataclasses import dataclass, field


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

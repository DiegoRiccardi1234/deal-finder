"""offerte.scrapers: registry e re-export."""

from offerte.scrapers.aliexpress import scrape_aliexpress
from offerte.scrapers.alibaba import scrape_alibaba
from offerte.scrapers.amazon import scrape_amazon
from offerte.scrapers.comet import scrape_comet
from offerte.scrapers.ebay import scrape_ebay
from offerte.scrapers.euronics import scrape_euronics
from offerte.scrapers.expert import scrape_expert
from offerte.scrapers.mediaworld import scrape_mediaworld
from offerte.scrapers.subito import scrape_subito
from offerte.scrapers.temu import scrape_temu
from offerte.scrapers.trovaprezzi import scrape_trovaprezzi
from offerte.scrapers.unieuro import scrape_unieuro
from offerte.scrapers.vinted import scrape_vinted
from offerte.scrapers.wallapop import scrape_wallapop

SCRAPERS = {
    "trovaprezzi": scrape_trovaprezzi,
    "amazon": scrape_amazon,
    "ebay": scrape_ebay,
    "vinted": scrape_vinted,
    "euronics": scrape_euronics,
    "unieuro": scrape_unieuro,
    "mediaworld": scrape_mediaworld,
    "wallapop": scrape_wallapop,
    "comet": scrape_comet,
    "expert": scrape_expert,
    "subito": scrape_subito,
    "aliexpress": scrape_aliexpress,
    "temu": scrape_temu,
    "alibaba": scrape_alibaba,
}

__all__ = list(SCRAPERS) + ["SCRAPERS"] + [f"scrape_{k}" for k in SCRAPERS]

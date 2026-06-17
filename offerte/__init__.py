"""offerte: package pubblico. Re-export API stabili."""

from offerte.ai import (
    detect_category_and_questions,
    fetch_specs_ai,
    filtra_risultati_con_ai,
    parse_comparison_query,
    parse_search_intent,
)
from offerte.dedup import _deduplica as _deduplica  # re-export privato (usato dai test)
from offerte.export import export_to_csv, print_results
from offerte.filters import is_relevant
from offerte.http import fetch_with_retry, get_headers
from offerte.models import Offerta
from offerte.orchestrator import cerca_offerte
from offerte.parsing import (
    _extract_json_object as _extract_json_object,  # re-export privato (usato da offerte.ai)
    parse_price,
    tokenize_query,
)
from offerte.scrapers import (
    scrape_aliexpress,
    scrape_alibaba,
    scrape_amazon,
    scrape_comet,
    scrape_ebay,
    scrape_euronics,
    scrape_expert,
    scrape_mediaworld,
    scrape_subito,
    scrape_temu,
    scrape_trovaprezzi,
    scrape_unieuro,
    scrape_vinted,
    scrape_wallapop,
)

__all__ = [
    "Offerta",
    "cerca_offerte",
    "parse_search_intent",
    "parse_comparison_query",
    "detect_category_and_questions",
    "fetch_specs_ai",
    "filtra_risultati_con_ai",
    "is_relevant",
    "parse_price",
    "tokenize_query",
    "fetch_with_retry",
    "get_headers",
    "print_results",
    "export_to_csv",
    "scrape_trovaprezzi",
    "scrape_amazon",
    "scrape_ebay",
    "scrape_vinted",
    "scrape_euronics",
    "scrape_unieuro",
    "scrape_mediaworld",
    "scrape_wallapop",
    "scrape_comet",
    "scrape_expert",
    "scrape_subito",
    "scrape_aliexpress",
    "scrape_temu",
    "scrape_alibaba",
]

"""offerte_tech.py — shim di compatibilità.

Tutto il codice è stato spostato nel package `offerte/`. Questo file
preserva gli import legacy (`from offerte_tech import ...`).
"""

from offerte import *  # noqa: F401,F403
from offerte.scrapers import *  # noqa: F401,F403

# Re-export interni usati dai test (oggetti privati testati direttamente)
from offerte._constants import _is_spec_token, _TECH_BRANDS  # noqa: F401
from offerte.ai import (  # noqa: F401
    _cerebras_chat,
    _cerebras_model,
    _get_cerebras_api_key,
    _get_cerebras_client,
)
from offerte.dedup import _deduplica  # noqa: F401
from offerte.filters import (  # noqa: F401
    _hard_spec_mismatch_reasons,
    _passes_hard_spec_filters,
)
from offerte.http import _random_delay  # noqa: F401
from offerte.parsing import (  # noqa: F401
    _extract_clothing_specs,
    _extract_gb_values,
    _extract_inches_values,
    _extract_json_object,
    _extract_ram_gb_values,
    _extract_storage_gb_values,
    _extract_shipping_from_text,
    _normalize_category,
    _parse_target_range,
    _within_price_range,
)
from offerte.scrapers._base import _get_ebay_token  # noqa: F401


def _build_parser():
    from offerte.cli import _build_parser as _bp

    return _bp()


if __name__ == "__main__":
    from offerte.cli import main as _cli_main

    _cli_main()

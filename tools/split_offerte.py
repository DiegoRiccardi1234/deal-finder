"""
split_offerte.py — One-shot refactor: spezza offerte_tech.py nel package offerte/.

Esegui da root:
    python tools/split_offerte.py

Crea:
  offerte/__init__.py
  offerte/_constants.py
  offerte/models.py
  offerte/http.py
  offerte/parsing.py
  offerte/ai.py
  offerte/filters.py
  offerte/dedup.py
  offerte/export.py
  offerte/orchestrator.py
  offerte/cli.py
  offerte/scrapers/__init__.py
  offerte/scrapers/_base.py
  offerte/scrapers/{trovaprezzi,amazon,ebay,vinted,euronics,unieuro,
                    mediaworld,wallapop,comet,expert,subito,
                    aliexpress,temu,alibaba}.py

Sostituisce offerte_tech.py con uno shim sottile.
"""
from __future__ import annotations

import ast
import os
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "offerte_tech.py"

# --------------------------------------------------------------------------- #
# Mappa: ogni voce è (target_module, ordered_items)                            #
# Ogni item è (kind, name) dove kind ∈ {func, class, raw_range, raw_lines}     #
# --------------------------------------------------------------------------- #

MAP: dict[str, list[tuple]] = {
    "offerte/_constants.py": [
        ("raw_range", 80, 188),  # tutti i try imports + costanti + _is_spec_token + _TECH_BRANDS
    ],
    "offerte/models.py": [
        ("class", "Offerta"),
    ],
    "offerte/http.py": [
        ("func", "get_headers"),
        ("func", "_random_delay"),
        ("func", "fetch_with_retry"),
    ],
    "offerte/parsing.py": [
        ("func", "parse_price"),
        ("func", "_within_price_range"),
        ("func", "_normalize_category"),
        ("func", "_extract_json_object"),
        ("func", "_extract_gb_values"),
        ("func", "_extract_ram_gb_values"),
        ("func", "_extract_storage_gb_values"),
        ("func", "_extract_inches_values"),
        ("func", "_parse_target_range"),
        ("func", "_extract_clothing_specs"),
        ("func", "_extract_shipping_from_text"),
        ("func", "tokenize_query"),
    ],
    "offerte/ai.py": [
        ("func", "_cerebras_model"),
        ("func", "_cerebras_chat"),
        ("func", "_get_cerebras_api_key"),
        ("func", "_get_cerebras_client"),
        ("func", "fetch_specs_ai"),
        ("func", "parse_comparison_query"),
        ("func", "detect_category_and_questions"),
        ("func", "parse_search_intent"),
        ("func", "filtra_risultati_con_ai"),
    ],
    "offerte/filters.py": [
        ("func", "_passes_hard_spec_filters"),
        ("func", "_hard_spec_mismatch_reasons"),
        ("func", "is_relevant"),
    ],
    "offerte/dedup.py": [
        ("func", "_deduplica"),
    ],
    "offerte/export.py": [
        ("func", "print_results"),
        ("func", "export_to_csv"),
    ],
    "offerte/orchestrator.py": [
        ("func", "cerca_offerte"),
    ],
    "offerte/cli.py": [
        ("func", "_build_parser"),
    ],
    "offerte/scrapers/_base.py": [
        ("func", "_get_ebay_token"),
        ("raw_range", 1667, 1671),  # ALIEXPRESS_* constants
        ("raw_range", 2692, 2693),  # WALLAPOP_LAT/LON
        ("raw_range", 2798, 2800),  # WALLAPOP city consts
    ],
    "offerte/scrapers/trovaprezzi.py": [("func", "scrape_trovaprezzi")],
    "offerte/scrapers/amazon.py": [("func", "scrape_amazon")],
    "offerte/scrapers/ebay.py": [("func", "scrape_ebay")],
    "offerte/scrapers/vinted.py": [("func", "scrape_vinted")],
    "offerte/scrapers/euronics.py": [("func", "scrape_euronics")],
    "offerte/scrapers/unieuro.py": [("func", "scrape_unieuro")],
    "offerte/scrapers/mediaworld.py": [("func", "scrape_mediaworld")],
    "offerte/scrapers/wallapop.py": [("func", "scrape_wallapop")],
    "offerte/scrapers/comet.py": [("func", "scrape_comet")],
    "offerte/scrapers/expert.py": [("func", "scrape_expert")],
    "offerte/scrapers/subito.py": [("func", "scrape_subito")],
    "offerte/scrapers/aliexpress.py": [("func", "scrape_aliexpress")],
    "offerte/scrapers/temu.py": [("func", "scrape_temu")],
    "offerte/scrapers/alibaba.py": [("func", "scrape_alibaba")],
}

# Header standard per ogni modulo. Generosi sui import: pyflakes è permissivo
# e il costo runtime di import unused è trascurabile.
COMMON_IMPORTS = """\
from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

try:
    from cerebras_model import (
        get_best_model as _get_best_model,
        cerebras_chat_with_retry as _cerebras_chat_lib,
    )
except Exception:
    _get_best_model = None  # type: ignore[assignment]
    _cerebras_chat_lib = None  # type: ignore[assignment]

_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"
"""

# Per-modulo: import locali. Usiamo wildcard verso _constants e modelli per
# evitare cross-reference fragili durante il primo split. Si può tightare dopo.
_STAR_FROM_CONSTANTS = "from offerte._constants import *  # noqa: F401,F403\n"

LOCAL_IMPORTS: dict[str, str] = {
    "offerte/_constants.py": "",
    "offerte/models.py": "",
    "offerte/http.py": _STAR_FROM_CONSTANTS,
    "offerte/parsing.py": _STAR_FROM_CONSTANTS,
    "offerte/ai.py": (
        _STAR_FROM_CONSTANTS
        + "from offerte.models import Offerta\n"
        + "from offerte.http import fetch_with_retry, get_headers\n"
        + "from offerte.parsing import *  # noqa: F401,F403\n"
        + "from offerte.filters import _hard_spec_mismatch_reasons, _passes_hard_spec_filters, is_relevant\n"
    ),
    "offerte/filters.py": (
        _STAR_FROM_CONSTANTS
        + "from offerte.models import Offerta\n"
        + "from offerte.parsing import *  # noqa: F401,F403\n"
    ),
    "offerte/dedup.py": (
        _STAR_FROM_CONSTANTS
        + "from offerte.models import Offerta\n"
    ),
    "offerte/export.py": "from offerte.models import Offerta\n",
    "offerte/orchestrator.py": (
        _STAR_FROM_CONSTANTS
        + "from offerte.models import Offerta\n"
        + "from offerte.http import fetch_with_retry, get_headers\n"
        + "from offerte.parsing import *  # noqa: F401,F403\n"
        + "from offerte.filters import is_relevant\n"
        + "from offerte.dedup import _deduplica\n"
        + "from offerte.ai import (\n"
        + "    detect_category_and_questions,\n"
        + "    fetch_specs_ai,\n"
        + "    filtra_risultati_con_ai,\n"
        + "    parse_comparison_query,\n"
        + "    parse_search_intent,\n"
        + ")\n"
        + "from offerte.export import export_to_csv, print_results\n"
        + "from offerte.scrapers import (\n"
        + "    scrape_aliexpress, scrape_alibaba, scrape_amazon, scrape_comet,\n"
        + "    scrape_ebay, scrape_euronics, scrape_expert, scrape_mediaworld,\n"
        + "    scrape_subito, scrape_temu, scrape_trovaprezzi, scrape_unieuro,\n"
        + "    scrape_vinted, scrape_wallapop,\n"
        + ")\n"
    ),
    "offerte/cli.py": (
        "import argparse\n"
        + "from offerte.export import export_to_csv, print_results\n"
        + "from offerte.orchestrator import cerca_offerte\n"
    ),
    "offerte/scrapers/_base.py": (
        _STAR_FROM_CONSTANTS
        + "from offerte.http import fetch_with_retry\n"
    ),
}

# Per gli scrapers, header comune con tutto il necessario
SCRAPER_LOCAL_IMPORTS = (
    _STAR_FROM_CONSTANTS
    + "from offerte.models import Offerta\n"
    + "from offerte.http import fetch_with_retry, get_headers, _random_delay\n"
    + "from offerte.parsing import *  # noqa: F401,F403\n"
    + "from offerte.filters import is_relevant\n"
    + "from offerte.scrapers._base import _get_ebay_token\n"
)

# --------------------------------------------------------------------------- #
def parse_source() -> tuple[list[str], dict[str, tuple[int, int]]]:
    """Ritorna (lines, defs_by_name -> (start, end_exclusive))."""
    text = SRC.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    defs: dict[str, tuple[int, int]] = {}
    for node in tree.body:
        name = getattr(node, "name", None)
        if name is None:
            continue
        defs[name] = (node.lineno, (node.end_lineno or node.lineno) + 1)
    return lines, defs


def extract_block(lines: list[str], start: int, end: int) -> str:
    """Estrai linee [start, end) (1-indexed start, exclusive end)."""
    return "".join(lines[start - 1 : end - 1])


CLI_MAIN_TAIL = '''

def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    cerca_offerte(
        query=args.query,
        budget_max=args.budget,
        prezzo_min=0,
        filtri_ai=None,
        top_n=args.top,
        export_csv=(args.export == "csv"),
        csv_filename=args.output,
        condizione=args.condizione,
        fonti=args.fonti,
        categoria="altro",
        cerebras_client=None,
        app_id="",
        cert_id="",
    )


if __name__ == "__main__":
    main()
'''


def build_module(target: str, items: list[tuple], lines: list[str], defs: dict) -> str:
    parts: list[str] = []
    parts.append(f'"""offerte: {target}"""\n')
    # _constants.py NON deve avere COMMON_IMPORTS perché quei imports attivano
    # try-blocks che noi vogliamo solo lì. Invece, _constants HA bisogno solo di
    # import standard.
    if target == "offerte/_constants.py":
        parts.append(
            "from __future__ import annotations\n"
            "import os\n"
            "import random\n"
            "import re\n"
        )
    else:
        parts.append(COMMON_IMPORTS)
    if target.startswith("offerte/scrapers/") and target != "offerte/scrapers/_base.py":
        parts.append(SCRAPER_LOCAL_IMPORTS)
    else:
        parts.append(LOCAL_IMPORTS.get(target, ""))
    parts.append("\n")
    for item in items:
        kind = item[0]
        if kind in ("func", "class"):
            name = item[1]
            if name not in defs:
                raise SystemExit(f"Definizione non trovata: {name}")
            s, e = defs[name]
            parts.append(extract_block(lines, s, e))
            parts.append("\n\n")
        elif kind == "raw_range":
            s, e = item[1], item[2] + 1
            parts.append(extract_block(lines, s, e))
            parts.append("\n")
    if target == "offerte/cli.py":
        parts.append(CLI_MAIN_TAIL)
    return "".join(parts)


def write_init_files() -> None:
    pkg = ROOT / "offerte"
    scrap = pkg / "scrapers"
    pkg.mkdir(exist_ok=True)
    scrap.mkdir(exist_ok=True)

    init_pkg = '''\
"""offerte: package pubblico. Re-export API stabili."""
from offerte.ai import (
    detect_category_and_questions,
    fetch_specs_ai,
    filtra_risultati_con_ai,
    parse_comparison_query,
    parse_search_intent,
)
from offerte.dedup import _deduplica
from offerte.export import export_to_csv, print_results
from offerte.filters import is_relevant
from offerte.http import fetch_with_retry, get_headers
from offerte.models import Offerta
from offerte.orchestrator import cerca_offerte
from offerte.parsing import (
    _extract_json_object,
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
'''
    (pkg / "__init__.py").write_text(init_pkg, encoding="utf-8")

    init_scr = '''\
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
'''
    (scrap / "__init__.py").write_text(init_scr, encoding="utf-8")


def main() -> None:
    lines, defs = parse_source()
    print(f"Source: {len(lines)} lines, {len(defs)} definitions")

    write_init_files()

    for target, items in MAP.items():
        path = ROOT / target
        path.parent.mkdir(parents=True, exist_ok=True)
        body = build_module(target, items, lines, defs)
        path.write_text(body, encoding="utf-8")
        print(f"  wrote {target}: {len(body.splitlines())} lines")

    # Shim minimale al posto di offerte_tech.py
    shim = '''\
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
'''
    (ROOT / "offerte_tech.py").write_text(shim, encoding="utf-8")
    print("  wrote offerte_tech.py shim")


if __name__ == "__main__":
    main()

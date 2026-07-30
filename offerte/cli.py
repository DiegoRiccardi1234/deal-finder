"""offerte: offerte/cli.py"""

from __future__ import annotations

import argparse
import os

from offerte import providers
from offerte.log import configure_logging
from offerte.orchestrator import cerca_offerte


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deal-finder",
        # Niente emoji nel testo di help: su console Windows (cp1252) argparse
        # va in UnicodeEncodeError e `--help` esce con un traceback.
        description="Cerca offerte tech su amazon.it, ebay.it ed altri",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Esempi:\n"
            '  deal-finder -q "notebook 14 pollici 16gb" -b 800 -n 10\n'
            '  deal-finder -q "ssd 1tb" --export csv\n'
            "\n"
            "Senza installazione (dal checkout):\n"
            '  python offerte_tech.py -q "ssd 1tb" --export csv\n'
        ),
    )
    parser.add_argument(
        "-q",
        "--query",
        required=True,
        metavar="TESTO",
        help='Query di ricerca (es. "notebook 14 pollici 16gb")',
    )
    parser.add_argument(
        "-b",
        "--budget",
        type=float,
        default=None,
        metavar="EUR",
        help="Budget massimo in euro (opzionale)",
    )
    parser.add_argument(
        "-n",
        "--top",
        type=int,
        default=10,
        metavar="N",
        help="Quanti risultati mostrare (default: 10)",
    )
    parser.add_argument(
        "--condizione",
        choices=["tutti", "nuovo", "usato"],
        default="tutti",
        metavar="STATO",
        help="Filtro condizione prodotto Amazon (default: tutti)",
    )
    parser.add_argument(
        "--fonti",
        nargs="+",
        choices=[
            "amazon",
            "ebay",
            "vinted",
            "euronics",
            "unieuro",
            "mediaworld",
            "wallapop",
            "comet",
            "expert",
        ],
        default=None,
        metavar="FONTE",
        help="Seleziona le fonti da consultare (default: tutte)",
    )
    parser.add_argument(
        "--provider",
        choices=list(providers.PROVIDERS),
        default=None,
        metavar="NOME",
        help=(
            "Provider AI per filtri/specs: cerebras, groq, openai, openrouter,\n"
            "anthropic, gemini. Default: env AI_PROVIDER o cerebras.\n"
            "Richiede la relativa API key in ambiente."
        ),
    )
    parser.add_argument(
        "--export",
        choices=["csv"],
        default=None,
        metavar="FORMATO",
        help="Esporta i risultati (attualmente supportato: csv)",
    )
    parser.add_argument(
        "--output",
        default="offerte.csv",
        metavar="FILE",
        help="Nome file di output per l'export (default: offerte.csv)",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    # Diagnostica su stderr, così stdout resta pulito per i risultati e per
    # `--export csv`. Alzabile con LOG_LEVEL=DEBUG.
    configure_logging()
    if args.provider:
        os.environ["AI_PROVIDER"] = args.provider
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

"""offerte: offerte/orchestrator.py"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
from collections.abc import Callable

from offerte._constants import *  # noqa: F401,F403
from offerte.models import Offerta
from offerte import source_status
from offerte.config import SEARCH_TOTAL_TIMEOUT
from offerte.log import get_logger
from offerte.parsing import *  # noqa: F401,F403
from offerte.dedup import _deduplica

log = get_logger(__name__)

#: Etichetta mostrata all'utente → chiave del registry `SCRAPERS`. Esplicita
#: invece di derivata: "MediaWorld.it" → "mediaworld" funzionerebbe, ma
#: "Trovaprezzi.it" → "trovaprezzi" e "eBay.it" → "ebay" no, e uno sbaglio qui
#: si vedrebbe solo come uno stato fonte mancante nella UI.
_LABEL_TO_KEY: dict[str, str] = {
    "Amazon.it": "amazon",
    "eBay.it": "ebay",
    "Trovaprezzi.it": "trovaprezzi",
    "Vinted.it": "vinted",
    "Euronics.it": "euronics",
    "Unieuro.it": "unieuro",
    "MediaWorld.it": "mediaworld",
    "Wallapop": "wallapop",
    "Comet.it": "comet",
    "Expert.it": "expert",
    "Subito.it": "subito",
    "AliExpress.com": "aliexpress",
    "Temu.com": "temu",
    "Alibaba.com": "alibaba",
}
from offerte.ai import (
    fetch_specs_ai,
    filtra_risultati_con_ai,
)
from offerte.export import export_to_csv, print_results
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


def cerca_offerte(
    query: str,
    budget_max: float | None = None,
    prezzo_min: float = 0,
    filtri_ai: dict[str, str] | None = None,
    top_n: int = 10,
    export_csv: bool = False,
    csv_filename: str = "offerte.csv",
    condizione: str = "tutti",
    fonti: list[str] | None = None,
    categoria: str = "altro",
    cerebras_client: object | None = None,
    app_id: str = "",
    cert_id: str = "",
    progress_callback: Callable[[str, int], None] | None = None,
) -> list[Offerta]:
    """
    Cerca offerte tech su amazon.it, ebay.it, euronics.it e mediaworld.it.

    Args:
        query:        Testo della ricerca (es. "notebook 14 pollici 16gb RAM").
        prezzo_min:   Prezzo minimo in euro (default: 0).
        budget_max:   Prezzo massimo in euro. None = nessun limite.
        filtri_ai:    Filtri semantici post-scraping (es. colore, storage).
        top_n:        Quante offerte mostrare (default: 10).
        export_csv:   Se True, salva i risultati in un file CSV.
        csv_filename: Nome del file CSV di output (default: "offerte.csv").
        condizione:   Filtro stato prodotto Amazon: "tutti", "nuovo", "usato".
        fonti:        Fonti da usare (amazon, ebay, vinted, euronics, unieuro, mediaworld). None = tutte.
        categoria:    Categoria normalizzata (tech, abbigliamento, altro).
        cerebras_client: Client Cerebras opzionale per l'arricchimento specs.
        app_id:       App ID eBay Browse API.
        cert_id:      Cert ID eBay Browse API.

    Returns:
        Lista di Offerta ordinata per prezzo crescente (max top_n elementi).
    """
    if not query or not query.strip():
        print("❌ La query di ricerca non può essere vuota.")
        return []

    prezzo_min = max(0.0, float(prezzo_min))

    print(f"\n{'=' * 70}")
    print(f'  🚀 Avvio ricerca: "{query}"')
    print(f"  💵 Prezzo min: € {prezzo_min:.2f}")
    if budget_max is not None:
        print(f"  💵 Budget max: € {budget_max:.2f}")
    print(f"  🏷️  Condizione: {condizione}")
    print(f"  📊 Mostra top: {top_n} risultati")
    print(f"{'=' * 70}")

    # Tokenizzazione query per il filtro di rilevanza
    query_tokens = tokenize_query(query)
    print(f"\n  🔑 Token di ricerca: {query_tokens}")

    fonti_norm = {f.strip().lower() for f in (fonti or []) if str(f).strip()}
    if not fonti_norm:
        # subito/aliexpress/temu/alibaba sono bloccati da bot-protection — esclusi dal default
        fonti_norm = {
            "amazon",
            "ebay",
            "trovaprezzi",
            "vinted",
            "euronics",
            "unieuro",
            "mediaworld",
            "wallapop",
            "comet",
            "expert",
        }
    log.info("Fonti attive: %s", ", ".join(sorted(fonti_norm)))

    # Lo stato per-fonte è per-ricerca: azzerarlo qui evita che la UI mostri il
    # blocco rimediato nella ricerca precedente.
    source_status.reset()

    # Lancio scraper in parallelo sulle fonti selezionate
    offerte: list[Offerta] = []
    future_to_label: dict = {}

    def _timed_call(fn: Callable, label: str, *args, **kwargs):
        """Cronometra lo scraper e registra l'esito della fonte.

        Qui si sa distinguere "è esploso" da "ha risposto": il caso *bloccato* lo
        segnala lo scraper stesso, che è l'unico a vedere lo status HTTP, e
        `source_status.report` non lo declassa a `empty`.
        """
        key = _LABEL_TO_KEY.get(label, label.split(".")[0].lower())
        t0 = time.perf_counter()
        try:
            res = fn(*args, **kwargs)
        except BaseException as exc:
            source_status.report_error(key, exc)
            raise
        finally:
            elapsed = time.perf_counter() - t0
            # Logging, non st.*: il thread non ha ScriptRunContext e Streamlit
            # emetterebbe un warning per ogni chiamata.
            log.debug("scrape %s: %.2fs", label, elapsed)
        n = len(res or [])
        if n:
            source_status.report_ok(key, n)
        else:
            source_status.report_empty(key)
        return res

    # NB: executor esplicito, non `with`. Il context manager fa join di tutti i
    # thread all'uscita, quindi una fonte appesa bloccherebbe la ricerca per il
    # suo intero timeout anche dopo che abbiamo smesso di attenderne il risultato:
    # il tetto sul tempo totale non avrebbe effetto sul tempo percepito.
    executor = ThreadPoolExecutor(max_workers=10)
    try:
        if "amazon" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_amazon,
                    "Amazon.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                    condizione,
                )
            ] = "Amazon.it"
        if "ebay" in fonti_norm:
            if app_id and cert_id:
                future_to_label[
                    executor.submit(
                        _timed_call,
                        scrape_ebay,
                        "eBay.it",
                        query,
                        prezzo_min,
                        budget_max,
                        condizione,
                        query_tokens,
                        app_id,
                        cert_id,
                    )
                ] = "eBay.it"
            else:
                print("    ⚠️  eBay non configurato — chiavi mancanti.")
                if progress_callback:
                    progress_callback("eBay.it", -2)
        if "vinted" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_vinted,
                    "Vinted.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                    condizione,
                )
            ] = "Vinted.it"
        if "euronics" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_euronics,
                    "Euronics.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "Euronics.it"
        if "trovaprezzi" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_trovaprezzi,
                    "Trovaprezzi.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "Trovaprezzi.it"
        if "unieuro" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_unieuro,
                    "Unieuro.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "Unieuro.it"
        if "mediaworld" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_mediaworld,
                    "MediaWorld.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                    condizione,
                )
            ] = "MediaWorld.it"
        if "wallapop" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_wallapop,
                    "Wallapop",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                    condizione,
                )
            ] = "Wallapop"
        if "comet" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_comet,
                    "Comet.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "Comet.it"
        if "expert" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_expert,
                    "Expert.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "Expert.it"
        if "subito" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_subito,
                    "Subito.it",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                    condizione,
                )
            ] = "Subito.it"
        if "aliexpress" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_aliexpress,
                    "AliExpress.com",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "AliExpress.com"
        if "temu" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_temu,
                    "Temu.com",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "Temu.com"
        if "alibaba" in fonti_norm:
            future_to_label[
                executor.submit(
                    _timed_call,
                    scrape_alibaba,
                    "Alibaba.com",
                    query,
                    prezzo_min,
                    budget_max,
                    query_tokens,
                )
            ] = "Alibaba.com"

        # Cap per-source: evita che una singola fonte (es. eBay con 50 risultati) soffochi le altre.
        # Distribuiamo top_n diviso per numer fonti per non avere un dominio assoluto, + extra safety margin
        _per_source_cap = max((top_n // max(1, len(fonti_norm))) + 5, 10)
        # Tetto al tempo totale: prima non c'era, quindi una fonte appesa
        # trascinava l'intera ricerca finché non scadevano i retry di `requests`
        # (TIMEOUT × (1+MAX_RETRIES)) — e il `with` non poteva chiudersi.
        _deadline = time.monotonic() + SEARCH_TOTAL_TIMEOUT
        try:
            for future in as_completed(
                future_to_label, timeout=max(1.0, _deadline - time.monotonic())
            ):
                label = future_to_label[future]
                try:
                    new_results = future.result()
                    new_results = new_results[:_per_source_cap]
                    offerte += new_results
                    if progress_callback:
                        progress_callback(label, len(new_results))
                except Exception as exc:
                    log.warning("%s: errore inatteso: %s", label, exc)
                    if progress_callback:
                        progress_callback(label, -1)
        except FutureTimeout:
            # Restituire i parziali è meglio che restituire niente: le fonti che
            # hanno risposto sono già in `offerte`.
            _pendenti = [lbl for fut, lbl in future_to_label.items() if not fut.done()]
            log.warning(
                "Ricerca interrotta dopo %.0fs: %d fonti ancora in corso (%s). "
                "Restituisco i risultati parziali.",
                SEARCH_TOTAL_TIMEOUT,
                len(_pendenti),
                ", ".join(_pendenti) or "-",
            )
            for lbl in _pendenti:
                # Va registrato come errore, non lasciato a `empty`: la fonte non
                # ha risposto in tempo, e `report()` impedisce che il `report_empty`
                # tardivo del thread ancora in corso sovrascriva questo motivo.
                source_status.report_error(_LABEL_TO_KEY.get(lbl, lbl.lower()), "timeout ricerca")
                if progress_callback:
                    progress_callback(lbl, -1)
    finally:
        # `wait=False` è il punto: i thread I/O-bound non sono interrompibili, ma
        # non li aspettiamo. Quelli non ancora partiti vengono annullati; quelli in
        # corso muoiono da soli allo scadere del timeout di `requests`.
        executor.shutdown(wait=False, cancel_futures=True)

    log.info("Totale risultati grezzi (post-filtro): %d", len(offerte))

    # Filtro finale range prezzo
    offerte = [
        o
        for o in offerte
        if o.prezzo >= prezzo_min and (budget_max is None or o.prezzo <= budget_max)
    ]
    print(f"  🧹 Dopo filtro range prezzo: {len(offerte)}")

    filtri_ai_effettivi = {
        k: v for k, v in (filtri_ai or {}).items() if str(k).strip() and str(v).strip()
    }
    if filtri_ai_effettivi:
        offerte = filtra_risultati_con_ai(offerte, filtri_ai_effettivi)
        print(f"  🎯 Dopo filtro/ranking AI: {len(offerte)}")

    # Filtro post-scraping condizione: rimuove ricondizionati/usati se l'utente vuole solo "nuovo"
    _KW_RICONDIZIONATO = {
        "ricondizionato",
        "refurbished",
        "rigenerato",
        "reconditioned",
        "second life",
        "open box",
        "ricondizionata",
        "usato",
        "used",
    }
    if condizione == "nuovo":
        offerte = [o for o in offerte if not any(k in o.nome.lower() for k in _KW_RICONDIZIONATO)]
        print(f"  🏷️  Dopo filtro 'nuovo' (no ricondizionati): {len(offerte)}")
    elif condizione == "usato":
        # Per usato su Amazon/store fisici filtra solo ricondizionati espliciti; eBay usa già conditionIds
        pass

    # Deduplicazione
    offerte = _deduplica(offerte)
    print(f"  🔄 Dopo deduplicazione: {len(offerte)} offerte uniche")

    # Ordinamento finale con ranking basato su spec tokens trovati nei titoli.
    spec_tokens = {t for t in query_tokens if _is_spec_token(t)}
    if not filtri_ai_effettivi:
        if spec_tokens:

            def _spec_score(o: Offerta) -> int:
                nl = o.nome.lower()
                return sum(
                    1 for t in spec_tokens if any(v in nl for v in _ALIASES.get(t, {t}) | {t})
                )

            offerte.sort(key=lambda o: (-_spec_score(o), o.prezzo))
        else:
            offerte.sort(key=lambda o: o.prezzo)

    # Tronca a top_n
    offerte_top = offerte[:top_n]

    if offerte_top:
        fetch_specs_ai(offerte_top, categoria, cerebras_client)

    # Output terminale
    print_results(offerte_top, query, budget_max, top_n)

    # Export CSV opzionale
    if export_csv and offerte_top:
        export_to_csv(offerte_top, csv_filename)

    return offerte_top

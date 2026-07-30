"""Test per le feature di prodotto: cache persistente, watchlist, price history."""

from __future__ import annotations

import os

import pytest


# ===========================================================================
# Cache ricerche persistente — offerte/cache.py
# ===========================================================================


def test_cache_key_is_order_independent_on_fonti() -> None:
    from offerte import cache

    k1 = cache.make_cache_key("iPhone 15", 0, 1000, "nuovo", ["amazon", "ebay"])
    k2 = cache.make_cache_key("iPhone 15", 0, 1000, "nuovo", ["ebay", "amazon"])
    assert k1 == k2


def test_cache_key_differs_on_query() -> None:
    from offerte import cache

    assert cache.make_cache_key("a", 0, 100, "tutti", []) != cache.make_cache_key(
        "b", 0, 100, "tutti", []
    )


def test_cache_write_then_read_within_ttl(tmp_path) -> None:
    from offerte import cache

    p = os.path.join(tmp_path, "cache.json")
    key = cache.make_cache_key("notebook", 0, 800, "nuovo", ["amazon"])
    cache.write(key, [{"nome": "Notebook X", "prezzo": 799.0}], now=1000.0, path=p)
    got = cache.read(key, ttl=300, now=1100.0, path=p)
    assert got == [{"nome": "Notebook X", "prezzo": 799.0}]


def test_cache_read_expired_returns_none(tmp_path) -> None:
    from offerte import cache

    p = os.path.join(tmp_path, "cache.json")
    key = cache.make_cache_key("notebook", 0, 800, "nuovo", ["amazon"])
    cache.write(key, [{"nome": "x"}], now=1000.0, path=p)
    assert cache.read(key, ttl=300, now=1500.0, path=p) is None  # 500s > 300s ttl


def test_cache_read_missing_key_returns_none(tmp_path) -> None:
    from offerte import cache

    p = os.path.join(tmp_path, "cache.json")
    assert cache.read("nope", ttl=300, now=1.0, path=p) is None


# ===========================================================================
# Watchlist / preferiti — watchlist.py
# ===========================================================================


def test_watchlist_add_then_load(tmp_path) -> None:
    import watchlist

    p = os.path.join(tmp_path, "wl.json")
    assert watchlist.add_item("iPhone 15", 999.0, "http://a/1", "amazon.it", path=p) is True
    items = watchlist.load(path=p)
    assert len(items) == 1
    assert items[0]["link"] == "http://a/1"
    assert items[0]["prezzo"] == 999.0


def test_watchlist_dedup_by_link(tmp_path) -> None:
    import watchlist

    p = os.path.join(tmp_path, "wl.json")
    assert watchlist.add_item("iPhone 15", 999.0, "http://a/1", "amazon.it", path=p) is True
    assert (
        watchlist.add_item("iPhone 15 (again)", 950.0, "http://a/1", "amazon.it", path=p) is False
    )
    assert len(watchlist.load(path=p)) == 1


def test_watchlist_remove(tmp_path) -> None:
    import watchlist

    p = os.path.join(tmp_path, "wl.json")
    watchlist.add_item("iPhone 15", 999.0, "http://a/1", "amazon.it", path=p)
    assert watchlist.is_watched("http://a/1", path=p) is True
    watchlist.remove("http://a/1", path=p)
    assert watchlist.is_watched("http://a/1", path=p) is False
    assert watchlist.load(path=p) == []


# ===========================================================================
# Price history + alert — price_history.py
# ===========================================================================


def test_price_history_records_and_reads(tmp_path) -> None:
    import price_history as ph

    p = os.path.join(tmp_path, "ph.json")
    ph.record("iphone 15", 999.0, now=1.0, path=p)
    ph.record("iphone 15", 949.0, now=2.0, path=p)
    hist = ph.history_for("iphone 15", path=p)
    assert [e["min_price"] for e in hist] == [999.0, 949.0]


def test_price_history_isolated_per_query(tmp_path) -> None:
    import price_history as ph

    p = os.path.join(tmp_path, "ph.json")
    ph.record("iphone 15", 999.0, now=1.0, path=p)
    ph.record("galaxy s24", 700.0, now=2.0, path=p)
    assert len(ph.history_for("iphone 15", path=p)) == 1
    assert len(ph.history_for("galaxy s24", path=p)) == 1


def test_price_history_lowest_ever(tmp_path) -> None:
    import price_history as ph

    p = os.path.join(tmp_path, "ph.json")
    ph.record("iphone 15", 999.0, now=1.0, path=p)
    ph.record("iphone 15", 949.0, now=2.0, path=p)
    ph.record("iphone 15", 1020.0, now=3.0, path=p)
    assert ph.lowest_ever("iphone 15", path=p) == 949.0
    assert ph.lowest_ever("sconosciuto", path=p) is None


def test_price_history_is_new_low(tmp_path) -> None:
    import price_history as ph

    p = os.path.join(tmp_path, "ph.json")
    ph.record("iphone 15", 999.0, now=1.0, path=p)
    ph.record("iphone 15", 949.0, now=2.0, path=p)
    assert ph.is_new_low("iphone 15", 900.0, path=p) is True  # sotto il minimo storico
    assert ph.is_new_low("iphone 15", 1000.0, path=p) is False
    assert ph.is_new_low("mai visto", 50.0, path=p) is True  # nessuno storico = nuovo minimo


def test_price_below_threshold() -> None:
    import price_history as ph

    assert ph.below_threshold(900.0, 950.0) is True
    assert ph.below_threshold(1000.0, 950.0) is False
    assert ph.below_threshold(950.0, 950.0) is True  # uguale alla soglia = alert


# ===========================================================================
# Persistenza SQLite — offerte/db.py + offerte/migrations.py
# ===========================================================================


def test_db_enables_wal_and_stamps_schema_version(tmp_path) -> None:
    """WAL serve per non bloccare i lettori durante una scrittura."""
    from offerte.db import get_db
    from offerte.migrations import MIGRATIONS

    db = get_db(os.path.join(tmp_path, "app.db"))
    assert db.query_one("PRAGMA journal_mode")[0].lower() == "wal"
    assert int(db.query_one("PRAGMA user_version")[0]) == len(MIGRATIONS)


def test_db_corrupt_file_raises_instead_of_looking_empty(tmp_path) -> None:
    """Il difetto peggiore del vecchio storage JSON era proprio questo.

    `except JSONDecodeError: return []` faceva sembrare vuoto un file corrotto, e
    la prima scrittura successiva lo sovrascriveva: storico perso in silenzio.
    Un database illeggibile deve invece farsi sentire.
    """
    import sqlite3

    from offerte.db import get_db

    p = os.path.join(tmp_path, "corrotto.db")
    with open(p, "wb") as fh:
        fh.write(b"non sono un database sqlite" * 20)

    with pytest.raises(sqlite3.DatabaseError):
        get_db(p).query("SELECT 1 FROM watchlist")


def test_concurrent_cache_writes_do_not_lose_updates(tmp_path) -> None:
    """Regressione sulla lost update della cache.

    Il vecchio `write()` faceva load → mutate → dump dell'intero JSON senza
    lock: con più sessioni Streamlit due scritture concorrenti si sovrascrivevano
    e restava solo una chiave. Ora ogni chiave è una riga con UPSERT.
    """
    import threading

    from offerte import cache

    p = os.path.join(tmp_path, "app.db")
    n = 24

    def writer(i: int) -> None:
        cache.write(f"key-{i}", {"i": i}, now=1000.0, path=p)

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for i in range(n):
        assert cache.read(f"key-{i}", ttl=300, now=1000.0, path=p) == {"i": i}


def test_cache_purge_expired_removes_only_stale_entries(tmp_path) -> None:
    from offerte import cache

    p = os.path.join(tmp_path, "app.db")
    cache.write("vecchia", {"a": 1}, now=1000.0, path=p)
    cache.write("fresca", {"a": 2}, now=1900.0, path=p)

    removed = cache.purge_expired(ttl=300, now=2000.0, path=p)

    assert removed == 1
    assert cache.read("vecchia", ttl=300, now=2000.0, path=p) is None
    assert cache.read("fresca", ttl=300, now=2000.0, path=p) == {"a": 2}


def test_search_history_dedupes_and_caps_entries(tmp_path) -> None:
    import search_history as sh

    p = os.path.join(tmp_path, "app.db")
    for i in range(sh.MAX_ENTRIES + 5):
        sh.save_search(f"query {i}", 0, 800, "tutti", ["amazon"], i, path=p)
    # Stessa query con case diverso: deve aggiornare, non aggiungere.
    sh.save_search("QUERY 3", 0, 900, "nuovo", ["ebay"], 42, path=p)

    entries = sh.load_history(path=p)
    assert len(entries) <= sh.MAX_ENTRIES
    queries = [e["query"].lower() for e in entries]
    assert len(queries) == len(set(queries)), "storico con doppioni"


def test_legacy_json_is_imported_once_and_files_are_kept(tmp_path) -> None:
    """Chi aggiorna da una versione precedente non deve perdere i dati."""
    import json

    from offerte.db import get_db, reset_instances

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "watchlist.json").write_text(
        json.dumps(
            [{"nome": "Cuffie X", "prezzo": 59.9, "link": "https://x.it/1", "fonte": "amazon.it"}]
        ),
        encoding="utf-8",
    )
    (data_dir / "price_history.json").write_text(
        json.dumps([{"query": "cuffie", "min_price": 59.9, "ts": 1000.0}]), encoding="utf-8"
    )
    (data_dir / "auth_sessions.json").write_text(json.dumps({"fp1": 9999.0}), encoding="utf-8")

    db_path = str(data_dir / "app.db")
    db = get_db(db_path)

    assert db.query_one("SELECT COUNT(*) c FROM watchlist")["c"] == 1
    assert db.query_one("SELECT COUNT(*) c FROM price_history")["c"] == 1
    assert db.query_one("SELECT COUNT(*) c FROM auth_sessions")["c"] == 1
    # I file originali sono conservati, non cancellati.
    assert (data_dir / "watchlist.json.migrated").exists()
    assert not (data_dir / "watchlist.json").exists()

    # Riaprire il database non deve duplicare nulla.
    reset_instances()
    db2 = get_db(db_path)
    assert db2.query_one("SELECT COUNT(*) c FROM watchlist")["c"] == 1


# ===========================================================================
# Knowledge base — stato in SQLite, seed tracciato di sola lettura
# ===========================================================================


def test_saving_kb_does_not_touch_the_tracked_seed_file(monkeypatch, tmp_path) -> None:
    """Regressione: l'updater riscriveva `data/knowledge_base.json`, che è tracciato.

    Ogni avvio della UI lasciava quindi il working tree sporco con un diff di due
    righe (`updated_at`, `version`). Lo stato deve finire in SQLite e il seed
    restare byte-identico.
    """
    import knowledge_base as kb
    from offerte.db import get_db

    monkeypatch.setattr(kb, "get_db", lambda: get_db(os.path.join(tmp_path, "app.db")))

    seed_before = kb._KB_PATH.read_bytes()

    stato = kb.load_kb()
    stato["version"] = int(stato.get("version", 0)) + 1
    stato["updated_at"] = "2030-01-01T00:00:00"
    kb._save_kb(stato)

    assert kb._KB_PATH.read_bytes() == seed_before, "il seed tracciato è stato riscritto"
    assert kb.load_kb()["version"] == stato["version"], "lo stato non è stato riletto da SQLite"


def test_track_unknown_dedupes_via_primary_key(monkeypatch, tmp_path) -> None:
    """La dedup è un vincolo del database, non una lettura fuori dal lock."""
    import knowledge_base as kb
    from offerte.db import get_db

    db = get_db(os.path.join(tmp_path, "app.db"))
    monkeypatch.setattr(kb, "get_db", lambda: db)

    kb.track_unknown("smartphone", "Pixel 42 Ultra")
    kb.track_unknown("smartphone", "Pixel 42 Ultra")
    kb.track_unknown("laptop", "Pixel 42 Ultra")  # stessa voce, altra categoria

    assert db.query_one("SELECT COUNT(*) c FROM kb_unknown_items")["c"] == 2


def test_update_flag_is_claimed_atomically() -> None:
    """Due rerun ravvicinati non devono avviare due updater sulla stessa KB."""
    import threading

    import knowledge_base as kb

    kb._release_update()
    vincitori: list[bool] = []
    barrier = threading.Barrier(8)

    def contendi() -> None:
        barrier.wait()
        vincitori.append(kb._try_claim_update())

    threads = [threading.Thread(target=contendi) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sum(vincitori) == 1, "più di un thread ha preso il turno di aggiornamento"
    kb._release_update()
    assert kb._try_claim_update() is True, "il flag non è stato rilasciato"
    kb._release_update()


# ===========================================================================
# Stato per-fonte — offerte/source_status.py
# ===========================================================================


def test_source_status_keeps_the_reason_instead_of_downgrading_to_empty() -> None:
    """Il difetto che questo meccanismo esiste per risolvere.

    Gli scraper ritornano `[]` anche quando la fonte li ha rifiutati, e chi li
    avvolge riporta l'esito dalla lista: senza questa regola un `report_empty`
    successivo cancellerebbe il motivo, e l'utente rivedrebbe «0 risultati» al
    posto di «bloccata (HTTP 403)».
    """
    from offerte import source_status as ss

    ss.reset()

    ss.report_blocked("euronics", 403)
    ss.report_empty("euronics")  # è ciò che fa il wrapper dopo il `return []`
    assert ss.get("euronics").state == ss.BLOCKED
    assert "403" in ss.get("euronics").describe()

    ss.report_error("amazon", TimeoutError("lento"))
    ss.report_empty("amazon")
    assert ss.get("amazon").state == ss.ERROR

    ss.report_disabled("temu", "CAPTCHA")
    ss.report_empty("temu")
    assert ss.get("temu").state == ss.DISABLED

    # Una fonte che poi produce risultati invece deve poter passare a ok.
    ss.report_ok("euronics", 12)
    assert ss.get("euronics").state == ss.OK
    assert ss.get("euronics").results == 12


def test_source_status_distinguishes_problems_from_no_matches() -> None:
    from offerte import source_status as ss

    ss.reset()
    ss.report_ok("comet", 5)
    ss.report_empty("unieuro")
    ss.report_blocked("trovaprezzi", 403)
    ss.report_error("vinted", "timeout")
    ss.report_disabled("subito", "Akamai")

    problemi = {s.fonte for s in ss.problems()}
    # `empty` e `disabled` NON sono problemi: la prima è una risposta valida,
    # la seconda una scelta nostra.
    assert problemi == {"trovaprezzi", "vinted"}


def test_source_status_reset_clears_previous_search() -> None:
    """Senza il reset la UI mostrerebbe il blocco della ricerca precedente."""
    from offerte import source_status as ss

    ss.reset()
    ss.report_blocked("euronics", 403)
    assert ss.snapshot()
    ss.reset()
    assert ss.snapshot() == {}


def test_orchestrator_label_map_covers_every_scraper() -> None:
    """Un'etichetta non mappata farebbe sparire lo stato della fonte dalla UI."""
    from offerte import orchestrator
    from offerte.scrapers import SCRAPERS

    mapped = set(orchestrator._LABEL_TO_KEY.values())
    assert mapped == set(SCRAPERS), (
        f"mancanti: {set(SCRAPERS) - mapped}, in eccesso: {mapped - set(SCRAPERS)}"
    )


# ===========================================================================
# Filtro di rilevanza — offerte/filters.py
# ===========================================================================


@pytest.mark.parametrize(
    ("query", "nome", "atteso"),
    [
        # Query composte SOLO da token di specifica: con `strict_specs=False`
        # venivano tutti saltati, il ramo OR cadeva sul `return False` finale e
        # ogni fonte scartava il 100% dei prodotti. "ssd 1tb" dava 0 risultati
        # da tutte e 10 le fonti pur avendone trovati a decine.
        ("ssd 1tb", "Crucial P3 1TB PCIe M.2 2280 SSD", True),
        ("ssd 1tb", "Samsung SSD 990 EVO Plus 1 TB M.2 NVMe", True),
        ("ssd 1tb", "Frullatore Moulinex", False),
        ("16gb ram", "Corsair Vengeance 16GB DDR5", True),
        ("16gb ram", "Frullatore Moulinex", False),
        # Il ramo AND (>2 token) accettava invece QUALUNQUE nome per verità
        # vacua, perché anche lì ogni token veniva saltato.
        ("ssd 1tb 512gb", "Frullatore Moulinex", False),
        # Nessuna regressione sulle query normali.
        ("scarpe", "Scarpe Nike Air Max", True),
        ("scarpe", "Frullatore Moulinex", False),
        ("iphone 15", "Apple iPhone 15 128GB", True),
        ("iphone 15", "Samsung Galaxy S25 256GB", False),
        ("notebook 16gb", "Notebook Lenovo IdeaPad 16GB", True),
        ("notebook 16gb", "Frullatore Moulinex", False),
        ("notebook 14 pollici 16gb", "Lenovo Notebook 14 IdeaPad 16GB", True),
    ],
)
def test_is_relevant_handles_spec_only_queries(query: str, nome: str, atteso: bool) -> None:
    from offerte.filters import is_relevant
    from offerte.parsing import tokenize_query

    assert is_relevant(nome, tokenize_query(query), strict_specs=False) is atteso


@pytest.mark.parametrize(
    ("query", "nome", "atteso"),
    [
        # Nomi reali osservati nei probe. I risultati sono ordinati per prezzo
        # crescente e un accessorio costa una frazione del dispositivo, quindi
        # senza questo filtro la testa della classifica per "iphone 15" era un
        # copriobiettivo da 4,95 € e una custodia da 59 €.
        ("iphone 15", "CAMERALENS - APPLE IPHONE 15 PRO / IPHONE 15 PRO MAX", False),
        ("iphone 15", "APPLE - Custodia MagSafe iPhone 15-Trasparente", False),
        ("iphone 15", "Cellular Line Tempered Glass iPhone 15 Plus", False),
        ("iphone 15", "Cavo USB-C per iPhone 15", False),
        # Il dispositivo deve passare.
        ("iphone 15", "Apple iPhone 15 128GB Nero", True),
        ("iphone 15", "APPLE - iPhone 15 Plus 128GB-Nero", True),
        # Se la query nomina l'accessorio, l'accessorio è il risultato voluto.
        ("custodia iphone 15", "APPLE - Custodia MagSafe iPhone 15-Trasparente", True),
        ("cover iphone 15", "Cover silicone iPhone 15 nera", True),
        # Marchi di soli accessori: il nome non sempre dice cosa sia il prodotto
        # ("SBS TESINCAMGLIP15"), quindi il marchio è il segnale.
        ("iphone 15", "Cellularline Protection Kit iPhone 15 Pro Max", False),
        ("iphone 15", "SBS - Camera glass TESINCAMGLIP15 iPhone 15/15 Plus", False),
        # Pezzi di ricambio dai marketplace dell'usato.
        ("iphone 15", "Scheda madre iPhone 15 256gb", False),
        ("iphone 15", "Chassis iPhone 15 Originale Apple", False),
        # Vinted e Wallapop sono paneuropei: gli annunci arrivano nella lingua
        # del venditore. Nomi reali osservati — senza queste voci i primi 12
        # risultati per "iphone 15" erano tutti custodie estere da 1,00 €.
        ("iphone 15", "Lot de 2 coques iPhone 15", False),
        ("iphone 15", "Funda iPhone 15 plus", False),
        ("iphone 15", "Carcasa iPhone 15", False),
        ("iphone 15", "Iphone 15 hoesje met bloem", False),
        # Tedesco: i sostantivi si compongono ("Handyhülle", "Hüllen"), quindi
        # servono le radici cercate come sottostringa e non come parola intera.
        ("iphone 15", "Handyhülle IPhone 15 Babyblau", False),
        ("iphone 15", "iPhone 15 Pro Max Handyhuellen", False),
        ("iphone 15", "Panzerglas iPhone 15", False),
        # Nessuna regressione su query non-accessorio.
        ("notebook 14 pollici 16gb", "Lenovo Notebook 14 IdeaPad 16GB", True),
        ("ssd 1tb", "Crucial P3 1TB PCIe M.2 SSD", True),
        ("scarpe", "Scarpe Nike Air Max", True),
        # "display" e "batteria" sono esclusi dalla lista di proposito: sono anche
        # prodotti a sé, e filtrarli romperebbe queste ricerche.
        ("monitor 27 pollici", "MONITOR SB243YG0BI - 23.8 POLLICI - NERO", True),
        ("powerbank 20000", "Batteria esterna powerbank 20000mAh", True),
    ],
)
def test_is_relevant_drops_accessories_unless_requested(
    query: str, nome: str, atteso: bool
) -> None:
    from offerte.filters import is_relevant
    from offerte.parsing import tokenize_query

    assert is_relevant(nome, tokenize_query(query), strict_specs=False) is atteso


def test_baseline_covers_every_source() -> None:
    """Una fonte fuori dalla baseline non verrebbe mai sorvegliata dal canary."""
    from offerte.scrapers import SCRAPERS
    from tests.probe_scrapers import load_baseline

    baseline = {k: v for k, v in load_baseline().items() if not k.startswith("_")}
    assert set(baseline) == set(SCRAPERS), (
        f"mancanti: {set(SCRAPERS) - set(baseline)}, in eccesso: {set(baseline) - set(SCRAPERS)}"
    )
    for fonte, cfg in baseline.items():
        assert cfg.get("expected") in {"ok", "blocked", "disabled", "any"}, fonte


def test_canary_alerts_only_on_regressions() -> None:
    """Il canary deve suonare quando una fonte 'ok' cade, e stare zitto altrimenti.

    Se allertasse anche sui miglioramenti o sulle fonti intermittenti, suonerebbe
    ogni settimana e verrebbe ignorato — che è come non averlo.
    """
    from tests.probe_scrapers import compare_with_baseline

    baseline = {
        "euronics": {"expected": "ok"},
        "comet": {"expected": "ok"},
        "amazon": {"expected": "blocked"},
        "aliexpress": {"expected": "any"},
        "subito": {"expected": "disabled"},
    }
    results = [
        # Regressione vera: attesa ok, ora bloccata.
        {"fonte": "euronics", "state": "blocked", "detail": "HTTP 403", "results": 0},
        # Va bene.
        {"fonte": "comet", "state": "ok", "detail": "", "results": 40},
        # Miglioramento: attesa bloccata, oggi funziona. Non è un guasto.
        {"fonte": "amazon", "state": "ok", "detail": "", "results": 28},
        # Intermittente dichiarata: silenziata.
        {"fonte": "aliexpress", "state": "empty", "detail": "", "results": 0},
        # Disattivata per scelta: nessun allarme.
        {"fonte": "subito", "state": "disabled", "detail": "Akamai", "results": 0},
    ]

    deviations = compare_with_baseline(results, baseline)

    assert [d["fonte"] for d in deviations] == ["euronics"]
    assert deviations[0]["osservato"] == "blocked"
    assert "403" in deviations[0]["detail"]


def test_canary_flags_a_source_that_went_silent() -> None:
    """Anche `empty` è una regressione per una fonte attesa `ok`.

    È il caso che è davvero successo: Trovaprezzi rispondeva 200 ma i selettori
    erano cambiati, quindi zero risultati senza alcun errore.
    """
    from tests.probe_scrapers import compare_with_baseline

    deviations = compare_with_baseline(
        [{"fonte": "trovaprezzi", "state": "empty", "detail": "", "results": 0}],
        {"trovaprezzi": {"expected": "ok"}},
    )
    assert len(deviations) == 1
    assert deviations[0]["osservato"] == "empty"


def test_accessory_filter_is_disabled_when_the_query_asks_for_one() -> None:
    """Il filtro guarda la query, non solo il nome del prodotto."""
    from offerte.filters import looks_like_accessory

    assert looks_like_accessory("Custodia MagSafe iPhone 15", ["iphone", "15"]) is True
    assert looks_like_accessory("Custodia MagSafe iPhone 15", ["custodia", "iphone"]) is False
    # Match su parola intera: "casena" o "docker" non devono attivare "case"/"dock".
    assert looks_like_accessory("Nokia Casena 3310", ["nokia"]) is False
    assert looks_like_accessory("Docker Book", ["libro"]) is False


# ===========================================================================
# Tetto al tempo totale di ricerca — offerte/orchestrator.py
# ===========================================================================


def test_search_returns_partials_without_waiting_for_a_hung_source(monkeypatch) -> None:
    """Una fonte appesa non deve trattenere l'intera ricerca.

    Due difetti in uno: senza tetto l'attesa durava finché non scadevano i retry
    di `requests`, e con il solo `as_completed(timeout=…)` il tempo *percepito*
    restava invariato perché `with ThreadPoolExecutor(...)` fa join dei thread
    all'uscita. Misurato: 60s prima, ~1s dopo.
    """
    import time

    from offerte import orchestrator as orch
    from offerte import source_status as ss
    from offerte.models import Offerta

    def _appeso(*a, **k):
        time.sleep(30)
        return []

    def _veloce(*a, **k):
        return [
            Offerta(
                nome="Prodotto veloce",
                prezzo=50.0,
                negozio="X",
                link="http://x",
                fonte="comet.it",
            )
        ]

    monkeypatch.setattr(orch, "scrape_euronics", _appeso)
    monkeypatch.setattr(orch, "scrape_comet", _veloce)
    monkeypatch.setattr(orch, "SEARCH_TOTAL_TIMEOUT", 1.0)

    t0 = time.perf_counter()
    results = orch.cerca_offerte(
        "test", budget_max=100.0, prezzo_min=0.0, top_n=5, fonti=["euronics", "comet"]
    )
    elapsed = time.perf_counter() - t0

    assert elapsed < 10, f"la ricerca ha atteso la fonte appesa ({elapsed:.1f}s)"
    assert len(results) == 1, "i risultati parziali della fonte veloce sono andati persi"

    stato = ss.snapshot()
    assert stato["comet"].state == ss.OK
    # La fonte scaduta va segnalata come problema, non come "nessun risultato":
    # il `report_empty` tardivo del thread ancora in corso non deve sovrascriverlo.
    assert stato["euronics"].state == ss.ERROR
    assert "timeout" in stato["euronics"].describe()


# ===========================================================================
# Pannello stato fonti nella UI — ui/sources.py
# ===========================================================================


def test_ui_source_rows_reflect_the_real_status() -> None:
    """La UI deve leggere il registro, non dedurre il blocco dal testo del log.

    Prima cercava nel log stringhe come `f"{key} -> errore"`, un formato che
    nessuno stampava: lo stato "BLOCCATA" era irraggiungibile e una fonte che ci
    aveva rifiutati appariva identica a una senza risultati.
    """
    from offerte import source_status as ss
    from offerte.models import Offerta
    from ui.sources import _status_rows_for_sources

    ss.reset()
    ss.report_ok("comet", 2)
    ss.report_blocked("euronics", 403)
    ss.report_error("amazon", "timeout")
    ss.report_empty("unieuro")
    ss.report_disabled("temu", "CAPTCHA")

    offerte = [
        Offerta(nome="A", prezzo=10.0, negozio="Comet", link="http://a", fonte="comet.it"),
        Offerta(nome="B", prezzo=20.0, negozio="Comet", link="http://b", fonte="comet.it"),
    ]
    fonti = ["comet", "euronics", "amazon", "unieuro", "temu"]

    # `log_text` deliberatamente vuoto: se la UI dipendesse ancora da lui, gli
    # stati problematici sparirebbero.
    rows = _status_rows_for_sources(offerte, fonti, log_text="")
    by_label = {r["label"]: r for r in rows}

    assert by_label["Comet.it"]["status"] == "ONLINE"
    assert "2 risultati" in by_label["Comet.it"]["detail"]

    assert by_label["Euronics.it"]["status"] == "BLOCCATA"
    assert "403" in by_label["Euronics.it"]["detail"]

    assert by_label["Amazon.it"]["status"] == "ERRORE"

    # Ha risposto, semplicemente non c'era nulla: non è un problema della fonte.
    assert by_label["Unieuro.it"]["status"] == "ONLINE"

    assert by_label["Temu"]["status"] == "DISATTIVATA"
    assert "CAPTCHA" in by_label["Temu"]["detail"]

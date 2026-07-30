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

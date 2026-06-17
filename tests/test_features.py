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

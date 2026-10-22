"""Le dipendenze opzionali e i payload incompleti non devono rompere la UI."""

import builtins
from pathlib import Path
import runpy

import pytest

from ui import presearch


def test_history_fallback_preserves_the_optional_module_call_interface(monkeypatch, tmp_path):
    original_import = builtins.__import__

    def without_history(name, *args, **kwargs):
        if name == "search_history":
            raise ImportError("optional history unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_history)
    namespace = runpy.run_path(str(Path(__file__).resolve().parents[1] / "ui" / "state.py"))
    path = str(tmp_path / "unused.sqlite3")
    assert namespace["load_history"](path=path) == []
    assert namespace["_save_search"]("notebook", 0, 800, "tutti", ["comet"], 2, path=path) is None
    assert namespace["load_history"]() == []
    assert not Path(path).exists()


@pytest.mark.parametrize("questions", [None, 42, "Quale uso?", {"question": "Quale uso?"}])
def test_invalid_clarifying_questions_do_not_crash_or_become_characters(monkeypatch, questions):
    monkeypatch.setattr(
        presearch, "detect_category_and_questions", lambda text: {"domande": questions}
    )
    assert presearch._pick_clarifying_question("notebook", set()) is None


def test_clarifying_questions_keep_the_first_unasked_valid_question(monkeypatch):
    monkeypatch.setattr(
        presearch,
        "detect_category_and_questions",
        lambda text: {"domande": ["Quale budget?", "  Quale uso?  "]},
    )
    assert presearch._pick_clarifying_question("notebook", {"Quale budget?"}) == "Quale uso?"


@pytest.mark.parametrize(
    "transcript,expected",
    [
        ("iPhone 15 da 128GB massimo 900", 0),
        ("notebook da 16 GB RAM massimo 900 euro", 0),
        ("notebook minimo 16GB RAM", 0),
        ("iPhone 15 minimo 500 euro massimo 900", 500),
        ("iPhone 15 da €500", 500),
        ("iPhone 15 da 500euro", 500),
        ("iPhone 15 da 500 a 900 euro", 500),
        ("iPhone 15 tra 500 e 900 euro", 500),
        ("iPhone 15 budget 500–900", 500),
        ("iPhone 15 da 128GB a 256GB massimo 900", 0),
    ],
)
def test_only_explicit_monetary_minimum_overrides_zero(transcript, expected):
    assert presearch._explicit_price_min(transcript) == expected
    payload = {"query": "iphone 15", "prezzo_min": 650, "budget_max": 900}
    result = presearch._sanitize_presearch_payload(payload, transcript)
    assert result["prezzo_min"] == expected
    assert result["budget_max"] == 900


def test_phone_fallback_does_not_invent_a_minimum(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        presearch,
        "st",
        SimpleNamespace(
            session_state={
                "preferenze_utente": {"trascrizione": "iPhone 15 da 128GB massimo 900"},
                "presearch_question_count": 1,
            }
        ),
    )
    result = presearch._presearch_fallback()
    assert result["prezzo_min"] == 0
    assert result["budget_max"] == 900


@pytest.mark.parametrize("suffix,minimum", [("massimo 900", 0), ("tra 500 e 900 euro", 500)])
def test_comparison_uses_only_explicit_minimum(monkeypatch, suffix, minimum):
    from types import SimpleNamespace

    state = {"presearch_messages": [], "preferenze_utente": {}}
    queued = []
    monkeypatch.setattr(presearch, "st", SimpleNamespace(session_state=state))
    monkeypatch.setattr(
        presearch, "parse_comparison_query", lambda text: ["iphone 15", "iphone 16"]
    )
    monkeypatch.setattr(presearch, "_queue_price_sync", lambda lo, hi: queued.append((lo, hi)))
    presearch._run_presearch_step("iphone 15 vs iphone 16 " + suffix, "")
    assert queued[0][0] == minimum

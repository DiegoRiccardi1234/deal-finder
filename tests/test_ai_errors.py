"""Errori AI comprensibili e fallback locale senza esporre dati del provider."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ui import presearch, state
from ui.ai_client import _ai_error_message


@pytest.mark.parametrize(
    ("status", "expected"),
    [(402, "piano"), (401, "chiave"), (403, "autorizzata"), (429, "sovraccarico")],
)
def test_ai_status_has_actionable_message_without_raw_provider_data(status, expected):
    secret = "sk-private-test-token"
    exc = RuntimeError(f"HTTP {status}: Authorization Bearer {secret}; provider response body")

    message = _ai_error_message(exc)

    assert expected in message
    assert "Impostazioni" in message
    assert secret not in message
    assert "provider response body" not in message


def test_sdk_status_takes_precedence_over_numbers_in_error_body():
    exc = RuntimeError("Request 429: private provider details")
    exc.status_code = 402

    assert "HTTP 402" in _ai_error_message(exc)


def test_http_response_status_is_recognized():
    exc = RuntimeError("private response")
    exc.response = SimpleNamespace(status_code=401)

    assert "chiave" in _ai_error_message(exc)


def test_unknown_error_message_does_not_reveal_exception():
    message = _ai_error_message(RuntimeError("secret-token stacktrace https://private.invalid"))

    assert "Impostazioni" in message
    assert "secret-token" not in message
    assert "stacktrace" not in message
    assert "private.invalid" not in message


@pytest.mark.parametrize("status", [402, 401, 429])
def test_presearch_failure_warns_and_preserves_heuristic_search(monkeypatch, status):
    ui = SimpleNamespace(session_state={}, warning=MagicMock())
    monkeypatch.setattr(presearch, "st", ui)
    monkeypatch.setattr(state, "st", ui)
    state._init_state()
    monkeypatch.setattr(presearch, "kb_manager", None)
    monkeypatch.setattr(presearch, "parse_comparison_query", lambda message: [])
    monkeypatch.setattr(presearch, "_get_ai_client", lambda api_key: object())

    def unavailable(*args, **kwargs):
        raise RuntimeError(f"HTTP {status}: sk-private-test-token")

    monkeypatch.setattr(presearch, "_ai_chat_with_retry", unavailable)
    presearch._run_presearch_step("notebook 16gb ram 1tb budget 800 euro per studio", "test-key")

    warning = ui.session_state["presearch_ai_warning"]
    ui.warning.assert_called_once_with(warning)
    assert "senza AI" in warning
    assert "Impostazioni" in warning
    assert "sk-private-test-token" not in warning
    assert ui.session_state["presearch_ready"] is True
    assert ui.session_state["query_ottimizzata"] == "notebook"
    assert ui.session_state["budget_max"] == 800
    assert ui.session_state["filtri_ai"] == {"ram": "16gb", "storage": "1tb"}

    # Il messaggio persiste al rerun ma sparisce quando una nuova richiesta riesce.
    monkeypatch.setattr(
        presearch,
        "_ai_chat_with_retry",
        lambda *args, **kwargs: json.dumps(
            {"pronto": True, "query": "notebook", "budget_max": 800, "categoria": "laptop"}
        ),
    )
    presearch._run_presearch_step("preferisco Lenovo", "test-key")
    assert "presearch_ai_warning" not in ui.session_state


def test_restart_clears_obsolete_ai_warning(monkeypatch):
    ui = SimpleNamespace(session_state={"presearch_ai_warning": "Vecchio errore"})
    monkeypatch.setattr(presearch, "st", ui)

    presearch._reset_presearch_chat()

    assert "presearch_ai_warning" not in ui.session_state

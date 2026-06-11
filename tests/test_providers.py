"""Test per l'astrazione multi-provider AI (offerte/providers.py)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


_ALL_KEYS = [
    "CEREBRAS_API_KEY", "GROQ_API_KEY", "OPENAI_API_KEY",
    "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
]


def _clear_keys(monkeypatch):
    for e in _ALL_KEYS + ["AI_PROVIDER"]:
        monkeypatch.delenv(e, raising=False)


def test_registry_has_all_providers():
    from offerte import providers
    for p in ["cerebras", "groq", "openai", "openrouter", "anthropic", "gemini"]:
        assert p in providers.PROVIDERS


def test_active_provider_default_is_cerebras(monkeypatch):
    _clear_keys(monkeypatch)
    from offerte import providers
    assert providers.active_provider() == "cerebras"


def test_active_provider_from_env(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "groq")
    from offerte import providers
    assert providers.active_provider() == "groq"


def test_active_provider_invalid_falls_back_to_cerebras(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "does-not-exist")
    from offerte import providers
    assert providers.active_provider() == "cerebras"


def test_get_api_key_reads_provider_specific_env(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "gk-123")
    from offerte import providers
    assert providers.get_api_key("groq") == "gk-123"
    assert providers.get_api_key("openai") == ""


def test_is_configured(monkeypatch):
    _clear_keys(monkeypatch)
    from offerte import providers
    assert providers.is_configured("openai") is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-1")
    assert providers.is_configured("openai") is True


def test_configured_providers_lists_only_those_with_keys(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    from offerte import providers
    assert set(providers.configured_providers()) == {"groq", "anthropic"}


def test_anthropic_adapter_normalizes_to_openai_shape():
    from offerte.providers import _AnthropicAdapter
    fake = MagicMock()
    fake.messages.create.return_value = MagicMock(content=[MagicMock(text="ciao mondo")])
    client = _AnthropicAdapter(fake)
    resp = client.chat.completions.create(
        model="claude-x", messages=[{"role": "system", "content": "sii breve"}, {"role": "user", "content": "hi"}]
    )
    assert resp.choices[0].message.content == "ciao mondo"
    # il system message va passato come parametro `system`, non in messages
    _, kwargs = fake.messages.create.call_args
    assert kwargs.get("system") == "sii breve"
    assert all(m["role"] != "system" for m in kwargs.get("messages", []))


def test_anthropic_adapter_models_list_has_ids():
    from offerte.providers import _AnthropicAdapter
    client = _AnthropicAdapter(MagicMock())
    ids = [m.id for m in client.models.list().data]
    assert ids and all(isinstance(i, str) for i in ids)


def test_gemini_adapter_normalizes_to_openai_shape():
    from offerte.providers import _GeminiAdapter
    fake_model = MagicMock()
    fake_model.generate_content.return_value = MagicMock(text="risposta gemini")
    adapter = _GeminiAdapter(model_factory=lambda name: fake_model)
    resp = adapter.chat.completions.create(
        model="gemini-2.0-flash", messages=[{"role": "user", "content": "ciao"}]
    )
    assert resp.choices[0].message.content == "risposta gemini"


def test_gemini_adapter_models_list_has_ids():
    from offerte.providers import _GeminiAdapter
    adapter = _GeminiAdapter(model_factory=lambda name: MagicMock())
    ids = [m.id for m in adapter.models.list().data]
    assert ids and all(isinstance(i, str) for i in ids)


def test_best_model_prefers_available_candidate():
    from offerte import providers
    client = MagicMock()
    client.models.list.return_value = MagicMock(data=[
        MagicMock(id="gpt-4o", context_window=0),
        MagicMock(id="gpt-4o-mini", context_window=0),
    ])
    # candidati openai = (gpt-4o-mini, gpt-4o): vince il primo candidato disponibile
    assert providers.best_model("openai", client) == "gpt-4o-mini"


def test_load_keys_from_secrets_sets_env(monkeypatch):
    import os
    _clear_keys(monkeypatch)
    from offerte import providers

    class _Secrets:
        def __init__(self, d):
            self._d = d
        def get(self, k, default=None):
            return self._d.get(k, default)

    providers.load_keys_from(_Secrets({"GROQ_API_KEY": "gk-xyz", "AI_PROVIDER": "groq"}))
    assert os.environ.get("GROQ_API_KEY") == "gk-xyz"
    assert os.environ.get("AI_PROVIDER") == "groq"


def test_load_keys_from_does_not_override_existing_env(monkeypatch):
    import os
    _clear_keys(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "from-env")
    from offerte import providers

    class _Secrets:
        def get(self, k, default=None):
            return {"OPENAI_API_KEY": "from-secrets"}.get(k, default)

    providers.load_keys_from(_Secrets())
    assert os.environ.get("OPENAI_API_KEY") == "from-env"


def test_best_model_falls_back_to_largest_context_window():
    from offerte import providers
    client = MagicMock()
    client.models.list.return_value = MagicMock(data=[
        MagicMock(id="x-small", context_window=8000),
        MagicMock(id="x-large", context_window=128000),
    ])
    # nessun candidato cerebras presente → si sceglie il context_window più ampio
    assert providers.best_model("cerebras", client) == "x-large"

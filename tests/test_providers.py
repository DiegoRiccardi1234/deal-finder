"""Test per l'astrazione multi-provider AI (offerte/providers.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest


_ALL_KEYS = [
    "CEREBRAS_API_KEY",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
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
        model="claude-x",
        messages=[{"role": "system", "content": "sii breve"}, {"role": "user", "content": "hi"}],
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


def test_gemini_passa_dall_endpoint_openai_compatible(monkeypatch):
    """Gemini non deve più dipendere dall'SDK `google-generativeai`.

    Quell'SDK si trascinava `googleapiclient` e `grpc` — 111 MB nel bundle
    Windows — e per giunta non sapeva elencare i modelli, quindi la scelta
    automatica leggeva una lista finta. Il test guarda il client costruito, non
    la tabella: è la tabella che potrebbe mentire.
    """
    _clear_keys(monkeypatch)
    monkeypatch.setenv("GEMINI_API_KEY", "gk-finta")
    from offerte import providers

    client = providers.build_client("gemini")
    assert client is not None
    assert type(client).__module__.startswith("openai")
    assert "generativelanguage.googleapis.com" in str(client.base_url)


def test_best_model_prefers_available_candidate():
    from offerte import providers

    client = MagicMock()
    client.models.list.return_value = MagicMock(
        data=[
            MagicMock(id="gpt-4o", context_window=0),
            MagicMock(id="gpt-4o-mini", context_window=0),
        ]
    )
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


def test_il_contesto_piu_ampio_non_e_piu_il_criterio():
    """Sostituisce la vecchia regola «vince il context_window più grande».

    Era un cattivo indicatore: il modello con la finestra più larga è spesso un
    reasoning-model, che sui compiti a JSON brucia il budget in ragionamento
    nascosto e tronca la risposta — un fallimento silenzioso, perché il
    dizionario esterno si chiude e `json.loads` passa lo stesso. Ora decidono la
    taglia, la vocazione a seguire istruzioni e quello che il modello ha
    combinato davvero.
    """
    from offerte import providers

    client = MagicMock()
    client.models.list.return_value = MagicMock(
        data=[
            MagicMock(id="qwq-32b-preview", context_window=128000),
            MagicMock(id="gemma-3-27b-it", context_window=8000),
        ]
    )
    assert providers.best_model("cerebras", client) == "gemma-3-27b-it"


def test_un_candidato_del_registry_resta_preferito():
    """La lista scritta a mano conta, ma non è più l'unica cosa che conta."""
    from offerte import providers

    client = MagicMock()
    client.models.list.return_value = MagicMock(
        data=[
            MagicMock(id="modello-a-caso-70b-instruct", context_window=8000),
            MagicMock(id="zai-glm-4.7", context_window=8000),
        ]
    )
    assert providers.best_model("cerebras", client) == "zai-glm-4.7"


# --------------------------------------------------------------------------- #
# Core AI provider-agnostic + niente modello hardcoded + flag CLI
# --------------------------------------------------------------------------- #


def test_core_get_ai_client_routes_through_active_provider(monkeypatch):
    """offerte.ai._get_ai_client() costruisce il client del provider ATTIVO
    (non più hardwired a Cerebras) via providers.build_client()."""
    _clear_keys(monkeypatch)
    monkeypatch.setenv("AI_PROVIDER", "groq")
    from offerte import providers
    import offerte.ai as ai

    sentinel = object()
    captured = {}

    def fake_build(provider=None):
        captured["provider"] = provider
        return sentinel

    monkeypatch.setattr(providers, "build_client", fake_build)
    assert ai._get_ai_client() is sentinel
    assert captured["provider"] == "groq"
    # l'alias storico deve puntare alla stessa logica
    assert ai._get_cerebras_client() is sentinel


def test_no_hardcoded_model_literal_in_source():
    """Nessun modello hardcoded come STRING LITERAL nel sorgente: la scelta è
    dinamica via providers.best_model(). Cerca la forma quotata `"llama-3.3-70b"`
    (il modello dismesso), che esclude sia i commenti in prosa sia il nome reale
    di Groq `"llama-3.3-70b-versatile"`."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    targets = (
        list((root / "offerte").rglob("*.py"))
        + list((root / "ui").rglob("*.py"))
        + [root / "app.py"]
    )
    needle = '"llama-3.3-70b"'
    offenders = []
    for f in targets:
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if needle in line:
                offenders.append(f"{f.relative_to(root)}:{i}")
    assert not offenders, f"modello hardcoded (string literal) trovato: {offenders}"


def test_cli_provider_flag_selects_active_provider(monkeypatch):
    """`--provider` sulla CLI imposta il provider attivo."""
    _clear_keys(monkeypatch)
    from offerte import providers
    from offerte.cli import _build_parser

    ns = _build_parser().parse_args(["-q", "mouse", "--provider", "openai"])
    assert ns.provider == "openai"
    # le choices del flag combaciano col registry provider
    assert set(providers.PROVIDERS).issuperset({ns.provider})
    # main() farebbe questo: il provider attivo diventa quello scelto
    monkeypatch.setenv("AI_PROVIDER", ns.provider)
    assert providers.active_provider() == "openai"


# ===========================================================================
# Classificazione errori AI e retry — offerte/ai.py
# ===========================================================================


class _HttpErr(Exception):
    """Imita un'eccezione SDK che espone `status_code`."""

    def __init__(self, status_code: int, message: str = "boom") -> None:
        super().__init__(message)
        self.status_code = status_code


def test_classify_ai_error_uses_status_code_not_substrings() -> None:
    """Lo status strutturato batte lo string-matching.

    Il codice precedente cercava "404"/"429" nel testo dell'eccezione: un
    messaggio che contiene "429" per altri motivi (un prompt, un id) veniva letto
    come rate limit.
    """
    from offerte import ai

    assert ai.classify_ai_error(_HttpErr(404)) == ai.AI_ERROR_MODEL_NOT_FOUND
    assert ai.classify_ai_error(_HttpErr(429)) == ai.AI_ERROR_RATE_LIMIT
    assert ai.classify_ai_error(_HttpErr(500)) == ai.AI_ERROR_TRANSIENT
    assert ai.classify_ai_error(_HttpErr(503)) == ai.AI_ERROR_TRANSIENT
    # 4xx che non sono 404/429: ritentare non serve.
    for status in (400, 401, 403, 422):
        assert ai.classify_ai_error(_HttpErr(status)) == ai.AI_ERROR_FATAL, status
    # Uno status esplicito vince sul testo fuorviante.
    assert ai.classify_ai_error(_HttpErr(401, "rate limit 429")) == ai.AI_ERROR_FATAL
    # Errori di rete senza status restano ritentabili.
    assert ai.classify_ai_error(TimeoutError("timed out")) == ai.AI_ERROR_TRANSIENT
    assert ai.classify_ai_error(ConnectionError("reset")) == ai.AI_ERROR_TRANSIENT


def test_retry_does_not_retry_fatal_errors(monkeypatch) -> None:
    """Regressione: una chiave non valida costava 4 chiamate e ~8s di attese."""
    from offerte import ai

    chiamate = {"n": 0}
    dormite: list[float] = []

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    chiamate["n"] += 1
                    raise _HttpErr(401, "invalid api key")

    monkeypatch.setattr(ai.time, "sleep", lambda s: dormite.append(s))

    with pytest.raises(_HttpErr):
        ai.cerebras_chat_with_retry(_Client(), [{"role": "user", "content": "x"}], model="m")

    assert chiamate["n"] == 1, "un errore fatale non deve essere ritentato"
    assert dormite == [], "nessuna attesa su errore fatale"


def test_retry_backs_off_exponentially_on_rate_limit(monkeypatch) -> None:
    from offerte import ai

    dormite: list[float] = []

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    raise _HttpErr(429, "slow down")

    monkeypatch.setattr(ai.time, "sleep", lambda s: dormite.append(s))
    # Jitter deterministico per poter asserire sui valori.
    monkeypatch.setattr(ai.random, "random", lambda: 0.5)

    with pytest.raises(_HttpErr):
        ai.cerebras_chat_with_retry(
            _Client(), [{"role": "user", "content": "x"}], model="m", max_retries=4, base_delay=2.0
        )

    # 3 attese fra 4 tentativi, ognuna il doppio della precedente.
    assert len(dormite) == 3
    assert dormite == [2.0, 4.0, 8.0]


def test_retry_renegotiates_model_on_404_without_burning_an_attempt(monkeypatch) -> None:
    """Un 404 significa "modello sbagliato", non "riprova identico"."""
    from offerte import ai

    tentativi = {"n": 0}
    modelli_usati: list[str] = []

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(model=None, **kwargs):
                    modelli_usati.append(model)
                    tentativi["n"] += 1
                    if tentativi["n"] == 1:
                        raise _HttpErr(404, "model_not_found")
                    return "ok"

    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    monkeypatch.setattr(ai, "invalidate_model", lambda: None)
    monkeypatch.setattr(ai, "get_best_model", lambda **kw: "modello-nuovo")

    out = ai.cerebras_chat_with_retry(
        _Client(), [{"role": "user", "content": "x"}], model="modello-morto", max_retries=2
    )

    assert out == "ok"
    assert modelli_usati == ["modello-morto", "modello-nuovo"]


def test_retry_stops_renegotiating_after_a_few_404s(monkeypatch) -> None:
    """Il 404 non consuma tentativi: senza un tetto si ciclerebbe all'infinito."""
    from offerte import ai

    chiamate = {"n": 0}

    class _Client:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    chiamate["n"] += 1
                    raise _HttpErr(404, "model_not_found")

    monkeypatch.setattr(ai.time, "sleep", lambda s: None)
    monkeypatch.setattr(ai, "invalidate_model", lambda: None)
    monkeypatch.setattr(ai, "get_best_model", lambda **kw: "sempre-lo-stesso")

    with pytest.raises(_HttpErr):
        ai.cerebras_chat_with_retry(
            _Client(), [{"role": "user", "content": "x"}], model="m", max_retries=3
        )

    # Limitato: 2 rinegoziazioni + i tentativi normali, non un ciclo infinito.
    assert chiamate["n"] <= 6

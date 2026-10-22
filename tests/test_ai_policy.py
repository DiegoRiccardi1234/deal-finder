"""Politica AI gratuita, failover e dati ancorati alle offerte, senza rete."""

import json
from types import SimpleNamespace as NS
from unittest.mock import MagicMock

import pytest

from offerte import ai, providers, model_selector, endpoint_health


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-policy-key")
    providers._catalog_cache.clear()
    ai.invalidate_model()
    model_selector.dimentica_penalita()
    endpoint_health.svuota_cache()
    yield
    providers._catalog_cache.clear()
    ai.invalidate_model()
    model_selector.dimentica_penalita()
    endpoint_health.svuota_cache()


def client_for(ids, create=None):
    return NS(
        api_key="fake-policy-key",
        models=NS(list=MagicMock(return_value=NS(data=[NS(id=i) for i in ids]))),
        chat=NS(completions=NS(create=create or MagicMock())),
    )


def completion(text="{}", reason="stop"):
    return NS(choices=[NS(message=NS(content=text), finish_reason=reason)])


def test_gemini_legacy_default_does_not_select_configured_paid(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "cerebras")
    monkeypatch.setenv("OPENAI_API_KEY", "paid-key")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert providers.active_provider() == "gemini"
    assert providers.build_client() is None
    assert "cerebras" not in providers.PROVIDERS


def test_gemini_task_pools_and_thinking(monkeypatch):
    ids = [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
        "gemini-3.8-flash",
        "gemini-pro",
    ]
    create = MagicMock(return_value=completion())
    client = client_for(ids, create)
    ai.chat_with_retry(client, [], compito="json")
    request = create.call_args.kwargs
    assert request["model"] == "gemini-3.1-flash-lite"
    assert request["reasoning_effort"] == "minimal"
    assert "extra_body" not in request
    ai.chat_with_retry(client, [], compito="consiglio")
    assert create.call_args.kwargs["model"] == "gemini-3.8-flash"
    assert "extra_body" not in create.call_args.kwargs
    assert create.call_args.kwargs["reasoning_effort"] == "low"


def test_gemini_resource_names_from_real_catalog_match_bare_task_models():
    client = client_for(
        [
            "models/gemini-3.1-flash-lite",
            "models/gemini-3.5-flash-lite",
            "models/gemini-3.8-flash",
        ]
    )
    assert providers.best_model("gemini", client, compito="json") == "gemini-3.1-flash-lite"
    assert providers.best_model("gemini", client, compito="consiglio") == "gemini-3.8-flash"


@pytest.mark.parametrize("bad", [completion("{}", "length"), completion(""), NS(choices=[])])
def test_bad_completion_uses_different_model_and_penalizes(bad):
    create = MagicMock(side_effect=[bad, completion('{"ok":true}')])
    client = client_for(["gemini-3.1-flash-lite", "gemini-3.5-flash-lite"], create)
    assert ai.chat_with_retry(client, []).choices[0].message.content == '{"ok":true}'
    assert [c.kwargs["model"] for c in create.call_args_list] == [
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash-lite",
    ]
    assert model_selector.penalita_di("gemini", "gemini-3.1-flash-lite") > 0
    assert create.call_args.kwargs["reasoning_effort"] == "minimal"


@pytest.mark.parametrize("compito, effort", [("json", "minimal"), ("consiglio", "low")])
def test_gemini_35_fallback_uses_task_appropriate_reasoning(compito, effort):
    create = MagicMock(return_value=completion())
    client = client_for(["models/gemini-3.5-flash-lite"], create)
    ai.chat_with_retry(client, [], compito=compito)
    assert create.call_args.kwargs["model"] == "gemini-3.5-flash-lite"
    assert create.call_args.kwargs["reasoning_effort"] == effort


def test_deprecated_gemini_25_catalog_entries_are_not_fallbacks():
    client = client_for(["models/gemini-2.5-flash-lite", "models/gemini-2.5-flash"])
    assert providers.ranked_models("gemini", client, compito="json") == []
    assert providers.ranked_models("gemini", client, compito="consiglio") == []


def test_truncated_only_model_is_not_accepted():
    client = client_for(
        ["gemini-3.1-flash-lite"], MagicMock(return_value=completion("{}", "length"))
    )
    with pytest.raises(ai.InvalidAICompletion):
        ai.chat_with_retry(client, [])


def test_model_cache_separates_task_and_key_and_honors_penalties():
    client = client_for(["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"])
    assert ai.get_best_model(client) == "gemini-3.1-flash-lite"
    assert ai.get_best_model(client, compito="consiglio") == "gemini-3.8-flash"
    model_selector.registra_penalita("gemini", "gemini-3.1-flash-lite", "troncato")
    assert ai.get_best_model(client) == "gemini-3.5-flash-lite"
    client.api_key = "replacement-key"
    ai.get_best_model(client)
    assert len(ai._model_cache) == 3


def test_openrouter_never_uses_paid_and_health_excludes_dead(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    ids = ["paid/model-70b", "free/good-27b-it:free", "free/dead-70b:free"]
    client = client_for(ids)
    monkeypatch.setattr(
        endpoint_health,
        "check_many",
        lambda ids: {m: endpoint_health.Health(m, "dead" not in m, 100.0) for m in ids},
    )
    assert providers.ranked_models("openrouter", client) == ["free/good-27b-it:free"]
    providers.ranked_models("openrouter", client)
    assert client.models.list.call_count == 1
    with pytest.raises(ValueError):
        ai.chat_with_retry(client, [], model="paid/model-70b")
    client.chat.completions.create.assert_not_called()


def test_openrouter_unavailable_catalog_does_not_fall_back_to_paid(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    client = client_for([])
    client.models.list.side_effect = OSError("offline")
    with pytest.raises(RuntimeError):
        ai.chat_with_retry(client, [])
    client.chat.completions.create.assert_not_called()


@pytest.mark.parametrize(
    "endpoints",
    [
        [],
        [{"status": 1, "uptime_last_5m": 100}],
        [{"uptime_last_5m": 100}],
        [{"status": 0, "uptime_last_5m": 0}],
    ],
)
def test_unhealthy_endpoints_are_excluded(endpoints):
    assert not endpoint_health._parse("x:free", {"data": {"endpoints": endpoints}}).alive


def test_anthropic_truncation_normalized():
    sdk = NS(
        messages=NS(
            create=MagicMock(return_value=NS(content=[NS(text="{}")], stop_reason="max_tokens"))
        )
    )
    result = providers._AnthropicAdapter(sdk).chat.completions.create(model="x", messages=[])
    assert result.choices[0].finish_reason == "length"


@pytest.mark.parametrize(
    "pricing, expected",
    [
        ({"prompt": 0, "completion": 0}, True),
        ({"prompt": "0", "completion": "0.0000"}, True),
        ({"prompt": "0.001", "completion": "0"}, False),
        ({"prompt": 0, "completion": 0.0001}, False),
        ({"prompt": 0}, False),
        ({"prompt": None, "completion": 0}, False),
        ({"prompt": False, "completion": 0}, False),
        ({"prompt": "unknown", "completion": 0}, False),
        ({"prompt": "NaN", "completion": 0}, False),
        (None, False),
    ],
)
def test_free_endpoint_requires_explicit_zero_prices(pricing, expected):
    endpoint = {"status": 0, "uptime_last_5m": 100, "pricing": pricing}
    assert (
        endpoint_health._parse("model:free", {"data": {"endpoints": [endpoint]}}).alive is expected
    )


def test_openrouter_request_enforces_zero_price_despite_unknown_health(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "openrouter")
    client = client_for(["model:free"], MagicMock(return_value=completion()))
    monkeypatch.setattr(
        endpoint_health,
        "check_many",
        lambda ids: {
            item: endpoint_health.Health(item, True, detail="verifica non riuscita") for item in ids
        },
    )
    ai.chat_with_retry(
        client, [], extra_body={"provider": {"sort": "price", "max_price": {"prompt": 10}}}
    )
    routing = client.chat.completions.create.call_args.kwargs["extra_body"]["provider"]
    assert routing["max_price"] == {"prompt": 0, "completion": 0, "request": 0}
    assert routing["sort"] == "price"


def test_specs_only_use_offer_facts_and_require_all_rows(monkeypatch):
    from offerte.models import Offerta

    item = Offerta("Telefono 128GB", 100.0, "https://example.invalid/item", "test")
    monkeypatch.setattr(
        ai,
        "_cerebras_chat",
        lambda *a, **kw: completion(
            json.dumps({"1": {"storage": "128 GB", "ram": "8 GB", "display": "OLED"}})
        ),
    )
    ai.fetch_specs_ai([item], "tech", object())
    assert item.specs == {"storage": "128 GB"}
    monkeypatch.setattr(
        ai, "_cerebras_chat", lambda *a, **kw: completion('{"2": {"storage":"128GB"}}')
    )
    ai.fetch_specs_ai([item], "tech", object())
    assert item.specs == {}


@pytest.mark.parametrize(
    "rows",
    [
        [{"idx": 1, "score": 0}],
        [{"idx": 1, "score": 0}, {"idx": 1, "score": 10}],
        [{"idx": 1, "score": 0}, {"idx": 3, "score": 10}],
    ],
)
def test_incomplete_scores_use_local_fallback(monkeypatch, rows):
    from offerte.models import Offerta

    items = [
        Offerta("Telefono rosa", 100, "https://a.invalid", "test"),
        Offerta("Telefono rosa", 110, "https://b.invalid", "test"),
    ]
    monkeypatch.setattr(ai, "_get_cerebras_client", lambda: object())
    monkeypatch.setattr(
        ai, "_cerebras_chat", lambda *a, **kw: completion(json.dumps({"scores": rows}))
    )
    assert ai.filtra_risultati_con_ai(items, {"colore": "rosa"}) == items


def test_kb_does_not_infer_at_startup_and_ignores_old_product_facts(monkeypatch):
    import knowledge_base as kb

    build = MagicMock(side_effect=AssertionError("no provider call"))
    monkeypatch.setattr(providers, "build_client", build)
    assert kb.init_kb_on_startup("configured-key") is None
    assert kb.force_update("configured-key") is False
    guide = kb.get_category_context(
        {"categorie": {"tech": {"modelli": ["Invented Phone"], "fasce_prezzo": {"top": "999"}}}},
        "tech",
    )
    assert "Invented Phone" not in guide and "999" not in guide
    build.assert_not_called()


def test_settings_does_not_activate_a_configured_paid_provider(monkeypatch, tmp_path):
    import os
    from streamlit.testing.v1 import AppTest
    from offerte import secrets_store

    for cfg in providers.PROVIDERS.values():
        monkeypatch.delenv(cfg.key_env, raising=False)
    monkeypatch.setenv("AI_PROVIDER", "cerebras")
    monkeypatch.setenv("OPENAI_API_KEY", "explicit-test-paid-key")
    monkeypatch.setattr(secrets_store, "percorso", lambda: tmp_path / "local_secrets.json")
    saved = []

    def save(field, value):
        saved.append((field, value))
        os.environ[field] = value
        return True

    monkeypatch.setattr(secrets_store, "salva", save)

    def render():
        from ui.settings import render_settings

        render_settings()

    app = AppTest.from_function(render).run()
    assert not app.exception
    assert saved == []
    assert app.selectbox[0].value == "gemini"
    assert app.button(key="rimuovi_openai").disabled
    app.selectbox[0].select("OpenAI").run()
    assert not app.exception
    assert saved == [("AI_PROVIDER", "openai")]


def test_final_recommendation_routes_advice_task(monkeypatch):
    from ui import recommendation

    monkeypatch.setattr(recommendation, "st", NS(session_state={}))
    monkeypatch.setattr(recommendation, "_build_comparison_payload", lambda *a: ([], {}))
    call = MagicMock(return_value="consiglio")
    monkeypatch.setattr(recommendation, "_ai_chat_with_retry", call)
    assert recommendation._call_final_recommendation(object(), [], {}, []) == "consiglio"
    assert call.call_args.kwargs["compito"] == "consiglio"


def test_comparison_advice_excludes_hidden_offers_and_keeps_empty_queries(monkeypatch):
    from offerte.models import Offerta
    from ui import recommendation

    shown = Offerta("Visible phone", 150, "store", "https://visible.invalid")
    hidden = Offerta("Hidden cheap phone", 50, "store", "https://hidden.invalid")
    monkeypatch.setattr(
        recommendation,
        "st",
        NS(
            session_state={
                "comparison_mode": True,
                "comparison_results": {"phone A": [hidden, shown], "phone B": [hidden]},
            }
        ),
    )
    products, summary = recommendation._build_comparison_payload([shown])
    assert [product["link"] for product in products] == [shown.link]
    assert [row["count"] for row in summary] == [1, 0]
    assert summary[1]["query"] == "phone B"
    assert summary[1]["best_name"] is summary[1]["best_price"] is summary[1]["best_store"] is None


def test_cli_explicit_provider_wins_over_saved_paid_without_rewriting_file(monkeypatch, tmp_path):
    import sys
    from offerte import cli, secrets_store

    secret_file = tmp_path / "local_secrets.json"
    original = json.dumps(
        {"AI_PROVIDER": "openai", "OPENAI_API_KEY": "fake-paid", "GEMINI_API_KEY": "fake-free"}
    )
    secret_file.write_text(original, encoding="utf-8")
    monkeypatch.setattr(secrets_store, "percorso", lambda: secret_file)
    monkeypatch.setattr(ai, "st", None)
    monkeypatch.setattr(sys, "argv", ["deal-finder", "-q", "mouse", "--provider", "gemini"])
    built = []
    monkeypatch.setattr(
        providers, "build_client", lambda provider: built.append(provider) or object()
    )
    monkeypatch.setattr(cli, "cerca_offerte", lambda **kwargs: ai._get_ai_client())
    cli.main()
    assert built == ["gemini"]
    assert secret_file.read_text(encoding="utf-8") == original
    assert providers.active_provider() == "openai"


def test_provider_override_is_restored_after_failure(monkeypatch):
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    with pytest.raises(RuntimeError):
        with providers.provider_override("groq"):
            assert providers.active_provider() == "groq"
            raise RuntimeError("interrupted operation")
    assert providers.active_provider() == "gemini"

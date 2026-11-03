"""Routing must never discover an implicit API fallback for a native account."""

import json
import threading
from types import SimpleNamespace

import pytest

from offerte import connection_routing as routing
from offerte.agent_runtime import NativeRuntimeProvider, RuntimeConnectionError


def configured_router(tmp_path):
    router = routing.ConnectionRouter(tmp_path)
    router.connections.use_codex_profile()
    return router


def test_native_success_without_api_key_and_schema_validated(tmp_path, monkeypatch):
    router = configured_router(tmp_path)
    calls = []

    def complete(self, messages, **kwargs):
        calls.append((messages, kwargs))
        return {"ok": True}

    monkeypatch.setattr(NativeRuntimeProvider, "complete", complete)
    monkeypatch.setattr(router, "_legacy", lambda *args: pytest.fail("Unexpected API inference"))
    schema = {"type": "object", "properties": {"ok": {"const": True}}, "required": ["ok"]}
    assert router.complete(
        "plan", [{"role": "user", "content": "synthetic"}], output_schema=schema
    ) == {"ok": True}
    assert calls[0][1]["output_schema"] == schema
    assert router.last_call["connection_id"] == "codex"


def test_native_quota_never_falls_back_to_configured_api(tmp_path, monkeypatch):
    router = configured_router(tmp_path)
    monkeypatch.setenv("GEMINI_API_KEY", "fixture-private")

    def fail(self, *args, **kwargs):
        raise RuntimeConnectionError("quota_exhausted")

    monkeypatch.setattr(NativeRuntimeProvider, "complete", fail)
    monkeypatch.setattr(router, "_legacy", lambda *args: pytest.fail("Unexpected API fallback"))
    with pytest.raises(RuntimeConnectionError, match="Quota"):
        router.complete("analysis", [{"role": "user", "content": "synthetic"}])
    assert router.last_call["error"] == "quota_exhausted"


def test_only_saved_ordered_fallbacks_run_and_no_retry_for_malformed_json(tmp_path, monkeypatch):
    router = configured_router(tmp_path)
    router.connections.save("claude", {"enabled": True})
    router.connections.save_routing({"analysis": {"primary": "codex", "fallbacks": ["claude"]}})
    called = []

    def complete(self, *args, **kwargs):
        called.append(self.kind)
        return {"ok": "wrong-type"} if self.kind == "codex" else {"ok": True}

    monkeypatch.setattr(NativeRuntimeProvider, "complete", complete)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
    assert router.complete(
        "analysis", [{"role": "user", "content": "synthetic"}], output_schema=schema
    ) == {"ok": True}
    assert called == ["codex", "claude"]


def test_model_changes_retain_cancellation_owner_but_use_updated_selection(tmp_path, monkeypatch):
    router = configured_router(tmp_path)
    models = []
    monkeypatch.setattr(
        NativeRuntimeProvider,
        "complete",
        lambda self, messages, **kw: models.append(kw["model"]) or "ok",
    )
    router.connections.save("codex", {"model": "first"})
    router.complete("plan", [{"role": "user", "content": "first"}])
    provider = router._providers[("plan", "codex")][1]
    router.connections.save("codex", {"model": "second"})
    router.complete("plan", [{"role": "user", "content": "second"}])
    assert router._providers[("plan", "codex")][1] is provider
    assert models == ["first", "second"]


def test_cancel_prevents_fallback_and_requires_new_operation_reset(tmp_path, monkeypatch):
    router = configured_router(tmp_path)
    router.cancel("plan")
    calls = []
    monkeypatch.setattr(
        NativeRuntimeProvider, "complete", lambda *args, **kw: calls.append(1) or "ok"
    )
    with pytest.raises(RuntimeConnectionError, match="annullata"):
        router.complete("plan", [{"role": "user", "content": "synthetic"}])
    assert not calls
    router.reset_cancel("plan")
    assert router.complete("plan", [{"role": "user", "content": "synthetic"}]) == "ok"


def test_cancellation_generation_prevents_late_api_result_after_reset(tmp_path, monkeypatch):
    router = routing.ConnectionRouter(tmp_path)
    started, finish = threading.Event(), threading.Event()
    errors = []

    def legacy(*args):
        started.set()
        assert finish.wait(2)
        return "old result", "fixture", None

    def work():
        try:
            router.complete("plan", [{"role": "user", "content": "synthetic"}])
        except RuntimeConnectionError as exc:
            errors.append(exc.code)

    monkeypatch.setattr(router, "_legacy", legacy)
    worker = threading.Thread(target=work)
    worker.start()
    assert started.wait(2)
    router.cancel("plan")
    router.reset_cancel("plan")
    finish.set()
    worker.join(2)
    assert not worker.is_alive() and errors == ["cancelled"]


def test_legacy_provider_override_does_not_change_process_environment(tmp_path, monkeypatch):
    from offerte import ai, providers

    router = routing.ConnectionRouter(tmp_path)
    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GROQ_API_KEY", "private-fixture")
    router.connections.save_routing({"advice": {"primary": "api:groq"}})
    seen = []
    monkeypatch.setattr(providers, "build_client", lambda provider: object())

    def chat(**kwargs):
        seen.append(providers.active_provider())
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    monkeypatch.setattr(ai, "chat_with_retry", chat)
    assert router.complete("advice", [{"role": "user", "content": "synthetic"}]) == "ok"
    assert seen == ["groq"] and providers.active_provider() == "gemini"


def test_api_sdk_error_does_not_escape_with_secret_payload(tmp_path, monkeypatch):
    router = routing.ConnectionRouter(tmp_path)

    def legacy(*args):
        raise RuntimeError("private-fixture secret user prompt")

    monkeypatch.setattr(router, "_legacy", legacy)
    with pytest.raises(RuntimeError) as error:
        router.complete("plan", [{"role": "user", "content": "private prompt"}])
    assert "private" not in str(error.value)
    assert "private" not in json.dumps(router.last_call)


def test_diagnostics_does_not_generate_completion_and_synthetic_test_has_no_fallback(
    tmp_path, monkeypatch
):
    router = configured_router(tmp_path)
    monkeypatch.setattr(
        "offerte.agent_runtime.get_runtime_status",
        lambda kind: {"installed": True, "authenticated": True, "quota": None},
    )
    monkeypatch.setattr(
        NativeRuntimeProvider, "complete", lambda self, *args, **kwargs: {"ok": True}
    )
    assert router.status("codex")["authenticated"]
    assert router.last_call is None
    assert router.test_connection("codex")["ok"]
    assert router.last_call["task"] == "diagnostics"


def test_routed_marker_only_exposes_the_task_that_was_selected(tmp_path):
    router = routing.ConnectionRouter(tmp_path)
    router.connections.save("codex", {"enabled": True})
    router.connections.save_routing({"advice": {"primary": "codex"}})
    assert routing.routed_client("plan", router=router) is None
    client = routing.routed_client("advice", router=router)
    assert client._dealfinder_routed and client.task == "advice" and client.router is router


def test_custom_completion_rejects_truncation_even_if_json_is_closed():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(finish_reason="length", message=SimpleNamespace(content='{"ok": true}'))
        ]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )
    with pytest.raises(routing.TruncatedCompletionError):
        routing.ConnectionRouter._compatible(client, "fixture", [], {"type": "object"})

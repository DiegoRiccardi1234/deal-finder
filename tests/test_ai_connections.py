"""Connection configuration is private and inert until explicitly routed."""

import json

import pytest

from offerte.ai_connections import TASKS, ConnectionRegistry, validate_base_url


def test_reading_configuration_creates_no_files_and_keeps_legacy_routes(tmp_path):
    store = ConnectionRegistry(tmp_path)
    rows = store.public_connections()
    assert {row["id"] for row in rows} >= {"codex", "claude", "api:gemini"}
    assert not store.get("claude")["enabled"]
    assert all(store.route(task) == [] for task in TASKS)
    assert list(tmp_path.iterdir()) == []


def test_native_profile_routes_every_task_without_touching_legacy_keys(tmp_path):
    keys = tmp_path / "local_secrets.json"
    keys.write_text('{"GEMINI_API_KEY":"fixture-private"}', encoding="utf-8")
    before = keys.read_bytes()
    registry = ConnectionRegistry(tmp_path)
    registry.use_codex_profile()
    assert all(registry.route(task) == ["codex"] for task in TASKS)
    assert registry.get("codex")["enabled"]
    assert not registry.get("claude")["enabled"]
    assert keys.read_bytes() == before


def test_compatible_secret_is_separate_omission_preserves_and_clear_removes(tmp_path):
    store = ConnectionRegistry(tmp_path)
    store.save(
        "local",
        {
            "name": "Local",
            "kind": "openai_compatible",
            "base_url": "http://127.0.0.1:1234/v1/",
            "model": "fixture-model",
            "api_key": "private-fixture",
            "enabled": True,
        },
    )
    assert store.secret("local") == "private-fixture"
    assert "private-fixture" not in store.path.read_text(encoding="utf-8")
    assert "private-fixture" not in json.dumps(store.public_connections())
    store.save("local", {"name": "Renamed"})
    assert store.secret("local") == "private-fixture"
    store.save("local", {"api_key": ""})
    assert store.secret("local") is None


@pytest.mark.parametrize(
    "base",
    [
        "http://example.org/v1",
        "https://user:secret@example.org/v1",
        "https://example.org/v1?token=secret",
        "https://example.org/v1#token",
        "https://example.org:0/v1",
        "https://example.org:99999/v1",
        "https://example.org:/v1",
        "https://example.org/space here",
        "https://example.org\\@127.0.0.1/v1",
        "file:///private/file",
        "https://example.org:\nbad/v1",
    ],
)
def test_invalid_urls_are_rejected_before_secrets_are_written(tmp_path, base):
    store = ConnectionRegistry(tmp_path)
    with pytest.raises(ValueError):
        store.save("endpoint", {"base_url": base, "model": "fixture", "api_key": "private"})
    assert not store.path.exists() and not store.secrets_path.exists()


@pytest.mark.parametrize(
    "base",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://[::1]:1234/v1",
        "https://api.example.org/v1",
    ],
)
def test_loopback_http_and_remote_https_supported(base):
    assert validate_base_url(base) == base


def test_routing_is_explicit_and_in_use_connection_cannot_be_deleted(tmp_path):
    store = ConnectionRegistry(tmp_path)
    store.save("codex", {"kind": "codex", "enabled": True})
    store.save("claude", {"kind": "claude", "enabled": True})
    store.save_routing({"plan": {"primary": "codex", "fallbacks": ["claude"]}})
    assert store.route("plan") == ["codex", "claude"]
    assert store.route("analysis") == []
    with pytest.raises(ValueError, match="connection_in_use"):
        store.delete("claude")
    store.save_routing({"plan": {"primary": None}})
    store.delete("claude")
    assert not store.get("claude")["enabled"]


@pytest.mark.parametrize(
    "choice",
    [
        {"primary": "codex", "fallbacks": ["codex"]},
        {"primary": None, "fallbacks": ["codex"]},
        {"primary": "unknown"},
        {"primary": "claude"},
        {"fallbacks": "codex"},
        {"primary": "codex", "fallbacks": [{}]},
    ],
)
def test_invalid_routes_do_not_change_configuration(tmp_path, choice):
    store = ConnectionRegistry(tmp_path)
    store.save("codex", {"enabled": True})
    before = store.path.read_bytes()
    with pytest.raises(ValueError):
        store.save_routing({"analysis": choice})
    assert store.path.read_bytes() == before


def test_hermes_is_explicitly_marked_external_and_requires_policy_acknowledgement(tmp_path):
    store = ConnectionRegistry(tmp_path)
    store.save(
        "hermes",
        {
            "kind": "openai_compatible",
            "name": "Hermes",
            "model": "hermes-agent",
            "base_url": "http://127.0.0.1:8642/v1",
            "enabled": True,
            "external_agent": True,
        },
    )
    with pytest.raises(ValueError, match="external_agent_policy_not_acknowledged"):
        store.save_routing({"plan": {"primary": "hermes"}})
    store.save("hermes", {"tools_policy_acknowledged": True})
    store.save_routing({"plan": {"primary": "hermes"}})
    row = next(item for item in store.public_connections() if item["id"] == "hermes")
    assert row["external_agent"] and row["billing"] == "external"


def test_corrupt_registry_does_not_fall_back_or_overwrite(tmp_path):
    file = tmp_path / "ai_connections.json"
    file.write_text("{broken", encoding="utf-8")
    store = ConnectionRegistry(tmp_path)
    with pytest.raises(ValueError, match="unreadable"):
        store.use_codex_profile()
    assert file.read_text(encoding="utf-8") == "{broken"


def test_native_ids_and_secrets_cannot_be_repurposed(tmp_path):
    store = ConnectionRegistry(tmp_path)
    for cid, payload in [
        ("codex", {"kind": "openai_compatible"}),
        ("second-codex", {"kind": "codex"}),
        ("gemini", {"kind": "openai_compatible"}),
        ("codex", {"api_key": "private"}),
        ("codex", {"enabled": "false"}),
    ]:
        with pytest.raises(ValueError):
            store.save(cid, payload)
    assert not store.path.exists() and not store.secrets_path.exists()


@pytest.mark.parametrize(
    "route",
    [
        {"primary": None, "fallbacks": ["codex"]},
        {"primary": "", "fallbacks": []},
        {"primary": "codex", "fallbacks": ["codex"]},
        {"primary": "unknown", "fallbacks": []},
    ],
)
def test_corrupt_saved_route_is_not_treated_as_legacy_api_default(tmp_path, route):
    store = ConnectionRegistry(tmp_path)
    store.path.write_text(json.dumps({"routing": {"plan": route}}), encoding="utf-8")
    with pytest.raises(ValueError, match="config_invalid"):
        store.route("plan")


def test_manually_corrupt_endpoint_cannot_expose_credentials_or_use_remote_http(tmp_path):
    store = ConnectionRegistry(tmp_path)
    store.save("local", {"model": "fixture", "base_url": "http://127.0.0.1:1234/v1"})
    value = json.loads(store.path.read_text(encoding="utf-8"))
    value["connections"]["local"]["base_url"] = "http://remote.example.org/v1"
    store.path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="https"):
        store.public_connections()
    value["connections"]["local"]["base_url"] = "https://example.org/v1"
    value["connections"]["local"]["api_key"] = "private-fixture"
    store.path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(ValueError, match="config_invalid"):
        store.public_connections()

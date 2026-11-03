"""Capability isolation, actual MCP negotiation and restart-safe local setup."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters
from starlette.testclient import TestClient

from offerte.agent_api import create_api
from offerte.agent_service import AgentService
from offerte.agent_tools import AgentToolError
from offerte.db import close_all
from offerte.mcp_server import RemoteDispatcher, create_mcp_server


@pytest.fixture
def service(tmp_path):
    current = AgentService(data_dir=tmp_path)
    yield current
    current.shutdown()
    close_all()


def test_rest_and_http_mcp_auth_never_grant_setup_or_confirmation(service):
    config = service.setup("http://127.0.0.1:8521")
    token = service.token_file.read_text()
    assert token not in json.dumps(config)
    assert set(config["configs"]) == {"codex", "claude_desktop", "cowork", "hermes"}
    with TestClient(create_api(service), base_url="http://127.0.0.1:8521") as client:
        route = "/api/agent/tools/dealfinder_get_context"
        body = {"arguments": {}}
        assert client.post(route, json=body).status_code == 401
        auth = {"Authorization": f"Bearer {token}"}
        assert client.post(route, json=body, headers=auth).status_code == 200
        assert (
            client.post(route, json=body, headers={**auth, "Host": "evil.example"}).status_code
            == 403
        )
        assert (
            client.post(
                route, json=body, headers={**auth, "Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        assert client.post("/api/agent/setup", json={}, headers=auth).status_code == 404
        assert client.post("/api/agent/confirm", json={}, headers=auth).status_code == 404
        assert (
            client.post("/api/agent/tools/dealfinder_confirm", json=body, headers=auth).status_code
            == 404
        )
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "isolated-test", "version": "1"},
            },
        }
        headers = {"Accept": "application/json, text/event-stream"}
        assert client.post("/mcp/", json=initialize, headers=headers).status_code == 401
        headers.update(auth)
        response = client.post("/mcp/", json=initialize, headers=headers)
        assert response.status_code == 200
        assert response.json()["result"]["serverInfo"]["name"] == "Deal Finder"
        headers["MCP-Protocol-Version"] = response.json()["result"]["protocolVersion"]
        listing = client.post(
            "/mcp/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        assert len(listing.json()["result"]["tools"]) == 10


def test_sdk_tools_are_typed_without_sensitive_capabilities():
    calls = []

    def dispatch(name, arguments):
        calls.append((name, arguments))
        return {"items": []}

    async def run():
        async with Client(create_mcp_server(dispatch)) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            assert len(names) == 10
            assert "dealfinder_start_batch_search" in names
            assert not any(
                any(word in name for word in ["confirm", "delete", "purchase", "setup"])
                for name in names
            )
            response = await client.call_tool(
                "dealfinder_list_offers", {"operation_id": "id", "limit": 1}
            )
            assert not response.is_error
            invalid = await client.call_tool(
                "dealfinder_list_offers", {"operation_id": "id", "limit": 1000}
            )
            assert invalid.is_error

    asyncio.run(run())
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example",
        "http://localhost:8521",
        "http://127.0.0.1:8521/path",
        "http://user:secret@127.0.0.1:8521",
        "http://127.0.0.1:8521?redirect=evil",
    ],
)
def test_stdio_rejects_non_loopback_or_ambiguous_origin(tmp_path, url):
    token = tmp_path / "token"
    token.write_text("x" * 64)
    with pytest.raises(ValueError):
        RemoteDispatcher(url, token)


def test_stdio_forwards_only_http_and_sanitizes_upstream_errors(tmp_path):
    token = tmp_path / "token"
    token.write_text("x" * 64)
    dispatcher = RemoteDispatcher("http://127.0.0.1:8521", token)
    seen = []

    def response(request):
        seen.append(request)
        return httpx.Response(403, json={"detail": "token secret https://private.example"})

    dispatcher.client.close()
    dispatcher.client = httpx.Client(
        transport=httpx.MockTransport(response), headers={"Authorization": "Bearer " + "x" * 64}
    )
    with pytest.raises(ValueError, match="dealfinder_request_failed"):
        dispatcher("dealfinder_get_context", {})
    assert seen[0].url.host == "127.0.0.1"
    assert seen[0].headers["authorization"] == "Bearer " + "x" * 64
    dispatcher.close()


def test_persisted_enable_resume_and_revoke_never_regenerate_token(service, tmp_path):
    config = service.enable_mcp(port=0)
    token = service.token_file.read_text()
    settings = json.loads((tmp_path / "agent" / "settings.json").read_text())
    assert settings["enabled"] is True
    service._api.stop()
    service._api = None
    service._capability = None
    assert service.resume_mcp()["enabled"] is True
    assert service.token_file.read_text() == token
    dispatcher = RemoteDispatcher(service.mcp_status()["url"], service.token_file)
    assert dispatcher("dealfinder_get_context", {})["max_products"] == 10
    dispatcher.close()
    assert "--token-file" in config["configs"]["codex"]
    service.revoke()
    assert not service.token_file.exists()
    assert service.resume_mcp()["enabled"] is False
    assert not service.token_file.exists()


def test_missing_token_or_corrupt_settings_fail_closed_without_creation(service, tmp_path):
    settings = tmp_path / "agent" / "settings.json"
    settings.write_text(json.dumps({"enabled": True, "port": 8521}))
    with pytest.raises(AgentToolError, match="invalid_mcp_settings"):
        service.resume_mcp()
    assert not service.token_file.exists()
    settings.write_text("invalid json")
    with pytest.raises(AgentToolError, match="invalid_mcp_settings"):
        service.resume_mcp()


def test_actual_stdio_adapter_from_foreign_cwd_uses_only_running_api(service, tmp_path):
    config = service.enable_mcp(port=0)
    command = sys.executable
    args = config["configs"]["claude_desktop"]["mcpServers"]["dealfinder"]["args"]
    foreign = tmp_path / "foreign-client-cwd"
    foreign.mkdir()

    async def run():
        params = StdioServerParameters(command=command, args=args, cwd=str(foreign))
        async with Client(params) as client:
            assert len((await client.list_tools()).tools) == 10
            result = await client.call_tool("dealfinder_get_context", {})
            assert not result.is_error
            assert result.structured_content["max_products"] == 10

    asyncio.run(run())
    assert list(foreign.iterdir()) == []


def test_os_workspace_owner_refuses_second_process_before_opening_database(service, tmp_path):
    code = (
        "import sys; from offerte.agent_service import AgentService; "
        "from offerte.agent_tools import AgentToolError; "
        "\ntry: AgentService(data_dir=sys.argv[1])"
        "\nexcept AgentToolError as e: print(e.code)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "workspace_owned_by_another_process"

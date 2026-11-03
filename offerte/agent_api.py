"""Explicitly enabled loopback API in the application's owning process."""

from __future__ import annotations

import json
import socket
import threading
import time
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from offerte.agent_tools import MAX_REQUEST_BYTES, AgentToolError


def require_local(request: Request, service: Any) -> None:
    if request.url.hostname != "127.0.0.1":
        raise AgentToolError("local_host_required", 403)
    origin = request.headers.get("origin")
    if origin:
        parsed = urlsplit(origin)
        if (
            parsed.scheme != request.url.scheme
            or parsed.netloc != request.url.netloc
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise AgentToolError("same_origin_required", 403)
    supplied = request.headers.get("authorization", "")
    bearer = supplied[7:] if supplied.startswith("Bearer ") else ""
    if not service.authenticated(bearer):
        raise AgentToolError("agent_token_required", 401)


class CapabilityMiddleware:
    def __init__(self, app: Any, service: Any) -> None:
        self.app, self.service = app, service

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            try:
                require_local(Request(scope), self.service)
            except AgentToolError as exc:
                await JSONResponse({"detail": exc.code}, status_code=exc.status_code)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def create_api(service: Any) -> Starlette:
    from offerte.mcp_server import create_mcp_server

    server = create_mcp_server(service.dispatch)
    mcp_app = server.streamable_http_app(
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        max_request_body_size=MAX_REQUEST_BYTES,
    )

    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with server.session_manager.run():
            yield

    async def tool(request: Request) -> JSONResponse:
        try:
            require_local(request, service)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > MAX_REQUEST_BYTES:
                    raise AgentToolError("request_too_large", 413)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                raise AgentToolError("invalid_request_json") from None
            if not isinstance(payload, dict) or set(payload) != {"arguments"}:
                raise AgentToolError("invalid_tool_arguments")
            from starlette.concurrency import run_in_threadpool

            result = await run_in_threadpool(
                service.dispatch, request.path_params["name"], payload["arguments"]
            )
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except AgentToolError as exc:
            return JSONResponse({"detail": exc.code}, status_code=exc.status_code)

    return Starlette(
        routes=[
            Route("/api/agent/tools/{name}", tool, methods=["POST"]),
            Mount("/mcp", app=CapabilityMiddleware(mcp_app, service)),
        ],
        lifespan=lifespan,
    )


class ApiHandle:
    def __init__(self, server: Any, worker: threading.Thread, listener: socket.socket) -> None:
        self.server, self.worker, self.listener = server, worker, listener
        self.port = int(listener.getsockname()[1])
        self.url = f"http://127.0.0.1:{self.port}"

    def stop(self) -> None:
        self.server.should_exit = True
        self.worker.join(10)
        if not self.worker.is_alive():
            self.listener.close()


def start_api(service: Any, port: int = 8521) -> ApiHandle:
    """UI must explicitly enable this; it is never started by an adapter or import."""
    import uvicorn

    if type(port) is not int or not 0 <= port <= 65535:
        raise AgentToolError("invalid_api_port")
    with service._lock:
        if service._closing.is_set():
            raise AgentToolError("app_shutting_down", 503)
        if service._api is not None and service._api.worker.is_alive():
            return service._api
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", port))
            config = uvicorn.Config(
                create_api(service),
                host="127.0.0.1",
                port=port,
                access_log=False,
                log_level="warning",
                http="h11",
            )
            server = uvicorn.Server(config)
            worker = threading.Thread(
                target=server.run,
                kwargs={"sockets": [listener]},
                name="dealfinder-agent-api",
                daemon=True,
            )
            worker.start()
            handle = ApiHandle(server, worker, listener)
            deadline = time.monotonic() + 10
            while not server.started:
                if not worker.is_alive() or time.monotonic() >= deadline:
                    server.should_exit = True
                    raise AgentToolError("agent_api_start_failed", 503)
                time.sleep(0.01)
            service._api = handle
            return handle
        except Exception:
            listener.close()
            raise

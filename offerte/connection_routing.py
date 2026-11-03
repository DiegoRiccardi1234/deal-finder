"""Task-scoped AI routing. A named route never enters the legacy provider pool."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any, cast

from offerte import providers
from offerte.ai_connections import TASKS, ConnectionRegistry
from offerte.agent_runtime import (
    NativeRuntimeProvider,
    RuntimeConnectionError,
    RuntimeKind,
    create_runtime_provider,
)
from offerte.runtime_types import TruncatedCompletionError, validate_output


class ConnectionRouter:
    """One router per workspace; cancellation belongs to an application task.

    No prompt, native account token or API key is returned in diagnostics. The
    existing API ranking/retry implementation remains authoritative for API pins.
    """

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.connections = ConnectionRegistry(data_dir)
        self._guard = threading.RLock()
        self._cancelled = {task: threading.Event() for task in (*TASKS, "diagnostics")}
        self._generations = {task: 0 for task in self._cancelled}
        self._providers: dict[tuple[str, str], tuple[str, Any]] = {}
        self._active: dict[str, set[NativeRuntimeProvider]] = {
            task: set() for task in (*TASKS, "diagnostics")
        }
        self.last_usage: dict[str, Any] | None = None
        self.last_call: dict[str, Any] | None = None

    def has_route(self, task: str) -> bool:
        return bool(self.connections.route(task))

    def _check_cancelled(self, task: str, generation: int | None = None) -> None:
        if self._cancelled[task].is_set() or (
            generation is not None and generation != self._generations[task]
        ):
            raise RuntimeConnectionError("cancelled")

    def cancel(self, task: str | None = None) -> None:
        tasks = [task] if task is not None else list(self._cancelled)
        with self._guard:
            for name in tasks:
                if name not in self._cancelled:
                    raise ValueError("invalid_routing_task")
                self._cancelled[name].set()
                self._generations[name] += 1
                owned = set(self._active[name])
                owned.update(
                    provider
                    for (cached_task, _), (_, provider) in self._providers.items()
                    if cached_task == name and isinstance(provider, NativeRuntimeProvider)
                )
                for provider in owned:
                    provider.cancel_active()

    def reset_cancel(self, task: str | None = None) -> None:
        tasks = [task] if task is not None else list(self._cancelled)
        with self._guard:
            for name in tasks:
                if name not in self._cancelled:
                    raise ValueError("invalid_routing_task")
                if self._active[name]:
                    raise RuntimeError("Connessione ancora in arresto.")
                self._cancelled[name].clear()
                for (cached_task, _), (_, provider) in self._providers.items():
                    if cached_task == name and isinstance(provider, NativeRuntimeProvider):
                        provider.reset_cancel()

    def _provider(self, task: str, cid: str, row: dict[str, Any]) -> Any:
        key = self.connections.secret(cid)
        native = row["kind"] in ("codex", "claude")
        signature = (
            row["kind"]
            if native
            else hashlib.sha256(
                (json.dumps(row, sort_keys=True) + "\0" + (key or "")).encode()
            ).hexdigest()
        )
        with self._guard:
            cached = self._providers.get((task, cid))
            if cached and cached[0] == signature:
                return cached[1]
            if native:
                provider: Any = create_runtime_provider(row["kind"], cid)
            else:
                # An explicitly configured endpoint gets only its own key. No
                # environment proxy/redirect may silently change the recipient.
                import httpx
                from openai import OpenAI

                transport = httpx.Client(trust_env=False, follow_redirects=False, timeout=300)
                provider = OpenAI(
                    api_key=key or "local-no-key",
                    base_url=row["base_url"],
                    timeout=300,
                    max_retries=0,
                    http_client=transport,
                )
            self._providers[(task, cid)] = (signature, provider)
            return provider

    def complete(
        self,
        task: str,
        messages: list[dict[str, str]],
        *,
        output_schema: dict[str, Any] | None = None,
    ) -> str | dict[str, Any]:
        if task not in TASKS:
            raise ValueError("invalid_routing_task")
        route = self.connections.route(task)
        if not route:
            # Preserves legacy behavior only when no named route was selected.
            route = [f"api:{providers.active_provider()}"]
        return self._run(task, route, messages, output_schema)

    def _run(
        self,
        task: str,
        route: list[str],
        messages: list[dict[str, str]],
        output_schema: dict[str, Any] | None,
    ) -> str | dict[str, Any]:
        if (
            not isinstance(messages, list)
            or not messages
            or len(messages) > 200
            or any(
                not isinstance(message, dict)
                or message.get("role") not in ("system", "user", "assistant")
                or not isinstance(message.get("content"), str)
                for message in messages
            )
        ):
            raise ValueError("invalid_ai_messages")
        last_error: Exception = RuntimeError("Nessuna connessione AI disponibile.")
        generation = self._generations[task]
        for cid in route:
            self._check_cancelled(task, generation)
            started = time.monotonic()
            provider = None
            try:
                if cid.startswith("api:"):
                    value, model, usage = self._legacy(cid[4:], messages, output_schema, task)
                else:
                    row = self.connections.get(cid)
                    if not row or not row.get("enabled"):
                        raise ValueError("connection_unavailable")
                    if row.get("external_agent") and not row.get("tools_policy_acknowledged"):
                        raise ValueError("external_agent_policy_not_acknowledged")
                    provider = self._provider(task, cid, row)
                    model = str(row.get("model") or "")
                    self._check_cancelled(task, generation)
                    if isinstance(provider, NativeRuntimeProvider):
                        with self._guard:
                            self._active[task].add(provider)
                        value = provider.complete(
                            messages, model=model or None, output_schema=output_schema
                        )
                        usage = provider.last_usage
                        model = model or "default"
                    else:
                        value, usage = self._compatible(provider, model, messages, output_schema)
                self._check_cancelled(task, generation)
                if value in (None, "", {}):
                    raise ValueError("empty_ai_completion")
                if output_schema is not None:
                    validate_output(value, output_schema)
                self.last_usage = usage
                self.last_call = {
                    "connection_id": cid,
                    "model": model,
                    "task": task,
                    "success": True,
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
                return value
            except Exception as exc:
                self.last_call = {
                    "connection_id": cid,
                    "task": task,
                    "success": False,
                    "error": getattr(exc, "code", type(exc).__name__),
                    "elapsed_ms": int((time.monotonic() - started) * 1000),
                }
                if (
                    self._cancelled[task].is_set()
                    or generation != self._generations[task]
                    or getattr(exc, "code", "") == "cancelled"
                ):
                    raise RuntimeConnectionError("cancelled") from None
                last_error = exc
            finally:
                if isinstance(provider, NativeRuntimeProvider):
                    with self._guard:
                        self._active[task].discard(provider)
        # Native errors are already classified. Raw SDK exceptions may contain
        # request bodies or endpoint credentials and must never reach the UI.
        if isinstance(last_error, RuntimeConnectionError):
            raise last_error
        if isinstance(last_error, TruncatedCompletionError):
            raise last_error
        raise RuntimeError(
            "La connessione AI non ha completato la richiesta. Controlla la diagnostica."
        ) from None

    @staticmethod
    def _legacy(
        name: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
        task: str,
    ) -> tuple[str | dict[str, Any], str, dict[str, Any] | None]:
        if name not in providers.PROVIDERS:
            raise ValueError("unknown_api_provider")
        from offerte.ai import chat_with_retry

        with providers.provider_override(name):
            client = providers.build_client(name)
            if client is None:
                raise ValueError("api_provider_unconfigured")
            request: dict[str, Any] = {}
            if schema is not None:
                request["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "dealfinder", "schema": schema, "strict": True},
                }
            result = chat_with_retry(
                client=client,
                messages=messages,
                compito="consiglio" if task == "advice" else "json",
                **request,
            )
            text = str(result.choices[0].message.content or "").strip()
            value: str | dict[str, Any] = json.loads(text) if schema is not None else text
            if schema is not None and not isinstance(value, dict):
                raise ValueError("invalid_json_completion")
            model = str(getattr(result, "model", "") or "automatic")
            return value, model, _usage(result)

    @staticmethod
    def _compatible(
        client: Any,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None,
    ) -> tuple[str | dict[str, Any], dict[str, Any] | None]:
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": 8192,
        }
        if schema is not None:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "dealfinder", "schema": schema, "strict": True},
            }
        response = client.chat.completions.create(**request)
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise ValueError("empty_ai_completion")
        if choices[0].finish_reason == "length":
            raise TruncatedCompletionError(model)
        if getattr(choices[0].message, "tool_calls", None):
            raise ValueError("unexpected_tool_call")
        text = str(choices[0].message.content or "").strip()
        value: str | dict[str, Any] = json.loads(text) if schema is not None else text
        if schema is not None and not isinstance(value, dict):
            raise ValueError("invalid_json_completion")
        return value, _usage(response)

    def status(self, connection_id: str) -> dict[str, Any]:
        """Metadata/auth only. This never generates a model completion."""
        from offerte.agent_runtime import get_runtime_status

        if connection_id in ("codex", "claude"):
            return {
                "connection_id": connection_id,
                **get_runtime_status(cast(RuntimeKind, connection_id)),
            }
        row = next(
            (item for item in self.connections.public_connections() if item["id"] == connection_id),
            None,
        )
        if row is None:
            raise ValueError("unknown_connection")
        return {
            "connection_id": connection_id,
            "configured": row["configured"],
            "quota": None,
            "external_agent": row.get("external_agent", False),
            "error": None if row["configured"] else "connection_unconfigured",
        }

    def test_connection(self, connection_id: str) -> dict[str, Any]:
        """One explicit synthetic completion. Never exercise fallback routes."""
        self.reset_cancel("diagnostics")
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        value = self._run(
            "diagnostics",
            [connection_id],
            [{"role": "user", "content": 'Prova sintetica: restituisci {"ok": true}.'}],
            schema,
        )
        return {
            "connection_id": connection_id,
            "ok": value == {"ok": True},
            "usage": self.last_usage,
        }

    def close(self) -> None:
        self.cancel()
        with self._guard:
            for _, provider in self._providers.values():
                if not isinstance(provider, NativeRuntimeProvider) and hasattr(provider, "close"):
                    provider.close()


def _usage(response: Any) -> dict[str, Any] | None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return None
    prompt = getattr(usage, "prompt_tokens", None)
    completion = getattr(usage, "completion_tokens", None)
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return None
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


class RoutedClient:
    """Marker consumed by the legacy chat shim, independent of API keys."""

    _dealfinder_routed = True

    def __init__(self, router: ConnectionRouter, task: str = "analysis") -> None:
        if task not in TASKS:
            raise ValueError("invalid_routing_task")
        self.router = router
        self.task = task


_ROUTERS: dict[str, ConnectionRouter] = {}
_ROUTERS_LOCK = threading.RLock()


def get_router(data_dir: Path | str | None = None) -> ConnectionRouter:
    if data_dir is None:
        from offerte.paths import data_dir as resolve_data_dir

        data_dir = resolve_data_dir()
    identity = str(Path(data_dir).resolve())
    with _ROUTERS_LOCK:
        if identity not in _ROUTERS:
            _ROUTERS[identity] = ConnectionRouter(data_dir)
        return _ROUTERS[identity]


def routed_client(
    task: str = "analysis",
    *,
    router: ConnectionRouter | None = None,
) -> RoutedClient | None:
    selected = router or get_router()
    return RoutedClient(selected, task) if selected.has_route(task) else None

"""Per-install connections and explicit routes, separate from legacy API keys.

Reading settings never launches a runtime or performs an inference. Existing
installations keep their API routing until a named route is saved explicitly.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from offerte import providers

TASKS = ("plan", "analysis", "advice")
KINDS = ("codex", "claude", "openai_compatible")
_LOCK = threading.RLock()
_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,79}$")


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("ai_connections_config_unreadable") from None
    if not isinstance(value, dict):
        raise ValueError("ai_connections_config_invalid")
    return value


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_base_url(value: str) -> str:
    base = value.strip().rstrip("/")
    if any(ord(char) < 33 or char == "\\" for char in base):
        raise ValueError("invalid_connection_url")
    try:
        parsed = urlsplit(base)
        port = parsed.port
    except ValueError:
        raise ValueError("invalid_connection_url") from None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.netloc.endswith(":")
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("invalid_connection_url")
    if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("remote_connection_requires_https")
    return base


class ConnectionRegistry:
    def __init__(self, data_dir: Path | str | None = None) -> None:
        if data_dir is None:
            from offerte.paths import data_dir as resolve_data_dir

            data_dir = resolve_data_dir()
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "ai_connections.json"
        self.secrets_path = self.data_dir / "ai_connection_secrets.json"

    def _configuration(self) -> dict[str, Any]:
        with _LOCK:
            config = _read(self.path)
            connections = config.get("connections", {})
            routing = config.get("routing", {})
            if not isinstance(connections, dict) or not isinstance(routing, dict):
                raise ValueError("ai_connections_config_invalid")
            for cid, row in connections.items():
                if (
                    not _ID.fullmatch(cid)
                    or cid in providers.PROVIDERS
                    or not isinstance(row, dict)
                    or row.get("kind") not in KINDS
                    or not isinstance(row.get("enabled", False), bool)
                    or (row.get("kind") in ("codex", "claude") and row["kind"] != cid)
                    or (cid in ("codex", "claude") and row["kind"] != cid)
                ):
                    raise ValueError("ai_connections_config_invalid")
                if (
                    not isinstance(row.get("name", cid), str)
                    or not isinstance(row.get("model", ""), str)
                    or any(
                        char in row.get("name", cid) + row.get("model", "") for char in "\r\n\x00"
                    )
                    or set(row)
                    - {
                        "id",
                        "name",
                        "kind",
                        "enabled",
                        "model",
                        "base_url",
                        "external_agent",
                        "tools_policy_acknowledged",
                    }
                    or any(
                        not isinstance(row.get(flag, False), bool)
                        for flag in (
                            "external_agent",
                            "tools_policy_acknowledged",
                        )
                    )
                ):
                    raise ValueError("ai_connections_config_invalid")
                if row["kind"] == "openai_compatible":
                    if not isinstance(row.get("base_url"), str) or not row.get("model"):
                        raise ValueError("ai_connections_config_invalid")
                    validate_base_url(row["base_url"])
            known = {
                "codex",
                "claude",
                *connections,
                *(f"api:{name}" for name in providers.PROVIDERS),
            }
            for task, route in routing.items():
                if task not in TASKS or not isinstance(route, dict):
                    raise ValueError("ai_connections_config_invalid")
                if route.get("primary") is not None and not isinstance(route["primary"], str):
                    raise ValueError("ai_connections_config_invalid")
                if not isinstance(route.get("fallbacks", []), list) or not all(
                    isinstance(cid, str) for cid in route.get("fallbacks", [])
                ):
                    raise ValueError("ai_connections_config_invalid")
                primary, fallbacks = route.get("primary"), route.get("fallbacks", [])
                ids = ([primary] if primary else []) + fallbacks
                if (
                    primary == ""
                    or (primary is None and fallbacks)
                    or len(ids) != len(set(ids))
                    or len(fallbacks) > 16
                    or any(cid not in known for cid in ids)
                    or set(route) - {"primary", "fallbacks"}
                ):
                    raise ValueError("ai_connections_config_invalid")
            return config

    def connections(self) -> list[dict[str, Any]]:
        with _LOCK:
            records = {
                "codex": {
                    "id": "codex",
                    "name": "Codex",
                    "kind": "codex",
                    "enabled": False,
                    "model": "",
                    "external_agent": False,
                },
                "claude": {
                    "id": "claude",
                    "name": "Claude Code",
                    "kind": "claude",
                    "enabled": False,
                    "model": "",
                    "external_agent": False,
                },
            }
            for cid, row in self._configuration().get("connections", {}).items():
                records[cid] = {**row, "id": cid}
            return list(records.values())

    def get(self, connection_id: str) -> dict[str, Any] | None:
        return next((row for row in self.connections() if row["id"] == connection_id), None)

    def secret(self, connection_id: str) -> str | None:
        with _LOCK:
            value = _read(self.secrets_path).get(connection_id)
            return str(value) if value else None

    def public_connections(self) -> list[dict[str, Any]]:
        result = []
        for row in self.connections():
            result.append(
                {
                    **{key: value for key, value in row.items() if key != "api_key"},
                    "secret_configured": bool(self.secret(row["id"])),
                    "configured": bool(row.get("enabled"))
                    and (row["kind"] in ("codex", "claude") or bool(row.get("base_url"))),
                    "capabilities": {"chat": True, "json": True},
                    "billing": "subscription" if row["kind"] in ("codex", "claude") else "external",
                }
            )
        for name, info in providers.PROVIDERS.items():
            result.append(
                {
                    "id": f"api:{name}",
                    "name": info.label,
                    "kind": "api",
                    "enabled": True,
                    "configured": providers.is_configured(name),
                    "secret_configured": providers.is_configured(name),
                    "readonly": True,
                    "model": "",
                    "external_agent": False,
                    "billing": "api",
                    "capabilities": {"chat": True, "json": True},
                }
            )
        return result

    def routing(self) -> dict[str, dict[str, Any]]:
        saved = self._configuration().get("routing", {})
        return {
            task: {
                "primary": (saved.get(task) or {}).get("primary"),
                "fallbacks": list((saved.get(task) or {}).get("fallbacks", [])),
            }
            for task in TASKS
        }

    def route(self, task: str) -> list[str]:
        if task not in TASKS:
            raise ValueError("invalid_routing_task")
        choice = self.routing()[task]
        return [choice["primary"], *choice["fallbacks"]] if choice["primary"] else []

    def save(self, connection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(connection_id, str) or not _ID.fullmatch(connection_id):
            raise ValueError("invalid_connection_id")
        if connection_id in providers.PROVIDERS:
            raise ValueError("reserved_api_provider_id")
        with _LOCK:
            previous = self.get(connection_id) or {}
            kind = payload.get("kind", previous.get("kind", "openai_compatible"))
            if kind not in KINDS:
                raise ValueError("invalid_connection_kind")
            if connection_id in ("codex", "claude") and kind != connection_id:
                raise ValueError("native_connection_kind_immutable")
            if kind in ("codex", "claude") and connection_id != kind:
                raise ValueError("duplicate_native_connection")
            name = str(payload.get("name", previous.get("name", connection_id))).strip()
            model = str(payload.get("model", previous.get("model", "")) or "").strip()
            enabled = payload.get("enabled", previous.get("enabled", False))
            external_agent = payload.get("external_agent", previous.get("external_agent", False))
            acknowledged = payload.get(
                "tools_policy_acknowledged", previous.get("tools_policy_acknowledged", False)
            )
            if (
                not name
                or len(name) > 100
                or len(model) > 200
                or any(char in name + model for char in "\r\n\x00")
                or not all(
                    isinstance(value, bool) for value in (enabled, external_agent, acknowledged)
                )
            ):
                raise ValueError("invalid_connection_fields")
            row: dict[str, Any] = {
                "id": connection_id,
                "name": name,
                "kind": kind,
                "model": model,
                "enabled": enabled,
                "external_agent": external_agent if kind == "openai_compatible" else False,
                "tools_policy_acknowledged": acknowledged if kind == "openai_compatible" else False,
            }
            if kind == "openai_compatible":
                row["base_url"] = validate_base_url(
                    str(payload.get("base_url", previous.get("base_url", "")))
                )
                if not model:
                    raise ValueError("connection_model_required")
            key = payload.get("api_key")
            if key is not None:
                if not isinstance(key, str) or len(key) > 8192 or any(c in key for c in "\r\n\x00"):
                    raise ValueError("invalid_connection_key")
                if kind != "openai_compatible":
                    raise ValueError("native_connection_has_no_api_key")
                keys = _read(self.secrets_path)
                if key.strip():
                    keys[connection_id] = key.strip()
                else:
                    keys.pop(connection_id, None)
                _write(self.secrets_path, keys)
            config = self._configuration()
            config.setdefault("connections", {})[connection_id] = row
            _write(self.path, config)
            return row

    def delete(self, connection_id: str) -> None:
        with _LOCK:
            if any(connection_id in self.route(task) for task in TASKS):
                raise ValueError("connection_in_use")
            config = self._configuration()
            config.setdefault("connections", {}).pop(connection_id, None)
            keys = _read(self.secrets_path)
            keys.pop(connection_id, None)
            _write(self.secrets_path, keys)
            _write(self.path, config)

    def save_routing(self, payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) - set(TASKS):
            raise ValueError("invalid_routing_task")
        with _LOCK:
            known = {row["id"]: row for row in self.public_connections()}
            routing = self.routing()
            for task, choice in payload.items():
                if not isinstance(choice, dict) or set(choice) - {"primary", "fallbacks"}:
                    raise ValueError("invalid_route")
                primary = choice.get("primary") or None
                fallbacks = choice.get("fallbacks", [])
                if not isinstance(fallbacks, list) or len(fallbacks) > 16:
                    raise ValueError("invalid_fallbacks")
                ids = ([primary] if primary else []) + fallbacks
                if not all(isinstance(cid, str) for cid in ids):
                    raise ValueError("invalid_fallbacks")
                if (not primary and fallbacks) or len(ids) != len(set(ids)):
                    raise ValueError("invalid_fallbacks")
                if any(cid not in known or not known[cid]["configured"] for cid in ids):
                    raise ValueError("connection_unavailable")
                if any(
                    known[cid].get("external_agent")
                    and not known[cid].get("tools_policy_acknowledged")
                    for cid in ids
                ):
                    raise ValueError("external_agent_policy_not_acknowledged")
                routing[task] = {"primary": primary, "fallbacks": fallbacks}
            config = self._configuration()
            config["routing"] = routing
            _write(self.path, config)
            return routing

    def use_codex_profile(self) -> dict[str, Any]:
        with _LOCK:
            self.save("codex", {"kind": "codex", "name": "Codex", "enabled": True})
            return self.save_routing(
                {task: {"primary": "codex", "fallbacks": []} for task in TASKS}
            )

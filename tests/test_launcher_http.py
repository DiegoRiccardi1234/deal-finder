"""La presenza di un vecchio namespace httptools non deve rompere il server."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import pytest


def test_requested_port_survives_restart_and_explicit_cli_wins(monkeypatch):
    from scripts import launch_tray

    monkeypatch.delenv("DEAL_FINDER_PORT", raising=False)
    assert launch_tray._requested_port(None) is None
    monkeypatch.setenv("DEAL_FINDER_PORT", "8540")
    assert launch_tray._requested_port(None) == 8540
    assert launch_tray._requested_port(8541) == 8541
    monkeypatch.setenv("DEAL_FINDER_PORT", "invalid")
    assert launch_tray._requested_port(8541) == 8541


@pytest.mark.parametrize("raw", ["invalid", "0", "65536", "-1"])
def test_invalid_restart_port_is_explicit_error(monkeypatch, raw):
    from scripts import launch_tray

    monkeypatch.setenv("DEAL_FINDER_PORT", raw)
    with pytest.raises(ValueError, match="porta"):
        launch_tray._requested_port(None)


def test_launcher_serve_http_con_namespace_httptools_residuo(tmp_path):
    # I file nativi rimasti da 4.1 rendono importabile la cartella, ma nel PYZ
    # nuovo manca __init__: questa è la condizione osservata nel vero update.
    (tmp_path / "httptools" / "parser").mkdir(parents=True)
    script = textwrap.dedent(
        """
        import importlib.machinery
        import importlib.util
        import os
        import socket
        import sys
        import time
        from urllib.request import urlopen

        root = os.environ["DEAL_FINDER_WORKSPACE"]
        spec = importlib.machinery.PathFinder.find_spec("httptools", [root])
        assert spec is not None and spec.origin is None
        namespace = importlib.util.module_from_spec(spec)
        assert not hasattr(namespace, "HttpRequestParser")
        # Isola il caso anche se l'ambiente di test ha un vero httptools.
        sys.modules["httptools"] = namespace
        sys.modules.pop("uvicorn.protocols.http.auto", None)

        from scripts import launch_tray
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]

        # Esegue il percorso del launcher: un helper non collegato all'avvio
        # non basta a superare questa prova.
        launch_tray._avvia_streamlit(port)

        from uvicorn import Config
        from uvicorn.protocols.http.h11_impl import H11Protocol
        async def app(scope, receive, send):
            pass
        protocol = Config(app, http="auto", ws="none", lifespan="off", log_config=None)
        protocol.load()
        assert protocol.http_protocol_class is H11Protocol

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=0.5) as response:
                    assert response.status == 200
                    assert response.read().strip() == b"ok"
                    print("H11 HTTP 200 with residual httptools namespace", flush=True)
                    break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("Il launcher non risponde con il namespace httptools residuo")
        """
    )
    env = dict(os.environ)
    env.update(DEAL_FINDER_WORKSPACE=str(tmp_path), DISABLE_AUTO_UPDATE="1")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=25,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "H11 HTTP 200 with residual httptools namespace" in result.stdout

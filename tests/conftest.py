import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen
from unittest.mock import MagicMock

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]


def pytest_collection_modifyitems(config, items):
    """Marca come `playwright` i test E2E (usano la fixture `page`) così che
    `-k "not playwright"` li escluda davvero, e li skippa quando Playwright
    non è installato, lasciando girare la suite unit."""
    try:
        import playwright.sync_api  # noqa: F401

        have_playwright = True
    except ImportError:
        have_playwright = False

    skip_e2e = pytest.mark.skip(reason="Playwright non installato")
    for item in items:
        if "page" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.playwright)
            if not have_playwright:
                item.add_marker(skip_e2e)


def _wait_for_server(url: str, timeout: float = 30.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except URLError:
            time.sleep(0.5)
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"Server non raggiungibile su {url} entro {timeout} secondi")


@pytest.fixture
def cerebras_mock() -> MagicMock:
    client = MagicMock()
    # La risposta deve essere in formato batch: {"1": {...specs...}}
    # perché fetch_specs_ai ora fa una singola chiamata per tutti i prodotti
    single_spec = {
        "display": "6.1 OLED",
        "processore": "A19",
        "ram": "8 GB",
        "storage": "256 GB",
        "batteria": None,
        "fotocamera": None,
        "peso": None,
        "os": "iOS",
    }
    content = json.dumps({"1": single_spec}, ensure_ascii=False)
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=content))]
    client.chat.completions.create.return_value = response
    return client


@pytest.fixture(scope="session")
def base_url() -> str:
    return "http://localhost:9000"


@pytest.fixture(scope="session")
def streamlit_server(base_url: str) -> str:
    env = os.environ.copy()
    env["APP_TEST_MODE"] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")

    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "app.py",
            "--server.headless",
            "true",
            "--server.port",
            "9000",
        ],
        cwd=ROOT_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_for_server(base_url)
        yield base_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

"""Exercise the real multi-product chat, with isolated local synthetic data."""

import os
import subprocess
import sys
import socket
from pathlib import Path

import pytest
from playwright.sync_api import expect

from conftest import _wait_for_server


@pytest.fixture(scope="module")
def assistant_url(tmp_path_factory):
    workspace = tmp_path_factory.mktemp("assistant-browser")
    secrets = workspace / "empty-secrets.toml"
    secrets.write_text("", encoding="utf-8")
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(term in key.upper() for term in ("API_KEY", "APP_PASSWORD", "AUTH_TOKEN"))
    }
    env.update(APP_TEST_MODE="1", DEAL_FINDER_WORKSPACE=str(workspace))
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "tests/browser_assistant_app.py",
            "--server.headless",
            "true",
            "--server.port",
            "9001",
            "--secrets.files",
            str(secrets),
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_server("http://localhost:9001")
        yield "http://localhost:9001"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.playwright
def test_five_products_budget_question_then_complete_and_reload(page, assistant_url):
    page.goto(assistant_url)
    page.get_by_role("heading", name="Cosa cerchiamo?").wait_for()
    page.get_by_role("textbox", name="Prodotti e preferenze").fill(
        "Cercami iPhone 15, notebook, cuffie, lavatrice e monitor"
    )
    page.get_by_role("button", name="Cerca e confronta", exact=True).click()
    expect(page.get_by_text("Qual è il budget massimo per", exact=False)).to_be_visible(
        timeout=15000
    )
    page.get_by_role("textbox", name="La tua risposta").fill(
        "iPhone e notebook massimo 500 euro, per gli altri non so"
    )
    page.get_by_role("button", name="Continua la ricerca").click()
    expect(page.get_by_text("Confronto sintetico completato", exact=False)).to_be_visible(
        timeout=20000
    )
    expect(page.get_by_text("5/5 prodotti completati", exact=True)).to_be_visible()
    expect(page.get_by_text("Senza tetto di prezzo", exact=False).first).to_be_visible()
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)
    artifacts = Path(__file__).resolve().parents[1] / ".giorno-notte" / "tmp"
    artifacts.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(artifacts / "assistant-desktop.png"), full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path=str(artifacts / "assistant-mobile.png"), full_page=True)
    page.set_viewport_size({"width": 1280, "height": 900})
    page.reload()
    page.get_by_role("heading", name="Cosa cerchiamo?").wait_for()
    page.get_by_text("Riprendi una ricerca", exact=True).click()
    page.get_by_role(
        "button", name="Cercami iPhone 15, notebook, cuffie, lavatrice e monitor"
    ).first.click()
    expect(page.get_by_text("Confronto sintetico completato", exact=False)).to_be_visible(
        timeout=15000
    )
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)


@pytest.mark.playwright
def test_local_mcp_setup_configs_and_revocation_without_inference(page, assistant_url):
    page.goto(assistant_url)
    page.get_by_role("heading", name="Cosa cerchiamo?").wait_for()
    sidebar = page.locator('[data-testid="stSidebar"]')
    sidebar.get_by_text("AI e assistenti", exact=True).click()
    sidebar.get_by_text("Collega un assistente via MCP", exact=True).click()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    field = sidebar.get_by_role("spinbutton", name="Porta locale MCP")
    field.fill(str(port))
    field.press("Enter")
    sidebar.get_by_role("button", name="Genera configurazione MCP").click()
    expect(sidebar.get_by_text("[mcp_servers.dealfinder]", exact=False)).to_be_visible(
        timeout=15000
    )
    expect(sidebar.get_by_text("mcp_servers:", exact=False)).to_be_visible()
    expect(sidebar.get_by_text("Cowork", exact=True)).to_be_visible()
    expect(page.locator('[data-testid="stException"]')).to_have_count(0)
    sidebar.get_by_role("button", name="Revoca collegamento MCP").click()
    expect(sidebar.get_by_text("Collegamento revocato.", exact=True)).to_be_visible()
    expect(sidebar.get_by_text("[mcp_servers.dealfinder]", exact=False)).to_have_count(0)

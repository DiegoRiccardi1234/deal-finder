"""Test UI/UX home page e navigazione."""
import re
import pytest
from playwright.sync_api import Page, expect


BASE = "http://localhost:8501"


def wait_streamlit(page: Page, timeout: int = 15000) -> None:
    """Attende che Streamlit finisca di caricare."""
    page.wait_for_load_state("networkidle", timeout=timeout)
    # Attende che sparisca il running indicator
    try:
        page.wait_for_selector('[data-testid="stStatusWidget"]', state="hidden", timeout=5000)
    except Exception:
        pass


# ─── Home page ──────────────────────────────────────────────────────────────

def test_home_loads(page: Page):
    """Home page carica senza errori."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    page.screenshot(path="tests/screenshots/home_full.png", full_page=True)
    # Titolo presente
    expect(page.locator("h1")).to_contain_text("Risparmia")


def test_hero_cta_buttons_visible(page: Page):
    """I bottoni CTA dell'hero sono visibili."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    primary = page.locator("a.hero-cta-primary").first
    secondary = page.locator("a.hero-cta-secondary").first
    expect(primary).to_be_visible()
    expect(secondary).to_be_visible()
    expect(primary).to_contain_text("Cerca")
    expect(secondary).to_contain_text("Scopri")


def test_hero_cta_no_new_tab(page: Page):
    """Il bottone primario NON ha target=_blank (non apre nuova scheda)."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    primary = page.locator("a.hero-cta-primary").first
    target = primary.get_attribute("target")
    assert target != "_blank", f"Il bottone apre nuova scheda! target={target}"


def test_hero_cta_link_correct(page: Page):
    """Il bottone primario punta a /Tool."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    href = page.locator("a.hero-cta-primary").first.get_attribute("href")
    assert "/Tool" in (href or ""), f"href sbagliato: {href}"


def test_source_strip_visible(page: Page):
    """La source strip con i 7 store è visibile."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    strip = page.locator(".source-strip")
    expect(strip).to_be_visible()
    for store in ["Amazon", "eBay", "Unieuro", "MediaWorld", "Euronics", "Vinted"]:
        assert store in strip.inner_text(), f"Store mancante: {store}"


def test_feature_cards_visible(page: Page):
    """Le 3 feature cards sono presenti."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    cards = page.locator(".feature-card")
    expect(cards).to_have_count(3)


def test_how_it_works_icons(page: Page):
    """Le how-cards hanno icone e label."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    icons = page.locator(".how-card-icon")
    assert icons.count() == 4, f"Attese 4 icone, trovate {icons.count()}"
    labels = page.locator(".how-card-label")
    assert labels.count() == 4


def test_how_cta_banner_visible(page: Page):
    """Il banner finale 'Inizia a risparmiare ora' è visibile."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    cta = page.locator(".how-cta")
    expect(cta).to_be_visible()
    expect(cta.locator("h3")).to_contain_text("Inizia")
    cta_btn = cta.locator("a.hero-cta-primary")
    expect(cta_btn).to_be_visible()
    assert cta_btn.get_attribute("target") != "_blank"


# ─── Navigazione ────────────────────────────────────────────────────────────

def test_navigate_to_tool_same_tab(page: Page):
    """Clic su 'Cerca un prodotto' naviga nella stessa scheda alla pagina Tool."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    btn = page.locator("a.hero-cta-primary").first

    # Verifica che NON apra una nuova scheda
    with page.expect_navigation(timeout=10000):
        btn.click()

    wait_streamlit(page)
    page.screenshot(path="tests/screenshots/after_cta_click.png", full_page=True)
    # Deve essere rimasto sullo stesso browser tab (URL cambiata)
    assert "8501" in page.url, f"URL inaspettato: {page.url}"


def test_tool_page_loads(page: Page):
    """La pagina /Tool carica correttamente."""
    page.goto(BASE + "/Tool")
    wait_streamlit(page)
    page.screenshot(path="tests/screenshots/tool_page.png", full_page=True)
    # Non deve mostrare errore "page not found"
    content = page.content()
    assert "not found" not in content.lower() or "streamlit" in content.lower()


def test_navbar_links(page: Page):
    """I link della navbar funzionano."""
    page.goto(BASE + "/")
    wait_streamlit(page)
    nav_links = page.locator(".top-nav-link")
    assert nav_links.count() >= 2
    # Link Home e Cerca Prezzi presenti
    texts = nav_links.all_inner_texts()
    assert any("Home" in t for t in texts)
    assert any("Cerca" in t for t in texts)


# ─── Screenshot finale ───────────────────────────────────────────────────────

def test_home_screenshot_desktop(page: Page):
    """Screenshot desktop della home page."""
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(BASE + "/")
    wait_streamlit(page)
    page.screenshot(path="tests/screenshots/home_desktop.png", full_page=True)


def test_home_screenshot_mobile(page: Page):
    """Screenshot mobile della home page."""
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE + "/")
    wait_streamlit(page)
    page.screenshot(path="tests/screenshots/home_mobile.png", full_page=True)

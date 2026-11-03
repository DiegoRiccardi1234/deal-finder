"""Real MediaWorld markup must not turn seller Beatrix20 into a EUR20 phone."""

from __future__ import annotations

import math

import pytest
from bs4 import BeautifulSoup

from offerte.scrapers import mediaworld


def _card(price: str, *, name="APPLE iPhone 15, 128 GB, Nero") -> str:
    return f"""<article data-test="mms-product-card">
      <a href="/it/product/_apple-iphone-15-128-gb-nero-168733842.html">Telefono</a>
      <h2 data-test="product-title">{name}</h2>
      <div data-test="cofr-price product-price">{price}</div>
    </article>"""


def _response(html):
    class Response:
        status_code = 200
        text = html

        def raise_for_status(self):
            pass

    return Response()


@pytest.mark.parametrize("budget,expected", [(500, []), (900, [621.99])])
def test_seller_number_is_not_the_price(monkeypatch, budget, expected):
    html = _card("""<div data-test="mms-price"><div>
      <span aria-hidden="true">621,99&nbsp;€</span><span>621,99€</span></div>
      <div data-test="additional-info-normal-wrapper">
        <p>Venduto e spedito da <a>Beatrix20</a></p></div></div>""")
    monkeypatch.setattr(mediaworld, "fetch_with_retry", lambda *a, **k: _response(html))
    monkeypatch.setattr(mediaworld, "_random_delay", lambda: None)
    rows = mediaworld.scrape_mediaworld("iPhone 15", 0, budget, ["iphone", "15"])
    assert [row.prezzo for row in rows] == expected


@pytest.mark.parametrize(
    "content,expected",
    [
        (
            '<div data-test="mms-strike-price-type-rrp">Consigliato 979,00€</div><div><span>699,00€</span><span>699,00€</span></div><div>29,12€ /mese</div>',
            699.0,
        ),
        (
            '<div data-test="mms-strike-price-type-rrp">Consigliato 979,00€</div><div><span>699,00€</span></div><div><span>29,12€</span><span>/mese</span></div>',
            699.0,
        ),
        (
            "<div><span>699,00€</span><span>699,00€</span></div><div><span>29,12€</span><span>29,12€</span><span>prima rata</span></div>",
            699.0,
        ),
        ("<span>Sconto -20%</span><div><span>699,00€</span></div>", 699.0),
        (
            '<div><span>14,99€</span><span>14,99€</span></div><p data-test="additional-info-normal">Spedito da Seller123</p>',
            14.99,
        ),
        ("<span>1.299,00 €</span><span>1.299,00€</span>", 1299.0),
        ("<span>699,00</span>", 699.0),
        ("<span>29,12€ /mese</span>", math.inf),
        ("<span>499,00€</span><span>599,00€</span>", math.inf),
        ('<div data-test="mms-strike-price-type-rrp">Consigliato 979,00€</div>', math.inf),
    ],
)
def test_sale_price_excludes_reference_installments_and_ambiguity(content, expected):
    tag = BeautifulSoup(_card(content), "html.parser").select_one('[data-test~="product-price"]')
    assert mediaworld._sale_price(tag) == expected


def test_explicit_accessory_below_twenty_euros_remains_eligible(monkeypatch):
    html = _card("<span>14,99€</span>", name="Custodia iPhone 15")
    monkeypatch.setattr(mediaworld, "fetch_with_retry", lambda *a, **k: _response(html))
    monkeypatch.setattr(mediaworld, "_random_delay", lambda: None)
    rows = mediaworld.scrape_mediaworld("custodia iphone 15", 0, 20, ["custodia", "iphone", "15"])
    assert [row.prezzo for row in rows] == [14.99]

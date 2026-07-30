"""offerte: offerte/scrapers/_base.py"""

from __future__ import annotations

import base64
import time

import requests

from offerte._constants import *  # noqa: F401,F403


def _get_ebay_token(app_id: str, cert_id: str) -> str:
    """Ottiene e cachea un access token eBay OAuth2 tramite client credentials."""
    now = time.time()
    cached_token = str(_EBAY_TOKEN_CACHE.get("token") or "")
    cached_expiry = float(_EBAY_TOKEN_CACHE.get("expires_at") or 0)
    if cached_token and now < cached_expiry - 60:
        return cached_token

    credentials = f"{app_id}:{cert_id}".encode()
    headers = {
        "Authorization": f"Basic {base64.b64encode(credentials).decode('ascii')}",
        "Content-Type": "application/x-www-form-urlencoded",
    }
    data = {
        "grant_type": "client_credentials",
        "scope": "https://api.ebay.com/oauth/api_scope",
    }
    response = requests.post(
        "https://api.ebay.com/identity/v1/oauth2/token",
        headers=headers,
        data=data,
        timeout=TIMEOUT,
    )
    response.raise_for_status()

    payload = response.json()
    access_token = str(payload.get("access_token") or "").strip()
    expires_in = int(payload.get("expires_in", 7200) or 7200)
    if not access_token:
        raise RuntimeError("Token eBay mancante nella risposta OAuth2")

    _EBAY_TOKEN_CACHE["token"] = access_token
    _EBAY_TOKEN_CACHE["expires_at"] = now + expires_in
    return access_token


# NB: qui vivevano anche copie delle costanti di endpoint di Unieuro, Comet e
# Wallapop, residuo dello split del monolite. Erano codice morto — ogni scraper
# definisce e usa le proprie — e duplicavano le chiavi Algolia in un secondo
# punto del repo. Rimosse: le costanti stanno nel modulo che le usa.

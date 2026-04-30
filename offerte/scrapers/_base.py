"""offerte: offerte/scrapers/_base.py"""
from __future__ import annotations

import base64
import json
import math
import os
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    from cerebras.cloud.sdk import Cerebras
except Exception:
    Cerebras = None

try:
    from offerte.ai import (
        get_best_model as _get_best_model,
        cerebras_chat_with_retry as _cerebras_chat_lib,
    )
except Exception:
    _get_best_model = None  # type: ignore[assignment]
    _cerebras_chat_lib = None  # type: ignore[assignment]

_CEREBRAS_MODEL_FALLBACK = "llama-3.3-70b"
from offerte._constants import *  # noqa: F401,F403
from offerte.http import fetch_with_retry

def _get_ebay_token(app_id: str, cert_id: str) -> str:
    """Ottiene e cachea un access token eBay OAuth2 tramite client credentials."""
    now = time.time()
    cached_token = str(_EBAY_TOKEN_CACHE.get("token") or "")
    cached_expiry = float(_EBAY_TOKEN_CACHE.get("expires_at") or 0)
    if cached_token and now < cached_expiry - 60:
        return cached_token

    credentials = f"{app_id}:{cert_id}".encode("utf-8")
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


_UNIEURO_ALGOLIA_URL = (
    "https://mnbcenyfii-dsn.algolia.net/1/indexes/*/queries"
    "?x-algolia-api-key=977ed8d06b718d4929ca789c78c4107a"
    "&x-algolia-application-id=MNBCENYFII"
)

_WALLAPOP_COMPONENTS_URL = "https://api.wallapop.com/api/v3/search/components"
_WALLAPOP_SECTION_URL = "https://api.wallapop.com/api/v3/search/section"

_COMET_ALGOLIA_URL = "https://mvk2s77iyi-dsn.algolia.net/1/indexes/*/queries"
_COMET_ALGOLIA_APP_ID = "MVK2S77IYI"
_COMET_ALGOLIA_API_KEY = "f7f4f516742fcb4597c1e71641f7d0ed"


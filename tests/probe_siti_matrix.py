from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

ANTI_BOT_PATTERNS = [
    "captcha",
    "robot",
    "access denied",
    "forbidden",
    "verify you are human",
    "cloudflare",
    "attention required",
    "security check",
]


@dataclass
class SiteConfig:
    name: str
    url: str
    query: str
    selectors: list[dict[str, str]]


SITES: list[SiteConfig] = [
    SiteConfig(
        name="trovaprezzi.it",
        query="notebook",
        url="https://www.trovaprezzi.it/cerca.aspx?nome=notebook",
        selectors=[
            {
                "name": "a.product-name",
                "price": ".price, .prezzo, .shop-price",
                "link": "a.product-name",
            },
            {
                "name": ".listing_title a, .listing-title a",
                "price": ".price, .prezzo",
                "link": ".listing_title a, .listing-title a",
            },
            {"name": "h2 a", "price": ".price, .prezzo", "link": "h2 a"},
        ],
    ),
    SiteConfig(
        name="subito.it",
        query="iphone 14",
        url="https://www.subito.it/annunci-italia/vendita/usato/?q=iphone+14",
        selectors=[
            {
                "name": '[data-testid="listing-card-title"], [class*="SmallCard_title"]',
                "price": '[data-testid="listing-price"], [class*="price"]',
                "link": 'a[href*="/annunci-"]',
            },
            {
                "name": "article h2, article h3",
                "price": "article [class*='price']",
                "link": "article a[href]",
            },
        ],
    ),
    SiteConfig(
        name="eprice.it",
        query="notebook",
        url="https://www.eprice.it/s/cerca/notebook",
        selectors=[
            {
                "name": "[data-testid*='product'] h2, [data-testid*='product'] h3",
                "price": "[data-testid*='price'], [class*='price']",
                "link": "a[href*='/product/'], a[href*='notebook']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
    SiteConfig(
        name="trony.it",
        query="notebook",
        url="https://www.trony.it/catalogsearch/result/?q=notebook",
        selectors=[
            {
                "name": "[class*='product-item-name'] a, [class*='product-name'] a",
                "price": "[class*='price']",
                "link": "a[href*='product'], a[href*='catalog']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
    SiteConfig(
        name="mediamarkt.it",
        query="notebook",
        url="https://www.mediamarkt.it/it/search.html?query=notebook",
        selectors=[
            {
                "name": "[data-test='mms-product-name'], [class*='productTitle']",
                "price": "[data-test='mms-price'], [class*='price']",
                "link": "a[href*='/product/']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
    SiteConfig(
        name="unieuro.it",
        query="notebook",
        url="https://www.unieuro.it/online/search?text=notebook",
        selectors=[
            {
                "name": "[data-testid*='product'] h2, [data-testid*='product'] h3",
                "price": "[data-testid*='price'], [class*='price']",
                "link": "a[href*='/online/']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
    SiteConfig(
        name="zalando.it",
        query="scarpe nike",
        url="https://www.zalando.it/catalogo/?q=scarpe+nike",
        selectors=[
            {
                "name": "[data-testid='product-card'] [data-testid='product-card-name'], article h3",
                "price": "[data-testid='product-card-price'], [class*='price']",
                "link": "a[href*='-']",
            },
            {
                "name": "article h3, article h2",
                "price": "article [class*='price']",
                "link": "article a[href]",
            },
        ],
    ),
    SiteConfig(
        name="asos.com",
        query="t-shirt",
        url="https://www.asos.com/it/search/?q=t-shirt",
        selectors=[
            {
                "name": "[data-auto-id='productTileTitle'], article h2, article h3",
                "price": "[data-auto-id='productTilePrice'], [class*='price']",
                "link": "a[href*='/prd/'], a[href*='/product/']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
    SiteConfig(
        name="aliexpress.it",
        query="notebook",
        url="https://it.aliexpress.com/wholesale?SearchText=notebook",
        selectors=[
            {
                "name": "a[href*='/item/'] h3, a[href*='/item/'] [class*='title']",
                "price": "a[href*='/item/'] [class*='price'], [class*='price-current']",
                "link": "a[href*='/item/']",
            },
            {"name": "h3", "price": ".price, [class*='price']", "link": "a[href*='/item/']"},
        ],
    ),
    SiteConfig(
        name="shein.com",
        query="vestito",
        url="https://it.shein.com/pdsearch/vestito/",
        selectors=[
            {
                "name": "[class*='S-product-item__name'], [class*='product-item__title']",
                "price": "[class*='from-price'], [class*='normal-price']",
                "link": "a[href*='-cat-'], a[href*='-p-']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
    SiteConfig(
        name="temu.com",
        query="notebook",
        url="https://www.temu.com/it/search_result.html?search_key=notebook",
        selectors=[
            {
                "name": "[class*='goods-title'], [class*='product-title']",
                "price": "[class*='price'], [data-testid*='price']",
                "link": "a[href*='/g-']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
    SiteConfig(
        name="alibaba.com",
        query="notebook",
        url="https://www.alibaba.com/trade/search?SearchText=notebook",
        selectors=[
            {
                "name": "[class*='search-card-e-title'], [class*='organic-gallery-title']",
                "price": "[class*='search-card-e-price-main'], [class*='price']",
                "link": "a[href*='/product-detail/'], a[href*='/product/']",
            },
            {"name": "h2 a, h3 a", "price": ".price, [class*='price']", "link": "a[href]"},
        ],
    ),
]


def detect_antibot(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in ANTI_BOT_PATTERNS)


def discover_api_hints_from_html(soup: BeautifulSoup, base_url: str) -> list[str]:
    hints: set[str] = set()
    tokens = ["algolia", "elasticsearch", "graphql", "/api/", "search", "query"]

    for s in soup.find_all("script"):
        src = str(s.get("src") or "")
        if src and any(t in src.lower() for t in tokens):
            hints.add(urljoin(base_url, src))
        txt = s.string or s.get_text(" ", strip=True)
        low = txt.lower()
        for token in tokens:
            if token in low:
                if token in ("/api/", "graphql"):
                    for m in re.findall(
                        r"https?://[^\"'\s]+|/api/[^\"'\s]+|/graphql[^\"'\s]*", txt
                    ):
                        hints.add(urljoin(base_url, m))
                else:
                    hints.add(f"inline:{token}")

    return sorted(hints)[:10]


def extract_selector_match(
    soup: BeautifulSoup, candidates: list[dict[str, str]]
) -> dict[str, Any] | None:
    best: dict[str, Any] | None = None
    best_score = -1

    for cand in candidates:
        name_sel = cand["name"]
        price_sel = cand["price"]
        link_sel = cand["link"]

        names = soup.select(name_sel)
        prices = soup.select(price_sel)
        links = [a for a in soup.select(link_sel) if a.get("href")]

        score = min(len(names), 20) + min(len(prices), 20) + min(len(links), 20)
        if score > best_score:
            sample_name = names[0].get_text(" ", strip=True)[:120] if names else ""
            sample_price = prices[0].get_text(" ", strip=True)[:50] if prices else ""
            sample_link = str(links[0].get("href", ""))[:180] if links else ""
            best = {
                "name": name_sel,
                "price": price_sel,
                "link": link_sel,
                "name_count": len(names),
                "price_count": len(prices),
                "link_count": len(links),
                "sample_name": sample_name,
                "sample_price": sample_price,
                "sample_link": sample_link,
            }
            best_score = score

    if not best:
        return None

    if best["name_count"] >= 2 and best["link_count"] >= 2 and best["price_count"] >= 1:
        return best
    return None


def probe_requests(site: SiteConfig) -> dict[str, Any]:
    out: dict[str, Any] = {
        "url": site.url,
        "status": None,
        "final_url": None,
        "html_len": 0,
        "title": "",
        "antibot": False,
        "selector_match": None,
        "api_hints": [],
        "error": None,
    }

    try:
        resp = requests.get(site.url, headers=HEADERS, timeout=25, allow_redirects=True)
        out["status"] = resp.status_code
        out["final_url"] = resp.url
        html = resp.text
        out["html_len"] = len(html)
        out["antibot"] = detect_antibot(html)
        soup = BeautifulSoup(html, "html.parser")
        out["title"] = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
        out["selector_match"] = extract_selector_match(soup, site.selectors)
        out["api_hints"] = discover_api_hints_from_html(soup, resp.url)
    except Exception as exc:
        out["error"] = str(exc)

    return out


def probe_playwright(site: SiteConfig, context) -> dict[str, Any]:
    out: dict[str, Any] = {
        "url": site.url,
        "status": None,
        "final_url": None,
        "html_len": 0,
        "title": "",
        "antibot": False,
        "selector_match": None,
        "api_hints": [],
        "error": None,
    }

    req_hints: set[str] = set()
    page = context.new_page()

    def on_request(req) -> None:
        u = req.url
        low = u.lower()
        if req.resource_type in ("xhr", "fetch") or any(
            t in low for t in ["algolia", "graphql", "/api/", "elasticsearch", "search", "query"]
        ):
            req_hints.add(u)

    page.on("request", on_request)

    try:
        resp = page.goto(site.url, wait_until="domcontentloaded", timeout=25000)
        page.wait_for_timeout(5000)
        html = page.content()
        out["status"] = resp.status if resp else None
        out["final_url"] = page.url
        out["html_len"] = len(html)
        out["antibot"] = detect_antibot(html)
        out["title"] = page.title()

        soup = BeautifulSoup(html, "html.parser")
        out["selector_match"] = extract_selector_match(soup, site.selectors)

        script_hints = discover_api_hints_from_html(soup, page.url)
        all_hints = set(script_hints)
        all_hints.update(req_hints)
        out["api_hints"] = sorted(all_hints)[:20]
    except Exception as exc:
        out["error"] = str(exc)
    finally:
        try:
            page.close()
        except Exception:
            pass

    return out


def probe_unieuro_algolia() -> dict[str, Any]:
    out: dict[str, Any] = {
        "ok": False,
        "status": None,
        "hits": 0,
        "sample": None,
        "error": None,
        "endpoint": (
            "https://mnbcenyfii-dsn.algolia.net/1/indexes/*/queries"
            "?x-algolia-api-key=977ed8d06b718d4929ca789c78c4107a"
            "&x-algolia-application-id=MNBCENYFII"
        ),
    }
    payload = {
        "requests": [
            {
                "indexName": "sgmproducts_prod",
                "query": "notebook",
                "hitsPerPage": 12,
                "page": 0,
            }
        ]
    }
    headers = {
        "content-type": "application/x-www-form-urlencoded",
        "x-algolia-application-id": "MNBCENYFII",
        "x-algolia-api-key": "977ed8d06b718d4929ca789c78c4107a",
        "user-agent": HEADERS["User-Agent"],
    }

    try:
        resp = requests.post(out["endpoint"], data=json.dumps(payload), headers=headers, timeout=20)
        out["status"] = resp.status_code
        data = resp.json()
        hits = data.get("results", [{}])[0].get("hits", [])
        out["hits"] = len(hits)
        if hits:
            first = hits[0]
            out["sample"] = {
                "name": first.get("name") or first.get("title"),
                "price": first.get("price") or first.get("pFinale"),
                "url": first.get("url") or first.get("link"),
            }
        out["ok"] = resp.status_code == 200 and len(hits) > 0
    except Exception as exc:
        out["error"] = str(exc)

    return out


def analyze_trovaprezzi(requests_result: dict[str, Any]) -> dict[str, Any]:
    analysis: dict[str, Any] = {
        "price_filter_pattern": False,
        "redirect_links_to_shops": False,
        "aggregated_shop_keywords": [],
        "estimated_shop_count": None,
        "api_rest_found": False,
    }

    try:
        resp = requests.get(requests_result["url"], headers=HEADERS, timeout=25)
        soup = BeautifulSoup(resp.text, "html.parser")

        hrefs = [str(a.get("href") or "") for a in soup.select("a[href]")]
        combined = "\n".join(hrefs).lower()
        analysis["price_filter_pattern"] = ("prezzo" in combined) or ("price" in combined)

        product_links = []
        for h in hrefs:
            full = urljoin(resp.url, h)
            netloc = urlparse(full).netloc.lower()
            if "trovaprezzi.it" in netloc and ("/prezzi/" in full or "/offerte/" in full):
                product_links.append(full)
        product_links = list(dict.fromkeys(product_links))[:3]

        keywords = [
            "amazon",
            "euronics",
            "unieuro",
            "mediaworld",
            "ebay",
            "trony",
            "comet",
            "expert",
            "eprice",
        ]
        found_kw: set[str] = set()
        ext_domains: set[str] = set()

        for plink in product_links:
            try:
                dresp = requests.get(plink, headers=HEADERS, timeout=20)
                text = dresp.text.lower()
                for kw in keywords:
                    if kw in text:
                        found_kw.add(kw)
                dsoup = BeautifulSoup(dresp.text, "html.parser")
                for a in dsoup.select("a[href]"):
                    h = str(a.get("href") or "")
                    if not h:
                        continue
                    full = urljoin(dresp.url, h)
                    netloc = urlparse(full).netloc.lower()
                    if netloc and "trovaprezzi.it" not in netloc:
                        ext_domains.add(netloc)
            except Exception:
                continue

        analysis["aggregated_shop_keywords"] = sorted(found_kw)
        analysis["estimated_shop_count"] = len(ext_domains) if ext_domains else None
        analysis["redirect_links_to_shops"] = len(ext_domains) > 0

        api_hints = requests_result.get("api_hints") or []
        analysis["api_rest_found"] = any(
            ("/api/" in h.lower() or "graphql" in h.lower() or "algolia" in h.lower())
            for h in api_hints
        )
    except Exception:
        pass

    return analysis


def choose_method(
    site_name: str, req: dict[str, Any], pw: dict[str, Any], unieuro_api: dict[str, Any] | None
) -> str:
    if site_name == "unieuro.it" and unieuro_api and unieuro_api.get("ok"):
        return "API diretta"
    if (
        req.get("selector_match")
        and not req.get("antibot")
        and (req.get("status") in (200, 201, 202))
    ):
        return "requests"
    if pw.get("selector_match") and not pw.get("antibot") and (pw.get("status") in (200, 201, 202)):
        return "Playwright"
    if req.get("selector_match"):
        return "requests"
    if pw.get("selector_match"):
        return "Playwright"
    return "nessuno"


def classify_feasibility(method: str, req: dict[str, Any], pw: dict[str, Any]) -> str:
    req_status = req.get("status")
    pw_status = pw.get("status")
    blocked = (
        req.get("antibot")
        or pw.get("antibot")
        or req_status in (403, 429)
        or pw_status in (403, 429)
    )

    if method in ("requests", "API diretta"):
        return "FACILE"
    if method == "Playwright":
        return "MEDIO"
    if blocked:
        if req_status in (403, 429) and pw_status in (403, 429, None):
            return "IMPOSSIBILE"
        return "DIFFICILE"
    return "DIFFICILE"


def format_api_line(
    req: dict[str, Any], pw: dict[str, Any], unieuro_api: dict[str, Any] | None, max_items: int = 4
) -> tuple[str, str]:
    hints: list[str] = []
    if unieuro_api and unieuro_api.get("ok"):
        hints.append(str(unieuro_api.get("endpoint")))
    hints.extend(req.get("api_hints") or [])
    hints.extend(pw.get("api_hints") or [])

    cleaned = []
    for h in hints:
        hh = str(h)
        if hh not in cleaned:
            cleaned.append(hh)

    if not cleaned:
        return ("no", "-")
    return ("si", "; ".join(cleaned[:max_items]))


def code_snippet(
    method: str, url: str, selectors: dict[str, Any] | None, api_endpoint: str | None = None
) -> str:
    if method == "API diretta":
        ep = api_endpoint or "https://example.algolia.net/1/indexes/*/queries"
        return "\n".join(
            [
                "import requests, json",
                f'url = "{ep}"',
                'payload = {"requests": [{"indexName": "sgmproducts_prod", "query": "notebook"}]}',
                "headers = {",
                '    "x-algolia-application-id": "MNBCENYFII",',
                '    "x-algolia-api-key": "977ed8d06b718d4929ca789c78c4107a",',
                '    "content-type": "application/x-www-form-urlencoded",',
                "}",
                "r = requests.post(url, data=json.dumps(payload), headers=headers, timeout=20)",
                'hits = r.json()["results"][0]["hits"]',
                "for h in hits[:3]:",
                '    print(h.get("name"), h.get("pFinale"), h.get("url"))',
            ]
        )

    if method == "Playwright":
        n = selectors["name"] if selectors else "h2, h3"
        p = selectors["price"] if selectors else ".price"
        l = selectors["link"] if selectors else "a[href]"
        return "\n".join(
            [
                "from playwright.sync_api import sync_playwright",
                "from bs4 import BeautifulSoup",
                f'url = "{url}"',
                "with sync_playwright() as pw:",
                '    browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])',
                '    page = browser.new_page(locale="it-IT")',
                '    page.goto(url, wait_until="domcontentloaded", timeout=20000)',
                "    page.wait_for_timeout(5000)",
                '    soup = BeautifulSoup(page.content(), "html.parser")',
                f'    print(len(soup.select("{n}")), len(soup.select("{p}")), len(soup.select("{l}")))',
                "    browser.close()",
            ]
        )

    n = selectors["name"] if selectors else "h2 a, h3 a"
    p = selectors["price"] if selectors else ".price"
    l = selectors["link"] if selectors else "a[href]"
    return "\n".join(
        [
            "import requests",
            "from bs4 import BeautifulSoup",
            f'url = "{url}"',
            'headers = {"User-Agent": "Mozilla/5.0"}',
            "r = requests.get(url, headers=headers, timeout=20)",
            'soup = BeautifulSoup(r.text, "html.parser")',
            f'names = soup.select("{n}")',
            f'prices = soup.select("{p}")',
            f'links = soup.select("{l}")',
            "for n, p, a in zip(names[:5], prices[:5], links[:5]):",
            "    print(n.get_text(strip=True), p.get_text(strip=True), a.get('href'))",
        ]
    )


def build_markdown(results: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# Report test scraping siti e-commerce")
    lines.append("")
    lines.append(f"Data test: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("")

    for row in results:
        lines.append(f"### {row['name']}")
        lines.append(f"- **URL testato:** {row['url']}")
        lines.append(f"- **Metodo che funziona:** {row['method']}")

        sel = row.get("selectors")
        if sel:
            lines.append(
                "- **Selettori trovati:** "
                f"nome=`{sel['name']}`, prezzo=`{sel['price']}`, link=`{sel['link']}`"
            )
        else:
            lines.append("- **Selettori trovati:** non affidabili / non stabili")

        lines.append(
            f"- **API interna trovata:** {row['api_yes_no']} - endpoint: {row['api_endpoint']}"
        )
        lines.append(f"- **Fattibilita:** {row['feasibility']}")
        lines.append(f"- **Note:** {row['notes']}")
        lines.append("- **Codice di esempio** (Python, max 20 righe):")
        lines.append("```python")
        lines.append(row["code"])
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    output_json = Path("tests/probe_siti_results.json")
    output_md = Path("report_siti.md")

    all_rows: list[dict[str, Any]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            locale="it-IT",
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": HEADERS["Accept-Language"]},
        )

        for site in SITES:
            print(f"[PROBE] {site.name} ...")
            req = probe_requests(site)
            pw_res = probe_playwright(site, context)
            unieuro_api = probe_unieuro_algolia() if site.name == "unieuro.it" else None
            tp_analysis = analyze_trovaprezzi(req) if site.name == "trovaprezzi.it" else None

            method = choose_method(site.name, req, pw_res, unieuro_api)
            feasibility = classify_feasibility(method, req, pw_res)

            selectors = None
            if method == "requests":
                selectors = req.get("selector_match")
            elif method == "Playwright":
                selectors = pw_res.get("selector_match")
            elif method == "API diretta":
                selectors = req.get("selector_match") or pw_res.get("selector_match")

            api_yes_no, api_endpoint = format_api_line(req, pw_res, unieuro_api)

            notes_parts = [
                f"requests status={req.get('status')} len={req.get('html_len')} antibot={req.get('antibot')}",
                f"playwright status={pw_res.get('status')} len={pw_res.get('html_len')} antibot={pw_res.get('antibot')}",
            ]
            if req.get("error"):
                notes_parts.append(f"requests error: {req.get('error')}")
            if pw_res.get("error"):
                notes_parts.append(f"playwright error: {pw_res.get('error')}")
            if site.name == "unieuro.it" and unieuro_api:
                notes_parts.append(
                    f"Algolia check: status={unieuro_api.get('status')} hits={unieuro_api.get('hits')}"
                )
            if site.name == "trovaprezzi.it" and tp_analysis:
                notes_parts.append(
                    "Trovaprezzi: "
                    f"filtro_prezzo_pattern={tp_analysis.get('price_filter_pattern')}, "
                    f"link_esterni={tp_analysis.get('redirect_links_to_shops')}, "
                    f"shop_keyword={','.join(tp_analysis.get('aggregated_shop_keywords') or []) or '-'}, "
                    f"shop_count_stimato={tp_analysis.get('estimated_shop_count')}"
                )

            snippet = code_snippet(
                method,
                site.url,
                selectors,
                (unieuro_api or {}).get("endpoint") if unieuro_api else None,
            )

            row = {
                "name": site.name,
                "url": site.url,
                "query": site.query,
                "requests": req,
                "playwright": pw_res,
                "unieuro_api": unieuro_api,
                "trovaprezzi_analysis": tp_analysis,
                "method": method,
                "selectors": selectors,
                "api_yes_no": api_yes_no,
                "api_endpoint": api_endpoint,
                "feasibility": feasibility,
                "notes": " | ".join(notes_parts),
                "code": snippet,
            }
            all_rows.append(row)

        browser.close()

    output_json.write_text(json.dumps(all_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    md = build_markdown(all_rows)
    output_md.write_text(md, encoding="utf-8")

    print("\n[OK] Salvati:")
    print(f" - {output_json}")
    print(f" - {output_md}")


if __name__ == "__main__":
    main()

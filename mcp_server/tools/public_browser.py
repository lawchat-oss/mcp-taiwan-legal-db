"""Bounded, fresh-session rendering of public pages protected by JavaScript checks.

Only explicitly opted-in source adapters call this helper. No stored browser profile,
login, background refresh, or pagination crawl is used.
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import asynccontextmanager

import httpx
from bs4 import BeautifulSoup

from mcp_server.tools.waf_bypass import _install_chromium, _is_missing_browser_error

TIMEOUT_MS = 30000
_lock = asyncio.Lock()
_last_finished = 0.0


def _ready(body: str, selector: str) -> bool:
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text() if soup.title else ""
    if re.search(r"Just a moment|Attention Required|請稍候|Access Denied|登入|Login", title, re.I):
        return False
    return soup.select_one("input[type=password]") is None and soup.select_one(selector) is not None


async def _launch(p, error_type):
    """Chromium 未安裝時（uvx 首次執行最常見）自動安裝一次再啟動，與司法院 WAF fallback 相同。"""
    try:
        return await p.chromium.launch(headless=True)
    except error_type as exc:
        if not _is_missing_browser_error(exc) or not await asyncio.to_thread(_install_chromium):
            raise
        return await p.chromium.launch(headless=True)


@asynccontextmanager
async def session(url: str, selector: str):
    """Fresh public browser page, serialized and closed even on timeout/cancellation."""
    from playwright.async_api import Error as BrowserError, TimeoutError as BrowserTimeout, async_playwright

    global _last_finished
    async with _lock:  # serialize new-source browser queries and leave a small gap
        await asyncio.sleep(max(0, 1 - (time.monotonic() - _last_finished)))
        try:
            async with async_playwright() as p:
                browser = await _launch(p, BrowserError)
                try:
                    ua = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          f"AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{browser.version} Safari/537.36")
                    context = await browser.new_context(user_agent=ua, locale="zh-TW")
                    page = await context.new_page()
                    page.set_default_timeout(TIMEOUT_MS)
                    await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
                    await page.locator(selector).first.wait_for(state="attached", timeout=TIMEOUT_MS)
                    body = await page.content()
                    if not _ready(body, selector):
                        raise RuntimeError(f"官網驗證尚未完成或頁面格式已變更：{url}")
                    yield page
                finally:
                    await browser.close()
        except BrowserTimeout as exc:
            raise RuntimeError(f"官網瀏覽器查詢逾時，可能仍停在驗證頁，不能判定查無資料：{url}") from exc
        except BrowserError as exc:
            raise RuntimeError(f"官網瀏覽器連線或啟動失敗：{exc}") from exc
        finally:
            _last_finished = time.monotonic()


async def render(url: str, selector: str) -> str:
    async with session(url, selector) as page:
        return await page.content()


async def get(http: httpx.AsyncClient, url: str, *, selector: str, params: dict | None = None) -> httpx.Response:
    """Prefer HTTP; render only when the response lacks the expected source markup."""
    response = await http.get(url, params=params)
    if response.is_success and _ready(response.text, selector):
        return response
    if response.status_code not in (200, 403, 429, 503):
        response.raise_for_status()
    if response.status_code == 429:
        response.raise_for_status()  # respect rate limits rather than opening another session
    body = await render(str(response.url), selector)
    return httpx.Response(200, text=body, request=httpx.Request("GET", response.url))


async def response_json(url: str, api_url: str) -> dict:
    """Capture one public SPA response, including tokens supplied by its own frontend."""
    async with session("about:blank", "body") as page:
        async with page.expect_response(lambda r: r.url == api_url or
                                        (api_url.endswith("?") and r.url.startswith(api_url)),
                                        timeout=TIMEOUT_MS) as pending:
            await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        response = await pending.value
        if response.status != 200:
            raise RuntimeError(f"官網公開 API 回傳 HTTP {response.status}：{url}")
        data = await response.json()
        if not isinstance(data, dict):
            raise RuntimeError(f"官網公開 API 格式不符：{url}")
        return data

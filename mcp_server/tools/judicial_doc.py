"""裁判書全文取得工具（HTTP data.aspx + Playwright fallback）"""

import logging
import time
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from mcp_server.config import (
    JUDICIAL_DATA_URL,
    PLAYWRIGHT_TIMEOUT,
    CACHE_JUDGMENT_TTL,
)
from mcp_server.cache.db import CacheDB
from mcp_server.parsers.judicial_parser import parse_judgment_page

logger = logging.getLogger(__name__)


class JudgmentDocClient:
    """裁判書全文取得：HTTP GET data.aspx → Playwright fallback"""

    def __init__(self, cache: CacheDB):
        self.cache = cache
        # SSL verification uses the OS-native trust store via truststore
        # (see mcp_server/config.py top-of-file injection). Full strict
        # verification is preserved on macOS / Windows / OpenSSL <3.6
        # Linux. On OpenSSL 3.6+ Linux the httpx path still fails and
        # falls back to Playwright below.
        self.http = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": "TaiwanLegalMCP/1.0"},
            follow_redirects=True,
        )
        self._playwright = None
        self._browser = None

    async def _ensure_browser(self):
        """首次呼叫時才啟動瀏覽器（persistent，生命週期跟隨 MCP server）"""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
            )
            logger.info("JudgmentDocClient Playwright 已啟動")

    async def close(self):
        await self.http.aclose()
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

    async def get_by_jid(self, jid: str) -> dict:
        """以 JID 取得裁判書全文

        JID 格式範例：TPSV,104,台上,472,20150326,1
        """
        # 快取查詢
        cached = await self.cache.get_judgment(jid)
        if cached:
            return {"success": True, "cached": True, **cached}

        # 方式 1：HTTP GET data.aspx（快速，無需瀏覽器）
        result = await self._fetch_via_http(jid)
        if result and result.get("success"):
            return result

        # 方式 2：Playwright fallback（需要瀏覽器）
        result = await self._fetch_via_playwright(jid)
        if result and result.get("success"):
            return result

        return {
            "success": False,
            "error": f"無法取得裁判書全文（JID: {jid}）",
            "jid": jid,
            "timestamp": datetime.now().isoformat(),
        }

    async def get_by_url(self, url: str) -> dict:
        """以 URL 取得裁判書全文"""
        from mcp_server.config import validate_url_domain
        if not validate_url_domain(url):
            return {"success": False, "error": "URL 域名不在白名單中"}

        # 嘗試從 URL 擷取 JID 作為快取 key
        import re
        jid_match = re.search(r"id=([^&]+)", url)
        cache_key = jid_match.group(1) if jid_match else url

        cached = await self.cache.get_judgment(cache_key)
        if cached:
            return {"success": True, "cached": True, **cached}

        # 用 HTTP 直接取得
        try:
            resp = await self.http.get(url)
            resp.raise_for_status()

            # 嘗試用 #jud selector 解析
            soup = BeautifulSoup(resp.text, "lxml")
            jud_el = soup.select_one("#jud")
            if jud_el:
                jud_html = str(jud_el)
                parsed = parse_judgment_page(f"<html><body>{jud_html}</body></html>")
            else:
                parsed = parse_judgment_page(resp.text)

            if parsed.get("full_text"):
                data = {
                    "source": "http",
                    "source_url": url,
                    "timestamp": datetime.now().isoformat(),
                    **parsed,
                }
                await self.cache.set_judgment(cache_key, data, source="http")
                return {"success": True, "cached": False, **data}
        except httpx.HTTPError as e:
            logger.warning("HTTP 取得裁判書失敗: %s", e)

        # HTTP 失敗，嘗試 Playwright
        return await self._fetch_via_playwright_url(url, cache_key)

    async def _fetch_via_http(self, jid: str) -> dict | None:
        """透過 HTTP GET data.aspx 取得裁判書（與語意搜尋爬蟲同樣方式）"""
        url = f"{JUDICIAL_DATA_URL}?ty=JD&id={jid}"

        try:
            start = time.monotonic()
            resp = await self.http.get(url)
            elapsed = time.monotonic() - start
            logger.info("HTTP data.aspx 回應: status=%d, elapsed=%.2fs, jid=%s",
                        resp.status_code, elapsed, jid)

            if resp.status_code != 200:
                return None

            # 用 BeautifulSoup 解析 #jud 元素
            soup = BeautifulSoup(resp.text, "lxml")
            jud_el = soup.select_one("#jud")

            if not jud_el:
                logger.info("data.aspx 無 #jud 元素 (JID: %s)", jid)
                return None

            full_text = jud_el.get_text(strip=False)
            if len(full_text) < 100:
                logger.info("data.aspx #jud 文字太短 (%d chars, JID: %s)", len(full_text), jid)
                return None

            # 用 parse_judgment_page 解析結構化欄位
            jud_html = str(jud_el)
            parsed = parse_judgment_page(f"<html><body>{jud_html}</body></html>")

            # 確保 full_text 使用原始的完整文字
            if not parsed.get("full_text") or len(parsed["full_text"]) < len(full_text.strip()):
                parsed["full_text"] = full_text.strip()

            data = {
                "source": "http_data_aspx",
                "source_url": url,
                "timestamp": datetime.now().isoformat(),
                **parsed,
            }

            await self.cache.set_judgment(jid, data, source="http_data_aspx")
            return {"success": True, "cached": False, **data}

        except (httpx.HTTPError, ValueError) as e:
            logger.warning("HTTP data.aspx 呼叫失敗: %s", e)
            return None

    async def _fetch_via_playwright(self, jid: str) -> dict | None:
        """透過 Playwright 載入 data.aspx 頁面"""
        url = f"{JUDICIAL_DATA_URL}?ty=JD&id={jid}"
        return await self._fetch_via_playwright_url(url, jid)

    async def _fetch_via_playwright_url(self, url: str, cache_key: str) -> dict | None:
        """Playwright 載入裁判書頁面（使用持久化瀏覽器）"""
        try:
            await self._ensure_browser()
            context = await self._browser.new_context(
                locale="zh-TW",
                timezone_id="Asia/Taipei",
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="networkidle", timeout=PLAYWRIGHT_TIMEOUT)
                html = await page.content()
                parsed = parse_judgment_page(html)

                if parsed.get("full_text"):
                    data = {
                        "source": "playwright",
                        "source_url": url,
                        "timestamp": datetime.now().isoformat(),
                        **parsed,
                    }
                    await self.cache.set_judgment(cache_key, data, source="playwright")
                    return {"success": True, "cached": False, **data}

                return None
            finally:
                await page.close()
                await context.close()

        except Exception as e:
            logger.error("Playwright 取得裁判書失敗: %s", e)
            return None

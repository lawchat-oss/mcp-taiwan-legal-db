"""裁判書全文取得工具（純 HTTP，不需要 Playwright）"""

import logging
import time
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from mcp_server.config import (
    JUDICIAL_DATA_URL,
    CACHE_JUDGMENT_TTL,
)
from mcp_server.cache.db import CacheDB
from mcp_server.parsers.judicial_parser import parse_judgment_page

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class JudgmentDocClient:
    """裁判書全文取得：HTTP GET data.aspx"""

    def __init__(self, cache: CacheDB):
        self.cache = cache
        self.http = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
        )

    async def close(self):
        await self.http.aclose()

    async def get_by_jid(self, jid: str) -> dict:
        """以 JID 取得裁判書全文

        JID 格式範例：TPSV,104,台上,472,20150326,1
        """
        # 快取查詢
        cached = await self.cache.get_judgment(jid)
        if cached:
            return {"success": True, "cached": True, **cached}

        # HTTP GET data.aspx
        result = await self._fetch_via_http(jid)
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

        try:
            resp = await self.http.get(url)
            resp.raise_for_status()

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

        return {
            "success": False,
            "error": f"無法取得裁判書全文（URL: {url}）",
            "timestamp": datetime.now().isoformat(),
        }

    async def _fetch_via_http(self, jid: str) -> dict | None:
        """透過 HTTP GET data.aspx 取得裁判書"""
        url = f"{JUDICIAL_DATA_URL}?ty=JD&id={jid}"

        try:
            start = time.monotonic()
            resp = await self.http.get(url)
            elapsed = time.monotonic() - start
            logger.info("HTTP data.aspx 回應: status=%d, elapsed=%.2fs, jid=%s",
                        resp.status_code, elapsed, jid)

            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "lxml")
            jud_el = soup.select_one("#jud")

            if not jud_el:
                logger.info("data.aspx 無 #jud 元素 (JID: %s)", jid)
                return None

            full_text = jud_el.get_text(strip=False)
            if len(full_text) < 100:
                logger.info("data.aspx #jud 文字太短 (%d chars, JID: %s)", len(full_text), jid)
                return None

            jud_html = str(jud_el)
            parsed = parse_judgment_page(f"<html><body>{jud_html}</body></html>")

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

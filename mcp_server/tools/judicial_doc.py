"""裁判書全文取得工具（httpx + F5 WAF cookie bypass）"""

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
from mcp_server.tools._errors import error_response
from mcp_server.tools.waf_bypass import (
    JudicialWAFBypass,
    WAFPermanentBlockError,
    get_with_waf_retry,
)

logger = logging.getLogger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class JudgmentDocClient:
    """裁判書全文取得：HTTP GET data.aspx + F5 WAF cookie bypass"""

    def __init__(self, cache: CacheDB, waf: JudicialWAFBypass):
        self.cache = cache
        self.waf = waf
        self.http = httpx.AsyncClient(
            timeout=30.0,
            headers={"User-Agent": _USER_AGENT},
            follow_redirects=True,
            cookies=waf.get_cookies(),
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
        try:
            result = await self._fetch_via_http(jid)
        except WAFPermanentBlockError:
            logger.warning("取裁判書遭司法院 WAF 硬擋 (JID: %s)", jid)
            return error_response(
                "司法院網站暫時無法通過 WAF 防護，請稍後重試", jid=jid,
            )
        if result and result.get("success"):
            return result

        return error_response(f"無法取得裁判書全文（JID: {jid}）", jid=jid)

    async def get_by_url(self, url: str) -> dict:
        """以 URL 取得裁判書全文"""
        from mcp_server.config import validate_url_domain
        if not validate_url_domain(url):
            return error_response("URL 域名不在白名單中", url=url)

        # 嘗試從 URL 擷取 JID 作為快取 key
        import re
        jid_match = re.search(r"id=([^&]+)", url)
        cache_key = jid_match.group(1) if jid_match else url

        cached = await self.cache.get_judgment(cache_key)
        if cached:
            return {"success": True, "cached": True, **cached}

        try:
            resp = await get_with_waf_retry(self.http, url, self.waf)
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
        except WAFPermanentBlockError:
            logger.warning("取裁判書遭司法院 WAF 硬擋 (URL: %s)", url)
            return error_response(
                "司法院網站暫時無法通過 WAF 防護，請稍後重試", url=url,
            )
        except httpx.HTTPError as e:
            logger.warning("HTTP 取得裁判書失敗: %s", e)

        return error_response(f"無法取得裁判書全文（URL: {url}）", url=url)

    async def _fetch_via_http(self, jid: str) -> dict | None:
        """透過 HTTP GET data.aspx 取得裁判書（遇 WAF 自動刷 cookie 重試）"""
        url = f"{JUDICIAL_DATA_URL}?ty=JD&id={jid}"

        try:
            start = time.monotonic()
            resp = await get_with_waf_retry(self.http, url, self.waf)
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

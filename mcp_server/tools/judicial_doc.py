"""裁判書全文取得工具（httpx + F5 WAF cookie bypass）"""

import json
import logging
import re
import time
from datetime import datetime
from urllib.parse import parse_qs, urlparse

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


_HISTORY_KEY = re.compile(r"GetJudHistory\.ashx\?jid=([0-9A-Za-z%.,`_-]+)")
_HISTORY_URL = "https://judgment.judicial.gov.tw/controls/GetJudHistory.ashx?jid="
_HISTORY_NOTE = (
    "司法院依案號串起的歷審清單。pending_supreme_court=true 表示案件目前上訴到最高法院／最高行政法院審理中；"
    "url 為空表示案件目前繫屬法院或該案號沒有裁判書。清單最後一筆之後沒有更高審級，不等於已經確定"
    "（可能仍在上訴期間內，或上級審裁判尚未上網）。"
)


def _history_item(c: dict) -> dict:
    href = c.get("href") or ""
    jid = parse_qs(urlparse(href).query).get("id", [""])[0]
    return {
        "desc": c.get("desc", ""),
        "jid": jid,
        "url": f"https://judgment.judicial.gov.tw/FJUD/{href}" if href else "",
        "pending_supreme_court": c.get("red") == 1,
    }


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
        if cached and "history_key" in cached:  # 舊版快取沒有歷審索引，重抓一次
            return await self._with_history({"success": True, "cached": True, **cached})

        # HTTP GET data.aspx
        try:
            result = await self._fetch_via_http(jid)
        except WAFPermanentBlockError:
            logger.warning("取裁判書遭司法院 WAF 硬擋 (JID: %s)", jid)
            return error_response(
                "司法院網站暫時無法通過 WAF 防護，請稍後重試", jid=jid,
            )
        if result and result.get("success"):
            return await self._with_history(result)

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
        if cached and "history_key" in cached:
            return await self._with_history({"success": True, "cached": True, **cached})

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
                    "history_key": _history_key(resp.text),
                    "timestamp": datetime.now().isoformat(),
                    **parsed,
                }
                await self.cache.set_judgment(cache_key, data, source="http")
                return await self._with_history({"success": True, "cached": False, **data})
        except WAFPermanentBlockError:
            logger.warning("取裁判書遭司法院 WAF 硬擋 (URL: %s)", url)
            return error_response(
                "司法院網站暫時無法通過 WAF 防護，請稍後重試", url=url,
            )
        except httpx.HTTPError as e:
            logger.warning("HTTP 取得裁判書失敗: %s", e)

        return error_response(f"無法取得裁判書全文（URL: {url}）", url=url)

    async def _with_history(self, result: dict) -> dict:
        """附上歷審清單。歷審會隨上訴變動，不跟全文一起快取 30 天，另以 24 小時快取。"""
        key = result.pop("history_key", "")
        if not key:
            return result
        cache_params = {"judgment_history": key}
        cached = await self.cache.get_search(cache_params)
        if cached is None:
            try:
                resp = await get_with_waf_retry(self.http, _HISTORY_URL + key, self.waf)
                resp.raise_for_status()
                cached = {"items": [_history_item(c) for c in json.loads(resp.text).get("list", [])]}
            except (httpx.HTTPError, ValueError, WAFPermanentBlockError, AttributeError) as e:
                logger.warning("取歷審清單失敗: %s", e)
                result["history_error"] = "歷審清單暫時取不到"
                return result
            await self.cache.set_search(cache_params, cached)
        result["history"] = cached["items"]
        result["history_note"] = _HISTORY_NOTE
        return result

    async def _fetch_via_http(self, jid: str) -> dict | None:
        """透過 HTTP GET data.aspx 取得裁判書（遇 WAF 自動刷 cookie 重試）"""
        # 用 httpx params 參數化：防 jid="x&ty=evil" 等注入綁 query string。
        params = {"ty": "JD", "id": jid}
        url = JUDICIAL_DATA_URL

        try:
            start = time.monotonic()
            resp = await get_with_waf_retry(self.http, url, self.waf, params=params)
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
                "source_url": str(resp.url),
                "history_key": _history_key(resp.text),
                "timestamp": datetime.now().isoformat(),
                **parsed,
            }

            await self.cache.set_judgment(jid, data, source="http_data_aspx")
            return {"success": True, "cached": False, **data}

        except (httpx.HTTPError, ValueError) as e:
            logger.warning("HTTP data.aspx 呼叫失敗: %s", e)
            return None


def _history_key(page_html: str) -> str:
    m = _HISTORY_KEY.search(page_html)
    return m.group(1) if m else ""

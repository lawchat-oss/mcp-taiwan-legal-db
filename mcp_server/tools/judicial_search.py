"""司法院裁判書搜尋工具（httpx + F5 WAF cookie bypass）"""

import asyncio
import random
import re
import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from mcp_server.config import (
    JUDICIAL_SEARCH_URL,
    SEARCH_DELAY_MIN,
    SEARCH_DELAY_MAX,
    COURT_CODES,
    CASE_TYPE_CODES,
)
from mcp_server.cache.db import CacheDB
from mcp_server.parsers.judicial_parser import parse_search_results
from mcp_server.tools.waf_bypass import JudicialWAFBypass, get_with_waf_retry

logger = logging.getLogger(__name__)

# 精確案號搜尋用 HTTP GET
_QRYRESULT_URL = "https://judgment.judicial.gov.tw/FJUD/qryresult.aspx"
_QRYRESULT_BASE = "https://judgment.judicial.gov.tw/FJUD/"
_IFRAME_SRC_RE = re.compile(r'<iframe[^>]*src=["\']([^"\']+)', re.IGNORECASE)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class JudicialSearchClient:
    """裁判書搜尋 — httpx + F5 WAF cookie bypass (Playwright 僅用於刷 cookie)"""

    def __init__(self, cache: CacheDB, waf: JudicialWAFBypass):
        self.cache = cache
        self.waf = waf
        self._last_search_time: float = 0

    async def close(self):
        pass

    async def _rate_limit(self):
        """限速：每次搜尋間至少間隔 SEARCH_DELAY"""
        now = asyncio.get_running_loop().time()
        elapsed = now - self._last_search_time
        min_delay = random.uniform(SEARCH_DELAY_MIN, SEARCH_DELAY_MAX)
        if elapsed < min_delay:
            await asyncio.sleep(min_delay - elapsed)
        self._last_search_time = asyncio.get_running_loop().time()

    async def search(
        self,
        keyword: str = "",
        court: str = "",
        case_type: str = "",
        year_from: int = 0,
        year_to: int = 0,
        case_word: str = "",
        case_number: str = "",
        main_text: str = "",
        max_results: int = 10,
    ) -> dict:
        """執行裁判書搜尋

        main_text maps to the 進階搜尋 form field jud_jmain (裁判主文) and
        filters by who won/lost via the operative part of the judgment,
        which is highly canonical (driven by civil/criminal procedure code
        templates) and therefore high-precision:
            main_text="被告應將 移轉" + keyword="借名登記"
            → cases where the defendant was ordered to transfer title,
              i.e. the borrowed-registration claim succeeded.
        """
        params = {
            "keyword": keyword,
            "court": court,
            "case_type": case_type,
            "year_from": year_from,
            "year_to": year_to,
            "case_word": case_word,
            "case_number": case_number,
            "main_text": main_text,
        }

        # 快取查詢
        cached = await self.cache.get_search(params)
        if cached:
            cached["cached"] = True
            return cached

        if not keyword and not case_number and not main_text:
            return {
                "success": False,
                "error": "至少需要提供 keyword / case_number / main_text 其一",
                "query": params,
            }

        # ── 精確案號搜尋：case_word + case_number → HTTP GET（快、準） ──
        # 只有在沒有全文/主文過濾條件時才可安全走 fast path；
        # 否則會忽略 keyword / main_text 並把錯結果寫進快取。
        if (
            params.get("case_word")
            and params.get("case_number")
            and not params.get("keyword")
            and not params.get("main_text")
        ):
            http_results = await self._precise_search_http(params, max_results)
            if http_results is not None:
                data = {
                    "success": True,
                    "query": params,
                    "total_count": len(http_results),
                    "results": http_results,
                    "cached": False,
                    "timestamp": datetime.now().isoformat(),
                }
                if http_results:
                    await self.cache.set_search(params, data)
                return data

        # ── 關鍵字搜尋 → HTTP POST（ASP.NET 表單） ──
        await self._rate_limit()

        try:
            results = await self._keyword_search_http(params, max_results)
            data = {
                "success": True,
                "query": params,
                "total_count": len(results),
                "results": results,
                "cached": False,
                "timestamp": datetime.now().isoformat(),
            }

            if results:
                await self.cache.set_search(params, data)

            return data

        except (asyncio.TimeoutError, httpx.TimeoutException):
            # httpx 的 timeout 實際型別是 httpx.TimeoutException（HTTPError 子類），
            # 必須在 httpx.HTTPError arm 之前攔截。asyncio.TimeoutError 涵蓋
            # waf_bypass 收斂上來的 Playwright warmup 逾時。
            logger.exception("搜尋逾時")
            return {
                "success": False,
                "error": "搜尋逾時，請稍後重試或縮小查詢範圍",
                "query": params,
                "timestamp": datetime.now().isoformat(),
            }
        except httpx.HTTPError:
            logger.exception("搜尋連線失敗")
            return {
                "success": False,
                "error": "連線司法院網站失敗，請稍後重試",
                "query": params,
                "timestamp": datetime.now().isoformat(),
            }
        except Exception:
            logger.exception("搜尋發生未預期錯誤")
            return {
                "success": False,
                "error": "搜尋發生未預期錯誤，請查看 server log 取得詳細資訊",
                "query": params,
                "timestamp": datetime.now().isoformat(),
            }

    async def _precise_search_http(
        self, params: dict, max_results: int,
    ) -> list[dict] | None:
        """精確案號搜尋 — HTTP GET

        case_word + case_number 都有值時使用。
        兩步驟：qryresult.aspx 取 iframe src → qryresultlst.aspx 取結果。
        同時查 V(民事)/M(刑事)/A(行政)，合併去重。
        回傳 None 表示查詢失敗。
        """
        case_word = params["case_word"].replace("臺", "台")
        case_number = str(params["case_number"])

        base_params: dict[str, str] = {
            "jud_case": case_word,
            "jud_no": case_number,
            "judtype": "JUDBOOK",
        }

        year = params.get("year_from") or params.get("year_to")
        if year:
            base_params["jud_year"] = str(year)

        if params.get("court"):
            court_code = COURT_CODES.get(params["court"], params["court"])
            base_params["jud_court"] = court_code

        if params.get("case_type"):
            sys_codes = [CASE_TYPE_CODES.get(params["case_type"], "V")]
        else:
            sys_codes = ["V", "M", "A"]

        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
                cookies=self.waf.get_cookies(),
            ) as client:
                # 先用一個 request 偵測是否被擋
                probe_params = {**base_params, "sys": sys_codes[0]}
                probe = await get_with_waf_retry(
                    client, _QRYRESULT_URL, self.waf, params=probe_params
                )
                # 其餘的 sys_code 並行（cookie 已新鮮）
                outer_responses = [probe]
                if len(sys_codes) > 1:
                    outer_tasks = [
                        client.get(_QRYRESULT_URL, params={**base_params, "sys": sc})
                        for sc in sys_codes[1:]
                    ]
                    outer_responses += list(
                        await asyncio.gather(*outer_tasks, return_exceptions=True)
                    )

                iframe_tasks = []
                for resp in outer_responses:
                    if isinstance(resp, Exception) or resp.status_code != 200:
                        continue
                    m = _IFRAME_SRC_RE.search(resp.text)
                    if m:
                        src = m.group(1).replace("&amp;", "&")
                        if not src.startswith("http"):
                            src = _QRYRESULT_BASE + src
                        iframe_tasks.append(client.get(src))

                if not iframe_tasks:
                    logger.info("精確搜尋: 無 iframe src，可能無結果")
                    return None

                iframe_responses = await asyncio.gather(
                    *iframe_tasks, return_exceptions=True,
                )

                all_results: list[dict] = []
                seen_jids: set[str] = set()

                for resp in iframe_responses:
                    if isinstance(resp, Exception) or resp.status_code != 200:
                        continue
                    page_results = parse_search_results(resp.text)
                    for r in page_results:
                        jid = r.get("jid", "")
                        if jid and jid not in seen_jids:
                            seen_jids.add(jid)
                            all_results.append(r)

                if not params.get("court"):
                    all_results.sort(key=lambda r: r.get("court_level", 99))

                logger.info(
                    "精確搜尋 HTTP GET: %s %s → %d 筆",
                    params["case_word"], case_number, len(all_results),
                )
                return all_results[:max_results]

        except Exception as e:
            logger.warning("精確搜尋 HTTP GET 失敗: %s", e)
            return None

    async def _keyword_search_http(
        self, params: dict, max_results: int,
    ) -> list[dict]:
        """關鍵字搜尋 — HTTP POST ASP.NET 表單

        流程：GET 表單頁取 __VIEWSTATE → POST 表單 → 從回應取 iframe src
        → GET 結果頁 → 分頁收集。
        """
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            cookies=self.waf.get_cookies(),
        ) as client:
            # Step 1: GET 表單頁，取 ASP.NET 狀態 token（遇 WAF 自動重試）
            r = await get_with_waf_retry(client, JUDICIAL_SEARCH_URL, self.waf)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            viewstate = soup.find("input", {"name": "__VIEWSTATE"})
            event_val = soup.find("input", {"name": "__EVENTVALIDATION"})
            viewgen = soup.find("input", {"name": "__VIEWSTATEGENERATOR"})

            if not viewstate or not event_val:
                raise RuntimeError(
                    "無法取得 ASP.NET 表單 token（__VIEWSTATE / __EVENTVALIDATION）。"
                    "可能 F5 WAF cookie warmup 失敗，請檢查 Playwright 是否已安裝。"
                )

            # Step 2: 建構 POST 表單資料
            form_data: dict[str, str] = {
                "__VIEWSTATE": viewstate["value"],
                "__EVENTVALIDATION": event_val["value"],
                "__VIEWSTATEGENERATOR": viewgen["value"] if viewgen else "",
                "__VIEWSTATEENCRYPTED": "",
                "judtype": "JUDBOOK",
                "whosub": "0",
                "ctl00$cp_content$btnQry": "送出查詢",
            }

            if params.get("keyword"):
                form_data["jud_kw"] = params["keyword"]
            if params.get("main_text"):
                form_data["jud_jmain"] = params["main_text"]
            if params.get("court"):
                court_code = COURT_CODES.get(params["court"], params["court"])
                form_data["jud_court"] = court_code
            if params.get("case_type"):
                type_code = CASE_TYPE_CODES.get(params["case_type"], params["case_type"])
                form_data["jud_sys"] = type_code
            else:
                # 預設搜所有案件類型（不勾 = 全搜）
                pass
            if params.get("year_from"):
                form_data["dy1"] = str(params["year_from"])
            if params.get("year_to"):
                form_data["dy2"] = str(params["year_to"])
            if params.get("case_word"):
                form_data["jud_case"] = params["case_word"]
            if params.get("case_number"):
                form_data["jud_no"] = str(params["case_number"])

            # Step 3: POST 表單（遇 WAF 自動重試）
            r2 = await get_with_waf_retry(
                client, JUDICIAL_SEARCH_URL, self.waf, method="POST", data=form_data
            )
            r2.raise_for_status()
            soup2 = BeautifulSoup(r2.text, "html.parser")

            iframe = soup2.find("iframe")
            if not iframe or not iframe.get("src"):
                logger.info("關鍵字搜尋: POST 後無 iframe，可能無結果")
                return []

            iframe_url = iframe["src"]
            if not iframe_url.startswith("http"):
                iframe_url = _QRYRESULT_BASE + iframe_url

            # Step 4: 分頁收集
            all_results: list[dict] = []
            seen_jids: set[str] = set()
            page_num = 1
            MAX_PAGES = 100

            while len(all_results) < max_results and page_num <= MAX_PAGES:
                r3 = await get_with_waf_retry(client, iframe_url, self.waf)
                if r3.status_code != 200:
                    logger.warning("第 %d 頁 HTTP 失敗: %d", page_num, r3.status_code)
                    break

                page_results = parse_search_results(r3.text)
                logger.info("第 %d 頁: 解析 %d 筆", page_num, len(page_results))

                if not page_results:
                    break

                new_count = 0
                for r_item in page_results:
                    jid = r_item.get("jid", "")
                    if jid and jid not in seen_jids:
                        seen_jids.add(jid)
                        all_results.append(r_item)
                        new_count += 1

                if new_count == 0 and page_num > 1:
                    logger.debug("第 %d 頁無新 JID，停止分頁", page_num)
                    break

                if len(all_results) >= max_results:
                    break

                # 下一頁
                next_url = self._extract_next_page_url(r3.text)
                if not next_url:
                    logger.info("無下一頁連結，結束分頁（共 %d 頁）", page_num)
                    break

                iframe_url = next_url
                page_num += 1

            if not params.get("court"):
                all_results.sort(key=lambda r: r.get("court_level", 99))

            logger.info("分頁收集完成: 總計 %d 筆 (max_results=%d)", len(all_results), max_results)
            return all_results[:max_results]

    @staticmethod
    def _extract_next_page_url(html: str) -> str | None:
        """從結果頁 HTML 中擷取下一頁 URL"""
        soup = BeautifulSoup(html, "html.parser")
        next_link = soup.find("a", id="hlNext")
        if not next_link:
            return None
        href = next_link.get("href")
        if not href:
            return None
        if href.startswith("/"):
            href = f"https://judgment.judicial.gov.tw{href}"
        elif not href.startswith("http"):
            href = _QRYRESULT_BASE + href
        return href

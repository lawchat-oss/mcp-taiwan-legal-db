"""司法院裁判書搜尋工具（精確案號 HTTP GET + 關鍵字 Playwright）"""

import asyncio
import random
import re
import logging
from datetime import datetime

import httpx

from mcp_server.config import (
    JUDICIAL_SEARCH_URL,
    PLAYWRIGHT_HEADLESS,
    PLAYWRIGHT_TIMEOUT,
    SEARCH_DELAY_MIN,
    SEARCH_DELAY_MAX,
    COURT_CODES,
    CASE_TYPE_CODES,
    validate_url_domain,
)
from mcp_server.cache.db import CacheDB
from mcp_server.parsers.judicial_parser import parse_search_results

logger = logging.getLogger(__name__)

# 精確案號搜尋用 HTTP GET（不需 Playwright）
_QRYRESULT_URL = "https://judgment.judicial.gov.tw/FJUD/qryresult.aspx"
_QRYRESULT_BASE = "https://judgment.judicial.gov.tw/FJUD/"
_IFRAME_SRC_RE = re.compile(r'<iframe[^>]*src=["\']([^"\']+)', re.IGNORECASE)


class JudicialSearchBrowser:
    """Playwright 瀏覽器管理 — 懶載入"""

    def __init__(self, cache: CacheDB):
        self.cache = cache
        self._playwright = None
        self._browser = None
        self._last_search_time: float = 0

    async def _ensure_browser(self):
        """首次呼叫時才啟動瀏覽器"""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=PLAYWRIGHT_HEADLESS,
                args=[
                    "--no-sandbox",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                    "--disable-extensions",
                ],
            )
            logger.info("Playwright Chromium 已啟動")

    async def close(self):
        """關閉瀏覽器"""
        if self._browser:
            await self._browser.close()
            self._browser = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None

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
        # 建構查詢參數（供快取 key 用）
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
        if params.get("case_word") and params.get("case_number"):
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
            # HTTP GET 失敗 → fallback 到 Playwright
            logger.info("精確搜尋 HTTP GET 失敗，fallback 到 Playwright")

        # ── 關鍵字搜尋或 fallback → Playwright ──
        await self._rate_limit()

        context = None
        page = None
        try:
            await self._ensure_browser()
            context = await self._browser.new_context(
                locale="zh-TW",
                timezone_id="Asia/Taipei",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            page = await context.new_page()

            results = await self._perform_search(page, params, max_results)
            data = {
                "success": True,
                "query": params,
                "total_count": len(results),
                "results": results,
                "cached": False,
                "timestamp": datetime.now().isoformat(),
            }

            # 寫入快取
            if results:
                await self.cache.set_search(params, data)

            return data

        except Exception as e:
            logger.error("搜尋失敗: %s", e)
            return {
                "success": False,
                "error": str(e),
                "query": params,
                "timestamp": datetime.now().isoformat(),
            }
        finally:
            if page:
                await page.close()
            if context:
                await context.close()

    async def _precise_search_http(
        self, params: dict, max_results: int,
    ) -> list[dict] | None:
        """精確案號搜尋 — HTTP GET（不需 Playwright）

        case_word + case_number 都有值時使用。
        兩步驟：qryresult.aspx 取 iframe src → qryresultlst.aspx 取結果。
        同時查 V(民事)/M(刑事)/A(行政)，合併去重。
        回傳 None 表示查詢失敗，應 fallback 到 Playwright。
        """
        # FJUD URL 使用「台」（非「臺」），與 citation_verifier 一致
        case_word = params["case_word"].replace("臺", "台")
        case_number = str(params["case_number"])

        base_params: dict[str, str] = {
            "jud_case": case_word,
            "jud_no": case_number,
            "judtype": "JUDBOOK",
        }

        # 可選：年度
        year = params.get("year_from") or params.get("year_to")
        if year:
            base_params["jud_year"] = str(year)

        # 可選：法院
        if params.get("court"):
            court_code = COURT_CODES.get(params["court"], params["court"])
            base_params["jud_court"] = court_code

        # 案件類型：指定則只查一種，否則 V/M/A 三路並查
        if params.get("case_type"):
            sys_codes = [CASE_TYPE_CODES.get(params["case_type"], "V")]
        else:
            sys_codes = ["V", "M", "A"]

        try:
            # SSL verification via OS-native trust store (truststore
            # injected at config.py import time).
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(15.0),
                follow_redirects=True,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36",
                },
            ) as client:
                # Step 1: 同時查各案件類型，取得 iframe src
                outer_tasks = []
                for sys_code in sys_codes:
                    p = {**base_params, "sys": sys_code}
                    outer_tasks.append(client.get(_QRYRESULT_URL, params=p))

                outer_responses = await asyncio.gather(
                    *outer_tasks, return_exceptions=True,
                )

                # Step 2: 從每個回應中提取 iframe src，取得實際結果
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

                # Step 3: 解析結果
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

                # 按法院層級排序
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

    async def _perform_search(self, page, params: dict, max_results: int) -> list[dict]:
        """操作搜尋表單 + 分頁收集"""
        # 導航到進階搜尋頁面（domcontentloaded 比 networkidle 快 5-10 秒）
        await page.goto(JUDICIAL_SEARCH_URL, wait_until="domcontentloaded", timeout=PLAYWRIGHT_TIMEOUT)
        # 等表單實際可用
        await page.wait_for_selector("#btnQry", state="visible", timeout=15000)

        # 填入關鍵字（全文內容 jud_kw）
        if params.get("keyword"):
            kw_input = page.locator("#jud_kw")
            if await kw_input.count() > 0:
                await kw_input.fill(params["keyword"])
            else:
                # 備用：嘗試其他可能的輸入欄位
                kw_input = page.locator("input[name='jud_kw']") or page.locator("input[type='text']").first
                if await kw_input.count() > 0:
                    await kw_input.fill(params["keyword"])

        # 填入裁判主文（jud_jmain）— filters by who won/lost
        # e.g. "被告應將" (defendant ordered to transfer), "原告之訴駁回" (plaintiff lost)
        if params.get("main_text"):
            main_input = page.locator("#jud_jmain")
            if await main_input.count() == 0:
                main_input = page.locator("input[name='jud_jmain']")
            if await main_input.count() > 0:
                await main_input.fill(params["main_text"])
            else:
                logger.warning("main_text field (#jud_jmain) not found, skipping")

        # 填入法院
        if params.get("court"):
            court_code = COURT_CODES.get(params["court"], params["court"])
            court_select = page.locator("#jud_court")
            if await court_select.count() > 0:
                try:
                    await court_select.select_option(value=court_code)
                except Exception:
                    pass  # 法院代碼可能不完全對應

        # 填入案件類型
        if params.get("case_type"):
            type_code = CASE_TYPE_CODES.get(params["case_type"], params["case_type"])
            # 案件類型可能是 checkbox
            type_cb = page.locator(f"input[value='{type_code}']")
            if await type_cb.count() > 0:
                await type_cb.check()

        # 填入年度
        if params.get("year_from"):
            year_input = page.locator("#dy1")
            if await year_input.count() > 0:
                await year_input.fill(str(params["year_from"]))

        if params.get("year_to"):
            year_input = page.locator("#dy2")
            if await year_input.count() > 0:
                await year_input.fill(str(params["year_to"]))

        # 填入字別和案號
        if params.get("case_word"):
            word_input = page.locator("#jud_case")
            if await word_input.count() > 0:
                await word_input.fill(params["case_word"])

        if params.get("case_number"):
            no_input = page.locator("#jud_no")
            if await no_input.count() > 0:
                await no_input.fill(params["case_number"])

        # 提交搜尋 — 點擊送出按鈕觸發 ASP.NET 表單 POST
        submitted = False

        btn = page.locator("#btnQry")
        if await btn.count() > 0:
            try:
                # 點擊按鈕並等待頁面導航（ASP.NET postback）
                # 用 load 而非 networkidle — 等 iframe 載入但不等所有 analytics
                async with page.expect_navigation(
                    wait_until="load", timeout=PLAYWRIGHT_TIMEOUT
                ):
                    await btn.click()
                submitted = True
            except Exception as e:
                logger.warning("按鈕點擊/導航失敗: %s", e)

        if not submitted:
            # 備用：直接提交表單
            try:
                async with page.expect_navigation(
                    wait_until="load", timeout=PLAYWRIGHT_TIMEOUT
                ):
                    await page.evaluate('document.getElementById("form1").submit()')
                submitted = True
            except Exception as e:
                logger.warning("表單直接提交失敗: %s", e)

        if not submitted:
            return []

        # 等結果 iframe 出現（取代固定 sleep(2)）
        try:
            await page.wait_for_selector(
                "iframe[name='iframe-data']", state="attached", timeout=15000,
            )
        except Exception:
            logger.info("iframe-data 未在 15s 內出現，嘗試直接解析主頁")

        # ═══ 分頁收集邏輯 ═══
        all_results = []
        seen_jids: set[str] = set()
        page_num = 1
        MAX_PAGES = 100  # 每頁 20 筆 × 100 頁 = 2000 筆上限

        # 先找 iframe（搜尋結果在 iframe-data 中）
        iframe = page.frame(name="iframe-data")
        if iframe is not None:
            logger.info("找到 iframe-data，從 iframe 讀取搜尋結果")
        else:
            logger.warning("iframe-data 不存在，降級到主頁解析（分頁可能不可用）")

        while len(all_results) < max_results and page_num <= MAX_PAGES:
            # 取得當前頁面內容
            target = iframe if iframe is not None else page
            try:
                await target.wait_for_load_state("load", timeout=PLAYWRIGHT_TIMEOUT)
            except Exception as e:
                logger.warning("第 %d 頁載入等待失敗: %s", page_num, e)

            html = await target.content()

            # 解析當前頁
            page_results = parse_search_results(html)
            logger.info("第 %d 頁: 解析 %d 筆", page_num, len(page_results))

            if not page_results:
                logger.info("第 %d 頁無結果，停止分頁", page_num)
                break

            # 重複檢測：若本頁全部 JID 都已見過，分頁可能卡住
            new_count = 0
            for r in page_results:
                jid = r.get("jid", "")
                if jid and jid not in seen_jids:
                    seen_jids.add(jid)
                    all_results.append(r)
                    new_count += 1

            if new_count == 0 and page_num > 1:
                logger.debug("第 %d 頁無新 JID，停止分頁", page_num)
                break

            # 如果已達目標數量，停止
            if len(all_results) >= max_results:
                logger.info("已收集 %d 筆（達標 max_results=%d），停止分頁", len(all_results), max_results)
                break

            # 下一頁（用 URL 導航，避免 strict mode 問題）
            next_url = await self._get_next_page_url(target)
            if not next_url:
                logger.info("無下一頁連結，結束分頁（共 %d 頁）", page_num)
                break

            try:
                await target.goto(next_url, wait_until="load", timeout=PLAYWRIGHT_TIMEOUT)
            except Exception as e:
                logger.warning("翻頁導航失敗 (page %d): %s", page_num + 1, e)
                break

            # 翻頁後重新取得 iframe reference（導航可能重建 frame）
            if iframe is not None:
                new_iframe = page.frame(name="iframe-data")
                if new_iframe is not None:
                    iframe = new_iframe
                else:
                    logger.warning("翻頁後 iframe-data 消失，嘗試等待後重取")
                    try:
                        await page.wait_for_selector("iframe[name='iframe-data']", timeout=5000)
                    except Exception:
                        pass
                    iframe = page.frame(name="iframe-data")
                    if iframe is None:
                        logger.error("iframe-data 無法恢復，停止分頁")
                        break

            page_num += 1

        # 按法院層級排序：最高(1) → 高等(2) → 地方(3)
        # 只在未指定 court 時排序（指定 court 時結果已是同一法院）
        # sort() 是穩定排序，同 court_level 保持原始順序（通常是日期）
        if not params.get("court"):
            all_results.sort(key=lambda r: r.get("court_level", 99))

        logger.info("分頁收集完成: 總計 %d 筆 (max_results=%d)", len(all_results), max_results)
        return all_results[:max_results]

    async def _get_next_page_url(self, target) -> str | None:
        """取得下一頁的 URL

        FJUD 頁面上下各有一個 <a id="hlNext">，必須用 .first 避免
        Playwright strict mode violation（locator resolved to 2 elements）。
        分頁是 URL 導航（href="/FJUD/qryresultlst.aspx?...&page=N"），非 AJAX。
        """
        try:
            btn = target.locator("#hlNext").first
            count = await btn.count()
            if count == 0:
                logger.debug("_get_next_page_url: #hlNext 不存在 (count=0)")
                return None
            visible = await btn.is_visible()
            if not visible:
                logger.debug("_get_next_page_url: #hlNext 不可見")
                return None
            href = await btn.get_attribute("href")
            if not href:
                logger.debug("_get_next_page_url: #hlNext 無 href 屬性")
                return None
            if href.startswith("/"):
                href = f"https://judgment.judicial.gov.tw{href}"
            logger.debug("_get_next_page_url: 找到下一頁 %s", href)
            return href
        except Exception as e:
            logger.warning("_get_next_page_url 異常: %s: %s", type(e).__name__, e)
            return None

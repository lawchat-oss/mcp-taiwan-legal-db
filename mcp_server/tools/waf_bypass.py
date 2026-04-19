"""F5 BIG-IP ASM / Shape Security WAF bypass for judgment.judicial.gov.tw.

策略：用 Playwright 跑一次 JS 挑戰拿 TSPD cookies，之後查詢用 httpx 帶 cookies。
偵測到 block 訊號（Request Rejected / bobcmn JS challenge 頁）時自動重跑 warmup。
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_COOKIE_FILE = Path(__file__).parent.parent / "data" / ".judicial_cookies.json"
_WARMUP_URL = "https://judgment.judicial.gov.tw/FJUD/Default_AD.aspx"


class JudicialWAFBypass:
    """管理 judgment.judicial.gov.tw 的 F5 WAF cookies。

    用法：
        waf = JudicialWAFBypass()
        await waf.ensure_ready()   # 啟動時 warm-up 一次（可選）
        cookies = waf.get_cookies()  # 傳給 httpx client

        r = await client.get(url)
        if waf.is_blocked(r.text):
            await waf.refresh()    # cookie 失效，重跑 warmup
            client.cookies.update(waf.get_cookies())
            r = await client.get(url)
    """

    def __init__(self):
        self._cookies: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._last_warmup_at: float = 0.0
        self._load_from_disk()

    def _load_from_disk(self) -> None:
        if _COOKIE_FILE.exists():
            try:
                data = json.loads(_COOKIE_FILE.read_text(encoding="utf-8"))
                self._cookies = data.get("cookies", {})
                self._last_warmup_at = data.get("saved_at", 0.0)
                logger.info("WAF bypass: loaded %d cookies from disk", len(self._cookies))
            except Exception as e:
                logger.warning("WAF bypass: failed to load cookies: %s", e)

    def _save_to_disk(self) -> None:
        """Atomic write + 0600 permissions (session token protection)."""
        try:
            _COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {"cookies": self._cookies, "saved_at": self._last_warmup_at},
                ensure_ascii=False,
            )
            tmp = _COOKIE_FILE.with_suffix(".json.tmp")
            tmp.write_text(payload, encoding="utf-8")
            os.chmod(tmp, 0o600)
            tmp.replace(_COOKIE_FILE)  # atomic
        except Exception as e:
            logger.warning("WAF bypass: failed to save cookies: %s", e)

    def get_cookies(self) -> dict[str, str]:
        """回傳當前 cookies（供 httpx client 使用）。"""
        return dict(self._cookies)

    async def ensure_ready(self) -> None:
        """啟動時呼叫，若尚無 cookies 則觸發 warmup。"""
        if not self._cookies:
            await self.refresh()

    async def refresh(self) -> None:
        """執行 Playwright warmup，重取 TSPD cookies。"""
        async with self._lock:
            # 若另一個 task 剛剛做完，避免重複 warmup
            now = time.time()
            if now - self._last_warmup_at < 5.0 and self._cookies:
                logger.debug("WAF bypass: skipping duplicate warmup (fresh < 5s)")
                return
            await self._run_warmup()

    async def _run_warmup(self) -> None:
        try:
            from playwright.async_api import (
                TimeoutError as PlaywrightTimeoutError,
                async_playwright,
            )
        except ImportError:
            raise RuntimeError(
                "Playwright 為繞過司法院 F5 WAF 所必需。"
                "請執行：pip install playwright && playwright install chromium"
            )

        logger.info("WAF bypass: running Playwright warmup...")
        t0 = time.time()
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage"],
                )
                try:
                    ctx = await browser.new_context(
                        locale="zh-TW",
                        timezone_id="Asia/Taipei",
                        user_agent=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/120.0.0.0 Safari/537.36"
                        ),
                    )
                    page = await ctx.new_page()
                    await page.goto(
                        _WARMUP_URL, wait_until="domcontentloaded", timeout=60000
                    )
                    # 等真表單出現（代表 F5 挑戰已過）
                    try:
                        await page.wait_for_selector("#btnQry", state="visible", timeout=15000)
                    except Exception:
                        logger.warning("WAF bypass: #btnQry 未顯示，cookies 可能仍無效")
                    cookies = await ctx.cookies()
                    self._cookies = {c["name"]: c["value"] for c in cookies}
                    self._last_warmup_at = time.time()
                    self._save_to_disk()
                    elapsed = time.time() - t0
                    logger.info(
                        "WAF bypass: warmup OK in %.1fs, got %d cookies",
                        elapsed, len(self._cookies),
                    )
                finally:
                    await browser.close()
        except PlaywrightTimeoutError as e:
            # 將 Playwright 專屬例外收斂成 stdlib asyncio.TimeoutError，
            # 讓上游 search handler 不必依賴 Playwright 型別。
            raise asyncio.TimeoutError("WAF warmup 逾時") from e

    @staticmethod
    def is_blocked(response_text: str) -> bool:
        """判斷 response 是否被 F5 WAF 擋住或是 JS 挑戰頁。"""
        if not response_text:
            return True
        # 小 body + Request Rejected = 硬擋
        if len(response_text) < 500 and "Request Rejected" in response_text:
            return True
        # JS challenge 頁含有特定 marker
        if "bobcmn" in response_text and "TSPD" in response_text:
            # 但也要避免誤判：真表單頁 cookie 裡雖含 TSPD，HTML 裡不會有 bobcmn
            return True
        return False


async def get_with_waf_retry(
    client, url, waf: JudicialWAFBypass, *, method: str = "GET", **kwargs
):
    """HTTP 請求 + 偵測被擋自動重跑 warmup 後重試一次。

    Args:
        client: httpx.AsyncClient 實例
        url: 目標 URL
        waf: JudicialWAFBypass 實例
        method: "GET" 或 "POST"
        **kwargs: 傳給 client.get / client.post 的額外參數（如 params, data）
    """
    func = client.get if method == "GET" else client.post
    r = await func(url, **kwargs)
    if waf.is_blocked(r.text):
        logger.info("WAF bypass: detected block, refreshing cookies")
        await waf.refresh()
        client.cookies.update(waf.get_cookies())
        r = await func(url, **kwargs)
    return r

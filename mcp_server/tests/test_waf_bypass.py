"""JudicialWAFBypass + get_with_waf_retry 單元測試。

覆蓋的故事線：
- refresh() 5 秒節流必須在 cookies 為空時也有效（否則 N 個並發冷請求會拉 N 次 Chromium）
- get_with_waf_retry 第二次仍被擋時必須 raise WAFPermanentBlockError，
  不可讓解析器拿 block HTML 產生假空結果
"""

import asyncio

import pytest

from mcp_server.tools import waf_bypass as waf_module
from mcp_server.tools.waf_bypass import (
    JudicialWAFBypass,
    WAFPermanentBlockError,
    get_with_waf_retry,
)


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeHttpClient:
    """httpx-like interface，only what get_with_waf_retry touches."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.cookies = _CookieBag()
        self.calls: list[tuple[str, str]] = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url))
        return _FakeResponse(self._responses.pop(0))

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url))
        return _FakeResponse(self._responses.pop(0))


class _CookieBag:
    def update(self, _other):
        pass


@pytest.fixture
def waf(tmp_path, monkeypatch):
    # 強制 cookie 檔指到 tmp，避免動到專案目錄的真 cookie
    monkeypatch.setattr(waf_module, "_COOKIE_FILE", tmp_path / ".judicial_cookies.json")
    w = JudicialWAFBypass()
    return w


@pytest.mark.asyncio
async def test_refresh_throttle_holds_when_cookies_stay_empty(waf, monkeypatch):
    """warmup 回空 cookies 時，5 秒內第二次呼叫不可再跑 Chromium。

    原本的 `and self._cookies` 會讓空 cookies 失去節流保護，
    N 個冷請求 → N 次 Playwright warmup。
    """
    call_count = 0

    async def fake_warmup(self):
        nonlocal call_count
        call_count += 1
        self._cookies = {}  # 模擬 F5 沒發 cookies 的失敗模式
        self._last_warmup_at = __import__("time").time()

    monkeypatch.setattr(JudicialWAFBypass, "_run_warmup", fake_warmup)

    await waf.refresh()
    await waf.refresh()

    assert call_count == 1, "第二次 refresh 應被 5s 節流擋下"


@pytest.mark.asyncio
async def test_get_with_waf_retry_raises_when_still_blocked_after_refresh(waf, monkeypatch):
    """第二次仍 block → raise WAFPermanentBlockError，不回傳 block HTML。"""
    # 讓 refresh 一瞬完成
    async def noop(self):
        self._cookies = {"TSPD": "stub"}
    monkeypatch.setattr(JudicialWAFBypass, "refresh", noop)

    blocked_html = "Request Rejected"
    client = _FakeHttpClient([blocked_html, blocked_html])

    with pytest.raises(WAFPermanentBlockError):
        await get_with_waf_retry(client, "https://judgment.judicial.gov.tw/x", waf)

    assert len(client.calls) == 2, "應嘗試兩次後才 raise"


@pytest.mark.asyncio
async def test_get_with_waf_retry_passes_when_retry_succeeds(waf, monkeypatch):
    """第二次通過 block 檢查 → 正常回傳 response，不 raise。"""
    async def noop(self):
        self._cookies = {"TSPD": "stub"}
    monkeypatch.setattr(JudicialWAFBypass, "refresh", noop)

    client = _FakeHttpClient(["Request Rejected", "<html>ok content</html>"])
    resp = await get_with_waf_retry(client, "https://judgment.judicial.gov.tw/x", waf)

    assert "ok content" in resp.text
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_get_with_waf_retry_no_block_returns_first_response(waf):
    """第一次就不是 block → 直接回傳，不觸發 refresh。"""
    client = _FakeHttpClient(["<html>clean</html>"])
    resp = await get_with_waf_retry(client, "https://judgment.judicial.gov.tw/x", waf)

    assert "clean" in resp.text
    assert len(client.calls) == 1

"""JudicialSearchBrowser 集成測試

用 in-memory SQLite cache + monkeypatch 繞過 Playwright，
驗證輸入驗證、快取命中、例外處理與全局超時行為。
"""

import asyncio

import httpx
import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.tools.judicial_search import JudicialSearchBrowser


@pytest.fixture
async def cache(tmp_path):
    db = CacheDB(db_path=tmp_path / "test_cache.db")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
async def browser(cache):
    yield JudicialSearchBrowser(cache)


@pytest.mark.asyncio
async def test_search_requires_any_key(browser):
    """完全沒給任何查詢條件 → 回傳明確驗證訊息"""
    result = await browser.search()
    assert result["success"] is False
    assert "keyword" in result["error"] or "case_number" in result["error"]


@pytest.mark.asyncio
async def test_search_returns_cached_result(browser, cache):
    """快取命中時應直接回傳，不觸發 Playwright"""
    params = {
        "keyword": "借名登記",
        "court": "",
        "case_type": "",
        "year_from": 0,
        "year_to": 0,
        "case_word": "",
        "case_number": "",
        "main_text": "",
    }
    await cache.set_search(params, {"success": True, "results": [{"jid": "X,1,2,3"}], "total_count": 1})

    result = await browser.search(keyword="借名登記")
    assert result["success"] is True
    assert result["cached"] is True
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_search_generic_exception_no_leak(browser, monkeypatch):
    """任意例外應被捕捉並回傳通用訊息，不外洩 str(e) 內部細節"""
    async def boom(self):
        raise RuntimeError("INTERNAL /Users/secret/path leak")
    monkeypatch.setattr(JudicialSearchBrowser, "_ensure_browser", boom)

    result = await browser.search(keyword="契約")
    assert result["success"] is False
    assert "/Users/secret/path" not in result["error"]
    assert "RuntimeError" not in result["error"]


@pytest.mark.asyncio
async def test_search_httpx_exception_gives_friendly_message(browser, monkeypatch):
    """httpx.HTTPError → 連線類訊息，仍不洩 raw"""
    async def boom(self):
        raise httpx.ConnectError("[Errno -2] Temporary failure /Users/secret")
    monkeypatch.setattr(JudicialSearchBrowser, "_ensure_browser", boom)

    result = await browser.search(keyword="契約")
    assert result["success"] is False
    assert "/Users/secret" not in result["error"]
    assert "連線" in result["error"]


@pytest.mark.asyncio
async def test_search_timeout_exception_gives_friendly_message(browser, monkeypatch):
    """asyncio.TimeoutError → 逾時訊息分流"""
    async def boom(self):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(JudicialSearchBrowser, "_ensure_browser", boom)

    result = await browser.search(keyword="契約")
    assert result["success"] is False
    assert "逾時" in result["error"]


@pytest.mark.asyncio
async def test_precise_case_with_main_text_skips_http_fast_path(browser, monkeypatch):
    """主文過濾存在時，不可走會忽略條件的精確案號 fast path"""
    calls: list[str] = []

    async def no_wait():
        return None

    class DummyPage:
        async def close(self):
            return None

    class DummyContext:
        async def new_page(self):
            return DummyPage()

        async def close(self):
            return None

    class DummyBrowser:
        async def new_context(self, **kwargs):
            return DummyContext()

    async def ensure_browser(self):
        self._browser = DummyBrowser()

    async def precise_search(self, params, max_results):
        calls.append("precise")
        return [{"jid": "HTTP,1,2,3", "case_id": "http"}]

    async def perform_search(self, page, params, max_results):
        calls.append("perform")
        assert params["main_text"] == "原告之訴駁回"
        return [{"jid": "PW,1,2,3", "case_id": "playwright", "court_level": 1}]

    monkeypatch.setattr(browser, "_rate_limit", no_wait)
    monkeypatch.setattr(JudicialSearchBrowser, "_ensure_browser", ensure_browser)
    monkeypatch.setattr(JudicialSearchBrowser, "_precise_search_http", precise_search)
    monkeypatch.setattr(JudicialSearchBrowser, "_perform_search", perform_search)

    result = await browser.search(
        case_word="台上",
        case_number="123",
        main_text="原告之訴駁回",
    )

    assert result["success"] is True
    assert [r["jid"] for r in result["results"]] == ["PW,1,2,3"]
    assert calls == ["perform"]


def test_global_timeout_constant_exists_and_reasonable():
    """SEARCH_GLOBAL_TIMEOUT 須存在且落在合理區間"""
    from mcp_server.config import SEARCH_GLOBAL_TIMEOUT
    assert isinstance(SEARCH_GLOBAL_TIMEOUT, (int, float))
    assert 30 <= SEARCH_GLOBAL_TIMEOUT <= 600

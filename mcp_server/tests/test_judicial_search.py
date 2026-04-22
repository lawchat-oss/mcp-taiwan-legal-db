"""JudicialSearchClient 集成測試

用 in-memory SQLite cache + monkeypatch 繞過 HTTP，
驗證輸入驗證、快取命中、例外處理與 fast-path 守衛。
"""

import asyncio

import httpx
import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.tools.judicial_search import JudicialSearchClient
from mcp_server.tools.waf_bypass import JudicialWAFBypass, WAFPermanentBlockError


@pytest.fixture
async def cache(tmp_path):
    db = CacheDB(db_path=tmp_path / "test_cache.db")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
async def client(cache):
    yield JudicialSearchClient(cache, JudicialWAFBypass())


async def _no_rate_limit(self):
    return None


@pytest.mark.asyncio
async def test_search_requires_any_key(client):
    """完全沒給任何查詢條件 → 回傳明確驗證訊息"""
    result = await client.search()
    assert result["success"] is False
    assert "keyword" in result["error"] or "case_number" in result["error"]


@pytest.mark.asyncio
async def test_search_returns_cached_result(client, cache):
    """快取命中時應直接回傳，不觸發 HTTP"""
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

    result = await client.search(keyword="借名登記")
    assert result["success"] is True
    assert result["cached"] is True
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_search_generic_exception_no_leak(client, monkeypatch):
    """任意例外應被捕捉並回傳通用訊息，不外洩 str(e) 內部細節"""
    async def boom(self, params, max_results):
        raise RuntimeError("INTERNAL /Users/secret/path leak")
    monkeypatch.setattr(JudicialSearchClient, "_keyword_search_http", boom)
    monkeypatch.setattr(JudicialSearchClient, "_rate_limit", _no_rate_limit)

    result = await client.search(keyword="契約")
    assert result["success"] is False
    assert "/Users/secret/path" not in result["error"]
    assert "RuntimeError" not in result["error"]


@pytest.mark.asyncio
async def test_search_httpx_exception_gives_friendly_message(client, monkeypatch):
    """httpx.HTTPError → 連線類訊息，仍不洩 raw"""
    async def boom(self, params, max_results):
        raise httpx.ConnectError("[Errno -2] Temporary failure /Users/secret")
    monkeypatch.setattr(JudicialSearchClient, "_keyword_search_http", boom)
    monkeypatch.setattr(JudicialSearchClient, "_rate_limit", _no_rate_limit)

    result = await client.search(keyword="契約")
    assert result["success"] is False
    assert "/Users/secret" not in result["error"]
    assert "連線" in result["error"]


@pytest.mark.asyncio
async def test_search_timeout_exception_gives_friendly_message(client, monkeypatch):
    """asyncio.TimeoutError → 逾時訊息分流（涵蓋 waf_bypass 收斂的 Playwright 逾時）"""
    async def boom(self, params, max_results):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(JudicialSearchClient, "_keyword_search_http", boom)
    monkeypatch.setattr(JudicialSearchClient, "_rate_limit", _no_rate_limit)

    result = await client.search(keyword="契約")
    assert result["success"] is False
    assert "逾時" in result["error"]


@pytest.mark.asyncio
async def test_search_httpx_timeout_routes_to_timeout_arm(client, monkeypatch):
    """httpx.TimeoutException 是 HTTPError 子類，必須先被 timeout arm 捕捉"""
    async def boom(self, params, max_results):
        raise httpx.ReadTimeout("timed out")
    monkeypatch.setattr(JudicialSearchClient, "_keyword_search_http", boom)
    monkeypatch.setattr(JudicialSearchClient, "_rate_limit", _no_rate_limit)

    result = await client.search(keyword="契約")
    assert result["success"] is False
    assert "逾時" in result["error"]
    assert "連線" not in result["error"]


@pytest.mark.asyncio
async def test_search_waf_permanent_block_gives_dedicated_message(client, monkeypatch):
    """WAFPermanentBlockError 要分流到 WAF 訊息，不是通用 / HTTPError。"""
    async def boom(self, params, max_results):
        raise WAFPermanentBlockError("blocked twice")
    monkeypatch.setattr(JudicialSearchClient, "_keyword_search_http", boom)
    monkeypatch.setattr(JudicialSearchClient, "_rate_limit", _no_rate_limit)

    result = await client.search(keyword="契約")
    assert result["success"] is False
    assert "WAF" in result["error"]
    assert "逾時" not in result["error"]


@pytest.mark.asyncio
async def test_precise_fast_path_propagates_waf_permanent_block(client, monkeypatch):
    """精確案號路徑的 WAFPermanentBlockError 不可被 precise 內部 Exception 吃掉。"""
    async def boom(self, params, max_results):
        raise WAFPermanentBlockError("blocked twice")
    monkeypatch.setattr(JudicialSearchClient, "_precise_search_http", boom)
    monkeypatch.setattr(JudicialSearchClient, "_rate_limit", _no_rate_limit)

    result = await client.search(case_word="台上", case_number="123")
    assert result["success"] is False
    assert "WAF" in result["error"]


@pytest.mark.asyncio
async def test_precise_case_with_main_text_skips_http_fast_path(client, monkeypatch):
    """主文過濾存在時，不可走會忽略條件的精確案號 fast path"""
    calls: list[str] = []

    async def precise_search(self, params, max_results):
        calls.append("precise")
        return [{"jid": "HTTP,1,2,3", "case_id": "http"}]

    async def keyword_search(self, params, max_results):
        calls.append("keyword")
        assert params["main_text"] == "原告之訴駁回"
        return [{"jid": "KW,1,2,3", "case_id": "keyword"}]

    monkeypatch.setattr(JudicialSearchClient, "_precise_search_http", precise_search)
    monkeypatch.setattr(JudicialSearchClient, "_keyword_search_http", keyword_search)
    monkeypatch.setattr(JudicialSearchClient, "_rate_limit", _no_rate_limit)

    result = await client.search(
        case_word="台上",
        case_number="123",
        main_text="原告之訴駁回",
    )

    assert result["success"] is True
    assert [r["jid"] for r in result["results"]] == ["KW,1,2,3"]
    assert calls == ["keyword"]

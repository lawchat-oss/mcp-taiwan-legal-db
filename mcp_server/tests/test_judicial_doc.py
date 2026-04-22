"""JudgmentDocClient 集成測試：白名單、快取、URL 驗證"""

import httpx
import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.tools.judicial_doc import JudgmentDocClient
from mcp_server.tools.waf_bypass import JudicialWAFBypass, WAFPermanentBlockError


@pytest.fixture
async def cache(tmp_path):
    db = CacheDB(db_path=tmp_path / "test_cache.db")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
async def client(cache):
    c = JudgmentDocClient(cache, JudicialWAFBypass())
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_get_by_url_rejects_non_whitelisted_domain(client):
    """SSRF 防護：非 ALLOWED_DOMAINS 必須被拒絕"""
    result = await client.get_by_url("https://evil.example.com/id=x")
    assert result["success"] is False
    assert "域名" in result["error"] or "whitelist" in result["error"].lower()


@pytest.mark.asyncio
async def test_get_by_url_rejects_file_scheme(client):
    """file:// 絕對不該放行"""
    result = await client.get_by_url("file:///etc/passwd")
    assert result["success"] is False


@pytest.mark.asyncio
async def test_get_by_jid_uses_cache(client, cache):
    """已快取的 JID 應直接返回，不發 HTTP"""
    jid = "TPSV,104,台上,472,20150326,1"
    await cache.set_judgment(jid, {
        "case_id": "104 台上 472",
        "court": "最高法院",
        "full_text": "測試用快取內容",
    }, source="test")

    result = await client.get_by_jid(jid)
    assert result["success"] is True
    assert result["cached"] is True
    assert result["court"] == "最高法院"


@pytest.mark.asyncio
async def test_fetch_via_http_passes_jid_as_params_not_raw_query(client, monkeypatch):
    """jid='x&ty=evil' 等惡意輸入必須走 httpx params，不可被串接進 URL。

    httpx 的 params= 會對 value 做 URL-encode，所以 '&' 會變 '%26'，
    不會被 server 解析成另一個 query 參數。
    """
    captured: dict = {}

    async def fake_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        # 回一個非 block 的 200 response，避開 get_with_waf_retry 的 retry 路徑。
        return httpx.Response(
            200,
            text="<html><body>no #jud</body></html>",
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(client.http, "get", fake_get)

    malicious = "x&ty=evil&id=other"
    await client._fetch_via_http(malicious)

    assert captured["params"] == {"ty": "JD", "id": malicious}
    assert "&" not in captured["url"], "URL 主體不該含手工串接的 query 分隔"


@pytest.mark.asyncio
async def test_get_by_jid_waf_permanent_block_gives_dedicated_message(client, monkeypatch):
    """WAFPermanentBlockError 要轉成結構化錯誤，不可往上冒泡到 MCP 框架。"""
    async def boom(self, jid):
        raise WAFPermanentBlockError("blocked twice")
    monkeypatch.setattr(JudgmentDocClient, "_fetch_via_http", boom)

    result = await client.get_by_jid("TPSV,104,台上,472,20150326,1")
    assert result["success"] is False
    assert "WAF" in result["error"]
    assert result["jid"] == "TPSV,104,台上,472,20150326,1"

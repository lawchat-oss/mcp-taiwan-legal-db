"""JudgmentDocClient 集成測試：白名單、快取、URL 驗證"""

import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.tools.judicial_doc import JudgmentDocClient


@pytest.fixture
async def cache(tmp_path):
    db = CacheDB(db_path=tmp_path / "test_cache.db")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
async def client(cache):
    c = JudgmentDocClient(cache)
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

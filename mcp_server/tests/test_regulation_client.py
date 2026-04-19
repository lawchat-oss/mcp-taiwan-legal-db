"""RegulationClient 集成測試：pcode 解析（精確 / 縮寫 / 模糊）+ URL 驗證"""

import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.tools.regulations import RegulationClient
from mcp_server.config import validate_url_domain


@pytest.fixture
async def cache(tmp_path):
    db = CacheDB(db_path=tmp_path / "test_cache.db")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
async def client(cache):
    c = RegulationClient(cache)
    yield c
    await c.close()


class TestResolvePcode:
    def test_exact_match(self, client):
        assert client.resolve_pcode("民法") == "B0000001"

    def test_alias_expansion_laobi(self, client):
        """勞基法 → 勞動基準法"""
        assert client.resolve_pcode("勞基法") == client.resolve_pcode("勞動基準法")

    def test_alias_expansion_xiaobao(self, client):
        """消保法 → 消費者保護法"""
        assert client.resolve_pcode("消保法") == client.resolve_pcode("消費者保護法")

    def test_unknown_returns_none(self, client):
        assert client.resolve_pcode("完全不存在的法規名稱_xyz_2099") is None

    def test_short_name_prefers_exact(self, client):
        """查「保險法」應命中「保險法」而非「全民健康保險法」"""
        pcode = client.resolve_pcode("保險法")
        assert pcode == "G0390002"


class TestUrlWhitelist:
    @pytest.mark.parametrize("url", [
        "https://judgment.judicial.gov.tw/FJUD/data.aspx?id=x",
        "https://data.judicial.gov.tw/anything",
        "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=B0000001",
    ])
    def test_allowed(self, url):
        assert validate_url_domain(url) is True

    @pytest.mark.parametrize("url", [
        "https://evil.example.com/",
        "http://169.254.169.254/latest/meta-data/",
        "https://judgment.judicial.gov.tw.evil.com/",
        "file:///etc/passwd",
        "https://judicial.gov.tw/",  # 缺 judgment/data prefix
    ])
    def test_blocked(self, url):
        assert validate_url_domain(url) is False

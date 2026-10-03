"""query_regulation 的條號選取：單條、區間、跨號多條；不指定條號時只回目錄。"""

from unittest.mock import AsyncMock

import pytest

from mcp_server.tools import regulations as reg

ARTICLES = [{"number": n, "content": f"第{n}條內容"} for n in
            ("1", "15", "15-1", "15-10", "16", "184", "185", "186", "247-1", "248")]


def test_parse_spec_accepts_mixed_forms():
    assert reg.parse_article_spec("184") == [((184,), (184,))]
    assert reg.parse_article_spec("第184條~第198條, 247之1、15-1") == [
        ((184,), (198,)), ((247, 1), (247, 1)), ((15, 1), (15, 1))]
    assert reg.parse_article_spec("198~184") == [((184,), (198,))]
    assert reg.article_label(reg.parse_article_spec("2-1-1")[0][0]) == "2-1-1"
    for bad in ("", "abc", "184~x"):
        with pytest.raises(ValueError):
            reg.parse_article_spec(bad)


def test_pick_orders_sub_articles_and_reports_missing():
    hits, extra = reg._pick(ARTICLES, reg.parse_article_spec("15~15-10, 999"))
    assert [a["number"] for a in hits] == ["15", "15-1", "15-10"]  # 15-10 在 15 與 16 之間，不是 16 之後
    assert extra == {"missing": ["999"]}


def test_pick_caps_and_points_to_next_article(monkeypatch):
    monkeypatch.setattr(reg, "MAX_ARTICLES", 2)
    hits, extra = reg._pick(ARTICLES, reg.parse_article_spec("184~248"))
    assert [a["number"] for a in hits] == ["184", "185"]
    assert extra["has_more"] and "第 186 條" in extra["note"]


async def test_outline_returns_no_article_text():
    client = reg.RegulationClient.__new__(reg.RegulationClient)
    client.get_all_articles = AsyncMock(return_value={
        "success": True, "law": {"pcode": "B0000001", "name": "民法"}, "articles": ARTICLES,
        "structure": [{"title": "第一章法例", "level": 2, "first_article": "1"}], "last_amended": "20260917",
        "source_url": "u"})
    r = await client.get_outline("B0000001")
    assert "articles" not in r and r["article_count"] == len(ARTICLES)
    assert (r["first_article"], r["last_article"]) == ("1", "248") and r["structure"][0]["first_article"] == "1"
    assert r["last_amended"] == "20260917"
    r = await client.get_articles("B0000001", reg.parse_article_spec("184,186"))
    assert [a["number"] for a in r["articles"]] == ["184", "186"]


async def test_single_missing_article_reports_missing_but_connection_error_does_not():
    import httpx

    from mcp_server.tools.regulations import RegulationClient

    cache = AsyncMock()
    cache.get_regulation.return_value = None
    client = RegulationClient.__new__(RegulationClient)
    client.cache = cache
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>")))
    r = await client.get_article("B0000001", "9999")
    assert r["success"] is False and r["missing"] == ["9999"]
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    r = await client.get_article("B0000001", "9999")
    assert r["success"] is False and "missing" not in r

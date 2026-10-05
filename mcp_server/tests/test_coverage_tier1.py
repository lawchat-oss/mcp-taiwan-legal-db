"""歷審、英譯法規、公布／施行日期、往後引用：解析與篩選。HTTP 一律不連網。"""

import io
import json
import zipfile

import mcp_server.server as server
from mcp_server.tools import constitutional_court as cc
from mcp_server.tools import judicial_doc as jd
from mcp_server.tools import regulations as reg
from mcp_server.updater import _law_meta


def test_history_key_and_items():
    page = '<script>url: "../controls/GetJudHistory.ashx?jid=TPSV%60110%60%e5%8f%b0%601000%60110%2c1"</script>'
    assert jd._history_key(page) == "TPSV%60110%60%e5%8f%b0%601000%60110%2c1"
    assert jd._history_key("<html></html>") == ""
    item = jd._history_item({"desc": "最高法院 110 年度 台上 字第 1000 號裁定(110.03.10)",
                             "href": "data.aspx?ty=JD&id=TPSV%2c110%2c%e5%8f%b0%e4%b8%8a%2c1000%2c20210310%2c1", "red": 1})
    assert item["jid"] == "TPSV,110,台上,1000,20210310,1"
    assert item["url"].startswith("https://judgment.judicial.gov.tw/FJUD/data.aspx?")
    assert item["pending_supreme_court"] is True
    assert jd._history_item({"desc": "x", "href": "", "red": 0}) == {
        "desc": "x", "jid": "", "url": "", "pending_supreme_court": False}


def test_law_meta_from_official_fields(monkeypatch):
    meta = _law_meta({"LawModifiedDate": "20251226", "LawEffectiveDate": "99991231",
                      "LawEffectiveNote": "第 387-1 條自公布後\r\n  六個月施行。", "LawCategory": "行政＞經濟部＞商業目"})
    assert meta == {"amended": "2025-12-26", "effective": "9999-12-31",
                    "effective_note": "第 387-1 條自公布後六個月施行。", "category": "行政＞經濟部＞商業目"}
    monkeypatch.setitem(reg._LAW_META, "X1", meta)
    fields = reg.law_meta_fields("X1")
    assert fields["last_amended"] == "2025-12-26" and fields["effective_date"].startswith("另定")
    assert reg.law_meta_fields("missing") == {}


async def test_search_regulations_amended_since_sorts_newest_first(monkeypatch):
    monkeypatch.setattr(server, "_PCODE_ALL", {"甲法": "A1", "乙法": "A2", "丙法": "A3"})
    monkeypatch.setattr(reg, "_LAW_META", {
        "A1": {"amended": "2026-09-01", "category": "行政＞勞動部＞勞動條件目"},
        "A2": {"amended": "2026-09-20", "category": "行政＞財政部＞賦稅目"},
        "A3": {"amended": "2020-01-01", "category": "行政＞勞動部＞勞動條件目"},
    })
    r = await server.search_regulations(amended_since="115-09-01")
    assert [x["law_name"] for x in r["results"]] == ["乙法", "甲法"]
    r = await server.search_regulations(category="勞動部")
    assert {x["law_name"] for x in r["results"]} == {"甲法", "丙法"}
    assert (await server.search_regulations(amended_since="yesterday"))["success"] is False
    assert (await server.search_regulations())["success"] is False


def _en_zip(laws: list[dict]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("EnLaw.json", "﻿" + json.dumps({"Laws": laws}))
    return buf.getvalue()


def test_en_parse_keeps_articles_only():
    rows = reg._en_parse(_en_zip([{
        "EngLawURL": "https://law.moj.gov.tw/Eng/LawClass/LawAll.aspx?pcode=B0000001", "EngLawName": "Civil Code",
        "EngLawModifiedDate": "20210120", "LawName": "民法",
        "EngLawArticles": [{"EngArticleType": "C", "EngArticleNo": "", "EngArticleContent": "Part I"},
                           {"EngArticleType": "A", "EngArticleNo": "Article 15-1", "EngArticleContent": "a\r\nb "}],
    }]))
    assert rows == [{"id": "B0000001", "date": "20210120", "name": "Civil Code",
                     "url": "https://law.moj.gov.tw/Eng/LawClass/LawAll.aspx?pcode=B0000001",
                     "articles": [{"number": "15-1", "content": "a\nb"}]}]


def test_en_parse_rejects_maintenance_page():
    try:
        reg._en_parse(b"<html>maintenance</html>")
    except ValueError:
        return
    raise AssertionError("維護頁不該被當成英譯資料")


async def test_get_english_flags_outdated_translation(monkeypatch):
    row = {"id": "B0000001", "date": "20210120", "name": "Civil Code", "url": "u",
           "articles": [{"number": "184", "content": "A person who..."}, {"number": "185", "content": "x"}]}

    async def load(http):
        return [row]

    monkeypatch.setattr(reg._EN_LAWS, "load", load)
    monkeypatch.setitem(reg._LAW_META, "B0000001", {"amended": "2026-08-17"})
    client = reg.RegulationClient.__new__(reg.RegulationClient)
    client.client = None
    r = await client.get_english("B0000001", reg.parse_article_spec("184"))
    assert [a["number"] for a in r["articles"]] == ["184"]
    assert "2026-08-17" in r["note"] and r["english_version_date"] == "2021-01-20"
    r = await client.get_english("B0000001", reg.parse_article_spec("184~185"))
    assert len(r["articles"]) == 2
    assert (await client.get_english("B0000001", reg.parse_article_spec("999")))["success"] is False
    r = await client.get_english("B0000001")  # 沒指定條號：不回條文
    assert r["articles"] == [] and r["article_count"] == 2 and r["last_article"] == "185"


def test_cited_by_scans_local_rulings(monkeypatch):
    monkeypatch.setattr(cc, "_old_cases", {
        "748": {"reasoning": "本件……", "date": "106"},
        "791": {"reasoning": "參照本院釋字第748號解釋意旨", "date": "109"},
        "800": {"reasoning": "釋字第7480號不相干", "date": "110"},
    })
    monkeypatch.setattr(cc, "_new_cases", {"112_4": {"main_text": "", "reasoning": "釋字第 748 號", "date": "112"}})
    r = cc.get_citations("釋字第748號", direction="cited_by")
    assert [x["case_id"] for x in r["cited_by"]] == ["釋字第791號", "112年憲判字第4號"]


def test_enumerated_citations_inherit_prefix():
    text = "參照釋字第477號、第747號及第762號；112年憲判字第4號、第11號；釋字第8號解釋、第2條"
    assert [c["case_id"] for c in cc._extract_citations(text)] == [
        "釋字第8號", "釋字第477號", "釋字第747號", "釋字第762號", "112年憲判字第4號", "112年憲判字第11號"]


def test_english_article_number_trailing_period():
    rows = reg._en_parse(_en_zip([{"EngLawURL": "https://x/?pcode=K0070004", "EngLawModifiedDate": "2020",
                                   "EngLawArticles": [{"EngArticleType": "A", "EngArticleNo": "Article 34-1.",
                                                       "EngArticleContent": "x"}]}]))
    assert rows[0]["articles"][0]["number"] == "34-1"


def test_search_interpretations_max_results_bounds(monkeypatch):
    for bad in (0, -1):
        r = cc.search_interpretations(max_results=bad)
        assert r["success"] is False and "max_results" in r["error"]
    monkeypatch.setattr(cc, "_load_old_listing", lambda: {n: str(n) for n in range(1, 301)})
    r = cc.search_interpretations(include_new=False, max_results=10_000)
    assert len(r["results"]) == 200 and r["truncated"] is True

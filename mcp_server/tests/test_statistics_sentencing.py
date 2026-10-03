"""統計表（司法統計、法務統計、犯罪狀況及其分析）與量刑資訊：表格轉文字、id 驗證、名稱對應。

HTTP 一律以 MockTransport 模擬，不連官方網站。
"""

import io
import json
import zipfile

import httpx
import pytest
from bs4 import BeautifulSoup

from mcp_server.cache.db import CacheDB
from mcp_server.tools import sentencing
from mcp_server.tools import statistics as st


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.fixture(autouse=True)
def _fresh_memo(monkeypatch):
    monkeypatch.setattr(st, "_memo", {})
    monkeypatch.setattr(sentencing, "_memo", {})


def _zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


ODS = _zip({"mimetype": "application/vnd.oasis.opendocument.spreadsheet", "content.xml": """<?xml version="1.0"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">
<office:body><office:spreadsheet><table:table table:name="1-4">
<table:table-row><table:table-cell table:number-columns-spanned="2"><text:p>收結 概況</text:p>
<text:p>Summary of Filings</text:p></table:table-cell><table:covered-table-cell/>
<table:table-cell table:number-columns-repeated="16000"/></table:table-row>
<table:table-row table:number-rows-repeated="1048000"><table:table-cell table:number-columns-repeated="16384"/></table:table-row>
<table:table-row><table:table-cell><text:p>新</text:p><text:p>收</text:p></table:table-cell>
<table:table-cell office:value-type="float" office:value="1234"><text:p>1,234</text:p></table:table-cell>
<table:table-cell><text:p>Newly Lodged</text:p></table:table-cell></table:table-row>
</table:table></office:spreadsheet></office:body></office:document-content>"""})

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
XLSX = _zip({
    "xl/workbook.xml": f'<workbook {_NS} xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                       '<sheets><sheet name="表3-1" sheetId="1" r:id="rId1"/></sheets></workbook>',
    "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                  '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
    "xl/sharedStrings.xml": f"<sst {_NS}><si><t>年別</t></si><si><r><t>人</t></r><r><t>數</t></r></si></sst>",
    "xl/worksheets/sheet1.xml": f'<worksheet {_NS}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
                                '<c r="C1" t="s"><v>1</v></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>113年</t>'
                                '</is></c><c r="C2"><v>12.50</v></c></row></sheetData></worksheet>',
})


# ─── 表格 → 文字 ───

def test_ods_keeps_chinese_paragraphs_and_caps_repeated_cells():
    assert st._file_text(ODS) == {"table_text": "## 1-4\n收結概況\n新收 | 1,234 | Newly Lodged"}


def test_xlsx_uses_shared_strings_and_column_letters():
    assert st._file_text(XLSX) == {"table_text": "## 表3-1\n年別 |  | 人數\n113年 |  | 12.5"}


def test_html_tables_take_innermost_table_once():
    inner = ('<table><tr><td colspan="2">有罪人數</td></tr><tr><td>罪 \xa0名 \xa0別</td><td>114年</td></tr>'
             '<tr><td>總計</td><td>198,452</td><td></td></tr></table>')
    soup = BeautifulSoup(f"<table><tr><td>{inner}</td></tr></table>{inner}", "html.parser")
    assert st.html_tables_text(soup) == "有罪人數\n罪名別 | 114年\n總計 | 198,452"


# ─── id 驗證 ───

@pytest.mark.parametrize("key,native", [
    ("judicial", "../../etc/passwd"), ("judicial", "267552"), ("judicial", "267552-ZZ"),
    ("moj", "INF_COMMON_X/1"), ("moj", "INF_COMMON_P/../1"), ("moj", "807"),
    ("cprc", "45180/../../x"), ("cprc", "abc"), ("cprc", "1/2/3"),
])
async def test_bad_native_ids_are_rejected_before_any_request(key, native):
    def handler(request):
        raise AssertionError(f"unexpected request {request.url}")

    async with _client(handler) as http:
        with pytest.raises(LookupError):
            await st.SOURCES[key][3](http, native)


async def test_cprc_media_must_belong_to_the_report():
    post = ('<h2 class="title">中華民國113年犯罪狀況及其分析</h2><section class="cp">發布日期：114-11-28 簡介</section>'
            '<div class="file_download"><ul><li><a href="/media/20215125/4-數據.xlsx?mediaDL=true" '
            'title="4. 第四篇-數據.xlsx (另開新視窗)">x</a></li></ul></div>')

    def handler(request):
        if request.url.path.endswith("/45180/post"):
            return httpx.Response(200, text=post)
        if request.url.path.startswith("/media/20215125/"):
            return httpx.Response(200, content=XLSX)
        return httpx.Response(404)

    async with _client(handler) as http:
        report = await st._cprc_get(http, "45180")
        assert report["date"] == "2025-11-28"
        assert report["files"] == [{"id": "cprc:45180/20215125", "title": "4. 第四篇-數據.xlsx",
                                    "url": "https://www.cprc.moj.gov.tw/media/20215125/4-數據.xlsx?mediaDL=true"}]
        chapter = await st._cprc_get(http, "45180/20215125")
        assert chapter["title"].endswith("／4. 第四篇-數據.xlsx") and "113年 |  | 12.5" in chapter["table_text"]
        with pytest.raises(LookupError):
            await st._cprc_get(http, "45180/99999")


# ─── 搜尋與用戶端 ───

def _judicial_handler(requests: list):
    def row(n, title, files):
        links = "".join(f'<a href="https://www.judicial.gov.tw/tw/dl-{i}-{"ab" * 16}.html">{f}</a>' for f, i in files)
        return (f'<tr><td data-title="項次">{n}</td><td data-title="項目">地方法院</td>'
                f'<td data-title="標題">{title}</td><td data-title="檔案下載">{links}</td></tr>')

    pages = {
        "/tw/np-1260-1.html": '<section class="np"><ul><li><a href="/tw/lp-2475-1.html" title="114年">114年</a></li>'
                              '<li><a href="/tw/lp-2393-1.html" title="113年">113年</a></li></ul></section>',
        "/tw/lp-2475-1-1-60.html": '<section class="lp"><table><tbody>' + row(1, "1. 刑事案件收結情形", [("PDF", 1), ("ODS", 2)])
                                   + "</tbody></table>共 61 筆資料，第 1/2 頁</section>",
        "/tw/lp-2475-1-2-60.html": '<section class="lp"><table><tbody>' + row(61, "61. 民事事件收結情形", [("PDF", 3)])
                                   + row(62, "62. 法官人數", [("PDF", 5)]) + "</tbody></table></section>",
    }

    def handler(request):
        requests.append(request.url.path)
        if request.url.path in pages:
            return httpx.Response(200, text=pages[request.url.path])
        if request.url.path == f"/tw/dl-2-{'ab' * 16}.html":
            return httpx.Response(200, content=ODS)
        return httpx.Response(404)
    return handler


async def test_judicial_search_walks_all_pages_and_prefers_ods():
    requests = []
    async with _client(_judicial_handler(requests)) as http:
        group = await st._judicial_search(http, "收結", 0, 1)
        assert group["category"] == "114年統計年報" and group["total"] == 2
        assert [i["id"] for i in group["items"]] == [f"judicial:2-{'ab' * 16}", f"judicial:3-{'ab' * 16}"]
        assert group["items"][0]["summary"] == "地方法院／格式：PDF、ODS"
        await st._judicial_search(http, "法官", 114, 1)
        assert len(requests) == 3  # 年度清單一天內只抓一次
        with pytest.raises(ValueError, match="113–114"):
            await st._judicial_search(http, "", 99, 1)


async def test_client_get_caps_caches_and_reports_bad_ids(tmp_path):
    cache = CacheDB(db_path=tmp_path / "c.db")
    await cache.initialize()
    client = st.StatisticsClient(cache)
    requests = []
    await client.http.aclose()
    client.http = _client(_judicial_handler(requests))
    try:
        first = await client.get(f"judicial:2-{'ab' * 16}")
        assert first["success"] and first["title"] == "收結概況" and first["truncated"] is False
        again = await client.get(f"judicial:2-{'ab' * 16}")
        assert again["cached"] and len(requests) == 1
        assert not (await client.get("judicial:../x"))["success"]
        assert not (await client.get("nope:1"))["success"]
        assert st.resolve_sources("月報, 法務統計") == ["judicial_monthly", "moj"]
        assert st.resolve_sources("火星") is None
    finally:
        await client.close()
        await cache.close()


# ─── 量刑資訊 ───

CRIMES = [{"id": "stole", "title": "竊盜案件"}, {"id": "fraud", "title": "詐欺案件"}]
COURTS = [{"id": "TPD", "name": "臺灣臺北地方法院"}, {"id": "TPY", "name": "臺灣臺北地方法院少年法庭"}]
LAWS = {"count": 9, "year": {"min": 96, "max": 115}, "rules": [
    {"id": "dummy_stole_1", "title": "刑法第 320 條", "name": "竊盜罪", "type": "radio", "options": [
        {"id": "stole_9_1", "name": "竊盜罪", "title": "第 1 項"},
        {"id": "stole_9_2", "name": "竊盜未遂罪", "title": "第 3 項、第 1 項"}]}]}
TREE = [
    {"id": "stole_002", "title": "加重減輕", "items": [{"category": "加重事由", "behavior": [
        {"id": "stole_154", "title": "刑法第47條　累犯", "type": "radio", "options": [
            {"id": "stole_154_1", "name": "是", "child": []}, {"id": "stole_154_-1", "name": "否", "child": []},
            {"id": "", "name": "暫不考慮", "child": []}]}]}]},
    {"id": "stole_004", "title": "犯罪動機", "items": [{"category": "", "behavior": [
        {"id": "stole_198", "title": "行為人犯罪之動機、目的", "type": "radio", "options": [
            {"id": "stole_198_0", "name": "未提及", "child": []},
            {"id": "stole_198_2", "name": "有具體內容", "child": [{"id": "stole_199", "name": "經濟窘迫"}]}]}]}]},
]
SEARCH = {"total": 521, "punishmentAvg": "有期徒刑3.8月", "punishmentMin": "有期徒刑2月", "punishmentMax": "有期徒刑3年",
          "searchId": "x", "punishments": [{"id": "imprisonment", "name": "有期徒刑", "total": 265, "avg": "3.8月",
                                            "min": "2月", "minCount": 3, "max": "3年", "maxCount": 1, "charts": [
                                                {"name": "有期徒刑", "unit": "month",
                                                 "series": [{"name": 2.0, "count": 3}, {"name": 3.5, "count": 9}]}]}]}


def _intellisen(calls: list, bodies: list):
    def handler(request):
        path = request.url.path.removeprefix("/api/frontend/")
        calls.append(path)
        if "crimes/list" in path or "judgements" in path:
            raise AssertionError("case list / judgment detail must never be called")
        routes = {"crimes": CRIMES, "courts": COURTS, "info": {"count": 64733}, "crimes/stole/laws": LAWS,
                  "crimes/stole/factors": TREE, "crimes/search": SEARCH}
        if path == "crimes/search":
            bodies.append(json.loads(request.content))
        return httpx.Response(200, json=routes[path]) if path in routes else httpx.Response(404)
    return handler


async def test_sentencing_maps_names_to_ids():
    calls, bodies = [], []
    async with _client(_intellisen(calls, bodies)) as http:
        result = await sentencing.sentencing_statistics(
            http, "竊盜", laws=["第320條第1項"], courts=["台北地院"],
            factors={"累犯": ["是"], "行為人犯罪之動機": ["經濟窘迫"]}, year_from=110)
        assert bodies[0] == {"id": "stole", "year": {"min": 110, "max": 115}, "laws": ["stole_9_1"], "courts": ["TPD"],
                             "factors": [{"id": "stole_154", "value": ["stole_154_1"]},
                                         {"id": "stole_198_2", "value": ["stole_199"]}]}
        stats = result["statistics"]
        assert stats["total"] == 521 and stats["by_penalty"][0]["histograms"][0] == {
            "name": "有期徒刑", "unit": "月", "series": [[2, 3], [3.5, 9]]}
        assert result["query"]["factors"] == {"stole_154_1": "刑法第47條 累犯：是",
                                              "stole_199": "行為人犯罪之動機、目的：有具體內容／經濟窘迫"}
        assert "量刑基準" in result["note"]

        await sentencing.sentencing_statistics(http, "stole", laws=["stole_9_2"])
        assert bodies[1]["year"] == {"min": 96, "max": 115} and bodies[1]["laws"] == ["stole_9_2"]
        assert calls.count("crimes/stole/laws") == 1 and calls.count("crimes/stole/factors") == 1


async def test_sentencing_catalogue_and_clear_errors():
    async with _client(_intellisen([], [])) as http:
        catalogue = await sentencing.sentencing_statistics(http)
        assert catalogue["crimes"]["stole"] == "竊盜案件" and catalogue["judgments_in_system"] == 64733
        with pytest.raises(ValueError, match="罪名「殺人」不存在.*stole=竊盜案件"):
            await sentencing.sentencing_statistics(http, "殺人")
        with pytest.raises(ValueError, match="法條「第1項」不明確"):
            await sentencing.sentencing_statistics(http, "竊盜", laws=["第1項"])
        with pytest.raises(ValueError, match="法院「臺北」不明確"):
            await sentencing.sentencing_statistics(http, "竊盜", courts=["臺北"])
        with pytest.raises(ValueError, match="選項「也許」不存在.*stole_154_1=是"):
            await sentencing.sentencing_statistics(http, "竊盜", factors={"累犯": ["也許"]})

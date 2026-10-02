"""行政函釋與判解（#9）：解析、id、去重、來源選擇。HTTP 一律以 MockTransport 模擬，不連官方網站。"""

import json
from urllib.parse import parse_qsl, urlparse

import httpx
import pytest

import mcp_server.server as server
from mcp_server.cache.db import CacheDB
from mcp_server.tools import agency_interpretations as ai
from mcp_server.tools import fint


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_unwrap_joins_fixed_width_lines_but_keeps_paragraphs():
    raw = """主\xa0\xa0\xa0\xa0旨：有關某某
          一案。
說    明：一、復貴院函。
          二、按行政程序法第 46 條係規
              範特定程序。修正理由說
明：「債權人」"""
    assert fint.unwrap(raw) == (
        "主 旨：有關某某一案。\n說 明：一、復貴院函。\n二、按行政程序法第 46 條係規範特定程序。修正理由說明：「債權人」"
    )


@pytest.mark.parametrize("raw, iso", [
    ("114.11.25", "2025-11-25"), ("民國 110 年 05 月 17 日", "2021-05-17"), ("105/05/31", "2016-05-31"),
    ("2025/03/12", "2025-03-12"), ("20260828", "2026-08-28"), ("2022-01-05T00:00:00", "2022-01-05"),
])
def test_date_normalizes_roc_and_gregorian(raw, iso):
    assert ai._date(raw) == iso


def test_resolve_sources_maps_names_and_falls_back_to_gazette():
    assert ai.resolve_sources("") == (list(ai.SOURCES), [])
    assert ai.resolve_sources("勞委會") == (["mol"], [])
    assert ai.resolve_sources("金管會") == (["fsc"], [])
    keys, others = ai.resolve_sources("內政部,交通部")
    assert keys == ["ris", "nlma", "gazette"] and others == ["交通部"]


def test_mof_stamp_handles_undated_old_letters():
    assert ai._mof_stamp("本文。（財政部83/02/28台財稅第831585153號函）\n") == {
        "doc_number": "財政部83/02/28台財稅第831585153號函", "date": "1994-02-28"}
    assert ai._mof_stamp("本文。（財政部48台財稅發第6180號令）")["date"] == ""


def test_tipo_duplicate_case_numbers_get_distinct_ids():
    xml = "<DATAS>" + "".join(
        f"<著作權解釋資料><發布日期>{d}</發布日期><令函案號><![CDATA[智著字第1號]]></令函案號>"
        f"<令函要旨><![CDATA[<p>要旨{d}</p>]]></令函要旨></著作權解釋資料>"
        for d in ("20200101", "20210101")
    ) + "</DATAS>"
    rows = ai._tipo_parse(xml.encode())
    assert [r["id"] for r in rows] == ["tipo:智著字第1號@20200101", "tipo:智著字第1號@20210101"]
    reordered = ai._tipo_parse(xml.replace("20200101", "X").replace("20210101", "20200101").replace("X", "20210101").encode())
    assert {r["id"]: r["_text"] for r in reordered} == {r["id"]: r["_text"] for r in rows}  # 官方檔重排不影響 id
    assert rows[0]["date"] == "2020-01-01" and rows[0]["_text"] == "要旨20200101"


async def test_moj_search_and_detail():
    listing = """<table><tr><td>1.</td><td><div><b>發文字號：</b>
      <a href="LawContentExShow.aspx?id=FE393340&type=E&kw=x">法務部 法律字第 11403512580 號</a></div>
      <div><b>發文日期：</b><span>114.10.27</span></div>
      <div><b>要　　旨：</b><pre>人民申請閱覽或複印者，應視其是否為行政程序進行中之案卷而
適用不同之規定</pre></div></td></tr></table>資料類別：行政函釋 1 筆"""
    detail = """<div class="div-extent">
      <div class="col-row"><div class="col-th">發文單位：</div><div class="col-td">法務部</div></div>
      <div class="col-row"><div class="col-th">發文字號：</div><div class="col-td">法律字第 11403512580 號</div></div>
      <div class="col-row"><div class="col-th">發文日期：</div><div class="col-td">民國 114 年 10 月 27 日</div></div>
      <div id="cp_content_Erela"><ul><li><a href="#">行政程序法 第 46 條</a></li></ul></div>
      <div class="col-row" id="cp_content_EFULLtr"><div class="col-td"><pre>主    旨：復如說明。
說    明：一、復貴部函。
正    本：教育部
副    本：本部法律事務司</pre></div></div></div>"""

    def handler(request):
        if request.url.path == "/LawResult.aspx":
            return httpx.Response(200, text=listing if request.url.params["check"] == "etype5" else "")
        return httpx.Response(200, text=detail)

    async with _client(handler) as http:
        groups = await ai._moj_search(http, ai.Query(keyword="閱覽"))
        doc = await ai._moj_get(http, "FE393340")
    item = groups[0]["items"][0]
    assert item == {
        "id": "moj:FE393340", "agency": "法務部", "category": "行政函釋", "doc_number": "法律字第 11403512580 號",
        "date": "2025-10-27", "summary": "人民申請閱覽或複印者，應視其是否為行政程序進行中之案卷而適用不同之規定",
    }
    assert groups[0]["total"] == 1 and groups[1]["total"] == 0
    assert doc["related_laws"] == ["行政程序法 第 46 條"]
    assert doc["full_text"] == "主 旨：復如說明。\n說 明：一、復貴部函。"  # 正本、副本省略


async def test_mol_get_rejects_partial_word_match():
    """勞動部明細的字別是部分比對：號碼不同時不可回傳別件函釋。"""
    page = '<input id="cph_content_FDLink" value="E,N00000,勞動條 3,1100130999,20210517"/>'
    async with _client(lambda r: httpx.Response(200, text=page)) as http:
        with pytest.raises(LookupError):
            await ai._mol_get(http, "e:勞動條 3:1100130312")


async def test_gazette_query_keeps_keyword_field_logic_triples_in_order(monkeypatch):
    seen = {}

    def handler(request):
        seen["pairs"] = parse_qsl(urlparse(str(request.url)).query, keep_blank_values=True)
        return httpx.Response(200, text="共0筆資料")

    monkeypatch.setattr(ai, "_session", lambda: _client(handler))
    await ai._gazette_search(None, ai.Query(keyword="私募", agency_names=["金管會"]))
    triples = [p for p in seen["pairs"] if p[0] in ("keywords", "fields", "logics")]
    assert triples == [
        ("keywords", "私募"), ("fields", "text"), ("logics", "AND"),
        ("keywords", "金融監督管理委員會"), ("fields", "title"), ("logics", "AND"),
        ("keywords", "釋"), ("fields", "title"), ("logics", "AND"),
    ]


async def test_search_dedupes_same_letter_across_sources(tmp_path, monkeypatch):
    letter = {"agency": "法務部", "doc_number": "法律字第 11403512580 號", "date": "2025-10-27", "summary": "x"}

    async def own(http, q):
        return [ai._group("法務部", "行政函釋", 1, [{"id": "moj:FE1", "category": "行政函釋", **letter}], False)]

    async def mirror(http, q):
        return [ai._group("司法院", "行政函釋", 1, [{"id": "fint:E:FE1", "category": "行政函釋", **letter}], False)]

    async def broken(http, q):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(ai, "SOURCES", {
        "moj": ("法務部", ("法務部",), own, None),
        "fint": ("司法院", ("司法院",), mirror, None),
        "pcc": ("工程會", ("工程會",), broken, None),
    })
    cache = CacheDB(db_path=tmp_path / "c.db")
    await cache.initialize()
    client = ai.AgencyInterpretationClient(cache)
    try:
        r = await client.search("閱覽", "", 0, 0, "", 1)
        assert [x["id"] for x in r["results"]] == ["moj:FE1"]
        assert any("error" in g for g in r["categories"])
        assert not await cache.get_search({"tool": "agency_interpretations"})  # 有來源失敗時不快取
    finally:
        await client.close()
        await cache.close()


async def test_fint_search_parses_tabs_and_rows(monkeypatch):
    form = '<input type="hidden" name="__VIEWSTATE" value="v"/>'
    result = ('<div id="result-count"><a data-code="D" href="qryresultlst.aspx?ty=D&q=abc">決議'
              '<span class="badge">21</span></a></div>')
    listing = """<table class="int-table"><tr><td>1.</td><td></td><td>
      <div class="row"><div class="col-th">會議次別：</div><div class="col-td">
        <a id="hlTitle" href="data.aspx?id=A%2c20170214%2c001&ro=1&ty=D&q=abc">最高法院 106 年度第 3 次民事庭會議</a></div></div>
      <div class="row"><div class="col-th">決議日期：</div><div class="col-td">民國 106 年 02 月 14 日</div></div>
      <div class="row"><div class="col-th">決議要旨：</div><div class="col-td text-pre">採甲說（有權處分說）。</div></div>
    </td></tr></table>"""
    posted = {}

    def handler(request):
        if request.method == "POST":
            posted.update(parse_qsl(request.content.decode()))
            return httpx.Response(200, text=result)
        if "qryresultlst" in request.url.path:
            assert request.url.params["page"] == "2"
            return httpx.Response(200, text=listing)
        return httpx.Response(200, text=form)

    real = httpx.AsyncClient
    monkeypatch.setattr(fint.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    (group,) = await fint.search("借名登記", ["決議"], 100, 110, page=2)
    assert posted["txtKW"] == "借名登記" and posted["dtype"] == "C" and posted["txtY1"] == "100"
    assert group["total"] == 21 and group["has_more"] is False
    assert group["items"] == [{
        "id": "D:A,20170214,001", "title": "最高法院 106 年度第 3 次民事庭會議",
        "date": "2017-02-14", "summary": "採甲說（有權處分說）。",
    }]


async def test_tools_validate_arguments():
    assert (await server.search_agency_interpretations())["success"] is False
    assert (await server.search_agency_interpretations(keyword="x", page=0))["success"] is False
    assert (await server.search_precedents())["success"] is False


def test_sources_have_unique_codes_and_callables():
    for key, (label, aliases, search, get) in ai.SOURCES.items():
        assert callable(search) and callable(get), key
    assert json.dumps(list(ai.SOURCES))  # 代碼都是字串，可當 id 前綴


async def test_fint_error_page_is_an_error_not_zero_hits(monkeypatch):
    real = httpx.AsyncClient
    monkeypatch.setattr(fint.httpx, "AsyncClient", lambda **kw: real(
        transport=httpx.MockTransport(lambda r: httpx.Response(503, text="maintenance")), **kw))
    with pytest.raises(httpx.HTTPStatusError):
        await fint.search("借名登記", ["決議"])


async def test_gazette_queries_each_agency_and_number_field(monkeypatch):
    queries = []

    def handler(request):
        queries.append([p for p in parse_qsl(urlparse(str(request.url)).query, keep_blank_values=True)
                        if p[0] == "keywords"])
        return httpx.Response(200, text="共0筆資料")

    monkeypatch.setattr(ai, "_session", lambda: _client(handler))
    groups = await ai._gazette_search(None, ai.Query(keyword="私募", number="1150001", agency_names=["金管會", "交通部"]))
    assert [g["category"] for g in groups] == ["解釋性規定及裁量基準（金管會）", "解釋性規定及裁量基準（交通部）"]
    assert [q[1][1] for q in queries] == ["金融監督管理委員會", "交通部"]
    assert queries[0][2] == ("keywords", "1150001")  # 有字號時第三組改比對公報字號，不丟掉關鍵字


def test_dedupe_key_keeps_different_agencies_with_same_serial():
    a = {"id": "moj:1", "date": "2025-01-02", "doc_number": "法律字第 1140000001 號"}
    b = {"id": "mol:1", "date": "2025-01-02", "doc_number": "勞動條字第1140000001號函"}
    c = {"id": "fint:E:1", "date": "2025-01-02", "doc_number": "法律字第1140000001號"}
    assert ai._dedupe_key(a) != ai._dedupe_key(b)
    assert ai._dedupe_key(a) == ai._dedupe_key(c)


async def test_snapshot_keeps_old_copy_when_download_is_corrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(ai, "USER_DATA_DIR", tmp_path)
    good = ("<DATAS><著作權解釋資料><發布日期>20200101</發布日期><令函案號>智著字第1號</令函案號>"
            "<令函要旨>合理使用</令函要旨></著作權解釋資料></DATAS>")
    snap = ai._Snapshot("https://example.invalid/x.xml", "t.xml", ai._tipo_parse, min_rows=1, refresh=0)
    snap.path.write_text(good, "utf-8")
    async with _client(lambda r: httpx.Response(200, text="<html>維護中")) as http:
        rows = await snap.load(http)
    assert [r["id"] for r in rows] == ["tipo:智著字第1號@20200101"]
    assert snap.path.read_text("utf-8") == good


async def test_unexpected_source_failure_stays_isolated(tmp_path, monkeypatch):
    async def ok(http, q):
        return [ai._group("法務部", "行政函釋", 0, [], False)]

    async def buggy(http, q):
        raise RuntimeError("site redesign")

    monkeypatch.setattr(ai, "SOURCES", {"moj": ("法務部", (), ok, None), "tipo": ("智慧局", (), buggy, None)})
    cache = CacheDB(db_path=tmp_path / "c.db")
    await cache.initialize()
    client = ai.AgencyInterpretationClient(cache)
    try:
        r = await client.search("x", "", 0, 0, "", 1)
        assert r["success"] and "RuntimeError" in r["categories"][1]["error"]
    finally:
        await client.close()
        await cache.close()


@pytest.mark.parametrize("raw, digits", [
    ("台財稅發第6180號令", "6180"), ("（77）台勞保二字第 31239 號函", "31239"),
    ("法律字第11403512580號", "11403512580"), ("1150001", "1150001"), ("", ""),
])
def test_number_digits_keeps_short_historical_numbers(raw, digits):
    assert ai.Query(number=raw).number_digits == digits


async def test_ris_zero_filtered_hits_is_zero(monkeypatch):
    def handler(request):
        if request.url.path.endswith("/toMain"):
            return httpx.Response(200, text='<meta name="_csrf" content="t"/>')
        return httpx.Response(200, json={"recordsTotal": 1444, "recordsFiltered": 0, "data": []})

    monkeypatch.setattr(ai, "_session", lambda: _client(handler))
    (group,) = await ai._ris_search(None, ai.Query(keyword="不存在的詞"))
    assert group["total"] == 0 and group["has_more"] is False


async def test_fint_interpretation_keeps_editor_note(monkeypatch):
    async def fake_get(http, item_id):
        return {"fields": {"發文單位": "司法院", "發文字號": "院台廳民一字第1號", "發文日期": "民國 90 年 01 月 02 日",
                           "要旨": "x", "編註": "本函自 110 年起停止適用"},
                "full_text": "主 旨：x", "related_laws": [], "attachments": [], "source_url": "u"}

    monkeypatch.setattr(ai.fint, "get", fake_get)
    doc = await ai._fint_get(None, "E:FE1")
    assert doc["notes"] == "本函自 110 年起停止適用" and doc["date"] == "2001-01-02"

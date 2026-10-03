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
    keys, others = ai.resolve_sources("內政部,客委會")
    assert keys == ["ris", "nlma", "land", "nfa", "moi", "gazette"] and others == ["客委會"]


@pytest.mark.parametrize("agency, keys", [
    ("銓敘部", ["mocs"]), ("保訓會", ["csptc"]), ("公務人員保障暨培訓委員會", ["csptc"]), ("考選部", ["moex"]),
    ("考試院", ["mocs", "csptc", "moex", "exam"]), ("地政司", ["land"]), ("內政部地政司", ["land"]),
    ("交通部", ["motc"]), ("央行", ["cbc"]), ("教育部", ["moe"]), ("國科會", ["nstc"]), ("公平會", ["ftc"]),
    ("台北市", ["taipei"]), ("北市", ["taipei"]), ("消防署", ["nfa"]),
])
def test_resolve_sources_routes_agencies_to_their_own_systems(agency, keys):
    assert ai.resolve_sources(agency) == (keys, [])


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


async def test_exec_family_reads_exam_and_moenv_rows():
    """考試院的連結在發文字號、環境部在標題；機關依 Ncid 分類決定。"""
    exam = """<table><tr><td>1.</td><td>
      <div id="x_ctl01_divTitle"><span class="co-th"><b>標  題：</b></span><span class="co-td"><i></i>有關<mark>考績</mark>委員會</span></div>
      <div><span class="co-th"><b>發文字號：</b></span><span class="co-td"><span>
        <a href="ExecutiveData.aspx?id=15292&type=2&KW=x" id="x_ctl01_aOdWord">公評字第11422602041號函</a></span></span></div>
      <div><span class="co-th"><b>發文日期：</b></span><span class="co-td"><span>114.08.21</span></span></div></td></tr></table>"""
    moenv = """<table><tr><td>1.</td><td>
      <div id="x_ctl01_divTitle"><span class="co-th"><b>標　　題：</b></span><span class="co-td">
        <a href="ExecutiveData.aspx?id=5336&type=2" id="x_ctl01_aLType">關於<mark>廢棄物</mark>清理法</a></span></div>
      <div><span class="co-th"><b>發文字號：</b></span><span class="co-td"><span>環部授循字第1156014073號函</span></span></div>
      <div><span class="co-th"><b>發文日期：</b></span><span class="co-td"><span>115.09.03</span></span></div></td></tr></table>
      共 1426 筆，頁次：1"""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=exam if request.url.host == "law.exam.gov.tw" else moenv)

    async with _client(handler) as http:
        (g1,) = await ai.SOURCES["csptc"][2](http, ai.Query(keyword="考績", start="20250101"))
        (g2,) = await ai.SOURCES["moenv"][2](http, ai.Query(keyword="廢棄物"))
    assert seen[0].params["Ncid"] == "04" and seen[0].params["StartDate"] == "20250101"  # 網址參數用西元
    assert g1["items"] == [{"id": "csptc:15292", "agency": "公務人員保障暨培訓委員會", "category": "行政函釋",
                            "doc_number": "公評字第11422602041號函", "date": "2025-08-21", "summary": "有關考績委員會"}]
    assert g1["total"] == 1 and g2["total"] == 1426 and g2["items"][0]["summary"] == "關於廢棄物清理法"


async def test_lawsys_family_is_config_per_site():
    listing = """<table class="tab-result"><tr><td>1.</td><td>115.07.27</td><td>
      <a id="x_hlkLawName" href="LawContent.aspx?id=GL002412&kw=x">各級學校<span>教師</span>解釋令</a></td><td>令</td></tr></table>
      法規類別 全部 121"""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=listing)

    async with _client(handler) as http:
        (group,) = await ai.SOURCES["moe"][2](http, ai.Query(keyword="教師", number="1155402068"))
        with pytest.raises(LookupError):
            await ai.SOURCES["moe"][3](http, "GL1&x=1")
    assert len(seen) == 1 and seen[0].host == "edu.law.moe.gov.tw" and seen[0].params["LNumber"] == "1155402068"
    assert group["items"][0] == {"id": "moe:GL002412", "agency": "教育部", "category": "行政規則（含解釋令、函）",
                                 "doc_number": "", "date": "2026-07-27", "summary": "各級學校教師解釋令"}
    assert group["total"] == 121 and group["has_more"] is True


_LAND = """<div class="pagebox"><div class="page">目前在第 1 頁 / 共有 <p> 3 </p> 筆</div></div>
<div class='main3box'><div class='main_title'><a href='#'>土地法 《第54條》</a></div>
 <div class='main_span'><table><tr><td><strong class='icon_t'>最新法規條文</strong></td></tr></table></div>
 <div class='main_span'> <STRONG class='icon_t'>解釋函</STRONG></div>
 <div class='main_span'><span>公布日期文號</span>內政部90年1月16日台內地字第8918054號函</div>
 <div class='main_span'><span>要旨</span>占有人主張<STRONG class='keyword'> 時效取得 </STRONG>所有權</div>
 <div class='main_span'><span>內容</span><br>一、按土地法。<br/>二、應予受理。</div>
 <div class='main_span'> <STRONG class='icon_t'>解釋函</STRONG></div>
 <div class='main_span'><font color="990000">已停止適用/廢止</font></div>
 <div class='main_span'><span>停止適用日期文號</span>內政部中華民國九十二年四月二十九日台內地字第０九二００六九九三七號函</div>
 <div class='main_span'><span>公布日期文號</span>內政部八十八年六月七日台（八八）內地字第八八○六九九八號函</div>
 <div class='main_span'><span>要旨</span>地上權位置勘測</div>
</div>
<div class='main3box'><div class='main_title'><a href='#'>民法 《第769條》</a></div>
 <div class='main_span'> <STRONG class='icon_t'>解釋函</STRONG></div>
 <div class='main_span'><span>公布日期文號</span>內政部90年1月16日台內地字第8918054號函</div>
 <div class='main_span'><span>要旨</span>占有人主張時效取得所有權</div>
</div>"""


async def test_land_merges_letter_listed_under_several_articles():
    async with _client(lambda r: httpx.Response(200, text=_LAND)) as http:
        (group,) = await ai._land_search(http, ai.Query(keyword="時效取得", start="20200101"))
        doc = await ai._land_get(http, "8918054:2001-01-16")
        with pytest.raises(LookupError):
            await ai._land_get(http, "8918054:2001-01-17")  # 同號不同日期是另一件
    first, old = group["items"]
    assert first == {"id": "land:8918054:2001-01-16", "agency": "內政部", "date": "2001-01-16",
                     "doc_number": "台內地字第8918054號函", "category": "地政法令解釋函", "summary": "占有人主張時效取得所有權",
                     "related_laws": ["土地法 《第54條》", "民法 《第769條》"], "notes": ""}
    assert old["id"] == "land:8806998:1999-06-07" and old["doc_number"] == "台（八八）內地字第8806998號函"
    assert old["notes"] == "已停止適用/廢止；停止適用：內政部92年4月29日台內地字第0920069937號函"
    assert group["total"] == 3 and "日期篩選未套用" in group["note"]
    assert doc["full_text"] == "一、按土地法。\n二、應予受理。" and "Etext=8918054" in doc["source_url"]


async def test_motc_total_from_last_page_link_and_roc_dates(monkeypatch):
    listing = """<table class="list-result"><tr><td>1.</td><td>
      <a href="/webMotcLaw2018/SLaw/Content?soid=13975&amp;cKeyword=x" title="t">交通部公路局 114.04.25.  路監交字第1145008025號函</a>
      </td></tr></table><a href="/webMotcLaw2018/SLaw/List?page=2&cKeyword=x">下一頁</a>
      <a href="/webMotcLaw2018/SLaw/List?page=3&cKeyword=x">最末頁</a>"""
    detail = """<main id="mainContent"><div class="con-area-top"><p><span>交通部 115.09.18.  交運字第11550128111號公告</span></p></div>
      <div class="con-area-middle con-explain"><p>公告委任交通部公路局辦理</p><pre>主旨：公告委任交通部公路局辦理外
      送員管理事項。
正本：交通部公路局</pre></div></main>"""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=listing if request.url.path.endswith("/List") else detail)

    monkeypatch.setattr(ai, "_motc_http", lambda: _client(handler))  # 交通部用自帶中繼憑證的獨立連線
    (group,) = await ai._motc_search(None, ai.Query(keyword="汽車運輸業", start="20240101", end="20251231"))
    doc = await ai._motc_get(None, "14124")
    assert seen[0].params["startDate"] == "1130101" and seen[0].params["endDate"] == "1141231"
    assert group["items"][0] == {"id": "motc:13975", "agency": "交通部公路局", "date": "2025-04-25",
                                 "doc_number": "路監交字第1145008025號函", "category": "行政解釋", "summary": ""}
    assert group["total"] == 75 and group["has_more"] is True
    assert doc["agency"] == "交通部" and doc["summary"] == "公告委任交通部公路局辦理"
    assert doc["full_text"] == "主旨：公告委任交通部公路局辦理外送員管理事項。"


async def test_cbc_needs_category_boxes_and_drops_recipients():
    listing = """<select id="currentPageChange"><option value="1">1</option><option value="16">16</option></select>
      <table class="list-result"><tr><td>1.</td><td align="left">
      <a href="/SOrder/SOrder/6?soid=7397"><label>(停)</label><span>【合理經營業務】建立進出
口押匯分戶卡</span></a><br />中央銀行68.03.26. 金融業務檢查處臺央檢字第419號函\x1a</td></tr></table>"""
    detail = """<div class="letters-page-content"><div class="jumbotron"><p><div><b>要旨：</b>調整準備金利率</div></p>
      <p><b>發文字號：</b>中央銀行業務局111年12月22日台央業字第1110046644號函</p></div>
      <div class="letters-desc-text">
        <div class="row"><div>主　　旨：</div><div><p>利率調整如說明。</p></div></div>
        <div class="row"><div>說　　明：</div><div><p>一、活期 0.396%。</p><p>二、不給付利息。</p></div></div>
        <div class="row"><div>正　　本：</div><div>本國銀行</div><div>副　　本：</div><div>金管會</div></div>
      </div><div class="attact-files-div"><ol></ol></div></div>"""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=listing if "SearchAgain" in request.url.path else detail)

    async with _client(handler) as http:
        (group,) = await ai._cbc_search(http, ai.Query(keyword="外匯"))
        doc = await ai._cbc_get(http, "465")
    assert seen[0].params.get_list("criteria.lawCheckBoxs") == list(ai._CBC_TYPES)
    assert group["items"] == [{"id": "cbc:6", "agency": "中央銀行", "date": "1979-03-26",
                               "doc_number": "金融業務檢查處臺央檢字第419號函", "category": "行政令函",
                               "summary": "(停)【合理經營業務】建立進出口押匯分戶卡"}]
    assert group["total"] == 160 and group["has_more"] is True
    assert doc["agency"] == "中央銀行業務局" and doc["date"] == "2022-12-22" and doc["summary"] == "調整準備金利率"
    assert doc["full_text"] == "主旨：利率調整如說明。\n說明：一、活期 0.396%。\n二、不給付利息。"


async def test_taipei_queries_city_and_central_categories():
    listing = """<p>共41筆，共3頁，目前第1頁</p><ul class="fx-list">
      <li class="num"><a href="/Law/Interpretation/Content/FE358100?curcateid=002">臺北市政府法務局 111.04.28 北市法二字第1113016023號函</a></li>
      <li class="pre">市有土地占建物說明</li></ul>"""
    detail = """<h3 class="small-subject row"><span>工務類</span></h3><article class="interpretation-content">
      <div class="row"><div class="col-title">發文字號：</div><div class="col-data">臺北市政府法務局 111.04.28 北市法二字第1113016023號函</div></div>
      <div class="row"><div class="col-title">發文日期：</div><div class="col-data">民國 111 年 04 月 28 日</div></div>
      <div class="row"><div class="col-title">要　　旨：</div><div class="col-data"><pre>市有土地占建物說
明</pre></div></div>
      <div class="row"><div class="col-data"><pre title="內容">主旨：復如說明。
正本：臺北市政府工務局</pre></div></div></article>"""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=listing if "SearchResult" in request.url.path else detail)

    async with _client(handler) as http:
        groups = await ai._taipei_search(http, ai.Query(keyword="違章建築 拆除", start="20200101"))
        doc = await ai._taipei_get(http, "FE358100")
    assert [u.params["curcateid"] for u in seen[:2]] == ["002", "003"]
    assert seen[0].params["SearchString.Keyword2"] == "拆除" and seen[0].params["DateRange.DateFrom"] == "20200101"
    assert groups[0]["items"][0] == {"id": "taipei:FE358100", "agency": "臺北市政府法務局", "date": "2022-04-28",
                                     "doc_number": "北市法二字第1113016023號函", "category": "解釋令函（臺北市）",
                                     "summary": "市有土地占建物說明"}
    assert groups[0]["total"] == 41 and groups[0]["has_more"] is True
    assert doc["summary"] == "市有土地占建物說明" and doc["full_text"] == "主旨：復如說明。" and doc["notes"] == "工務類"


async def test_nfa_lists_titles_and_returns_attachments():
    listing = """<table><tr id="trRow"><td>108/10/18</td><td><a href="?type=d">法令解釋</a></td><td></td>
      <td><a href="news.aspx?id=1861">函詢防火管理人複訓疑義案   </a></td></tr></table>"""
    detail = """<table><tr><th>摘 要：</th><td>函詢防火管理人複訓疑義案</td></tr>
      <tr><th>發布機關：</th><td>內政部</td></tr><tr><th>公發布日：</th><td>108/10/18</td></tr>
      <tr><th>文 號：</th><td>內授消字第1080824190號函</td></tr>
      <tr><th>檔案下載：<br></th><td><li><a href="downloadFile.aspx?sdMsgId=1861&FileId=1968">函</a></li></td></tr></table>"""

    def handler(request):
        return httpx.Response(200, text=listing if request.url.path.endswith("index.aspx") else detail)

    async with _client(handler) as http:
        (group,) = await ai._nfa_search(http, ai.Query(keyword="防火管理"))
        doc = await ai._nfa_get(http, "1861")
        (by_number,) = await ai._nfa_search(http, ai.Query(number="1080824190"))
    assert group["items"] == [{"id": "nfa:1861", "agency": "內政部消防署", "category": "法令解釋", "doc_number": "",
                               "date": "2019-10-18", "summary": "函詢防火管理人複訓疑義案"}]
    assert group["total"] == 1 and group["has_more"] is False
    assert doc["doc_number"] == "內授消字第1080824190號函" and doc["full_text"] == ""
    assert doc["attachments"][0]["url"].endswith("downloadFile.aspx?sdMsgId=1861&FileId=1968")
    assert by_number["total"] == 0 and "note" in by_number

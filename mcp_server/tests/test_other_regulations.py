"""地方法規、條約協定、交易所規章：列表／全文解析、條號篩選、id 驗證。HTTP 一律以 MockTransport 模擬。"""

from urllib.parse import parse_qsl

import httpx
import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.tools import other_regulations as orx

S = orx.SOURCES


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=f"<html><body>{body}</body></html>")


# ── 共用工具 ────────────────────────────────────────────────

@pytest.mark.parametrize("raw, no", [
    ("第 15 條之 1", "15-1"), ("十五之一", "15-1"), ("15-1", "15-1"), ("第一百零五條", "105"),
    ("第　二　條", "2"), ("第廿七條", "27"), ("第 5 條", "5"), ("Article 1", "Article 1"),
])
def test_article_no(raw, no):
    assert orx._article_no(raw) == no


def test_split_articles_joins_wrapped_lines_and_skips_chapters():
    lines = ["前言不收", "第一章　總則", "第一條　　臺中市為改善殯葬文化，加強", "  殯葬設施之管理，特制定本自治",
             "  條例。", "第二條 &nbsp;本自治條例之主管機關為民政局(以下簡稱", "  民政局)", "  。",
             "  民政局得委任所屬機關執行，並依建築法", "第十六條所定之程序辦理：", "一 拆除者。", "二 全倒者。",
             "第三條之一　（刪除）"]
    lines[5] = lines[5].replace("&nbsp;", "  ")
    assert orx._split_articles(lines) == [
        {"number": "1", "content": "臺中市為改善殯葬文化，加強殯葬設施之管理，特制定本自治條例。"},
        {"number": "2", "content": "本自治條例之主管機關為民政局(以下簡稱民政局)。\n"
                                   "民政局得委任所屬機關執行，並依建築法第十六條所定之程序辦理：\n一 拆除者。\n二 全倒者。"},
        {"number": "3-1", "content": "（刪除）"},
    ]


def test_join_keeps_english_word_spacing():
    assert orx._join(["The Agreement shall", "apply to persons", "1. who are residents"]) == \
        "The Agreement shall apply to persons\n1. who are residents"


def test_resolve_sources_aliases():
    assert orx.resolve_sources("台中") == ["taichung"]
    assert orx.resolve_sources("北市、新北") == ["taipei", "ntpc"]
    assert orx.resolve_sources("中市,高市,南市") == ["taichung", "kaohsiung", "tainan"]
    assert orx.resolve_sources("新竹") == ["hsinchu_county", "hsinchu_city"]
    assert orx.resolve_sources("外交部") == ["mofa"]
    assert orx.resolve_sources("租稅協定") == ["mof_tax"]
    assert set(orx.resolve_sources("條約")) == {"moj_treaty", "mofa"}
    assert orx.resolve_sources("證交所 櫃買中心 期交所") == ["twse", "tpex", "taifex"]
    assert orx.resolve_sources("桃園") == ["taoyuan"] and orx.resolve_sources("馬祖") == ["lienchiang"]
    assert orx.resolve_sources("雲林") == ["yunlin"]
    assert orx.resolve_sources("") == list(S)


def test_shape_selects_articles_and_outlines_split_texts():
    data = {"title": "X", "articles": [{"number": n, "content": n * 3} for n in ("1", "2", "3", "15-1")]}
    assert orx._shape(data, "第二條")["articles"] == [{"number": "2", "content": "222"}]
    assert [a["number"] for a in orx._shape(data, "一至三, 十五之一")["articles"]] == ["1", "2", "3", "15-1"]
    picked = orx._shape(data, "2,9")
    assert [a["number"] for a in picked["articles"]] == ["2"] and picked["missing"] == ["9"]
    with pytest.raises(LookupError):
        orx._shape(data, "9")
    with pytest.raises(ValueError):
        orx._shape(data, "abc")
    outline = orx._shape(data, "")  # 分條的規範沒指定條號：只回條號範圍
    assert "articles" not in outline and (outline["article_count"], outline["last_article"]) == (4, "15-1")
    split = orx._shape({"title": "T", "full_text": "前言\n第一條 甲。\n第二條 乙。"}, "")
    assert "full_text" not in split and split["article_count"] == 2 and split["preamble"] == "前言"
    nested = {"title": "T", "articles": [{"number": n, "content": n} for n in ("2", "2-1", "2-1-1", "3")]}
    assert [a["number"] for a in orx._shape(nested, "2-1-1")["articles"]] == ["2-1-1"]
    assert [a["number"] for a in orx._shape(nested, "2-1~2-2")["articles"]] == ["2-1", "2-1-1"]
    labeled = {"title": "T", "articles": [{"number": n, "content": n} for n in ("壹", "貳")]}
    assert orx._shape(labeled, "")["article_numbers"] == ["壹", "貳"]
    assert orx._shape(labeled, "貳")["articles"] == [{"number": "貳", "content": "貳"}]
    full = orx._shape({"title": "T", "full_text": "前言\n第一條 甲。\n第二條 乙。"}, "2")
    assert full["articles"] == [{"number": "2", "content": "乙。"}] and "full_text" not in full
    raw = "一、要點。" * 20000  # 未分條的要點、條約：照原樣回傳全文
    assert orx._shape({"full_text": raw}, "")["full_text"] == raw
    with pytest.raises(LookupError, match="未分條"):
        orx._shape({"title": "T", "full_text": "一、要點。"}, "1")


# ── 主管法規共用系統 ────────────────────────────────────────

GLRS_LIST = """
<ul class="nav myTab"><li class="active"><a>法規類別 全部<span class="badge">13</span></a></li></ul>
<table class="tab-result">
<tr><th>序</th><th>資料日期</th><th>法規名稱</th><th>位階</th></tr>
<tr><td>1.</td><td> 113.07.19</td><td><a id="x_hlkLawName" href="LawContent.aspx?id=GL001566&amp;kw=k">高雄市臨時性
<span>違章建築</span>管理辦法</a><div id="x_divHLawName">原名稱：舊名</div></td><td> 自治規則 </td></tr>
</table>"""

GLRS_TABLE = """
<table class="table tab-edit"><tr><th>法規名稱：</th><td>臺中市殯葬管理自治條例</td></tr>
<tr><th>公發布日：</th><td>民國 101 年 06 月 12 日<div id="x_divCauseStatusAnnDate" class="text-danger"></div></td></tr>
<tr><th>修正日期：</th><td>民國 114 年 07 月 23 日</td></tr><tr><th>法規體系：</th><td>臺中市法規/民政類/自治條例</td></tr></table>
<div id="ctl00_cp_content_divContent"><table id="ctl00_cp_content_tableLawArticleBasic">
<tr><td class="law-char">第一章　總則</td></tr>
<tr><td></td><td><div class="ClearCss"><span>第一條　　臺中市為改善殯葬文化，<br>
&nbsp; &nbsp; 特制定本自治條例。</span></div></td></tr>
<tr><td></td><td><div class="ClearCss"><span>第二條 &nbsp; &nbsp;主管機關為民政局。</span></div></td></tr>
</table></div>"""

GLRS_BLOCK = """
<table class="tab-edit"><tr><th>法規名稱：</th><td>高雄市某要點</td></tr><tr><th>公發布日：</th><td>民國 105 年 01 月 07 日</td></tr>
<tr><th>法規體系：</th><td>工務局</td></tr></table>
<div id="ctl00_cp_content_divContent"><div class="ClearCss"><span>一、為辦理某事，<br>
特訂定本要點。<br><br>二、本要點主管機關為工務局。<br></span></div></div>"""


async def test_glrs_search_uses_get_endpoint_without_zero_params():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(parse_qsl(request.url.query.decode()))
        assert request.url.path == "/LawResult.aspx"
        return _html(GLRS_LIST)

    async with _client(handler) as http:
        g = await S["kaohsiung"][2](http, "違章建築", 2)
    assert seen["GroupID"] == "6,7,2" and seen["page"] == "2" and "0" not in seen.values()
    assert g["total"] == 13 and not g["has_more"]
    assert g["items"] == [{"id": "GL001566", "title": "高雄市臨時性違章建築管理辦法", "issuer": "高雄市",
                           "category": "自治規則", "date": "2024-07-19"}]


async def test_glrs_get_table_and_block_layouts():
    pages = {"GL001385": GLRS_TABLE, "GL001111": GLRS_BLOCK}

    async def get(native):
        async with _client(lambda r: _html(pages[r.url.params["id"]])) as http:
            return await S["taichung"][3](http, native)

    d = await get("GL001385")
    assert (d["title"], d["category"], d["date"]) == ("臺中市殯葬管理自治條例", "自治條例", "2025-07-23")
    assert d["articles"] == [{"number": "1", "content": "臺中市為改善殯葬文化，特制定本自治條例。"},
                             {"number": "2", "content": "主管機關為民政局。"}]
    assert "status" not in d and d["source_url"].endswith("LawContent.aspx?id=GL001385")
    d = await get("GL001111")
    assert d["category"] == "行政規則" and d["date"] == "2016-01-07"
    assert d["full_text"] == "一、為辦理某事，特訂定本要點。\n二、本要點主管機關為工務局。" and "articles" not in d


@pytest.mark.parametrize("key, bad", [
    ("taichung", "GL001385&x=1"), ("taichung", "../GL001385"), ("taipei", "FL1"), ("ntpc", "1C0170177"),
    ("moj_treaty", "B0000001"), ("mofa", "../x.pdf"), ("mofa", "26/1.pdf"), ("mof_tax", "../10422"),
    ("twse", "FL007304'"), ("tpex", "LW1"), ("taifex", "lw10812093"),
])
async def test_get_rejects_bad_ids_without_requests(key, bad):
    def handler(request):
        raise AssertionError("不應發出請求")

    async with _client(handler) as http:
        with pytest.raises(LookupError):
            await S[key][3](http, bad)


async def test_glrs_get_missing_record_raises():
    async with _client(lambda r: _html("<p>查無資料</p>")) as http:
        with pytest.raises(LookupError):
            await S["tainan"][3](http, "GL999999")


# ── 臺北市、新北市 ───────────────────────────────────────────

TAIPEI_LIST = """<p>共 87 筆，共 5 頁</p><table>
<tr><td>1.</td><td><a class="law-link" href="/Law/LawSearch/LawArticleContentResult/FL058967?x=1" title="連結至臺北市違章建築處理規則">臺北市<em>違章建築</em>處理規則</a></td></tr>
<tr><td>2.</td><td><a class="law-link" href="/Law/LawSearch/LawInformation/FL028795?x=1" title="連結至臺北市某作業規定"><span class="abolished">(廢)</span>臺北市某作業規定</a></td></tr>
</table>"""

TAIPEI_LAW = """<div class="info-upper">
<div class="form-group"><label>名　　稱</label><div class="col-input">{name}</div></div>
<div class="form-group"><label>法規位階</label><div class="col-input">{level}</div></div>
<div class="form-group"><label>修正日期</label><div class="col-input">民國 110 年 12 月 23 日</div></div></div>
<ul class="law law-content">{items}</ul>"""


async def test_taipei_search_and_get():
    def handler(request: httpx.Request) -> httpx.Response:
        if "SearchResult" in request.url.path:
            assert request.url.params["cursearchtype"] == "2" and request.url.params["page"] == "1"
            return _html(TAIPEI_LIST)
        if request.url.path.endswith("FL058967"):
            return _html(TAIPEI_LAW.format(name="臺北市違章建築處理規則", level="自治規則", items="""
                <li class="chapter-2">第一章 總則</li>
                <li><div class="row"><div class="col-no">第 1 條</div><div class="col-data"><div class="law-articlepre">臺北市政府為實施建築管理，有效執行違章
建築之處理規定，特訂定本規則。</div></div></div></li>
                <li><div class="row"><div class="col-no">第 2-1 條</div><div class="col-data"><div class="law-articlepre">主管機關為都發局。</div></div></div></li>"""))
        if request.url.path.endswith("FL028795"):
            return _html(TAIPEI_LAW.format(name="(廢) 臺北市某作業規定", level="行政規則：屬行政程序法第159條",
                                           items="<li>一、為處理違建，\n    特訂定本規定。</li><li>二、本規定自即日生效。</li>"))
        return _html("<div class='info-upper'></div>")

    async with _client(handler) as http:
        g = await S["taipei"][2](http, "違章建築", 1)
        assert g["total"] == 87 and g["has_more"]
        assert [(i["id"], i["title"], i["category"], i.get("status")) for i in g["items"]] == [
            ("FL058967", "臺北市違章建築處理規則", "自治規則或委辦規則", None),
            ("FL028795", "臺北市某作業規定", "行政規則", "已廢止")]
        d = await S["taipei"][3](http, "FL058967")
        assert (d["category"], d["date"]) == ("自治規則", "2021-12-23")
        assert d["articles"] == [{"number": "1", "content": "臺北市政府為實施建築管理，有效執行違章建築之處理規定，特訂定本規則。"},
                                 {"number": "2-1", "content": "主管機關為都發局。"}]
        d = await S["taipei"][3](http, "FL028795")
        assert d["title"] == "臺北市某作業規定" and d["status"] == "已廢止" and d["category"] == "行政規則"
        assert d["full_text"] == "一、為處理違建，特訂定本規定。\n二、本規定自即日生效。"
        with pytest.raises(LookupError):
            await S["taipei"][3](http, "FL999999")


NTPC_LIST = """<table class="tab-list2">
<tr><td>[3]</td><td><a id="cph_content_rptLegislationB_hlkQuery1A_0" href="FLAWQRY03.aspx?fname=1B0070002&amp;K1=x">都市更新條例</a></td></tr>
<tr><td>[4]</td><td><a id="cph_content_rptLegislationC_hlkQuery1A_0" href="FLAWQRY03.aspx?fname=1C0170177&amp;K1=x">新北市強制拆除違章建築收費自治條例</a></td></tr>
<tr><td>[9]</td><td><a id="cph_content_rptLegislationC_hlkQuery1A_1" href="FLAWQRY03.aspx?fname=1C0170009&amp;K1=x">新北市舊有違章建築修繕辦法</a></td></tr>
</table>"""

NTPC_LAW = """<table class="my-table"><tr class="sub-title"><th>法規名稱：</th><td id="cph_content_lawheader_law">
<a href="/Scripts/FLAWDAT01.aspx?lncode=1C0170009">新北市舊有違章建築修繕辦法</a> (民國 100 年 02 月 18 日 公發布)</td></tr></table>
<table class="my-table tab-law01">
<tr><td class="col-th"><a href="FLAWDOC01.aspx?fcode=C0170009&amp;flno=4">第  4  條</a></td><td class="col-td"><pre>下列之舊違章建築，不得申請修繕：
一  拆除或整理計畫業經核定公布不在此限。
二  全倒或全毀者。</pre></td></tr></table>"""


async def test_ntpc_search_keeps_local_rows_and_get_parses_articles():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("SimpleQ2.aspx"):
            assert dict(request.url.params) == {"C1": "L", "K1": "違章建築"}
            return _html(NTPC_LIST)
        assert request.url.params["fcode"] == "C0170009"
        return _html(NTPC_LAW)

    async with _client(handler) as http:
        g = await S["ntpc"][2](http, "違章建築", 1)
        assert [(i["id"], i["category"]) for i in g["items"]] == [("C0170177", "自治條例"), ("C0170009", "自治規則或委辦規則")]
        assert g["total"] == 2 and not g["has_more"]
        d = await S["ntpc"][3](http, "C0170009")
    assert d["date"] == "2011-02-18"
    assert d["articles"] == [{"number": "4", "content": "下列之舊違章建築，不得申請修繕：\n一 拆除或整理計畫業經核定公布不在此限。\n二 全倒或全毀者。"}]


# ── 條約協定 ────────────────────────────────────────────────

def _moj_list(facets, rows):
    links = "".join(f'<li><a href="LawSearchResult.aspx?cur={c}&ty=CONVENTION&kw=x">{n}<span class="badge">{k}</span></a></li>'
                    for n, c, k in facets)
    trs = "".join(f'<tr><td>1.</td><td>{fei}<a href="../Hot/AddHotLaw.ashx?pcode={p}&cur=x" title="{t}">{t}</a> (民國 104 年 11 月 26 日 )</td></tr>'
                  for p, t, fei in rows)
    return _html(f"<ul>{links}</ul><table>{trs}</table>")


async def test_moj_treaty_search_country_facet_and_note():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        facets = [("美國", "CD1800400000000", 2), ("日本", "CD1900500000000", 1)]
        if request.url.params.get("cur") == "CD1900500000000":
            return _moj_list(facets, [("Y0040274", "亞東關係協會避免所得稅雙重課稅協定", "")])
        return _moj_list(facets, [("Y0010001", "中美互免海空運所得稅協定", '<span class="label-fei">終</span>')])

    async with _client(handler) as http:
        g = await S["moj_treaty"][2](http, "所得稅", 1)
        assert len(calls) == 1 and g["total"] == 3 and "美國 2、日本 1" in g["note"]
        assert g["items"] == [{"id": "Y0010001", "title": "中美互免海空運所得稅協定", "issuer": "美國", "category": "協定",
                               "date": "2015-11-26", "status": "已終止"}]
        g = await S["moj_treaty"][2](http, "日本 所得稅", 1)
    assert calls[-1] == {"ty": "CONVENTION", "kw": "所得稅", "cur": "CD1900500000000", "page": "1"}
    assert g["total"] == 1 and [i["id"] for i in g["items"]] == ["Y0040274"] and not g["has_more"]


MOJ_TREATY = """<table><tr><th>法規名稱：</th><td><a id="hlLawName">亞東關係協會避免所得稅雙重課稅協定（中譯本）</a></td></tr>
<tr id="trLNODate"><th>簽訂日期：</th><td>民國 104 年 11 月 26 日</td></tr>
<tr id="trVALIDDATE"><th>生效日期：</th><td>民國 105 年 06 月 13 日</td></tr><tr><th>簽約國：</th><td>亞太地區 ＞ 日本</td></tr></table>
<div class="law-content law-agree"><div class="row"><div class="col-th">沿革：</div><div class="col-td text-pre">1.簽署</div></div>
<div class="row"><div class="col-no"></div><div class="col-data text-pre">雙方協會，

第一條  適用之人
        一、本協定適用於具有一方或雙方領域居住
            者身分之人。
第二條  適用之租稅
        一、所得稅。</div></div></div>"""


async def test_moj_treaty_get_returns_one_block_and_handles_400():
    def handler(request: httpx.Request) -> httpx.Response:
        return _html(MOJ_TREATY) if request.url.params["pcode"] == "Y0040274" else httpx.Response(400)

    async with _client(handler) as http:
        d = await S["moj_treaty"][3](http, "Y0040274")
        with pytest.raises(LookupError):
            await S["moj_treaty"][3](http, "Y9999999")
    assert (d["date"], d["effective_date"], d["issuer"]) == ("2015-11-26", "2016-06-13", "亞太地區 ＞ 日本")
    assert d["full_text"] == "雙方協會，\n第一條 適用之人\n一、本協定適用於具有一方或雙方領域居住者身分之人。\n第二條 適用之租稅\n一、所得稅。"
    assert orx._shape(d, "1")["articles"] == [{"number": "1", "content": "適用之人\n一、本協定適用於具有一方或雙方領域居住者身分之人。"}]


MOFA_ROW = """<form action="./Result.aspx?x=1"><input type="hidden" name="__VIEWSTATE" value="vs"/>
<table><tr><td><span id="g_ctl02_frmGrd_tycountry_c">日本<br>JAPAN</span></td>
<td><span id="g_ctl02_frmGrd_tysigneddate">2015/11/26</span></td>
<td><span id="g_ctl02_frmGrd_tysubject_c"><a href="ShowPicOut.aspx?FileFolder=21&amp;FileName=211041126-1_C.pdf">避免所得稅雙重課稅協定（中譯本）</a><br>
<a href="ShowPicOut.aspx?FileFolder=21&amp;FileName=211041126-1_E.pdf">AGREEMENT</a></span></td></tr></table>
<span id="frmGrd_ASPager_lblPageTotal">[{cur} of 4]</span></form>"""


async def test_mofa_search_jumps_page_by_postback_and_get_pdf():
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("ShowPicOut.aspx"):
            assert request.url.params["FileFolder"] == "21"
            return httpx.Response(200, content=b"%PDF-1.3 scanned")
        if request.method == "POST":
            posts.append(dict(parse_qsl(request.content.decode())))
            return _html(MOFA_ROW.replace("{cur}", "3"))
        assert request.url.params["tycountry_c"] == "日本" and request.url.params["tykeyword"] == "所得稅"
        return _html(MOFA_ROW.replace("{cur}", "1"))

    async with _client(handler) as http:
        g = await S["mofa"][2](http, "日本 所得稅", 3)
        d = await S["mofa"][3](http, "211041126-1_C.pdf")
    assert posts[0]["frmGrd_ASPager$txtPage"] == "3" and posts[0]["__VIEWSTATE"] == "vs"
    assert g["items"] == [{"id": "211041126-1_C.pdf", "title": "避免所得稅雙重課稅協定（中譯本）", "issuer": "日本",
                           "category": "協定", "date": "2015-11-26"}]
    assert g["total"] == 40 and g["has_more"] and "估計" in g["note"]
    assert d["full_text"] == "" and d["pdf_url"].endswith("FileName=211041126-1_C.pdf") and "掃描" in d["note"]


MOF_LIST = """<table><tr><td>一、全面性所得稅協定：（一）亞洲：
<a href="/download/10422" title="與日本全面性租稅協定(pdf檔案下載;另開新視窗)">日本</a>、新加坡*(
<a href="/download/e2f85e54882042c68be4490cd13c1dfb" title="Singapore-ch.pdf">舊約</a>)</td></tr>
<tr><td>二、海、空或海空國際運輸所得互免所得稅單項協定：</td></tr>
<tr><td><a href="/download/3b0e6335170f4172ae2b5931b4133afa" title="日本.pdf">日本</a></td></tr></table>"""


async def test_mof_tax_list_filter_and_get():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/download/"):
            assert request.url.path == "/download/10422"
            return httpx.Response(200, content=b"not a pdf")
        return _html(MOF_LIST)

    async with _client(handler) as http:
        g = await S["mof_tax"][2](http, "日本", 1)
        assert [(i["id"], i["title"]) for i in g["items"]] == [
            ("10422", "與日本全面性租稅協定"), ("3b0e6335170f4172ae2b5931b4133afa", "與日本海空運輸所得互免所得稅協定")]
        assert (await S["mof_tax"][2](http, "租稅", 1))["total"] == 3
        assert (await S["mof_tax"][2](http, "新加坡", 1))["items"][0]["title"] == "與新加坡（舊約）全面性所得稅協定"
        d = await S["mof_tax"][3](http, "10422")
        with pytest.raises(LookupError):
            await S["mof_tax"][3](http, "99999")  # 清單上沒有的 id 不下載
    assert d["title"] == "與日本全面性租稅協定" and d["pdf_url"] == "https://www.mof.gov.tw/download/10422" and d["note"]


# ── 交易所規章 ──────────────────────────────────────────────

TWSE_LIST = """<form action="./SearchList.aspx?KW=x"><input type="hidden" name="__VIEWSTATE" value="p{n}"/>
<p>規章：共 192 筆</p><table><tr><td>{n}.</td><td><a href="LawNoContent.aspx?FID=FL00711{n}&amp;KW=x">對有價證券<span>上市</span>公司處理程序</a>（115.09.30）</td></tr></table></form>"""

TWSE_LAW = """<div class="Content"><div class="law-name"><b>法規名稱：</b>臺灣證券交易所股份有限公司營業細則（115.09.24）</div>
<h4><b>第 一 章 總則</b></h4>
<div class="law-no"><b>第 1 條</b><pre>本細則依證券交易法
訂定之。</pre></div><div class="law-no"><b>第 2-1 條</b><pre>（刪除）</pre></div></div>"""


async def test_twse_search_pages_by_next_postback_and_get():
    posts = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("LawContent.aspx"):
            return _html(TWSE_LAW if request.url.params["FID"] == "FL007304" else '<div class="law-name"><b>法規名稱：</b></div>')
        if request.method == "POST":
            form = dict(parse_qsl(request.content.decode()))
            assert form["__EVENTTARGET"] == "ctl00$cphMain$ucPager$butNext"
            posts.append(form["__VIEWSTATE"])
            return _html(TWSE_LIST.replace("{n}", str(len(posts) + 1)))
        assert dict(request.url.params) == {"KW": "上市", "lname": "1", "lcontent": "1"}
        return _html(TWSE_LIST.replace("{n}", "1"))

    async with _client(handler) as http:
        g = await S["twse"][2](http, "上市", 3)
        d = await S["twse"][3](http, "FL007304")
        with pytest.raises(LookupError):
            await S["twse"][3](http, "FL999999")
        with pytest.raises(ValueError):
            await S["twse"][2](http, "上市", orx.TWSE_MAX_PAGE + 1)
    assert posts == ["p1", "p2"]  # 每次帶上一頁的 ViewState 往下翻
    assert g["items"] == [{"id": "FL007113", "title": "對有價證券上市公司處理程序", "issuer": "臺灣證券交易所",
                           "category": "規章", "date": "2026-09-30"}]
    assert g["total"] == 192 and g["has_more"]
    assert (d["title"], d["date"]) == ("臺灣證券交易所股份有限公司營業細則", "2026-09-24")
    assert d["articles"] == [{"number": "1", "content": "本細則依證券交易法訂定之。"}, {"number": "2-1", "content": "（刪除）"}]


SELAW_LIST = """<div class="con-pageleft"><span>共<span class="page-num">68</span>筆</span></div><table>
<tr><td>1.</td><td><a href="/Chinese/RegulatoryInformationResult?sysNumber=LW10812093&amp;releaseDate=2026-09-30"
 title="財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司重大訊息之查證暨公開處理程序">…<font>上櫃</font>…</a></td></tr>
<tr><td></td><td><a href="/English/LawArticle?sysNumber=LW10812093">EN</a></td></tr></table>"""

SELAW_LAW = """<table class="table con-table-top">
<tr><td>法規名稱</td><td><a href="#">某處理程序</a> <font color="red">(現行法規)</font><a class="icon-lang">EN</a></td></tr>
<tr><td>發佈日期</td><td>民國105年5月26日</td></tr></table>
<div class="con-rules content"><div class="title-rule-book"></div>
<ol class="rules-lv01"><li><div class="title-rule-list"><a>第1條</a></div></li></ol>
<ol class="rules-lv02"><li><div>本程序依法訂定之。</div></li></ol>
<ol class="rules-lv01"><li><div class="title-rule-list"><a>第13-1條</a></div></li></ol>
<ol class="rules-lv02"><li><div>應記載下列事項：</div><ol class="rules-lv03"><li><div class="li-rule-lv04">一、名稱。</div></li>
<li><div class="li-rule-lv04">二、地址。</div></li></ol></li><li><div>前項事項應公告。</div></li></ol></div>"""


async def test_selaw_search_filters_by_org_and_get_has_note():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("LawList"):
            assert request.url.params["criteria.cbxlawSimpleName"] == "0207"
            assert request.url.params["criteria.lawPageNumber"] == "2"
            return _html(SELAW_LIST)
        if request.url.params["sysNumber"] == "LW10812093":
            return _html(SELAW_LAW)
        return _html("<p>很抱歉，此資料可能遭到移除或不存在</p>")

    async with _client(handler) as http:
        g = await S["taifex"][2](http, "保證金", 2)
        d = await S["taifex"][3](http, "LW10812093")
        with pytest.raises(LookupError):
            await S["taifex"][3](http, "LW99999999")
    assert g["total"] == 68 and g["has_more"] and "不得轉載" in g["note"]
    assert g["items"] == [{"id": "LW10812093", "title": "財團法人中華民國證券櫃檯買賣中心對有價證券上櫃公司重大訊息之查證暨公開處理程序",
                           "issuer": "臺灣期貨交易所", "category": "規章", "date": "2026-09-30"}]
    assert (d["title"], d["date"], d["status"]) == ("某處理程序", "2016-05-26", "現行法規") and "不得轉載" in d["note"]
    assert d["articles"] == [{"number": "1", "content": "本程序依法訂定之。"},
                             {"number": "13-1", "content": "應記載下列事項：\n一、名稱。\n二、地址。\n前項事項應公告。"}]


# ── 用戶端：來源隔離、id 前綴、快取、條號篩選 ───────────────────

async def test_client_isolates_source_errors_prefixes_ids_and_filters_articles(tmp_path, monkeypatch):
    cache = CacheDB(db_path=tmp_path / "c.db")
    await cache.initialize()
    client = orx.OtherRegulationClient(cache)
    calls = []

    async def ok_search(http, keyword, page):
        return orx._group("甲", "地方法規", 1, [{"id": "GL000001", "title": "t", "issuer": "甲", "category": "自治條例",
                                                "date": "2020-01-01"}], False)

    async def bad_search(http, keyword, page):
        raise httpx.ConnectError("reset")

    async def ok_get(http, native_id):
        calls.append(native_id)
        return {"title": "甲自治條例", "issuer": "甲", "category": "自治條例", "date": "", "source_url": "u",
                "articles": [{"number": "1", "content": "一"}, {"number": "2", "content": "二"}]}

    monkeypatch.setattr(orx, "SOURCES", {"a": ("甲", ("甲市",), ok_search, ok_get),
                                         "b": ("乙", ("乙市",), bad_search, ok_get)})
    try:
        r = await client.search("違章建築", "", 1)
        assert r["success"] and [i["id"] for i in r["results"]] == ["a:GL000001"]
        assert r["categories"][1] == {"source": "乙", "error": "ConnectError: reset"}
        assert (await client.search(" ", "", 1))["success"] is False
        assert (await client.search("x", "桃園", 1))["success"] is False
        r = await client.get("a:GL000001", "第2條")
        assert r["articles"] == [{"number": "2", "content": "二"}] and not r["cached"]
        r = await client.get("a:GL000001", "")
        assert r["cached"] and r["article_count"] == 2 and "articles" not in r and calls == ["GL000001"]
        assert (await client.get("a:GL000001", "9"))["success"] is False
        assert "條號寫法" in (await client.get("a:GL000001", "abc"))["error"]
        assert (await client.get("GL000001", ""))["success"] is False
    finally:
        await client.close()
        await cache.close()

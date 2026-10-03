"""準司法機關決定：列表／全文解析、日期、id 驗證、合併去重。HTTP 一律以 MockTransport 模擬，不連官方網站。"""

import io
import json
import zipfile
from datetime import date, timedelta
from urllib.parse import parse_qs

import httpx
import pytest

from mcp_server.tools import quasi_judicial as qj


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def _offline(request):
    raise AssertionError(f"不應連線：{request.url}")


@pytest.fixture
def sessions(monkeypatch):
    """需要 cookie/CSRF 的來源用 _session() 另開 client；改成同一個 MockTransport。"""
    def use(handler):
        monkeypatch.setattr(qj, "_session", lambda: _client(handler))
    return use


def test_sources_shape_and_aliases():
    for key, (label, aliases, search, get) in qj.SOURCES.items():
        assert isinstance(label, str) and isinstance(aliases, tuple) and callable(search) and callable(get), key
    assert {"pcc_complaint", "uflb", "csptc", "fsc_sanction", "lawyer_discipline"} <= set(qj.SOURCES)
    cy = {k: v for k, v in qj.SOURCES.items() if k.startswith("cy_")}
    assert len(cy) == 4 and all("監察院" in v[1] for v in cy.values())
    assert "彈劾" in qj.SOURCES["cy_impeachment"][1] and "糾舉" in qj.SOURCES["cy_censure"][1]


@pytest.mark.parametrize("key, bad", [
    ("pcc_complaint", "調1140031"), ("pcc_complaint", "訴113/../x"), ("uflb", "114-56/../../x"),
    ("csptc", "12;DROP"), ("fsc_sanction", "abc-202609240002"), ("fsc_sanction", "fsc-2026"),
    ("cy_report", "137-1"), ("cy_report", "133-x"), ("lawyer_discipline", "../FF2639"),
])
async def test_get_rejects_malformed_ids_without_network(key, bad):
    async with _client(_offline) as http:
        with pytest.raises(LookupError):
            await qj.SOURCES[key][3](http, bad)


# ── 工程會 ──────────────────────────────────────────────────

PCC_INDEX = '<form id="queryForm"><input type="hidden" name="_csrf" value="tok"/></form>'
PCC_LIST = """<span id="pagebanner">共有<span class="red">786</span>筆資料</span><table class="tb_01">
<tr class="tb_b2"><td>調1130462</td><td>2024/12/31</td><td>履約期限之計算</td><td>結案 (2025/02/14)</td><td>逾期未繳費</td></tr>
<tr class="tb_b2"><td>訴1130324</td><td>2024/12/31</td><td>第101條第1項第8款</td><td>結案 (2025/06/27)</td>
<td>撤銷(有理由) <a href="/piat/piaq/readPiaqCaseDetail/訴1130324" title="判斷理由">判斷理由</a></td></tr>
<tr class="tb_b2"><td>訴1130325</td><td>2024/12/30</td><td>押標金</td><td>審理中</td><td></td></tr></table>"""


async def test_pcc_search_by_year_keeps_complaints_only(sessions):
    seen = {}

    def handler(request):
        if request.url.path.endswith("/index"):
            return httpx.Response(200, text=PCC_INDEX)
        seen.update(_form(request))
        return httpx.Response(200, text=PCC_LIST)

    sessions(handler)
    async with _client(_offline) as http:
        g = await qj._pcc_search(http, "", 113, 113, "", 1)
    assert seen["_csrf"] == "tok" and seen["caseRecvStartDate"] == "2024/01/01"
    assert seen["caseRecvEndDate"] == "2024/12/31" and seen["pagesize"] == "100"
    assert g["total"] == 786 and g["has_more"] and "調解" in g["note"]
    assert [(i["id"], i["date"]) for i in g["items"]] == [
        ("pcc_complaint:訴1130324", "2025-06-27"), ("pcc_complaint:訴1130325", "2024-12-30")]
    assert g["items"][0]["summary"] == "第101條第1項第8款；撤銷(有理由)"
    assert g["items"][1]["summary"].endswith("（未公開判斷理由）")


async def test_pcc_keyword_defaults_to_last_year_and_filters_page(sessions):
    seen = {}

    def handler(request):
        if request.url.path.endswith("/index"):
            return httpx.Response(200, text=PCC_INDEX)
        seen.update(_form(request))
        return httpx.Response(200, text=PCC_LIST)

    sessions(handler)
    async with _client(_offline) as http:
        g = await qj._pcc_search(http, "押標金", 0, 0, "", 1)
    assert seen["caseRecvStartDate"] == (date.today() - timedelta(days=365)).strftime("%Y/%m/%d")
    assert [i["doc_number"] for i in g["items"]] == ["訴1130325"]
    assert "最近 12 個月" in g["note"] and "沒有關鍵字檢索" in g["note"]


async def test_pcc_search_rejects_partial_case_number(sessions):
    sessions(_offline)
    async with _client(_offline) as http:
        g = await qj._pcc_search(http, "", 0, 0, "1130123", 1)
    assert g["total"] == 0 and "訴1130123" in g["note"]


async def test_pcc_get_returns_reasoning_and_missing_raises():
    detail = """<table class="tb_01"><tr><td class="tbg_1">案號</td><td class="tbg_2"><strong>訴1130123</strong></td></tr>
<tr><td class="tbg_1">收案日期</td><td class="tbg_2">2024/05/13</td><td class="tbg_1">結案日期</td><td class="tbg_2">2024/07/12</td></tr>
<tr><td class="tbg_1">案件審理結果</td><td class="tbg_2">部分撤銷、部分不受理</td></tr>
<tr><td class="tbg_1">判斷理由</td><td class="tbg_2">一、按押標金。
二、據上論結。</td></tr></table>"""
    empty = detail.replace("<strong>訴1130123</strong>", "")

    def handler(request):
        return httpx.Response(200, text=detail if "1130123" in request.url.path else empty)

    async with _client(handler) as http:
        d = await qj._pcc_get(http, "訴1130123")
        with pytest.raises(LookupError):
            await qj._pcc_get(http, "訴9990001")
    assert d["full_text"] == "一、按押標金。\n二、據上論結。" and d["date"] == "2024-07-12"
    assert d["summary"] == "部分撤銷、部分不受理" and "判斷理由" in d["notes"]
    assert d["source_url"].endswith("%E8%A8%B41130123")


# ── 勞動部裁決 ──────────────────────────────────────────────

UFLB_ROW = {"caseNo": "114-56", "caseType": "不當影響工會活動爭議", "appoName": "某工會", "oppoTitle": "某客運公司",
            "filterStatus": "構成不當勞動行為", "decideSugUploadTime": "115/12/27",
            "link": "/attachment/downloadFile/114-56.pdf?objid=1&serverName=x"}


def _uflb_handler(seen, rows, total):
    def handler(request):
        if request.url.path == "/front/querydecision":
            return httpx.Response(200, text='<input type="hidden" name="_csrf" value="c1"/>')
        seen.append(_form(request))
        return httpx.Response(200, json={"recordsTotal": 466, "recordsFiltered": total, "data": rows})
    return handler


async def test_uflb_search_converts_doc_number_and_ignores_upload_date(sessions):
    seen = []
    sessions(_uflb_handler(seen, [UFLB_ROW], 1))
    async with _client(_offline) as http:
        g = await qj._uflb_search(http, "", 0, 0, "114年勞裁字第56號", 1)
    assert seen[0]["keyword"] == "114-56" and seen[0]["_csrf"] == "c1"
    item = g["items"][0]
    assert item["id"] == "uflb:114-56" and item["doc_number"] == "114年勞裁字第56號" and item["date"] == ""
    assert item["summary"] == "不當影響工會活動爭議；申請人：某工會；相對人：某客運公司；構成不當勞動行為"


async def test_uflb_year_uses_case_prefix_and_filters_text_hits(sessions):
    seen = []
    rows = [dict(UFLB_ROW, caseNo="113-43"), dict(UFLB_ROW, caseNo="114-58")]
    sessions(_uflb_handler(seen, rows, 47))
    async with _client(_offline) as http:
        g = await qj._uflb_search(http, "", 113, 113, "", 1)
    assert seen[0]["keyword"] == "113-"
    assert [i["id"] for i in g["items"]] == ["uflb:113-43"] and "案號年度" in g["note"]


async def test_uflb_paging_cap_at_400(sessions):
    seen = []
    sessions(_uflb_handler(seen, [UFLB_ROW] * 20, 466))
    async with _client(_offline) as http:
        g = await qj._uflb_search(http, "", 0, 0, "", 20)  # start = 380
        assert g["has_more"] is False and "400" in g["note"]
        g = await qj._uflb_search(http, "", 0, 0, "", 21)  # start = 400：官網必回 0 筆，不送出
    assert len(seen) == 1 and g["items"] == []


def test_strip_line_numbers_only_when_sequential():
    page = "第3頁，共38頁 \n主  文 1 \n一、確認相對人構成工會法第35 條第 1 項第 52 \n款之不當勞動行為。 3 \n二、駁回。4 \n三、其餘。5 \n"
    assert qj._strip_line_numbers(page)[1:6] == [
        "主  文 ", "一、確認相對人構成工會法第35 條第 1 項第 5", "款之不當勞動行為。 ", "二、駁回。", "三、其餘。"]
    plain = "依第1\n條規定\n"
    assert qj._strip_line_numbers(plain) == ["依第1", "條規定"]


def test_decision_date_and_layout():
    assert qj._decision_date("如主文。中華民國1 1 5年9月4日如不服") == "2026-09-04"
    assert qj._decision_date("無日期") == ""
    text = "決定如下：主文一、確認。二、駁回。事實及理由壹、程序部分：一、合法。【裁決要旨】本文"
    assert qj._layout(text) == "決定如下：\n主文\n一、確認。\n二、駁回。\n事實及理由\n壹、程序部分：\n一、合法。\n【裁決要旨】\n本文"


# ── 保訓會 ──────────────────────────────────────────────────

CSPTC_INDEX = ('<input type="hidden" name="__VIEWSTATE" value="vs0"/>'
               '<input type="hidden" name="__EVENTVALIDATION" value="ev0"/>')
CSPTC_LIST = """<input type="hidden" name="__VIEWSTATE" value="vs{n}"/>
<span id="ContentPlaceHolder1_lblTotal">25</span><span id="ContentPlaceHolder1_lblcurPage">{n}</span>
<table><tr><td></td><th>案件類型：</th><td><span id="ContentPlaceHolder1_gvFjudge_lblFClassType_0">再審議案件/考績事件</span></td></tr>
<tr><td></td><th>決定字號：</th><td><a href="SearchContent.aspx?IN_ID=199007&amp;KWD1=考績" id="ContentPlaceHolder1_gvFjudge_hplCaseNo_0">115 公審決再字 000092 號</a></td></tr>
<tr><td></td><th>決定日期：</th><td><span id="ContentPlaceHolder1_gvFjudge_lblJDate_0">民國 115 年 08 月 25 日</span></td></tr>
<tr><td></td><th>決定結果：</th><td><span id="ContentPlaceHolder1_gvFjudge_lblJSource_0">再審議不受理。</span></td></tr>
<!-- <tr><th>案由摘要：</th><td><span id="ContentPlaceHolder1_gvFjudge_lblJTitle_0">陳某某復審決定書</span></td></tr> -->
</table>"""


async def test_csptc_search_page_jump_and_fields(sessions):
    posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, text=CSPTC_INDEX)
        posts.append(_form(request))
        return httpx.Response(200, text=CSPTC_LIST.format(n=len(posts)))

    sessions(handler)
    async with _client(_offline) as http:
        g = await qj._csptc_search(http, "考績 丙等", 113, 114, "", 2)
    first, jump = posts
    assert first["__VIEWSTATE"] == "vs0" and first[qj._CS + "btnSearch"] == "送出查詢"
    assert first[qj._CS + "txtKWD1"] == "考績,丙等"
    assert first[qj._CS + "txtStartDate"] == "1130101" and first[qj._CS + "txtEndDate"] == "1141231"
    assert jump["__VIEWSTATE"] == "vs1" and jump["__EVENTTARGET"] == qj._CS + "ddlSelectPage"
    assert jump[qj._CS + "ddlSelectPage"] == "1"
    assert g["total"] == 25 and g["has_more"] is False
    assert g["items"] == [{
        "id": "csptc:199007", "agency": "公務人員保障暨培訓委員會", "category": "再審議案件",
        "doc_number": "115公審決再字第000092號", "date": "2026-08-25", "summary": "再審議案件/考績事件；再審議不受理。"}]
    assert "陳某某" not in json.dumps(g, ensure_ascii=False)  # HTML 註解裡未遮蔽的姓名不能外流


async def test_csptc_search_by_doc_number(sessions):
    posts = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, text=CSPTC_INDEX)
        posts.append(_form(request))
        return httpx.Response(200, text=CSPTC_LIST.format(n=1))

    sessions(handler)
    async with _client(_offline) as http:
        await qj._csptc_search(http, "", 0, 0, "114公審決字第000020號", 1)
    assert (posts[0][qj._CS + "txtCaseYear"], posts[0][qj._CS + "ddlCaseWord"], posts[0][qj._CS + "txtCaseNo"]) == (
        "114", "公審決", "000020")


async def test_csptc_get_full_text_and_empty_shell():
    detail = """<div class="frame"><table class="MdetP-Table" id="ContentPlaceHolder1_Table1">
<tr><td><b>案件類型：</b>復審案件/升官等訓練事件</td></tr><tr><td><b>決定字號：</b>114公審決字第000020號</td></tr>
<tr><td><b>決定日期：</b>民國 114 年 1 月 14 日</td></tr>
<tr><td><b>全文內容：</b><br/>公務人員保障暨培訓委員會復審決定書　 114公審決字第000020號</td></tr></table>
<table class="MdetP-Table"><tr><td>復 審 人：</td><td>ΟΟΟ</td></tr></table>
<table class="MdetP-Table"><tr><td><p>主   文</p></td></tr><tr><td><p>復審駁回。</p></td></tr>
<tr><td valign="top">一.</td><td>按規定。</td></tr></table><table class="MdetP-Table"></table></div>"""
    shell = detail.replace("114公審決字第000020號</td>", "再審議案件/</td>")

    def handler(request):
        return httpx.Response(200, text=detail if request.url.params["IN_ID"] == "184254" else shell)

    async with _client(handler) as http:
        d = await qj._csptc_get(http, "184254")
        with pytest.raises(LookupError):
            await qj._csptc_get(http, "999999999")
    assert d["doc_number"] == "114公審決字第000020號" and d["date"] == "2025-01-14" and d["category"] == "復審案件"
    assert d["full_text"].splitlines() == [
        "公務人員保障暨培訓委員會復審決定書 114公審決字第000020號", "復 審 人： ΟΟΟ", "主 文", "復審駁回。", "一. 按規定。"]


# ── 金管會 ──────────────────────────────────────────────────

FSC_MAIN = """<select id="page" name="page"><option>1</option><option>2</option></select><ul>
<li role="row"><span class="no">編號</span><span class="date">裁處書發文日期</span></li>
<li role="row"><span class="no">1</span><span class="date">2026-07-21</span><span class="unit">證期局</span><span class="title">
<a href="home.jsp?id=131&parentpath=0,2&mcustomize=multimessages_view.jsp&dataserno=202607210001&dtable=Penalty"
 title="台新證券處分案(金管證券罰字第1150383460號)">台新證券......</a></span></li>
<li role="row"><span class="no">2</span><span class="date">2026-09-24</span><span class="unit">保險局</span><span class="title">
<a href="home.jsp?id=131&parentpath=0,2&mcustomize=multimessages_view.jsp&dataserno=202609240002&dtable=Penalty"
 title="台灣人壽懸帳缺失，核處新臺幣960萬元罰鍰。">台灣人壽......</a></span></li></ul>"""
FSC_BUREAU = """<select name="page"><option>1</option></select>
<div class="whitebackground"><div class="sort1">1</div><div class="ptitle1"><a
 href="home.jsp?id=104&parentpath=0,2,102&mcustomize=multimessages_view.jsp&dataserno=202607210009&dtable=Penalty"
 title="台新證券處分案（115年7月21日金管證券罰字第1150383460號）">台新證券</a></div><div class="pdate1">2026-07-21</div></div>
<div class="whitebackground"><div class="sort1">2</div><div class="ptitle1"><a
 href="home.jsp?id=104&parentpath=0,2,102&mcustomize=multimessages_view.jsp&dataserno=202608120002&dtable=Penalty"
 title="摩爾投顧處分案（金管證投罰字第1150383881號）">摩爾投顧</a></div><div class="pdate1">2026-08-12</div></div>"""


async def test_fsc_search_merges_dedupes_and_tolerates_one_site_down():
    seen = {}

    def handler(request):
        host = request.url.host
        seen[host] = dict(request.url.params)
        if host == "www.fsc.gov.tw":
            return httpx.Response(200, text=FSC_MAIN)
        if host == "www.sfb.gov.tw":
            return httpx.Response(200, text=FSC_BUREAU)
        if host == "www.ib.gov.tw":
            return httpx.Response(503, text="down")
        return httpx.Response(200, text='<select name="page"></select>')

    async with _client(handler) as http:
        g = await qj._fsc_search(http, "洗錢", 113, 0, "", 1)
    assert seen["www.sfb.gov.tw"]["keyword"] == "洗錢" and seen["www.fsc.gov.tw"]["qptdate"] == "2024-01-01"
    assert seen["www.fsc.gov.tw"]["qdldate"] == "" and seen["www.fsc.gov.tw"]["id"] == "131"
    assert [i["id"] for i in g["items"]] == [  # 證期局重複的台新案被去掉，依日期新到舊
        "fsc_sanction:fsc-202609240002", "fsc_sanction:sfb-202608120002", "fsc_sanction:fsc-202607210001"]
    assert g["items"][2]["doc_number"] == "金管證券罰字第1150383460號"
    assert g["items"][0]["agency"] == "金融監督管理委員會（保險局）" and g["items"][0]["category"] == "重大裁罰"
    assert g["has_more"] and g["total"] == 20 + 2  # 金管會 2 頁（估計）+ 證期局 2 筆
    assert "保險局連線失敗" in g["note"] and "非重大裁罰" in g["note"]


async def test_fsc_get_picks_site_container_and_missing_raises():
    sfb = """<h3>摩爾投顧處分案</h3><div class="page_content"><div class="contentdate">2026-08-12</div>
<div class="main-a_03"><p>金融監督管理委員會　裁處書</p><p>發文字號：金管證投罰字第1150383881號</p><p>主旨：處罰鍰。</p></div></div>"""
    missing = '<div class="page_content">您所查詢的網址不存在</div>'

    def handler(request):
        return httpx.Response(200, text=sfb if request.url.params["dataserno"] == "202608120002" else missing)

    async with _client(handler) as http:
        d = await qj._fsc_get(http, "sfb-202608120002")
        with pytest.raises(LookupError):
            await qj._fsc_get(http, "fsc-209901010001")
    assert d["full_text"] == "金融監督管理委員會　裁處書\n發文字號：金管證投罰字第1150383881號\n主旨：處罰鍰。"
    assert d["doc_number"] == "金管證投罰字第1150383881號" and d["date"] == "2026-08-12"
    assert d["agency"] == "金管會證券期貨局" and d["summary"] == "摩爾投顧處分案"


# ── 監察院 ──────────────────────────────────────────────────

CY_LIST = """<table><tr><th>審議日期</th></tr>
<tr><td data-title="審議日期"><span>115/01/14</span></td><td data-title="調查案號"><span>115司調0003</span></td>
<td data-title="文件案由/案名"><span>紀委員調查「法官周○○案」報告。<a href="/CyBsBoxContent.aspx?n=133&amp;s=49407">...詳全文</a></span></td></tr>
<tr><td colspan="7">很抱歉，查詢結果無吻合資料</td></tr></table><span class="count"><i>/</i>135</span>"""


async def test_cy_search_posts_roc_dates_and_follows_query_handle(monkeypatch):
    monkeypatch.setattr(qj, "_cy_state", None)
    calls = []

    def handler(request):
        calls.append((request.method, str(request.url)))
        if request.method == "POST":
            calls.append(_form(request))
            return httpx.Response(302, headers={"location": "/CyBsBox.aspx?CSN=1&n=133&_Query=abc"})
        if "_Query" in str(request.url) or "page=" in str(request.url):
            return httpx.Response(200, text=CY_LIST)
        return httpx.Response(200, text='<input type="hidden" name="__VIEWSTATE" value="vs"/>'
                                        '<input type="hidden" name="other" value="x"/>')

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:  # 未開自動轉址
        g = await qj.SOURCES["cy_report"][2](http, "法官", 113, 114, "", 2)
    form = calls[2]
    assert form["__VIEWSTATE"] == "vs" and "other" not in form
    assert form["MEETINGSDate_1"] == "113-01-01" and form["MEETINGEDate_1"] == "114-12-31" and form["keyword"] == "法官"
    assert calls[-1] == ("GET", "https://www.cy.gov.tw/CyBsBox.aspx?CSN=1&n=133&_Query=abc&page=2&PageSize=20")
    assert g["source"] == "監察院調查報告" and g["total"] == 135 and g["has_more"]
    assert g["items"] == [{"id": "cy_report:133-49407", "agency": "監察院", "category": "調查報告",
                           "doc_number": "115司調0003", "date": "2026-01-14", "summary": "紀委員調查「法官周○○案」報告。"}]


async def test_cy_browse_without_filters_is_plain_get():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return httpx.Response(200, text=CY_LIST.replace('<span class="count"><i>/</i>135</span>', ""))

    async with _client(handler) as http:
        g = await qj.SOURCES["cy_censure"][2](http, "", 0, 0, "", 1)
    assert urls == ["https://www.cy.gov.tw/CyBsBox.aspx?n=136&CSN=3&page=1&PageSize=20"]
    assert g["total"] == 1 and not g["has_more"] and g["items"][0]["category"] == "糾舉案"


def _docx(*paras: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paras)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="w"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


async def test_cy_get_reads_unwrapped_case_field_and_docx():
    detail = """<div class="area-essay"><table><tbody>
<tr><th>類　　別：</th><td>彈劾案文</td></tr><tr><th>審議日期：</th><td> 115/07/28</td></tr>
<tr><th>字　　號：</th><td>115年劾字第31號</td></tr><th>案　　由：</th><td>前處長江某收賄，爰依法提案彈劾。</td>
<tr><th>本案文件：</th><td><a class="doc" href="https://cybsbox.cy.gov.tw/CYBSBoxSSL/edoc/download/1">舊檔.doc</a>
<a class="docx" href="https://cybsbox.cy.gov.tw/CYBSBoxSSL/edoc/download/2">案文.docx</a>
<a class="pdf" href="https://cybsbox.cy.gov.tw/CYBSBoxSSL/edoc/download/3">審查決定書.pdf</a></td></tr></tbody></table></div>"""

    def handler(request):
        if request.url.host == "www.cy.gov.tw":
            return httpx.Response(200, text=detail if request.url.params["s"] == "49759" else "<html></html>")
        return httpx.Response(200, content=b"\xd0\xcf\x11\xe0" if request.url.path.endswith("/1")
                              else _docx("彈劾案文", "案由：收賄。"))

    async with _client(handler) as http:
        d = await qj._cy_get(http, "135-49759")
        with pytest.raises(LookupError):
            await qj._cy_get(http, "135-1")
    assert d["full_text"] == "彈劾案文\n案由：收賄。" and d["summary"] == "前處長江某收賄，爰依法提案彈劾。"
    assert d["doc_number"] == "115年劾字第31號" and d["date"] == "2026-07-28" and d["category"] == "彈劾案文"
    assert d["pdf_url"].endswith("/download/3") and len(d["documents"]) == 3


# ── 律師懲戒 ────────────────────────────────────────────────

def _lawyer_rows():
    return [
        {"sendorg": "律師懲戒覆審委員會", "caseno": "109年度台覆字第19號", "name": "黃振銘", "restitle": "覆審之請求駁回。",
         "resdate": "110/09/24", "hashcode": "FF2639D0E526B620EEBB3620225DC2"},
        {"sendorg": "律師懲戒委員會", "caseno": "111年度律懲字第50號、<br>113年度律懲字第2號", "name": "黃振銘",
         "restitle": "黃振銘應予除名。", "resdate": "114/10/16", "hashcode": "A120985D20F33767A7AB6F3BDBAFB1"},
        {"sendorg": "臺灣律師懲戒委員會", "caseno": "95年度律懲字第1號", "name": "黃振銘", "restitle": "申誡",
         "resdate": "095/01/01", "hashcode": ""},
    ]


async def test_lawyer_search_requires_keyword_and_explains_empty():
    async with _client(_offline) as http:
        g = await qj._lawyer_search(http, "", 0, 0, "", 1)
    assert g["total"] == 0 and "需要關鍵字" in g["note"]

    def handler(request):
        return httpx.Response(200, json={"data": {"discipline": [], "length": 0}, "status": 1, "message": ""})

    async with _client(handler) as http:
        g = await qj._lawyer_search(http, "除名", 0, 0, "", 1)
    assert "超過 100 筆" in g["note"]


async def test_lawyer_search_sorts_filters_years_and_skips_hashless():
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"data": {"discipline": _lawyer_rows(), "length": 3}, "status": 1})

    async with _client(handler) as http:
        g = await qj._lawyer_search(http, "黃振銘", 0, 0, "", 1)
        g2 = await qj._lawyer_search(http, "", 114, 0, "黃振銘", 1)
    assert bodies == [{"keyword": "黃振銘"}, {"keyword": "黃振銘"}]
    assert [i["id"] for i in g["items"]] == [
        "lawyer_discipline:A120985D20F33767A7AB6F3BDBAFB1", "lawyer_discipline:FF2639D0E526B620EEBB3620225DC2"]
    assert g["items"][0]["doc_number"] == "111年度律懲字第50號、113年度律懲字第2號" and g["items"][0]["date"] == "2025-10-16"
    assert "另有 1 件" in g["note"]
    assert [i["date"] for i in g2["items"]] == ["2025-10-16"]


def test_unwrap_fixed_width_keeps_headings():
    raw = "上列覆審請求人，本會決議如下：\n　　主　文\n覆審之請求駁回。\n    事  實 \n一、被付懲戒人黃振銘律師經臺灣高雄地方法院（下稱高雄地院\n    ）自高雄律師公會徵詢。\n    理　由\n一、理由。"
    assert qj._unwrap_fixed(raw).splitlines() == [
        "上列覆審請求人，本會決議如下：", "主　文", "覆審之請求駁回。", "事  實",
        "一、被付懲戒人黃振銘律師經臺灣高雄地方法院（下稱高雄地院）自高雄律師公會徵詢。", "理　由", "一、理由。"]


async def test_lawyer_get_html_text_and_missing():
    row = dict(_lawyer_rows()[0], header="律師懲戒覆審委員會決議書",
               rescontent="<br>律師懲戒覆審委員會決議書<br>　　主　文<br>覆審之請求駁回。<br>    理　由<br>一、按律師法第<br>    89條。")

    def handler(request):
        if json.loads(request.content)["hash"] == row["hashcode"]:
            return httpx.Response(200, json={"data": row, "status": 1})
        return httpx.Response(404, text="Not Found")

    async with _client(handler) as http:
        d = await qj._lawyer_get(http, row["hashcode"])
        with pytest.raises(LookupError):
            await qj._lawyer_get(http, "FF2639D0E526B620EEBB3620225DC0")
    assert d["full_text"] == "律師懲戒覆審委員會決議書\n主　文\n覆審之請求駁回。\n理　由\n一、按律師法第89條。"
    assert d["category"] == "律師懲戒覆審委員會決議書" and d["date"] == "2021-09-24"
    assert d["source_url"].endswith("/discipline/FF2639D0E526B620EEBB3620225DC2")

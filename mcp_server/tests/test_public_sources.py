"""New public sources: bounded queries, official fields, authentication failures, no network."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, unquote

import httpx
import pytest

from mcp_server.tools import agency_interpretations as ai, appeals as ap, medical_discipline as md
from mcp_server.tools import public_browser as pb, public_captcha as pc, other_regulations as rg


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def html(text):
    return httpx.Response(200, text=text)


def form(request):
    return parse_qs(request.content.decode(), keep_blank_values=True)


def test_new_sources_are_explicit_and_do_not_claim_in_force():
    for key in ("ncc", "hakka", "ocac", "sports"):
        assert ai.resolve_sources(key) == ([key], [])
        assert key not in ai.resolve_sources("")[0]
    from mcp_server.tools import admin_decisions as ad
    assert set(ap.SOURCES).isdisjoint(ad.DEFAULT_SOURCES)
    assert "medical_discipline" not in ad.DEFAULT_SOURCES


@pytest.mark.parametrize("key", ["hakka", "ocac", "sports"])
async def test_new_lawsys_sources_query_and_body(monkeypatch, key):
    seen = []
    listing = '''<p>法規類別 全部 1</p><table class="tab-result"><tr><td>1</td><td>115.01.02</td>
      <td><a id="xhlkLawName" href="LawContent.aspx?id=GL000001">公開要點</a></td><td></td></tr></table>'''
    detail = '''<table class="tab-edit"><tr><th>法規名稱：</th><td>公開要點</td></tr>
      <tr><th>公發布日：</th><td>115.01.02</td></tr></table><div id="ctl00_cp_content_divLawContent50">一、本文。</div>'''
    def handler(r):
        seen.append(r)
        return html(listing if r.url.path.endswith("LawResult.aspx") else detail)
    factory = client
    if key == "hakka":
        original = httpx.AsyncClient
        monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler)))
    async with factory(handler) as http:
        groups = await ai.SOURCES[key][2](http, ai.Query(keyword="公開"))
        doc = await ai.SOURCES[key][3](http, "GL000001")
    assert seen[0].url.params["GroupID"] == "2"
    assert groups[0]["items"][0]["id"] == key + ":GL000001"
    assert "status" not in groups[0]["items"][0] and "status" not in doc
    assert doc["full_text"] == "一、本文。"
    if key == "ocac":
        assert seen[0].url.path == "/law/LawResult.aspx"


async def test_ncc_omits_empty_fields_and_reads_letter():
    def handler(r):
        if r.url.path.endswith("results.aspx"):
            assert "N2" not in r.url.params and "sdate" not in r.url.params
            return html('''<table class="tab-result2"><tr><td><div><a href="FINTQRY04.aspx?fecode=FE381420">通傳字第1號</a></div>
              <div>113.01.02</div><div class="list-issue">要旨：電臺。</div></td></tr></table>''')
        return html('''<table class="fint-table"><tr><th>發文字號：</th><td>通傳字第1號</td></tr>
          <tr><th>發文日期：</th><td>113.01.02</td></tr></table><div class="fint-pre"><pre>主旨：本文。</pre></div>''')
    async with client(handler) as http:
        g = await ai._ncc_search(http, ai.Query(keyword="電臺"))
        d = await ai._ncc_get(http, "FE381420")
        with pytest.raises(LookupError):
            await ai._ncc_get(http, "../../etc/passwd")
    assert g[0]["items"][0]["date"] == "2024-01-02" and "status" not in d
    assert d["full_text"] == "主旨：本文。"


async def test_browser_http_first_and_rate_limit(monkeypatch):
    render = AsyncMock(return_value='<table class="tab-result"></table>')
    monkeypatch.setattr(pb, "render", render)
    async with client(lambda r: html('<table class="tab-result"></table>')) as http:
        await pb.get(http, "https://law.sports.gov.tw/LawResult.aspx", selector=".tab-result")
    render.assert_not_called()
    async with client(lambda r: httpx.Response(403, text="Just a moment")) as http:
        r = await pb.get(http, "https://law.sports.gov.tw/LawResult.aspx", params={"KW": "申請"}, selector=".tab-result")
        assert r.status_code == 200 and "KW=" in render.call_args.args[0]
    render.reset_mock()
    async with client(lambda r: httpx.Response(429)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await pb.get(http, "https://law.sports.gov.tw/", selector=".tab-result")
    render.assert_not_called()


@pytest.mark.parametrize("outcome", ["success", "challenge", "timeout", "cancelled"])
async def test_fresh_browser_cleanup_on_every_exit(monkeypatch, outcome):
    import asyncio
    import playwright.async_api as pw
    page = SimpleNamespace(goto=AsyncMock(), set_default_timeout=lambda _: None,
                           content=AsyncMock(return_value='<body><div id="results"></div></body>'))
    wait = AsyncMock()
    if outcome == "timeout":
        wait.side_effect = pw.TimeoutError("verification did not finish")
    if outcome == "challenge":
        page.content.return_value = '<title>Just a moment</title><div id="results"></div>'
    page.locator = lambda _: SimpleNamespace(first=SimpleNamespace(wait_for=wait))
    browser = SimpleNamespace(version="153.0", close=AsyncMock(),
                              new_context=AsyncMock(return_value=SimpleNamespace(new_page=AsyncMock(return_value=page))))
    manager = AsyncMock()
    manager.__aenter__.return_value = SimpleNamespace(chromium=SimpleNamespace(launch=AsyncMock(return_value=browser)))
    monkeypatch.setattr(pw, "async_playwright", lambda: manager)
    monkeypatch.setattr(pb, "_last_finished", 0)
    monkeypatch.setattr(pb, "_lock", asyncio.Lock())
    async def run():
        async with pb.session("https://example.gov.tw/", "#results"):
            if outcome == "cancelled":
                raise asyncio.CancelledError()
    if outcome == "success":
        await run()
    else:
        with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else RuntimeError):
            await run()
    browser.close.assert_awaited_once()
    assert "storage_state" not in browser.new_context.call_args.kwargs


async def test_yunlin_browser_fallback(monkeypatch):
    get = AsyncMock(return_value=html('<table class="tab-result"></table>'))
    monkeypatch.setattr(pb, "get", get)
    async with client(lambda _: pytest.fail("should use browser helper")) as http:
        await rg._glrs_request(http, "https://law.yunlin.gov.tw/LawResult.aspx", params={"KW": "自治條例"})
    assert get.call_args.kwargs["params"]["KW"] == "自治條例"


async def test_moe_and_moa_fresh_form_query(monkeypatch):
    for key, rid, href in (("moe", "115010037", "hope_view.aspx?cid=115010037"),
                            ("moa", "10087", "Doc11.aspx?No=10087")):
        def handler(r):
            if r.method == "GET":
                return html('<form><input name="__VIEWSTATE" value="fresh"></form>')
            d = form(r)
            assert d["__VIEWSTATE"] == ["fresh"]
            if key == "moe":
                assert d["ctl00$cphContent$ddlOper1"] == ["1"]
            table = 'class="tableList"' if key == "moe" else 'id="ContentPlaceHolder1_GridView1"'
            return html(f'<table {table}><tr><td>1</td><td><a href="{href}">訴願決定</a></td><td>115/01/02</td><td>教字第1號</td></tr></table>共 1 筆')
        monkeypatch.setattr(ap, "_client", lambda: client(handler))
        g = await ap.SOURCES[key][2](None, "教師", 0, 0, "123", 1)
        assert g["items"][0]["id"] == key + ":" + rid
        with pytest.raises(ValueError):
            await ap.SOURCES[key][2](None, "教師", 0, 0, "", 6)


async def test_moe_and_moa_details():
    async with client(lambda r: html('<pre>教育部訴願決定書\n發文字號：教字第1號\n發文日期：115年1月2日\n主文：本文。</pre>')) as http:
        assert (await ap._moe_get(http, "115010037"))["doc_number"] == "教字第1號"
    async with client(lambda r: html('<table>農業部訴願決定書 農字第1號' + '本文。'*40 + '</table>')) as http:
        assert "本文" in (await ap._moa_get(http, "10087"))["full_text"]


async def test_moea_csrf_and_exact_public_record(monkeypatch):
    posts = []
    def handler(r):
        if r.method == "GET":
            return httpx.Response(200, json={"headerName": "X-XSRF-TOKEN", "token": "public-csrf"})
        d = json.loads(r.content);posts.append(d)
        assert r.headers["X-XSRF-TOKEN"] == "public-csrf" and d["isOpen"] is True and "dateRange" in d
        return httpx.Response(200, json={"totalSize": 11, "resultList": [{"caseno": "B110907010", "sendReceno": "經字第1號",
            "casrea": "專利", "sendDay": "2026-01-02", "mainText": "駁回", "fact": "事實", "reason": "理由"}]})
    monkeypatch.setattr(ap, "_client", lambda: client(handler))
    g = await ap._moea_search(None, "專利", 114, 115, "", 2)
    d = await ap._moea_get(None, "B110907010")
    assert posts[0]["dateRange"] == {"startDate": "2025-01-01", "endDate": "2026-12-31"}
    assert posts[1]["caseNoString"] == "B110907010" and "理由" in d["full_text"]
    assert not g["has_more"]


async def test_moenv_api_search_and_exact_get():
    def handler(r):
        assert form(r)["length"] == ["10"]
        return httpx.Response(200, json={"recordsFiltered": 11, "data": [{"caseNo": "11500140051EA05", "date": "115/09/23",
            "docNo": "1150008974", "name": "甲", "subject": "廢棄物", "summary": "駁回", "fact": "事實", "reason": "理由"}]})
    async with client(handler) as http:
        g = await ap._moenv_search(http, "廢棄物", 115, 115, "", 1)
        d = await ap._moenv_get(http, "11500140051EA05")
        with pytest.raises(LookupError):
            await ap._moenv_get(http, "11500140052EA05")
    assert g["has_more"] and d["date"] == "2026-09-23" and "甲" in d["full_text"]


async def test_moenv_single_past_year_is_not_a_reversed_range():
    sent = []

    def handler(r):
        sent.append(form(r))
        return httpx.Response(200, json={"recordsFiltered": 0, "data": []})
    async with client(handler) as http:
        await ap._moenv_search(http, "廢棄物", 0, 110, "", 1)
    assert sent[0]["DateStartString"] == ["110/01/01"] and sent[0]["DateEndString"] == ["110/12/31"]


async def test_moc_public_spa_response_and_no_stored_token(monkeypatch):
    response = AsyncMock(side_effect=[{"total": 11, "rows": [{"id": 155174, "title": "票券", "issueDate": 0}]},
                                     {"decideDocumentNo": "文規字第1號", "reason": "理由", "content": "駁回"},
                                     {"total": 0, "rows": []}])
    monkeypatch.setattr(pb, "response_json", response)
    g = await ap._moc_search(None, "票券 發行", 114, 115, "", 2)
    d = await ap._moc_get(None, "155174")
    url = unquote(response.call_args_list[0].args[0])
    assert '"search":"票券"' in url and "發行" not in url and "offset=10" in url
    assert "未套用" in g["note"] and "只查「票券」" in g["note"] and d["doc_number"] == "文規字第1號"
    await ap._moc_search(None, "票券", 0, 0, "文規字第1號", 1)
    assert '"search":"文規字第1號"' in unquote(response.call_args_list[2].args[0])


async def test_mol_public_voice_validation(monkeypatch):
    paths = []
    def handler(r):
        paths.append(r.url.path)
        if r.url.path.endswith("GetVoice"):
            return httpx.Response(200, json="1234")
        if r.url.path.endswith("GetValidateCode"):
            return httpx.Response(200, content=b"image")
        if r.url.path.endswith("AppealCaseDecision"):
            return html('<form><input name="token" value="fresh"></form>')
        assert r.url.params["validCode"] == "1234" and r.url.params["token"] == "fresh"
        return html('<main><table><tbody><tr><td>1</td><td>案號</td><td>115/1/2</td><td>勞字第1號</td><td>不受理</td><td><a href="AppealCaseDecisionContent?caseId=343209">全文</a></td></tr></tbody></table>共 1 筆</main>')
    monkeypatch.setattr(ap, "_client", lambda: client(handler))
    assert (await ap._mol_search(None, "勞基法", 0, 0, "", 1))["items"][0]["id"] == "343209"
    assert len(paths) == 4
    async with client(lambda r: html('<main><div class="con-flow">勞動部訴願決定書 勞字第1號 主文：不受理。</div></main>')) as http:
        assert "不受理" in (await ap._mol_get(http, "343209"))["full_text"]


@pytest.mark.parametrize("key", ["moi", "mohw"])
async def test_captcha_ambiguous_empty_is_failure_after_two_attempts(monkeypatch, key):
    calls = []
    def handler(r):
        calls.append(r)
        if r.method == "POST":
            return html("查無資料！")
        return html('<form><input name="token" value="fresh"></form>')
    monkeypatch.setattr(ap, "_client", lambda: client(handler))
    monkeypatch.setattr(pc, "recognize", AsyncMock(return_value="1234"))
    monkeypatch.setattr(ap.asyncio, "sleep", AsyncMock())
    with pytest.raises(RuntimeError, match="不能判定查無資料"):
        await ap._captcha_search(key, None, "土地", 115, 115, "", 1)
    assert len([r for r in calls if r.method == "POST"]) == 2


async def test_moi_captcha_fields_and_metadata(monkeypatch):
    def handler(r):
        if r.method == "GET":
            return html('<form><input name="__RequestVerificationToken" value="fresh"><input type="checkbox" name="CaseTypeArray" value="01" checked></form>')
        d = form(r)
        assert d["Captcha"] == ["1234"] and d["CaseTypeArray"] == ["01"] and d["IsIncludeFullTextKw"] == ["True"]
        return html('<table class="appeals_table"><tbody><tr><td>1</td><td><a href="Detail?desid=W4vyZvSqNl0%3D">1150700112</a></td><td>115/09/29</td><td>1150136854</td><td>撤銷。</td></tr></tbody></table>')
    monkeypatch.setattr(ap, "_client", lambda: client(handler))
    monkeypatch.setattr(pc, "recognize", AsyncMock(return_value="1234"))
    g = await ap._captcha_search("moi", None, "土地", 115, 115, "", 1)
    assert g["items"][0]["id"] == "W4vyZvSqNl0="
    async with client(lambda r: html('<div class="page_pdf">台內法字第1號 訴願決定書 主文：撤銷。</div>')) as http:
        assert "撤銷" in (await ap._moi_get(http, "W4vyZvSqNl0="))["full_text"]


async def test_medical_list_public_names_and_unsafe_attachment_ids(monkeypatch):
    def handler(r):
        if r.method == "GET":
            return html('<form><input name="token" value="fresh"></form>')
        assert form(r)["CER_REF_ID"] == ["A"]
        return html('<table id="disTable"><tbody><tr><td>何政岳</td><td>雲林</td><td>醫***611</td><td><a href="../Downloads/23.何政岳.pdf">PDF</a></td><td>115/04/24 ~ 120/04/23</td></tr></tbody></table>')
    monkeypatch.setattr(ap, "_client", lambda: client(handler))
    g = await md.search(None, "何政岳", 0, 0, "", 1)
    assert "何政岳" in g["items"][0]["summary"]
    assert "status" not in g["items"][0]
    for value in ("../secret.pdf", "a/b.pdf", "a\\b.pdf"):
        with pytest.raises(LookupError):
            await md.get(None, value.encode().hex())
    with pytest.raises(ValueError):
        md._filename("https://matest.tradevan.com.tw/Accessibility/Downloads/a.pdf")


async def test_cec_title_query_and_pdf_detail(monkeypatch):
    def handler(r):
        if "/api/" in r.url.path:
            assert r.url.params["keyword"] == "民調" and r.url.params["page"] == "2"
            return httpx.Response(200, json={"code": 0, "data": {"articleList": [{"directType": "005", "directPath": "64682",
                "directName": "民調案", "beginTime": "20260102"}], "pages": {"totalCount": 11, "totalPage": 2}}})
        return html('<h2 class="title">民調案</h2><a href="https://web.cec.gov.tw/api/file/a.pdf">PDF</a>')
    monkeypatch.setattr(ap, "_pdf_text", AsyncMock(return_value="中選法字第1號 本文。"))
    async with client(handler) as http:
        g = await ap._cec_search(http, "民調", 0, 0, "", 2)
        d = await ap._cec_get(http, "64682")
    assert not g["has_more"] and g["items"][0]["date"] == "2026-01-02"
    assert d["doc_number"] == "中選法字第1號"


async def test_keelung_query_does_not_claim_pdf_fulltext_search(monkeypatch):
    def handler(r):
        if r.url.path.endswith("2669.html"):
            assert r.url.params["nowPage"] == "3" and r.url.params["q_stitle"] == "114基府訴決字第113號"
            return html('共 23 筆資料<a href="https://www.klcg.gov.tw/tw/klcg1/2669-317764.html">114基府訴決字第113號</a>')
        return html('<a href="/wSite/public/Attachment/03602/a.pdf">PDF</a>')
    monkeypatch.setattr(ap, "_pdf_text", AsyncMock(return_value="基府訴決字第113號 本文。"))
    async with client(handler) as http:
        g = await ap._keelung_search(http, "114基府訴決字第113號", 114, 114, "", 3)
        d = await ap._keelung_get(http, "317764")
    assert "只比對標題" in g["note"] and "未套用" in g["note"] and not g["has_more"]
    assert d["pdf_url"].startswith("https://www.klcg.gov.tw/")


def odt(text):
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("content.xml", '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:text><text:p>' + text + '</text:p></office:text></office:body></office:document-content>')
    return buf.getvalue()


async def test_dgpa_page_filter_and_odt_body():
    seen = []
    def handler(r):
        seen.append(r)
        if r.url.path.endswith("informationlist"):
            return html('共 95 筆<a href="information?uid=130&pid=12927">115.04.02 蔡○○年終考核</a>')
        if r.url.path.endswith("information"):
            return html('<a href="/FileConversion?filename=dgpa/files/a.odt">決定書</a>')
        return httpx.Response(200, content=odt("人總字第1號 本文。"))
    async with client(handler) as http:
        g = await ap._dgpa_search(http, "其他", 0, 0, "", 1)
        d = await ap._dgpa_get(http, "12927")
    assert g["items"] == [] and g["total"] == 95 and g["has_more"]
    assert "空頁不代表" in g["note"] and "本文" in d["full_text"] and len(seen) == 3


async def test_mohw_ocr_query_pagination_and_odt(monkeypatch):
    def handler(r):
        if r.method == "GET":
            return html('<form><input name="__VIEWSTATE" value="fresh"></form>')
        d = form(r)
        if "TBOXCaptcha" in d:
            assert d["TBOXCaptcha"] == ["1234"]
        else:
            assert d["ctl00$content$ucPage$txtPageSelector"] == ["2"]
        return html('<form><input name="__VIEWSTATE" value="page"></form>共 11 筆資料<table><tbody><tr><td>1</td><td>甲</td><td>1140029795</td><td>115/01/02</td><td><a href="AppealDownload.aspx?AppNo=1140029795&type=odt">ODT</a></td></tr></tbody></table>')
    monkeypatch.setattr(ap, "_client", lambda: client(handler))
    monkeypatch.setattr(pc, "recognize", AsyncMock(return_value="1234"))
    g = await ap._captcha_search("mohw", None, "醫師", 115, 115, "", 2)
    assert g["items"][0]["id"] == "1140029795" and not g["has_more"]
    async with client(lambda r: httpx.Response(200, content=odt("衛部法字第1號 本文。"))) as http:
        assert "本文" in (await ap._mohw_get(http, "1140029795"))["full_text"]


@pytest.mark.parametrize("key", ["cec", "moenv"])
async def test_public_api_error_is_not_empty_search(key):
    async with client(lambda r: httpx.Response(200, json={"error": "verification required"})) as http:
        with pytest.raises(RuntimeError):
            await ap.SOURCES[key][2](http, "關鍵字", 0, 0, "", 1)


async def test_moea_invalid_csrf_stops_before_query(monkeypatch):
    calls = []
    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"login": True})
    monkeypatch.setattr(ap, "_client", lambda: client(handler))
    with pytest.raises(RuntimeError, match="CSRF"):
        await ap._moea_query({})
    assert len(calls) == 1


async def test_customs_csrf_pagination_and_document(monkeypatch):
    def handler(r):
        if r.url.path.endswith("singlehtml/41"):
            return html('<h2>關稅法釋函</h2><div class="article-page"><article>中華民國114年10月15日台財關字第1141021317號令<p>展延一年。</p></article></div>')
        if r.method == "GET":
            return html('<form action="/multiplehtml/41"><input type="hidden" name="csrfToken" value="fresh"></form>')
        assert form(r)["page"] == ["2"] and form(r)["csrfToken"] == ["fresh"]
        return html('42 筆資料<a href="/singlehtml/41?cntId=451282d4399d458ea088d800ba7d1433">關稅法釋函</a>')
    monkeypatch.setattr(ai, "_session", lambda: client(handler))
    g = await ai._customs_search(None, ai.Query(keyword="關稅法", page=2))
    async with client(handler) as http:
        d = await ai._customs_get(http, "451282d4399d458ea088d800ba7d1433")
    assert g[0]["has_more"] and "日期條件未套用" in g[0]["note"]
    assert d["date"] == "2025-10-15" and d["doc_number"] == "台財關字第1141021317號"


async def test_join_law_scope_and_selected_attachment(monkeypatch):
    from mcp_server.tools import legislative_records as lr
    rid = "f204991b-092b-42d2-af44-abadeee731d1"
    def handler(r):
        if r.method == "POST":
            d = json.loads(r.content)
            assert d["searchScope"] == "Law" and d["searchType"] == "finish" and d["page"] == 2
            assert r.headers["X-Requested-With"] == "XMLHttpRequest"
            return httpx.Response(200, json={"success": True, "result": [{"policyUid": rid, "dataType": "Law", "policyTitle": "條例草案"}], "totalResults": 21, "totalPages": 2})
        return html('<div class="policy-detail"><h2 class="shareMailSubject">條例草案</h2><div class="shareMailBody">公告內容</div><a href="/attachments/a/download/草案.pdf">草案對照表</a></div>')
    monkeypatch.setattr(ai, "_session", lambda: client(handler))
    pdf = AsyncMock(return_value=("條文對照表內容", False))
    monkeypatch.setattr(lr, "_pdf_text", pdf)
    g = await lr.search_join("條例", "closed", 2)
    async with client(handler) as http:
        d = await lr.get_join(http, rid)
        with pytest.raises(LookupError):
            await lr.get_join(http, "../../secret")
    assert not g["has_more"] and g["consultation_state"] == "已結束"
    assert d["title"] == "條例草案" and "條文對照表" in d["full_text"]
    pdf.assert_awaited_once()


async def test_mac_main_scoped_title_search_and_full_letter():
    def handler(r):
        if r.url.path.endswith("Content_List.aspx"):
            return html('<div id="base-content"><div class="content-list"><a href="cp.aspx?n=A145E1ED3077E950">廣告刊登疑義</a><a href="cp.aspx?n=8EACD8043F3732DB">條例第34條</a></div></div>')
        return html('<title>大陸委員會-廣告刊登疑義</title><div id="CCMS_Content"><div class="area-editor">中華民國101年4月27日陸法字第1019902888號函<p>函文本文。</p></div></div>')
    async with client(handler) as http:
        g = await ai._mac_letters_search(http, ai.Query(keyword="刊登"))
        d = await ai._mac_letters_get(http, "A145E1ED3077E950")
    assert len(g[0]["items"]) == 1 and "不代表全部" in g[0]["note"]
    assert d["date"] == "2012-04-27" and d["doc_number"] == "陸法字第1019902888號" and "status" not in d


@pytest.mark.parametrize("key", ["hakka", "ocac"])
async def test_new_lawsys_challenge_is_not_empty(monkeypatch, key):
    async def blocked(*args, **kw):
        return httpx.Response(200, text='<title>Just a moment</title>', request=httpx.Request("GET", "https://example.gov.tw/"))
    class Http:
        headers = {}
        get = staticmethod(blocked)
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    if key == "hakka":
        monkeypatch.setattr(ai.httpx, "AsyncClient", lambda **kw: Http())
    with pytest.raises(RuntimeError):
        await ai._lawsys_search(key, Http(), ai.Query(keyword="公開"))


def test_login_form_never_counts_as_public_law_content():
    assert not pb._ready('<input type="password"><table class="tab-edit"></table>', ".tab-edit")


async def test_error_pages_are_not_scanned_decisions_or_empty_documents():
    async with client(lambda r: html('<title>Just a moment</title>')) as http:
        with pytest.raises(ValueError, match="未回傳 PDF"):
            await ap._pdf_text(http, "https://web.cec.gov.tw/api/file/decision.pdf")
        with pytest.raises(ValueError, match="未回傳 ODT"):
            await ap._mohw_get(http, "1140029795")
        with pytest.raises(ValueError, match="未回傳決定書"):
            await ap._moi_get(http, "W4vyZvSqNl0=")


@pytest.mark.parametrize("payload,status", [({"reason": "公開內容"}, 200), ({"error": "denied"}, 403), ([], 200)])
async def test_browser_captures_public_api_or_reports_bad_response(monkeypatch, payload, status):
    from contextlib import asynccontextmanager
    response = SimpleNamespace(status=status, json=AsyncMock(return_value=payload))
    pending = SimpleNamespace(value=AsyncMock(return_value=response)())
    event = AsyncMock()
    event.__aenter__.return_value = pending
    page = SimpleNamespace(goto=AsyncMock(), expect_response=lambda *a, **kw: event)
    @asynccontextmanager
    async def session(*args):
        yield page
    monkeypatch.setattr(pb, "session", session)
    if status == 200 and isinstance(payload, dict):
        assert await pb.response_json("https://appeal.moc.gov.tw/", "https://themedata.culture.tw/api/") == payload
    else:
        with pytest.raises(RuntimeError):
            await pb.response_json("https://appeal.moc.gov.tw/", "https://themedata.culture.tw/api/")


async def test_browser_sources_install_missing_chromium_once(monkeypatch):
    from types import SimpleNamespace

    from mcp_server.tools import public_browser

    class Missing(Exception):
        pass

    calls = []

    async def launch(headless):
        calls.append(headless)
        if len(calls) == 1:
            raise Missing("Executable doesn't exist at /x; please run playwright install")
        return "browser"

    monkeypatch.setattr(public_browser, "_install_chromium", lambda: True)
    p = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
    assert await public_browser._launch(p, Missing) == "browser" and len(calls) == 2

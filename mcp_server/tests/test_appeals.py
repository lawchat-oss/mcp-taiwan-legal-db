"""各部會、地方政府訴願決定（appeals）：列表解析、id 驗證、分頁換算。HTTP 一律以 MockTransport 模擬。"""

import base64
from urllib.parse import parse_qsl

import httpx
import pytest

from mcp_server.tools import appeals as ap


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=f"<html><body>{body}</body></html>")


def _u(path: str) -> str:
    return base64.b64encode(path.encode()).decode()


def _search(key):
    return ap.SOURCES[key][2]


def _get(key):
    return ap.SOURCES[key][3]


def test_sources_shape():
    for key, (label, aliases, search, get) in ap.SOURCES.items():
        assert key.isascii() and label.endswith("訴願決定") and "訴願" in aliases
        assert callable(search) and callable(get)


@pytest.mark.parametrize("key", list(ap.SOURCES))
async def test_get_rejects_malformed_ids_without_requests(key):
    def handler(request):
        raise AssertionError(f"不該發出請求：{request.url}")

    async with _client(handler) as http:
        for bad in ("../../etc/passwd", "1?x=1", "", "GL1&a=b"):
            with pytest.raises(LookupError):
                await _get(key)(http, bad)


def test_helpers():
    assert ap._doc_no("中央銀行113年3月3日台央法字第1130008955號訴願決定書") == "台央法字第1130008955號"
    assert ap._doc_no("嘉義市政府訴願決定書府行法字第1145024408號訴願人") == "府行法字第1145024408號"
    assert ap._doc_no("臺北市政府 113.12.17 府訴三字第 1136086353 號訴願決定書") == "府訴三字第1136086353號"
    assert ap._signed("中華民國114年1月2日函…委員李○中華民國11 5年7月30日如不服") == "2026-07-30"
    assert ap._number("府授法訴字第1150124384號") == "1150124384" and ap._number("1140935") == "1140935"
    assert ap._estimate(3, 3, 20, 5) == 45 and ap._estimate(1, 3, 20, 20) == 60


def test_local_filters_keyword_number_and_year():
    rows = [ap._item("1", "工程願字第1140026767號", "2026-01-15", "訴願人因工程施工查核扣點事件"),
            ap._item("2", "工程願字第1090025322號", "2020-12-08", "訴願人因工程施工查核成績事件"),
            ap._item("3", "工程願字第1120013159號", "2023-07-27", "訴願人因政府採購違約金事件")]
    assert [i["id"] for i in ap._local(rows, "工程 施工", 0, 0, "", 1)["items"]] == ["1", "2"]
    assert [i["id"] for i in ap._local(rows, "", 0, 0, "工程願字第1120013159號", 1)["items"]] == ["3"]
    g = ap._local(rows, "", 110, 112, "", 1)
    assert [i["id"] for i in g["items"]] == ["3"] and g["total"] == 1 and not g["has_more"]


async def test_taichung_list_and_page_estimate():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        return _html("""<table><thead><tr><th>序號</th><th>案號</th><th>案由類型</th><th>決定書文號</th>
          <th>決定書日期</th><th>決定結果類別</th></tr></thead><tbody>
          <tr><td>1</td><td>1140935</td><td>違章建築</td><td>1150124384</td><td>115/5/4</td><td>駁回</td></tr>
          </tbody></table><div class="pager"><a>«</a><span class="current">1</span><a>2</a>
          <span class="spacer">...</span><a>15</a><a>»</a></div>""")

    async with _client(handler) as http:
        g = await _search("taichung")(http, "違章建築", 114, 0, "", 1)
        await _search("taichung")(http, "", 0, 0, "府授法訴字第1150124384號", 2)
    assert g["items"] == [{"id": "taichung:1140935", "agency": "臺中市政府", "category": "訴願決定",
                           "doc_number": "1150124384", "date": "2026-05-04", "summary": "違章建築（駁回）"}]
    assert g["total"] == 300 and g["has_more"] and "估計" in g["note"]
    assert sent[0]["PageIndex"] == "0" and sent[0]["decisiondate1"] == "114/01/01" and sent[0]["decisiondate2"] == ""
    assert sent[1]["PageIndex"] == "1" and sent[1]["decisionid"] == "1150124384" and sent[1]["case_no"] == ""


async def test_taipei_chains_category_facets_into_pages():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        return _html("""<ul class="treemenu"><li><a class="typeLink" data-id="1000">都發<span>(33)</span></a></li>
          <li><a class="typeLink" data-id="0500">工務<span>(5)</span></a></li></ul>
          <table class="table-result"><tbody><tr><td>1.</td><td>都發</td><td>訴願駁回</td><td>1136086353</td>
          <td><a href="#">1601-045</a></td></tr></tbody></table>""")

    async with _client(handler) as http:
        g1 = await _search("taipei")(http, "違章建築", 113, 113, "", 1)
        assert len(sent) == 1  # 第 1 頁就是預設列出的第一個類別，不必再發請求
        g3 = await _search("taipei")(http, "違章建築", 113, 113, "", 3)
        g4 = await _search("taipei")(http, "違章建築", 113, 113, "", 4)
    assert g1["total"] == 38 and g1["has_more"] and len(sent) == 4
    assert g1["items"][0]["id"] == "taipei:1601-045" and g1["items"][0]["doc_number"] == "1136086353"
    assert sent[2]["curcateid"] == "0500" and sent[2]["page"] == "1"  # 都發佔 2 頁，第 3 頁是工務第 1 頁
    assert not g3["has_more"] and g4["items"] == []
    assert sent[0]["year"] == "113"


async def test_ntpc_list_and_number_routing():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        return _html("""<div class="p-left">查詢結果共計：252筆</div><table class="tab-News">
          <tr><td>1</td><td class="date">115.09.16</td><td><a href="/Scripts/Su_contents03.aspx?NO=3&amp;ecode=Z00000&amp;ecase=%e6%96%b0%e5%8c%97%e5%ba%9c%e8%a8%b4%e6%b1%ba&amp;eno=1151365070&amp;EANO=1153071048&amp;EDATE=20260916">因違反建築法事件提起訴願</a></td><td>1153071048</td></tr>
          <tr><td>2</td><td class="date">092.07.15</td><td><a href="/Scripts/Su_contents03.aspx?ecase=%ef%bc%88%e7%84%a1%ef%bc%89&amp;eno=%ef%bc%88%e7%84%a1%ef%bc%8992860171&amp;EANO=92860171">因土地增值稅事件提起訴願</a></td><td>92860171</td></tr>
          </table>""")

    async with _client(handler) as http:
        g = await _search("ntpc")(http, "違章建築", 113, 0, "", 13)
        await _search("ntpc")(http, "", 0, 0, "新北府訴決字第1151365070號", 1)
        await _search("ntpc")(http, "", 0, 0, "1153071048", 1)
    assert [(i["id"], i["doc_number"], i["date"]) for i in g["items"]] == [
        ("ntpc:1153071048", "新北府訴決字第1151365070號", "2026-09-16"), ("ntpc:92860171", "", "2003-07-15")]
    assert g["total"] == 252 and not g["has_more"]  # 13 × 20 = 260 ≥ 252
    assert sent[0]["sdate"] == "20240101" and sent[0]["edate"] == "99991231"
    assert sent[1]["N2"] == "1151365070" and sent[2]["EANO"] == "1153071048"


async def test_kaohsiung_reverses_small_result_sets(monkeypatch):
    posts = []
    row = ('<tr valign="top"><td><a href="plead31.aspx?entry={0}">11202{0:04d}</a></td><td>2024/01/{0:02d}</td>'
           '<td>環保局</td><td>廢棄物清理法事件</td><td>訴願駁回。</td></tr>')

    def handler(request):
        if request.method == "GET":
            return _html('<form id="aspnetForm"><input type="hidden" name="__VIEWSTATE" value="v0">'
                         '<select name="ctl00$ContentPlaceHolder1$start_year"><option value="115" selected>115</option>'
                         '</select></form>')
        data = dict(parse_qsl(request.content.decode()))
        posts.append(data)
        page = 3 if data.get("__EVENTARGUMENT") == "Page$3" else 1
        rows = "".join(row.format(n) for n in ((21, 22, 23, 24, 25) if page == 3 else range(1, 11)))
        return _html(f'<form id="aspnetForm"><input type="hidden" name="__VIEWSTATE" value="v1"></form>'
                     f'<span>搜尋結果:25筆</span><table id="ctl00_ContentPlaceHolder1_GV">{rows}</table>')

    monkeypatch.setattr(ap, "_client", lambda **kw: _client(handler))
    g = await _search("kaohsiung")(None, "違章建築", 0, 0, "", 1)
    assert posts[0]["ctl00$ContentPlaceHolder1$start_year"] == "95" and posts[0]["ctl00$ContentPlaceHolder1$key1"] == "違章建築"
    assert posts[1]["__VIEWSTATE"] == "v1" and posts[1]["__EVENTARGUMENT"] == "Page$3"  # 站方最後一頁是最新的
    assert [i["id"] for i in g["items"]] == [f"kaohsiung:{n}" for n in (25, 24, 23, 22, 21)]
    assert g["total"] == 25 and g["has_more"] and "note" not in g


async def test_glrs_list_and_params():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        return _html("""<li class="pageinfo">共 133 筆，頁次：1 / 7</li><table class="tab-list"><tr><th>序</th></tr>
          <tr><td>1.</td><td>115.07.27</td><td><a href="LawContent.aspx?id=GL001647&amp;kw=x">花蓮縣政府訴願決定書
          (115年訴字第<span>16</span>號)</a></td><td>訴願決定書</td></tr></table>""")

    async with _client(handler) as http:
        g = await _search("hualien")(http, "建築", 114, 0, "", 1)
    assert g["items"][0] == {"id": "hualien:GL001647", "agency": "花蓮縣政府", "category": "訴願決定",
                             "doc_number": "115年訴字第16號", "date": "2026-07-27",
                             "summary": "花蓮縣政府訴願決定書(115年訴字第16號)"}
    assert g["total"] == 133 and g["has_more"]
    assert sent[0]["NLawTypeID"] == "9" and sent[0]["CategoryID"] == "24" and sent[0]["StartDate"] == "20250101"


async def test_glrs_get_refuses_non_decisions():
    def handler(request):
        return _html('<table class="tab-edit"><tr><th>法規體系：</th><td>自治規則</td></tr></table>'
                     '<div class="law-content">場地使用管理辦法</div>')

    async with _client(handler) as http:
        with pytest.raises(LookupError):
            await _get("kinmen")(http, "GL000001")


def test_rhythm_row_variants():
    rel = _u("/001/Upload/14/RelFile/8965/356811/x.pdf")
    soup = ap.BeautifulSoup(f"""<div id="CCMS_Content"><table><tbody>
      <tr><td data-title="主題"><a href="#" data-fancyboxopen="RelData.aspx?sms=9710&amp;ParentSN=1001148">
        115年苗府訴字第82號郭○佑洗錢防制法事件訴願決定書</a></td><td data-title="上版日期">115-10-02</td></tr>
      <tr><td data-title="編號">1</td><td data-title="決定書案號">1150907-13</td>
        <td data-title="決定書日期">2026-09-07 15:20:00</td>
        <td data-title="主旨"><a href="https://ws.hsinchu.gov.tw/Download.ashx?u={rel}">訴願人因洗錢防制法事件</a></td></tr>
      </tbody></table></div>""", "html.parser")
    assert ap._r5_rows(soup) == [
        ap._item("1001148", "苗府訴字第82號", "2026-10-02", "115年苗府訴字第82號郭○佑洗錢防制法事件訴願決定書"),
        ap._item("356811", "1150907-13", "2026-09-07", "訴願人因洗錢防制法事件"),
    ]
    yilan = ap.BeautifulSoup('<div class="directory_list"><h4><a href="OpenData_DealData.aspx?n=9929&amp;sms=12330'
                             '&amp;s=409560">府訴字第1150146779號</a></h4><span>發佈時間：115-09-24</span></div>',
                             "html.parser")
    assert ap._r5_rows(yilan) == [ap._item("409560", "府訴字第1150146779號", "2026-09-24", "府訴字第1150146779號")]


async def test_rhythm_search_posts_then_pages_with_query_guid():
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(302, headers={"Location": "/News.aspx?n=13382&sms=12660&_Query=abc"})
        if "_Query" not in str(request.url):
            return _html('<form action="./News.aspx?n=13382&amp;sms=12660&amp;page=1&amp;PageSize=20&amp;Create=1">'
                         '<input type="hidden" name="__VIEWSTATE" value="vs"></form>')
        return _html("""<div id="CCMS_Content"><table><tbody><tr><td data-title="發布日期">115-09-18</td>
          <td data-title="主旨"><a href="News_Content.aspx?n=13382&amp;s=149539">臺東縣政府訴願決定書(案號115039)</a></td>
          </tr></tbody></table></div><div class="page"><a href="News.aspx?n=13382&amp;_Query=abc&amp;page=1">1</a>
          <a href="News.aspx?n=13382&amp;_Query=abc&amp;page=17&amp;PageSize=20">17</a></div>""")

    async with _client(handler) as http:
        g = await _search("taitung")(http, "", 114, 114, "", 2)
    post = dict(parse_qsl(seen[1].content.decode()))
    assert str(seen[1].url).endswith("/News.aspx?n=13382&sms=12660&page=1&PageSize=20&Create=1")
    assert post["__VIEWSTATE"] == "vs" and post["jNewsModule_field_1"] == "539"
    assert post["jNewsModule_field_SDate_1"] == "114/01/01" and post["jNewsModule_field_EDate_1"] == "114/12/31"
    assert str(seen[2].url).endswith("_Query=abc&page=2&PageSize=20")
    assert g["items"] == [{"id": "taitung:149539", "agency": "臺東縣政府", "category": "訴願決定",
                           "doc_number": "115039", "date": "2026-09-18", "summary": "臺東縣政府訴願決定書(案號115039)"}]
    assert g["total"] == 340 and g["has_more"]


async def test_rhythm_get_bundle_caps_files(monkeypatch):
    monkeypatch.setattr(ap, "pdf_to_text", lambda blob: blob.decode())
    links = "".join(
        f'<li data-index="{i}"><a href="https://ws/Download.ashx?u={_u(f"/001/Upload/1/relfile/12642/256927/{i}.pdf")}'
        f'&amp;n={_u(f"115000{i}-決定書-張○{i}.pdf")}">pdf</a></li>' for i in range(7))
    other = f'<a href="https://ws/Download.ashx?u={_u("/001/Upload/1/relfile/12642/1/x.pdf")}">相關法規</a>'

    def handler(request):
        if "Download.ashx" in str(request.url):
            return httpx.Response(200, content="訴願人：張○○ 中華民國115年8月25日".encode())
        return _html(f'<div id="CCMS_Content"><h3>115年第4次訴願審議委員會決定書</h3><ul>{links}</ul>{other}</div>')

    cfg = ap._Rhythm("https://www.cyhg.gov.tw/", "1220", "12642")  # 不走舊版 TLS，才能用 MockTransport
    async with _client(handler) as http:
        d = await ap._r5_get(cfg, http, "256927")
    assert len(d["attachments"]) == 7 and "前 5 份" in d["notes"]
    assert d["full_text"].count("【") == 5 and d["date"] == "2026-08-25"
    assert d["summary"] == "115年第4次訴願審議委員會決定書" and d["pdf_url"].startswith("https://ws/Download.ashx")


async def test_moj_hides_unmasked_titles():
    def handler(request):
        return _html("""<p>共 2 筆資料，第 1/1 頁</p><table>
          <tr><td data-title="序號">01</td><td data-title="標題"><a href="/media/1/a.pdf?mediaDL=true">
            法訴字第11513521450號-趙○○因申請提供資訊事件(1150824)</a></td><td data-title="公開日期/發布日期">115/09/29</td></tr>
          <tr><td data-title="序號">02</td><td data-title="標題"><a href="/media/2/b.pdf?mediaDL=true">
            法訴字第10813504470號謝清彥因申請政府資訊事件(1080725)</a></td><td data-title="公開日期/發布日期">108/08/26</td></tr>
          </table>""")

    async with _client(handler) as http:
        g = await _search("moj")(http, "資訊", 0, 0, "", 1)
        with pytest.raises(LookupError):
            await _get("moj")(http, "10813504470")
    assert g["items"] == [{"id": "moj:11513521450", "agency": "法務部", "category": "訴願決定",
                           "doc_number": "法訴字第11513521450號", "date": "2026-08-24",
                           "summary": "趙○○因申請提供資訊事件"}]
    assert "1 件" in g["note"] and g["total"] == 2


async def test_vac_pages_locally_and_needs_a_criterion():
    forms = []
    rows = "".join(f'<tr><td>{n}.</td><td><a href="../sp-appeal-CDQC-1.html?ID={n}&amp;CheckStr={n:032X}">'
                   f'王○{n}因就養事件訴願決定書</a></td><td>民國115年8月{n % 28 + 1}日</td><td>實體</td></tr>'
                   for n in range(1, 26))

    def handler(request):
        forms.append(dict(parse_qsl(request.content.decode(), keep_blank_values=True)))
        return _html(f"<table>{rows}</table>")

    async with _client(handler) as http:
        g = await _search("vac")(http, "", 0, 0, "", 2)
    assert forms[0]["TBOXDecisionDateFrom"] == "1990/01/01"
    assert g["total"] == 25 and len(g["items"]) == 5 and not g["has_more"]
    assert g["items"][0]["id"] == f"vac:21-{21:032X}" and g["items"][0]["date"] == "2026-08-22"


async def test_cbc_and_fsc_lists():
    def handler(request):
        if "cbc" in request.url.host:
            return _html("""<table><tr><td>1.</td><td><a href="/DOrder/DOrder?doid=12">人事懲處事件
              中央銀行113年3月3日台央法字第1130008955號訴願決定書</a></td></tr></table><div>目前第 1 頁，共有 2 頁</div>""")
        return _html("""<div class="newslist"><ul><li role="row"><span>編號</span></li><li role="row">
          <span class="no">1</span><span class="date">2026-05-28</span><span class="unit">11500001</span>
          <span class="title"><a href="home.jsp?id=809&amp;dataserno=202605280001">上列再審申請人因保險事件</a></span>
          </li></ul></div>頁數 1/4 ,共有 <span class="red">66</span> 筆""")

    async with _client(handler) as http:
        cbc = await _search("cbc")(http, "", 0, 0, "", 1)
        fsc = await _search("fsc_appeal")(http, "保險", 0, 0, "", 1)
    assert [(i["id"], i["doc_number"], i["date"], i["summary"]) for i in cbc["items"]] == [
        ("cbc:12", "台央法字第1130008955號", "2024-03-03", "人事懲處事件")]
    assert cbc["total"] == 20 and cbc["has_more"]
    assert [(i["id"], i["doc_number"], i["date"]) for i in fsc["items"]] == [
        ("fsc_appeal:202605280001", "11500001", "2026-05-28")]
    assert fsc["total"] == 66 and fsc["has_more"]


def test_motc_rows_keep_postback_target():
    soup = ap.BeautifulSoup("""<table id="MainContent_MainContent_dgG"><tr><td>
      <span id="MainContent_MainContent_dgG_dwG_m13_book_send_no_0">1150014941</span>
      <a id="MainContent_MainContent_dgG_dwG_m13_header_0"
         href="javascript:__doPostBack('ctl00$ctl00$MainContent$MainContent$dgG$ctl03$dwG_m13_header','')">違反汽車運輸業管理事件</a>
      </td></tr></table>""", "html.parser")
    assert ap._motc_rows(soup) == [(ap._item("1150014941", "", "", "違反汽車運輸業管理事件"),
                                    "ctl00$ctl00$MainContent$MainContent$dgG$ctl03$dwG_m13_header")]

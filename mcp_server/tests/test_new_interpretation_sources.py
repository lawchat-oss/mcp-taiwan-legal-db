"""新增的函釋來源：新北市、消保處、監察院陽光法令、標準檢驗局、人事總處、中選會。HTTP 以 MockTransport 模擬。"""


import httpx
import pytest

from mcp_server.tools import agency_interpretations as ai


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.fixture(autouse=True)
def _fresh_lists():
    ai._DAILY.clear()
    yield
    ai._DAILY.clear()


async def test_ntpc_lists_largest_categories_and_reports_the_rest(monkeypatch):
    monkeypatch.setattr(ai, "NTPC_MAX_CATEGORIES", 1)
    counts = """<table class="tab-list2">
      <tr><td>[ 2 ]</td><td><a href="FLAWDOC02_Search.aspx?rtype=E&ecode=D01300&K1=x">政府採購類</a></td></tr>
      <tr><td>[ 27 ]</td><td><a href="FLAWDOC02_Search.aspx?rtype=E&ecode=D00701&K1=x">營建類 (建築管理)</a></td></tr></table>"""
    listing = """<table><tr><td>1</td><td>發文字號：</td><td>
      <a href="FLAWDOC03.aspx?rtype=E&ecode=D00701&ecase=%e5%8f%b0%e5%85%a7%e5%9c%8b&eno=1140817216&K1=x">台內國字第 1140817216 號</a></td></tr>
      <tr><td></td><td>要　　旨：</td><td><pre>有關都市更新
容積獎勵</pre></td></tr></table>"""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=counts if "SimpleQ2" in request.url.path else listing)

    async with _client(handler) as http:
        groups = await ai._ntpc_search(http, ai.Query(keyword="違章建築"))
    assert seen[1].params["ecode"] == "D00701"  # 筆數多的類別先取
    (group,) = groups
    assert group["total"] == 27 and "政府採購類 2" in group["note"]
    assert group["items"][0]["id"] == "ntpc:D00701:台內國:1140817216"
    assert group["items"][0]["summary"] == "有關都市更新容積獎勵"


async def test_ntpc_get_validates_id_and_detects_missing():
    detail = """<table class="tab-ex"><tr><th>發文單位：</th><td>新北市政府</td></tr>
      <tr><th>發文字號：</th><td>新北府工建字第 1101006442 號</td></tr>
      <tr><th>發文日期：</th><td>民國 110 年 06 月 09 日</td></tr>
      <tr id="cph_content_trERelaLaw"><th><a>相關法條</a>：</th><td><a>建築法 第 99 條</a><br/></td></tr>
      <tr><td class="m-td"><pre>主    旨：公告防疫措施。</pre></td></tr></table>"""
    async with _client(lambda r: httpx.Response(200, text=detail if r.url.params["eno"] == "1101006442" else "<html/>")) as http:
        doc = await ai._ntpc_get(http, "D00701:新北府工建:1101006442")
        with pytest.raises(LookupError):
            await ai._ntpc_get(http, "D00701:新北府工建:1")
        with pytest.raises(LookupError):
            await ai._ntpc_get(http, "D00701:a&b=1:1")
    assert (doc["agency"], doc["date"], doc["related_laws"]) == ("新北市政府", "2021-06-09", ["建築法 第 99 條"])
    assert doc["full_text"] == "主 旨：公告防疫措施。"


@pytest.mark.parametrize("text, head", [
    ("【行政院消費者保護委員會書函】  中華民國八十七年七月一日  台八十七消保法字第○○七五二號  受文者：張○○君  一、台端八十七年六月十日來函",
     {"agency": "行政院消費者保護委員會", "date": "1998-07-01", "doc_number": "台八十七消保法字第00752號"}),
    ("…進行行政監督。行政院消費者保護處  函 中華民國111年6月1日 院臺消保字第1110176984號 【主旨】 復台端111年5月31日來信",
     {"agency": "行政院消費者保護處", "date": "2022-06-01", "doc_number": "院臺消保字第1110176984號"}),
    ("【行政院消費者保護委員會書函】 受文者：○○法律事務所 一、貴所八十四年一月十八日（八四）齊文字第○○七二號函。",
     {"agency": "行政院消費者保護委員會", "date": "", "doc_number": ""}),  # 引述的來函不是本函
])
def test_cpc_head_reads_only_the_letterhead(text, head):
    assert ai._cpc_head(text) == head


async def test_cpc_list_is_fetched_once_and_matched_locally():
    page = """<div class="news_box pdf_box"><a href="/Page/24C4B877E850ED4E/efd85fb7-f001-4524-a79a-80745919fcf2">
      <div class="title">[第一條]消費者保護法具有基本法性質</div><span class="date">101-05-23</span>
      <p>【行政院消費者保護委員會書函】 中華民國八十七年七月一日 台八十七消保法字第○○七五二號 受文者：張○○君</p></a></div>"""
    calls = []

    def handler(request):
        calls.append(request.url)
        return httpx.Response(200, text=page)

    async with _client(handler) as http:
        (hit,) = await ai._cpc_search(http, ai.Query(keyword="基本法"))
        (miss,) = await ai._cpc_search(http, ai.Query(keyword="定型化契約"))
    assert len(calls) == 1  # 第二次用程序內快取
    assert hit["items"][0]["id"] == "cpc:efd85fb7-f001-4524-a79a-80745919fcf2" and hit["items"][0]["date"] == "1998-07-01"
    assert miss["total"] == 0


async def test_sunshine_metadata_comes_from_the_list():
    listing = """<div id="CCMS_Content"><table><tbody><tr>
      <td data-title="標題"><a href="News_Content.aspx?n=24&s=37450">有關利益衝突迴避法第3條疑義</a></td>
      <td data-title="發布機關">法務部</td><td data-title="發布日期">115/4/13</td>
      <td data-title="發布文號">法廉字第11505001490號</td></tr></tbody></table></div>"""
    detail = """<div id="CCMS_Content"><div class="area-essay"><p>法務部115年4月13日法廉字第115001490號</p>
      <p>一、按本法規定。</p></div></div>"""

    async with _client(lambda r: httpx.Response(200, text=detail if "News_Content" in r.url.path else listing)) as http:
        (group,) = await ai._sunshine_search(http, ai.Query(keyword="利益衝突"))
        doc = await ai._sunshine_get(http, "37450")
    assert group["items"][0]["date"] == "2026-04-13"
    assert doc["doc_number"] == "法廉字第11505001490號"  # 內文的文號漏了「0」，以清單為準
    assert doc["summary"] == "有關利益衝突迴避法第3條疑義" and "一、按本法規定。" in doc["full_text"]


async def test_bsmi_skips_unpublished_and_searches_full_text():
    payload = {"resultList": [
        {"uuId1": "a1", "explanationNo": "經標二字第10720000480號", "explanationtitle": "束褲界定",
         "explanationContent": "<p>結構由多層裁片拼縫</p>", "explanationStatusName": "公開",
         "explanationDateString": "民國107年04月27日"},
        {"uuId1": "b2", "explanationNo": "　", "explanationtitle": "感應電動機", "explanationContent": "　",
         "explanationStatusName": "不公開", "explanationDateString": "民國097年10月02日"},
    ]}
    async with _client(lambda r: httpx.Response(200, json=payload)) as http:
        (group,) = await ai._bsmi_search(http, ai.Query(keyword="裁片"))
        with pytest.raises(LookupError):
            await ai._bsmi_get(http, "b2")
    assert [i["id"] for i in group["items"]] == ["bsmi:a1"] and group["items"][0]["date"] == "2018-04-27"


async def test_dgpa_marks_only_labelled_items_and_reads_stop_notice():
    listing = """<table><tr><td>1.</td><td><div><b>標題：</b><span class="label-fei">停止適用</span>
      <a href="LegalData.aspx?id=11715&type=2">行政院人事行政局 （89）局考字第150097號書函</a></div>
      <div><b>日期：</b><span>089.02.16</span></div><div><b>要旨：</b><pre>未休假加班費</pre></div></td></tr>
      <tr><td>2.</td><td><div><b>標題：</b><a href="LegalData.aspx?id=1014&type=2">行政院人事行政局 89局考字第150097號書函</a></div>
      <div><b>日期：</b><span>089.02.16</span></div></td></tr></table>"""
    detail = """<h3 id="ctl00_cp_content_h3ValidStatus2">本則資料依 113.01.01 總處培字第1120025654號函 停止適用</h3>
      <table class="tab-edit"><tr><th>發文機關：</th><td>行政院人事行政局</td></tr>
      <tr><th>發文字號：</th><td>（89）局考字第150097號書函</td></tr><tr><th>要旨：</th><td>未休假加班費</td></tr></table>"""

    async with _client(lambda r: httpx.Response(200, text=detail if "LegalData" in r.url.path else listing)) as http:
        (group,) = await ai._dgpa_search(http, ai.Query(keyword="加班費"))
        doc = await ai._dgpa_get(http, "11715")
    stopped, duplicate = group["items"]
    assert stopped["status"] == "停止適用" and "status" not in duplicate  # 同一件函重複登錄、只有一筆有標
    assert doc["status_note"] == "本則資料依 113.01.01 總處培字第1120025654號函 停止適用"
    assert doc["summary"] == "未休假加班費"


async def test_cec_title_suffix_marks_stopped_and_unmarked_is_unknown():
    listing = """<table class="tab-result">
      <tr><td>1.</td><td>099.08.03</td><td><a id="x_hlkLawName" href="LawContent.aspx?id=GL000067">
        宣傳品不得修改。(本會111年6月29日中選法字第1113550207號函停止適用)</a></td><td>函釋</td></tr>
      <tr><td>2.</td><td>111.06.29</td><td><a id="y_hlkLawName" href="LawContent.aspx?id=GL000447">
        有關中選法字第0990007395號函，自即日起停止適用，請查照。</a></td><td>函釋</td></tr></table>"""
    async with _client(lambda r: httpx.Response(200, text=listing)) as http:
        (group,) = await ai.SOURCES["cec"][2](http, ai.Query(keyword="宣傳品"))
    stopped, stopping_notice = group["items"]
    assert stopped["status"] == "停止適用" and stopped["status_note"].startswith("本會111年6月29日")
    assert "status" not in stopping_notice  # 停掉別件的函本身不是停止適用


def test_local_search_pages_newest_first():
    rows = [{"id": f"x:{d}", "summary": "定型化契約", "doc_number": "", "date": d} for d in ("1998-07-01", "", "2022-06-01")]
    (group,) = ai._local_search(rows, ai.Query(keyword="定型化契約"), "甲", "乙", "")
    assert [i["date"] for i in group["items"]] == ["2022-06-01", "1998-07-01", ""]  # 清單原本依條次排列

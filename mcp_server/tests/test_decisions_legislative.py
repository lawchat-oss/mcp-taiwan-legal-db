"""訴願決定、公平會處分書、立法理由：解析與請求組裝。HTTP 一律以 MockTransport 模擬。"""

from urllib.parse import parse_qsl

import httpx
import pytest

import mcp_server.server as server
from mcp_server.tools import admin_decisions as ad
from mcp_server.tools import legislative as lg
from mcp_server.tools.pdf_text import pdf_to_text


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.mark.parametrize("n, zh", [
    (1, "一"), (10, "十"), (11, "十一"), (20, "二十"), (100, "一百"), (101, "一百零一"), (110, "一百一十"),
    (184, "一百八十四"), (1001, "一千零一"), (1010, "一千零一十"), (1225, "一千二百二十五"),
])
def test_chinese_number(n, zh):
    assert lg.chinese_number(n) == zh


def test_article_label_and_civil_code_books():
    assert lg.article_label("15-1") == "第十五條之一"
    assert lg._law_title("民法", "184") == "民法第二編債"
    assert lg._law_title("民法", "1030-1") == "民法第四編親屬"
    assert lg._law_title("勞動基準法", "24") == "勞動基準法"


def test_parse_history_handles_nbsp_and_split_labels():
    html = """<table><tr><td class="row0"><font class="artino">第三十七條 之一</font>
      <font class="upddate">(0730719 制定)</font><table><tr><td class="artiupd_TH_2">條文甲</td></tr></table>
      <font class="upddate">(1051206 修正)</font><table><tr><td class="artiupd_TH_2">條文乙</td>
      <td class="artiupd_RS_2">一、理由。</td></tr></table></td></tr></table>"""
    assert lg.parse_history(html) == {"第三十七條之一": [
        {"date": "0730719", "action": "制定", "text": "條文甲", "reason": ""},
        {"date": "1051206", "action": "修正", "text": "條文乙", "reason": "一、理由。"},
    ]}


async def test_legislative_tool_uses_official_law_name(monkeypatch):
    seen = {}

    async def fake_get(name, article_no):
        seen["name"] = name
        return {"success": True}

    monkeypatch.setattr(server, "reg_client", type("R", (), {"resolve_pcode": staticmethod(lambda n: "C0000001")})())
    monkeypatch.setattr(server, "legislative", type("L", (), {"get": staticmethod(fake_get)})())
    await server.get_legislative_history("刑法", "339-4")
    assert seen["name"] == "中華民國刑法"


def test_pdf_to_text_rejects_non_pdf():
    assert pdf_to_text(b"<html>maintenance</html>") == ""


def test_ey_layout_strips_page_headers_and_breaks_sections():
    raw = "案號：A-115-000633第1頁(共6頁)行政院訴願決定書本院決定如下：主文訴願駁回。事實一、甲案號：A-115-000633 第 2 頁(共6頁)乙。二、丙。"
    assert ad._ey_layout(raw) == "行政院訴願決定書本院決定如下：\n主文\n訴願駁回。\n事實\n一、甲乙。\n二、丙。"


async def test_ey_search_routes_number_and_hides_unmasked_old_cases():
    sent = []

    def handler(request):
        sent.append(dict(parse_qsl(request.content.decode(), keep_blank_values=True)))
        return httpx.Response(200, json={"Total": 2, "Data": [
            {"DCS_ID": "A-115-000633", "DCS_DATE": "115/08/21", "DCS_MASKEDSHORTREASON": "張○○因護照事件"},
            {"DCS_ID": "40743", "DCS_DATE": "108/09/05", "DCS_MASKEDSHORTREASON": "王某某因申請應用檔案事件"},
        ]})

    async with _client(handler) as http:
        group = await ad._ey_search(http, "", 0, 0, "A-115-000633", 1)
        await ad._ey_search(http, "", 0, 0, "院臺訴字第1155016714號", 1)
    assert sent[0]["CaseNo"] == "A-115-000633" and sent[0]["No"] == ""
    assert sent[1]["No"] == "1155016714" and sent[1]["CaseNo"] == ""
    assert [i["id"] for i in group["items"]] == ["ey:A-115-000633"]
    assert group["items"][0]["date"] == "2026-08-21" and "1 件" in group["note"]


async def test_ftc_search_postback_and_rows(monkeypatch):
    monkeypatch.setattr(ad, "_ftc_state", {"__VIEWSTATE": "v"})
    monkeypatch.setattr(ad, "_ftc_state_at", 9e18)
    page = """<span id="ContentPlaceHolder1_lb_totalRows">14</span>
      <ul class="result-list"><li><span>發文日期</span><p>2026/09/17</p></li>
      <li><span>類型</span><p>處分書及不處分決議書</p></li>
      <li><span>相關法條</span><p>公平交易法第21條<br/></p></li>
      <li class="result-reason"><a href="https://www.ftc.gov.tw/uploadDecision/abc.pdf"><p>某公司違反公平交易法處分案。</p></a></li></ul>"""
    posted = []

    def handler(request):
        posted.append(dict(parse_qsl(request.content.decode())))
        return httpx.Response(200, text=page)

    async with _client(handler) as http:
        group = await ad._ftc_search(http, "中古車", 0, 0, "", 2)
    assert posted[0]["__EVENTTARGET"].endswith("dl_toPage") and posted[0]["__VIEWSTATE"] == "v"
    assert group["total"] == 14 and group["has_more"] is False
    assert group["items"] == [{
        "id": "ftc:abc.pdf", "agency": "公平交易委員會", "category": "處分書及不處分決議書", "doc_number": "",
        "date": "2026-09-17", "summary": "某公司違反公平交易法處分案。", "related_laws": ["公平交易法第21條"],
    }]


async def test_ftc_get_rejects_path_traversal():
    async with _client(lambda r: httpx.Response(200, content=b"")) as http:
        with pytest.raises(LookupError):
            await ad._ftc_get(http, "../internet/secret.pdf")


async def test_new_tools_validate_arguments():
    assert (await server.search_administrative_decisions())["success"] is False
    assert (await server.search_administrative_decisions(keyword="x", page=0))["success"] is False

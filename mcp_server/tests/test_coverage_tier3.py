"""憲法法庭卷宗、立法資料、立法歷程、智慧局審查基準、精選裁判：解析與請求組裝。HTTP 一律以 MockTransport 模擬。"""

import json
from datetime import date

import httpx
import pytest
from bs4 import BeautifulSoup

from mcp_server.tools import constitutional_docket as cd
from mcp_server.tools import ip_guidelines as ipg
from mcp_server.tools import legislative_records as lr
from mcp_server.tools.agency_interpretations import Query
from mcp_server.tools.legislative import parse_process


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def test_case_file_json_documents_use_page_headings():
    payload = {"atts": [
        {"doc_att_id": 1, "doc_att_group": "openAtt5", "doc_att_title": "某協會法庭之友意見書",
         "doc_att_content": "/uploads/docAtt/a.pdf", "doc_att_txt": "人性尊嚴應受保障", "doc_att_category": "1"},
        {"doc_att_id": 2, "doc_att_group": "meetAtt2", "doc_att_title": "言詞辯論筆錄",
         "doc_att_content": "/uploads/docAtt/b.pdf", "doc_att_txt": "", "doc_att_category": "1"},
        {"doc_att_id": 3, "doc_att_group": "resultAtt3", "doc_att_title": "最高法院判決",
         "doc_att_content": "https://judgment.judicial.gov.tw/FJUD/x", "doc_att_txt": "", "doc_att_category": "1"},
    ], "news": [{"doc_id": "351716", "doc_title": "言詞辯論公告"}]}
    html = f"""<div class="file_list" id="x_open_att5"><strong>法庭之友意見書（官方標題）</strong></div>
      <textarea id="jsonLabel">{json.dumps(payload, ensure_ascii=False)}</textarea>"""
    docs, news = cd._json_documents(BeautifulSoup(html, "html.parser"))
    assert [(d["id"], d["group"]) for d in docs] == [
        ("1", "法庭之友意見書（官方標題）"), ("2", "言詞辯論筆錄"), ("", "確定終局裁判")]
    assert docs[0]["url"].endswith("/download/download.aspx?id=1") and docs[2]["url"].startswith("https://judgment")
    assert news == [{"id": "news:351716", "title": "言詞辯論公告"}]


def test_case_file_html_documents_group_by_field():
    html = """<ul><li class="title">聲請書/ 確定終局裁判</li><li class="text">
      <a href="/download/download.aspx?id=443317">立法委員聲請書</a>
      <a href="/download/download.aspx?fid=100&id=310680">一鍵打包下載</a></li></ul>"""
    docs = cd._html_documents(BeautifulSoup(html, "html.parser"))
    assert [(d["id"], d["group"], d["title"]) for d in docs] == [("443317", "聲請書/ 確定終局裁判", "立法委員聲請書")]


def test_docket_rows_and_pagination():
    html = """<div class="caseProcessTb"><ul class="tcont">
      <div class="cont">1</div><div class="cont">2026-04-08</div><div class="cont">甲</div>
      <div class="cont"><a href="/docdata.aspx?fid=52&id=359527">114年度憲民字第1689號</a></div>
      <div class="cont">主案</div><div class="cont">為損害賠償事件</div></ul></div>
      <a id="ctl_hl_paging_last" href="/docdata.aspx?fid=52&type=1&page=5">末頁</a>"""
    rows = cd._docket_rows(html, "52", cd._DOCKETS["pending"][2])
    assert rows == [{"id": "docket:359527", "受理日期": "2026-04-08", "聲請人": "甲", "案號": "114年度憲民字第1689號",
                     "主案／併案": "主案", "案由": "為損害賠償事件"}]
    assert cd._last_page(html) == 5 and cd._last_page("<html></html>") == 1


async def test_case_file_document_id_validation():
    client = cd.ConstitutionalDocketClient.__new__(cd.ConstitutionalDocketClient)
    assert (await client.document("../etc"))["success"] is False


def test_current_term_and_roc_dates():
    assert lr.current_term(date(2026, 10, 3)) == 11
    assert lr.current_term(date(2028, 2, 1)) == 12
    assert lr._roc("115/07/17") == "2026-07-17" and lr._roc("") == ""


async def test_search_bills_pending_uses_current_term_and_fixes_links():
    seen = {}

    def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"totalItems": 21, "items": [{
            "id": "202110226160000", "title": "「勞動基準法第五十五條條文修正草案」，請審議案。", "content": "本院某黨團",
            "content3": "115/07/17", "content4": "11-05-18", "content5": "交付審查",
            "attachments": [{"attachmentType": "PDF", "link": "https://ppg.ly.gov.tw/ppg/download/agenda1\\02\\pdf\\x.pdf"}],
        }]})

    async with _client(handler) as http:
        group = await lr.search_bills(http, "勞動基準法", "pending", 0, 1)
    assert seen["term"] == str(lr.current_term()) and seen["keyword"] == "勞動基準法"
    assert group["has_more"] is True
    assert group["items"][0]["id"] == "bill:202110226160000"
    assert group["items"][0]["pdf_url"] == "https://ppg.ly.gov.tw/ppg/download/agenda1/02/pdf/x.pdf"
    assert group["items"][0]["date"] == "2026-07-17"


async def test_record_ids_are_validated():
    client = lr.LegislativeRecordsClient.__new__(lr.LegislativeRecordsClient)

    class NoCache:
        async def get_judgment(self, key):
            return None

    client.cache, client.http = NoCache(), None
    for bad in ("bill:../x", "gazette:../../etc/passwd", "draft:abc", "lispdf:zz", "other:1"):
        assert (await client.get(bad))["success"] is False


def test_parse_legislative_process_dedupes_repeated_table():
    row = """<tr><td>三讀</td><td>1130715</td><td><a href="https://lis.ly.gov.tw/lgcgi/lypdftxt?xdd!cec8">113卷071期</a></td>
      <td></td><td></td></tr>"""
    html = f"<p>三讀日期： 1130715 審查委員會： 社福及衛環 公布日期： 1130731</p><table>{row}{row}</table>"
    process = parse_process(html)
    assert process["summary"] == "三讀日期：1130715 審查委員會：社福及衛環 公布日期：1130731"
    assert process["steps"] == [{"stage": "三讀", "date": "2024-07-15", "gazette": "113卷071期", "proposer": "",
                                 "document": "", "gazette_pdf_id": "lispdf:cec8"}]


async def test_ip_guidelines_list_and_title_search(monkeypatch):
    monkeypatch.setattr(ipg, "_rows", [])
    page = """<html><head><title>智慧財產局專利主題網－第二篇 發明專利實體審查</title></head><body>
      <div class="list"><ul><li><a href="https://www.tipo.gov.tw/tw/patents/998-66049.html">
      <div class="listTitle">第三章 專利要件 (2024年7月1日施行版)</div></a></li></ul></div></body></html>"""

    async with _client(lambda r: httpx.Response(200, text=page)) as http:
        groups = await ipg.search(http, Query(keyword="專利要件"))
    assert groups[0]["items"][0]["id"] == "tipo_guide:998-66049"
    assert groups[0]["items"][0]["summary"] == "第二篇 發明專利實體審查－第三章 專利要件 (2024年7月1日施行版)"


async def test_ip_guidelines_rejects_bad_ids():
    with pytest.raises(LookupError):
        await ipg.get(None, "../../etc")


def test_sheet_limit_is_reported():
    from mcp_server.tools import statistics as st
    sheets = [(f"表{i}", [["a", "1"]]) for i in range(st.MAX_SHEETS + 1)]
    assert st.sheets_to_text(sheets).endswith(st.SHEETS_OMITTED)
    assert not st.sheets_to_text(sheets[:st.MAX_SHEETS]).endswith(st.SHEETS_OMITTED)


async def test_named_journal_restricts_the_query(monkeypatch):
    from mcp_server.tools import literature as lit
    seen = []

    async def fake_query(http, rows, year_from, year_to, page, key):
        seen.append([r[2] for r in rows if r[1] == "JT"])
        return {"source": key, "category": "", "total": 0, "items": [], "has_more": False}

    class NoCache:
        async def get_search(self, params):
            return None

        async def set_search(self, params, data):
            pass

    monkeypatch.setattr(lit, "_ncl_query", fake_query)
    client = lit.LiteratureClient.__new__(lit.LiteratureClient)
    client.cache, client.http = NoCache(), None
    await client.search("個人資料", "政大法學評論")
    assert seen == [["政大法學評論"]]


async def test_ip_guidelines_skip_number_and_date_filters():
    (group,) = await ipg.search(None, Query(number="法律字第11403512580號"))
    assert group["total"] == 0 and "字號" in group["note"]


def test_split_articles_accepts_hyphenated_numbers():
    from mcp_server.tools.other_regulations import _split_articles
    arts = _split_articles(["第 1 條 甲", "第 1-1 條 乙", "第 1 條之 2 丙", "第 2 條 丁"])
    assert [(a["number"], a["content"]) for a in arts] == [("1", "甲"), ("1-1", "乙"), ("1-2", "丙"), ("2", "丁")]


def test_journal_pdf_pick_refuses_ambiguous_author():
    from mcp_server.tools.literature import _pick
    cands = [("其他篇名 王小明", "a.pdf"), ("又一篇 王小明", "b.pdf")]
    assert _pick(cands, {"title": "完全不同的題目", "authors": ["王小明"]}) is None
    assert _pick(cands[:1], {"title": "完全不同的題目", "authors": ["王小明"]}) == "a.pdf"


def test_html_table_rowspan_keeps_columns_aligned():
    from mcp_server.tools.statistics import html_table_rows
    html = """<table><tr><th rowspan="2">年度</th><th colspan="2">人數</th></tr>
      <tr><th>男</th><th>女</th></tr><tr><td>114</td><td>10</td><td>20</td></tr></table>"""
    assert html_table_rows(BeautifulSoup(html, "html.parser").table) == [
        ["年度", "人數", ""], ["", "男", "女"], ["114", "10", "20"]]


def test_single_checkbox_factor_rejects_negation():
    from mcp_server.tools.sentencing import _factor_form
    flat = [{"id": "stole_154", "title": "累犯", "type": "checkbox", "options": {}, "_form_ids": {}}]
    assert _factor_form(flat, {"累犯": ["是"]}) == [{"id": "stole_154", "value": ["stole_154"]}]
    with pytest.raises(ValueError):
        _factor_form(flat, {"累犯": ["否"]})


async def test_partial_source_failure_is_not_cached(monkeypatch):
    from mcp_server.tools import admin_decisions as ad

    async def partial(http, *args):
        return {"source": "金管會", "category": "裁罰案件", "total": 1, "has_more": False, "partial": True,
                "items": [{"id": "fsc_sanction:x", "date": "2026-01-01"}]}

    stored = []

    class Cache:
        async def get_search(self, params):
            return None

        async def set_search(self, params, data):
            stored.append(data)

    monkeypatch.setitem(ad.SOURCES, "fsc_sanction", ("金管會裁罰案件", ("金管會",), partial, None))
    client = ad.AdminDecisionClient.__new__(ad.AdminDecisionClient)
    client.cache, client.http = Cache(), None
    r = await client.search("洗錢", "fsc_sanction", 0, 0, "", 1)
    assert r["total_count"] == 1 and stored == []

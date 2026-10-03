"""法學研究文獻：解析、id 驗證與官網全文定位。HTTP 一律以 MockTransport 模擬。"""

import json
from urllib.parse import parse_qsl

import httpx
import pytest

from mcp_server.cache.db import CacheDB
from mcp_server.tools import literature as lit


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _html(body: str) -> httpx.Response:
    return httpx.Response(200, text=body, headers={"Content-Type": "text/html; charset=utf-8"})


@pytest.fixture
def fake_pdf(monkeypatch):
    seen = []

    def fake(blob: bytes) -> str:
        seen.append(blob)
        return "" if b"scan" in blob else f"text{len(seen)}"

    monkeypatch.setattr(lit, "pdf_to_text", fake)
    return seen


def test_helpers():
    assert lit._ym("190  2014.10[民103.10]") == "2014-10"
    assert lit._ym("報告日期： 民國 115 年 03 月") == "2026-03"
    assert lit._ym("民國 113 年 11 月 30 日") == "2024-11-30"
    assert lit._ncl_title("論著作權合理使用之運作:Application of Fair Use: A Study") == "論著作權合理使用之運作"
    assert lit._ncl_title("歐盟GDPR=The GDPR") == "歐盟GDPR"
    assert lit._ncl_title("著作權合理使用概括規定之回顧與前瞻:") == "著作權合理使用概括規定之回顧與前瞻"
    assert lit._ncl_title("A Case Analysis of the DMCA") == "A Case Analysis of the DMCA"
    assert lit._names(["黃明展", "Huang, Ming-chan"]) == ["黃明展"]
    assert lit._names(["Smith, John"]) == ["Smith, John"]
    assert lit._volume("54:1 2025.03[民114.03]") == "54:1"
    assert lit._ncl_terms("刊名:興大法學 個人資料") == [("0", "JT", "興大法學"), ("0", "*", "個人資料")]


def test_resolve_sources():
    assert lit.resolve_sources("") == list(lit.SOURCES)
    assert lit.resolve_sources("司法研究年報、國圖") == ["jirs", "ncl"]
    assert lit.resolve_sources("政大法學評論") == ["journals"]
    assert lit.resolve_sources("月旦") is None


# ── 司法院專題研究報告 ──

JIRS_ROW = """<TR><TD>{n}</TD><TD>報告名稱：</TD><TD><a href="EBookQry04.asp?S=V&scode=V&sname=x&seq={n}">{title}</a></TD></TR>
<TR><TD></TD><TD>報告日期：</TD><TD>{date}</TD></TR><TR><TD></TD><TD>報 告 人 &nbsp;：</TD><TD>{author}</TD></TR>
<TR><TD>&nbsp;</TD><TD></TD><TD></TD></TR>"""
JIRS_LIST = "<TD>共 {total} 筆 / 每頁 20 筆</TD><TABLE>{rows}</TABLE>"
JIRS_DETAIL = """<TABLE><TR><TD>報告名稱：</TD><TD>&#38867;&#22283;量刑準則</TD></TR>
<TR><TD>報 告 人 &nbsp;：</TD><TD>呂寧莉</TD></TR><TR><TD>報告日期：</TD><TD>民國 115 年 03 月 </TD></TR>
<TR><TD>資料來源：</TD><TD>司法研究年報 第 42 輯（刑事類）第 5 篇</TD></TR>
<tr><td>附　　檔：</td><td><a href="EBookDownload.asp?pfid=0000415961&showType=1&lk=V%2C20260300%2C0010">00封面.PDF (555KB)</a><br>
<a href="EBookDownload.asp?pfid=0000415962&showType=1&lk=V%2C20260300%2C0010">01第一章 緒論.PDF (923KB)</a></td></tr></TABLE>"""


async def test_jirs_search_title_then_full_text_fallback():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        if "sname" in request.url.params:
            return _html(JIRS_LIST.format(total=0, rows=""))
        row = JIRS_ROW.format(n=1, title='韓國<font color=#FF0000>量刑</font>準則', date="民國 115 年 03 月", author="呂寧莉")
        return _html(JIRS_LIST.format(total=21, rows=row))

    async with _client(handler) as http:
        group = await lit._jirs_search(http, "量刑", 113, 115, 1)
    assert sent[0]["sname"] == "量刑" and sent[0]["sdate"] == "20240100" and sent[0]["edate"] == "20261231"
    assert sent[1]["keyword"] == "量刑" and "sname" not in sent[1]
    assert group["total"] == 21 and group["has_more"] is True and "全文" in group["note"]
    assert group["items"] == [{"id": "jirs:202603:韓國量刑準則", "title": "韓國量刑準則", "authors": ["呂寧莉"],
                               "venue": "司法院專題研究報告", "date": "2026-03", "summary": ""}]


async def test_jirs_multiword_uses_full_text_and():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        return _html(JIRS_LIST.format(total=0, rows=""))

    async with _client(handler) as http:
        await lit._jirs_search(http, "合理使用 著作權", 0, 0, 1)
    assert sent == [{"S": "V", "scode": "V", "page": "1", "keyword": "合理使用&著作權"}]


async def test_jirs_get_finds_report_by_title_and_concatenates_chapters(fake_pdf):
    sent = []

    def handler(request):
        sent.append(request.url)
        path = request.url.path
        if path.endswith("EBookQRY03.asp"):
            rows = "".join(JIRS_ROW.format(n=n, title=t, date="民國 114 年 03 月", author="甲") for n, t in
                           [(1, "刑法第 57 條之量刑審酌事項之研究續篇"), (2, "刑法第 57 條之量刑審酌事項")])
            return _html(JIRS_LIST.format(total=2, rows=rows))
        if path.endswith("EBookQry04.asp"):
            return _html(JIRS_DETAIL)
        return httpx.Response(200, content=b"\r\n<html><body></body></html>%PDF-1.4 chapter")

    async with _client(handler) as http:
        doc = await lit._jirs_get(http, "202503:刑法第 57 條之量刑審酌事項")
    assert sent[0].params["sname"] == "條之量刑審酌事項"  # 篇名欄位不接受空白：取最長一段
    assert sent[0].params["sdate"] == "20250300" and sent[1].params["seq"] == "2"
    assert all(b.startswith(b"%PDF") for b in fake_pdf)  # 切掉下載檔前的 HTML
    assert doc["title"] == "韓國量刑準則" and doc["venue"] == "司法研究年報 第 42 輯（刑事類）第 5 篇"
    assert doc["date"] == "2026-03" and doc["report_key"] == "V,20260300,0010"
    assert doc["full_text"] == "【00封面】\ntext1\n\n【01第一章 緒論】\ntext2"
    assert [c["title"] for c in doc["chapters"]] == ["00封面", "01第一章 緒論"]
    assert doc["full_text_truncated"] is False and "note" not in doc


async def test_jirs_get_stops_at_text_cap(monkeypatch):
    monkeypatch.setattr(lit, "pdf_to_text", lambda b: "字" * lit.MAX_FULL_TEXT)
    row = JIRS_ROW.format(n=1, title="韓國量刑準則", date="民國 115 年 03 月", author="甲")

    def handler(request):
        if request.url.path.endswith("EBookQRY03.asp"):
            return _html(JIRS_LIST.format(total=1, rows=row))
        if request.url.path.endswith("EBookQry04.asp"):
            return _html(JIRS_DETAIL)
        return httpx.Response(200, content=b"%PDF-1.4")

    async with _client(handler) as http:
        doc = await lit._jirs_get(http, "202603:韓國量刑準則")
    assert doc["full_text_truncated"] is True and len(doc["full_text"]) == lit.MAX_FULL_TEXT
    assert "1 章未擷取" in doc["note"]


# ── 國家圖書館 期刊論文索引 ──

NCL_LIST = """<div>檢索結果筆數 (1328) 已超過系統最大設定值 (300)</div><ul class="page-list">
<li><div class="list-content"><ul class="page-list-content">
 <li><p><a class="articleTitle" href="/NclService/JournalContentDetail?SysId=A15027451&amp;q=1"
      title="從美加趨勢論著作權法中合理使用之適用:A Study of Fair Use">從美加</a></p></li>
 <li><p class="authorName"><a title="曾勝珍">曾勝珍</a><a title="Tseng, Sheng-chen">Tseng</a>;</p></li>
 <li><p class="journalName"><a title="嶺東財經法學">嶺東財經法學</a></p></li>
 <li><p class="volumeNo">5  2012.12[民101.12]</p></li><li><span>頁　次：</span><p>頁109-141</p></li>
</ul><a href="/NclService/pdfdownload?filePath=x&amp;xmlId=1" title="PDF全文"></a></div></li></ul>"""

NCL_DETAIL = """<table class="data-detail">
<tr><th>題　名</th><td id="articleTitle"> 從美加趨勢論著作權法中合理使用之適用=A Study of Fair Use </td></tr>
<tr><th>作　者</th><td id="authorName"><a title="曾勝珍">曾勝珍</a>; <a title="洪維拓">洪維拓</a>;</td></tr>
<th>書刊名</th><td id="journalName"><a title="{journal}"> {journal}</a></td></tr>
<tr><th>卷　期</th><td>{volume}  2012.12[民101.12]</td></tr><tr><th>頁　次</th><td>頁109-141</td></tr>
<tr><th>關鍵詞</th><td><a>著作權</a>; <a>合理使用</a>;</td></tr>
<tr><th>中文摘要</th><td>合理使用向來被視為利器。</td></tr></table>{pdf}"""
NCL_PDF_LINK = '<a href="/NclService/pdfdownload?filePath=x&amp;key=k&amp;xmlId=1" title="PDF全文">PDF</a>'


async def test_ncl_search_builds_and_query_and_caps_paging():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        return _html(NCL_LIST)

    async with _client(handler) as http:
        group = await lit._ncl_search(http, "合理使用 刊名:嶺東財經法學", 100, 0, 15)
    q = sent[0]
    assert (q["q[0].f"], q["q[0].i"], q["q[1].o"], q["q[1].f"], q["q[1].i"]) == ("*", "合理使用", "0", "JT", "嶺東財經法學")
    assert q["pys"] == "2011" and "pye" not in q and q["page"] == "15"
    assert group["total"] == 1328 and group["has_more"] is False and "300" in group["note"]
    assert group["items"] == [{
        "id": "ncl:A15027451", "title": "從美加趨勢論著作權法中合理使用之適用", "authors": ["曾勝珍"],
        "venue": "嶺東財經法學", "volume": "5", "pages": "109-141", "date": "2012-12", "summary": "",
        "has_full_text": True,
    }]


async def test_ncl_get_returns_text_with_licence_note(fake_pdf):
    def handler(request):
        if request.url.path.endswith("pdfdownload"):
            return httpx.Response(200, content=b"%PDF-1.6 body")
        return _html(NCL_DETAIL.format(journal="嶺東財經法學", volume="5", pdf=NCL_PDF_LINK))

    async with _client(handler) as http:
        doc = await lit._ncl_get(http, "A15027451")
    assert doc["title"] == "從美加趨勢論著作權法中合理使用之適用" and doc["authors"] == ["曾勝珍", "洪維拓"]
    assert (doc["venue"], doc["volume"], doc["pages"], doc["date"]) == ("嶺東財經法學", "5", "109-141", "2012-12")
    assert doc["keywords"] == ["著作權", "合理使用"] and doc["abstract"] == "合理使用向來被視為利器。"
    assert doc["full_text"] == "text1" and "請勿轉載" in doc["note"]
    assert doc["pdf_url"].startswith("https://tpl.ncl.edu.tw/NclService/pdfdownload?")


async def test_ncl_get_missing_record_and_scanned_pdf(fake_pdf):
    async with _client(lambda r: _html("<html>首頁</html>")) as http:
        with pytest.raises(LookupError):
            await lit._ncl_get(http, "A99999999")

    def handler(request):
        if request.url.path.endswith("pdfdownload"):
            return httpx.Response(200, content=b"%PDF-1.4 scan")
        return _html(NCL_DETAIL.format(journal="智慧財產權月刊", volume="209", pdf=NCL_PDF_LINK))

    async with _client(handler) as http:
        doc = await lit._ncl_get(http, "A16016916")
    assert doc["full_text"] == "" and "掃描" in doc["note"]


# ── GRB ──

async def test_grb_search_and_get():
    posts = []

    def handler(request):
        posts.append(request)
        assert request.headers["Referer"] == "https://www.grb.gov.tw/"
        if request.url.path == "/searcher":
            return httpx.Response(200, json={"totalRows": 161, "obj": [{
                "id": 13540821, "title": "全球化下<span class='highlight'>營</span>業秘密保護之研究", "planYear": 109,
                "hostNameList": ["許曉芬"], "abstractC": "本研究從比較法觀點出發",
            }]})
        if request.url.path == "/searcher/13540821":
            return httpx.Response(200, json={
                "id": 13540821, "title": "全球化下營業秘密保護之研究", "host1NameC": ["許曉芬"],
                "planOrganName": "科技部", "excuOrganName": "國立成功大學法律學系", "periodStym": "10908",
                "planNo": "MOST109-2410-H006-070-MY2", "abstractC": "計畫摘要內容",
                "grb05Report2": {"abstractC": "成果摘要內容", "abstractE": None},
            })
        return httpx.Response(500, json={"status": 500})

    async with _client(handler) as http:
        group = await lit._grb_search(http, "營業秘密", 105, 0, 2)
        doc = await lit._grb_get(http, "13540821")
        with pytest.raises(LookupError):
            await lit._grb_get(http, "1")
    form = dict(parse_qsl(posts[0].content.decode()))
    assert form["planYearSt"] == "105" and form["planYearEn"] == "999" and form["rowsPerPage"] == "10"
    assert form["nowPage"] == "2" and group["has_more"] is True
    assert group["items"][0] | {"summary": ""} == {
        "id": "grb:13540821", "title": "全球化下營業秘密保護之研究", "authors": ["許曉芬"], "venue": "",
        "date": "2020", "summary": ""}
    assert json.loads(posts[1].content) == {}
    assert doc["venue"] == "科技部／國立成功大學法律學系" and doc["date"] == "2020-08"
    assert doc["abstract"] == "【計畫摘要】\n計畫摘要內容\n\n【成果報告摘要】\n成果摘要內容"
    assert doc["source_url"] == "https://www.grb.gov.tw/search/planDetail?id=13540821"
    assert doc["plan_no"] == "MOST109-2410-H006-070-MY2" and "reCAPTCHA" in doc["note"]


@pytest.mark.parametrize("key, native_id", [
    ("jirs", "seq=3"), ("jirs", "2026:報告"), ("ncl", "../x"), ("ncl", "A1"), ("grb", "12a"),
    ("grb", "1/../2"), ("journals", "javascript:x"),
])
async def test_ids_are_validated_before_any_request(key, native_id):
    def handler(request):
        raise AssertionError(f"不應發出請求：{request.url}")

    async with _client(handler) as http:
        with pytest.raises(LookupError):
            await lit.SOURCES[key][3](http, native_id)


# ── 開放取用期刊 ──

async def test_journals_search_restricts_to_journal_titles():
    sent = []

    def handler(request):
        sent.append(dict(request.url.params))
        return _html("<div>檢索結果筆數(0)</div>")

    async with _client(handler) as http:
        group = await lit._journals_search(http, "個人資料", 0, 0, 1)
    q = sent[0]
    assert [q[f"q[{n}].i"] for n in range(3)] == [*lit.JOURNALS, "個人資料"]
    assert [q[f"q[{n}].o"] for n in (1, 2)] == ["1", "0"]  # (刊名 OR 刊名) AND 關鍵字
    assert group["total"] == 0 and group["source"] == "開放取用法學期刊"


async def test_journals_get_resolves_iias_pdf(fake_pdf):
    hosts = []

    def handler(request):
        hosts.append(request.url.host + request.url.path)
        if request.url.host == "tpl.ncl.edu.tw":
            return _html(NCL_DETAIL.format(journal="中研院法學期刊", volume="38", pdf="").replace(
                "從美加趨勢論著作權法中合理使用之適用", "論預測性警務在危害防止與個人資料保護領域之法律問題"))
        if request.url.path == "/publication_list/9":
            return _html('<a href="publication_post/1884/9" title="第38期">38</a>'
                         '<a href="publication_post/31/9" title="第 3 期">3</a>')
        if request.url.path == "/publication_post/1884/9":
            return _html("""<div class="publication-content">
              <div class="chapter"><h3>美洲人權法院與合公約性審查</h3><ul class="author"><li>作者 翁燕菁</li></ul>
                <ul class="download"><li><a href="https://publication.iias.sinica.edu.tw/25501162.pdf">下載</a></li></ul></div>
              <div class="chapter"><h3>論預測性警務在危害防止與個人資料保護領域之法律問題</h3>
                <ul class="author"><li>作者 謝碩駿</li><li>頁碼 1-102</li></ul>
                <ul class="download"><li><a href="https://publication.iias.sinica.edu.tw/23626162.pdf">下載</a></li></ul></div>
            </div>""")
        return httpx.Response(200, content=b"%PDF-1.4")

    async with _client(handler) as http:
        doc = await lit._journals_get(http, "A2026053796")
    assert doc["pdf_url"] == "https://publication.iias.sinica.edu.tw/23626162.pdf"
    assert doc["full_text"] == "text1" and doc["abstract"] == "合理使用向來被視為利器。" and "note" not in doc
    assert hosts[-1] == "publication.iias.sinica.edu.tw/23626162.pdf" and len(hosts) == 4


async def test_journals_get_resolves_nccu_by_author_when_title_differs(fake_pdf):
    def handler(request):
        if request.url.host == "tpl.ncl.edu.tw":
            return _html(NCL_DETAIL.format(journal="政大法學評論", volume="175", pdf=""))
        if request.url.path == "/zh_tw/articles":
            return _html('<a href="/zh_tw/articles/x-175" title="政大法學評論第175期 (2023年12月)">175</a>'
                         if request.url.params["page_no"] == "2" else
                         '<a href="/zh_tw/articles/x-1750" title="政大法學評論第1750期">1750</a>')
        if request.url.path == "/zh_tw/articles/x-175":
            return _html('<a href="/uploads/asset/data/1/1-%E6%B4%AA%E7%91%A9%E5%AE%B9%E8%80%81%E5%B8%AB1-88.pdf">別的題目</a>'
                         '<a href="/uploads/asset/data/2/2-%E6%9B%BE%E5%8B%9D%E7%8F%8D%E8%80%81%E5%B8%AB89-164.pdf">改過的標題</a>')
        return httpx.Response(200, content=b"%PDF-1.4")

    async with _client(handler) as http:
        doc = await lit._journals_get(http, "A15027451")
    assert doc["pdf_url"].startswith("http://review.law.nccu.edu.tw/uploads/asset/data/2/")
    assert doc["full_text"] == "text1"


async def test_journals_get_falls_back_to_abstract_when_site_fails():
    def handler(request):
        if request.url.host == "tpl.ncl.edu.tw":
            return _html(NCL_DETAIL.format(journal="政大法學評論", volume="161", pdf=""))
        raise httpx.ConnectError("reset")

    async with _client(handler) as http:
        doc = await lit._journals_get(http, "A20020254")
    assert doc["full_text"] == "" and doc["abstract"] and "連線失敗" in doc["note"]


# ── 用戶端：快取與錯誤隔離 ──

async def test_client_isolates_errors_and_never_caches_ncl(tmp_path, monkeypatch):
    calls = []

    async def ok_search(http, keyword, yf, yt, page):
        return lit._group("ncl", 1, [{"id": "ncl:A1", "title": "t", "date": ""}], False)

    async def oa_search(http, keyword, yf, yt, page, journals=()):
        return lit._group("journals", 1, [{"id": "journals:A1", "title": "t", "date": ""}], False)

    async def broken(http, *args):
        raise httpx.ConnectError("down")

    async def ncl_get(http, native_id):
        calls.append(native_id)
        return lit._doc("全文", title="t", authors=[], venue="v", date="", abstract="", source_url="u")

    monkeypatch.setattr(lit, "SOURCES", {
        "ncl": ("國圖", ("國圖",), ok_search, ncl_get),
        "journals": ("期刊", ("期刊",), oa_search, ncl_get),
        "jirs": ("司法院", ("司法院",), broken, broken),
    })
    cache = CacheDB(db_path=tmp_path / "c.db")
    await cache.initialize()
    client = lit.LiteratureClient(cache)
    try:
        r = await client.search("x")
        assert [i["id"] for i in r["results"]] == ["journals:A1"]  # 重複的國圖那筆去掉
        assert any("error" in g for g in r["categories"]) and "cached" not in (await client.search("x"))
        assert (await client.get("ncl:A1"))["full_text"] == "全文"
        assert (await client.get("ncl:A1"))["cached"] is False and calls == ["A1", "A1"]
        await client.get("journals:A1")
        assert (await client.get("journals:A1"))["cached"] is True
        assert (await client.get("jirs:x"))["success"] is False
        assert (await client.get("nope:x"))["success"] is False
    finally:
        await client.close()
        await cache.close()

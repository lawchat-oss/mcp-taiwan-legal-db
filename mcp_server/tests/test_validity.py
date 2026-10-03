"""函釋效力標示（停止適用／適用中）：各來源的官網標示怎麼轉成 status。HTTP 以 MockTransport 模擬。"""

import httpx

from mcp_server.tools import agency_interpretations as ai
from mcp_server.tools import fint


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


async def test_lawsys_marks_repealed_and_moves_repeal_number_to_note():
    listing = """<table class="tab-result">
      <tr><td>1.</td><td>112.01.04</td><td><span class="label-fei">廢/停</span>
        <a id="x_hlkLawName" href="LawContent.aspx?id=FL049913">保險業通報重大偶發事件</a></td><td>函</td></tr>
      <tr><td>2.</td><td>114.12.30</td><td><a id="y_hlkLawName" href="LawContent.aspx?id=GL004216">冷熱錢包之令</a></td>
        <td>令</td></tr></table>法規類別 全部 2"""
    detail = """<table class="tab-edit">
      <tr><th>法規名稱：</th><td><span class="label-fei">廢/停</span> 保險業通報重大偶發事件</td></tr>
      <tr><th>公發布日：</th><td>民國 99 年 11 月 09 日</td></tr>
      <tr><th>廢止/停止適用日期：</th><td>民國 112 年 01 月 04 日</td></tr>
      <tr><th>發文字號：</th><td>金管保財字第11104621863號函</td></tr></table>
      <div id="ctl00_cp_content_divContent">一、所稱保險業</div>"""

    async with _client(lambda r: httpx.Response(200, text=detail if "LawContent" in r.url.path else listing)) as http:
        (group,) = await ai.SOURCES["fsc"][2](http, ai.Query(keyword="保險"))
        doc = await ai.SOURCES["fsc"][3](http, "FL049913")
    stopped, current = group["items"]
    assert stopped["status"] == "停止適用" and stopped["status_note"].startswith("廢/停")
    assert current["status"] == "適用中" and "status_note" not in current
    assert doc["summary"] == "保險業通報重大偶發事件"  # 名稱開頭不再帶「廢/停」
    assert doc["date"] == "2010-11-09" and doc["doc_number"] == ""  # 字號欄是廢止令的，不是這件的
    assert doc["status"] == "停止適用"
    assert doc["status_note"] == "2023-01-04 廢止或停止適用（金管保財字第11104621863號函）"


async def test_taipei_abolished_marker_and_unmarked_is_unknown():
    listing = """<p>共2筆</p>
      <ul class="fx-list"><li class="num"><span class="abolished">(廢)</span>
        <a href="/Law/Interpretation/Content/FE353389">臺北市政府都市發展局 110.03.03 北市都設字第1103002689號函</a></li>
        <li class="pre">甲</li></ul>
      <ul class="fx-list"><li class="num">
        <a href="/Law/Interpretation/Content/FE385681">臺北市政府都市發展局 113.11.11 北市都設字第1133082427號函</a></li>
        <li class="pre">乙</li></ul>"""
    detail = """<article class="interpretation-content">
      <div class="row"><div class="col-title">發文字號：</div><div class="col-data"><span class="abolished">(廢)</span>
        臺北市政府都市發展局 110.03.03 北市都設字第1103002689號函</div></div></article>"""

    async with _client(lambda r: httpx.Response(200, text=listing if "SearchResult" in r.url.path else detail)) as http:
        groups = await ai._taipei_search(http, ai.Query(keyword="騎樓"))
        doc = await ai._taipei_get(http, "FE353389")
    stopped, unmarked = groups[0]["items"]
    assert stopped["status"] == "停止適用" and "status" not in unmarked  # 官網不會標「適用中」
    assert doc["agency"] == "臺北市政府都市發展局" and doc["status"] == "停止適用"


async def test_fint_fei_marker_is_status_not_part_of_doc_number(monkeypatch):
    detail = """<div class="col-xs-8"><div class="int-table">
      <div class="row"><div class="col-th">發文字號：</div><div class="col-td">
        <span id="lblFei" title="本解釋已廢止或不再援用" class="fei">廢</span> 法矯字第 11404008340 號</div></div>
      <div class="row"><div class="col-th">編註：</div><div class="col-td">
        1.依本筆資料，原法務部民國113年11月13日法矯字第11304018620號函，自114年7月15日起停止適用。
        2.本筆資料，依據法務部民國115年3月3日法矯字第11504003350號函，自115年3月5日起停止適用。</div></div>
      <div class="row"><div class="col-all">主旨：x</div></div></div></div>"""

    async with _client(lambda r: httpx.Response(200, text=detail)) as http:
        raw = await fint.get(http, "E:FE391249")
        doc = await ai._fint_get(http, "E:FE391249")
    assert raw["status"] == "停止適用" and raw["status_note"] == "本解釋已廢止或不再援用"
    assert doc["doc_number"] == "法矯字第 11404008340 號"
    assert doc["status_note"].startswith("本筆資料，依據法務部民國115年3月3日")  # 不是「依本筆資料…」那句


async def test_fint_editor_note_alone_does_not_mark_stopped(monkeypatch):
    """編註列的是這件停掉的其他函；沒有「廢」標示就不輸出 status。"""
    async def fake_get(http, item_id):
        return {"fields": {"發文字號": "法律字第1號", "編註": "依本筆資料，原法務部函，自114年起停止適用。"},
                "full_text": "x", "related_laws": [], "attachments": [], "source_url": "u"}

    monkeypatch.setattr(ai.fint, "get", fake_get)
    assert "status" not in await ai._fint_get(None, "E:FE1")


async def test_exam_platform_label_and_stop_date():
    listing = """<table><tr><td>1.</td><td>
      <div id="x_ctl01_divTitle"><span class="co-th"><b>標  題：</b></span><span class="co-td">
        <span class="label-fei" id="x_spanValidStatus">廢/停</span>所詢退休人員</span></div>
      <div><span class="co-th"><b>發文字號：</b></span><span class="co-td">
        <a href="ExecutiveData.aspx?id=15251&type=2">部管二字第1145819466號書函</a></span></div></td></tr></table>"""
    detail = """<table class="tab-edit">
      <tr><th>發文字號：</th><td id="ctl00_cp_content_tdIssue2"><span class="label-fei">廢/停</span>部管二字第1145819466號書函</td></tr>
      <tr id="ctl00_cp_content_trValidDate"><th>停止適用日期：</th><td>民國 114 年 08 月 19 日</td></tr>
      <tr><th>標　　題：</th><td>所詢退休人員</td></tr></table>"""

    async with _client(lambda r: httpx.Response(200, text=detail if "ExecutiveData" in r.url.path else listing)) as http:
        (group,) = await ai.SOURCES["mocs"][2](http, ai.Query(keyword="退休"))
        doc = await ai.SOURCES["mocs"][3](http, "15251")
    assert group["items"][0]["summary"] == "所詢退休人員" and group["items"][0]["status"] == "停止適用"
    assert doc["doc_number"] == "部管二字第1145819466號書函"
    assert (doc["status"], doc["status_note"]) == ("停止適用", "停止適用日期 2025-08-19")


def test_motc_suffix_becomes_status():
    assert ai._motc_head("交通部觀光署 111.06.01. 觀潭管字第11103019301號公告（停止適用）") == {
        "agency": "交通部觀光署", "date": "2022-06-01", "doc_number": "觀潭管字第11103019301號公告",
        "status": "停止適用", "status_note": "停止適用"}
    assert ai._motc_head("交通部 101.03.05. 交航字第10150026521號（部分停止適用）")["status"] == "部分停止適用"
    assert "status" not in ai._motc_head("交通部 111.02.03. 交路字第1110416478號函")


def test_nlma_marks_only_hand_typed_suffix():
    import json
    blob = json.dumps([
        {"id": 10278, "title": "釋示建築法第54條所稱「開工」之意義。（停止適用）", "content": "", "publish_up": "1990-01-01"},
        {"id": 17761, "title": "釋示建築法第77條", "content": "", "publish_up": "2020-01-01"},
    ]).encode()
    stopped, unmarked = ai._nlma_parse(blob)
    assert stopped["status"] == "停止適用" and "status" not in unmarked


async def test_moj_label_leaves_doc_number_and_uses_own_stop_note():
    detail = """<div class="div-extent">
      <div class="col-row"><div class="col-th">發文字號：</div><div class="col-td"><span class="label-fei">廢</span>
        法矯署安字第 11304004410 號</div></div></div>
      <div id="cp_content_Etr"><div class="col-td">1.依本筆資料，原某函，自 113 年起停止適用。
        2.本筆資料，依據法務部矯正署民國 114 年 4 月 23 日函，自 114 年 5 月 1 日停止適用。</div></div>"""
    async with _client(lambda r: httpx.Response(200, text=detail)) as http:
        doc = await ai._moj_get(http, "FE378867")
    assert doc["doc_number"] == "法矯署安字第 11304004410 號" and doc["status"] == "停止適用"
    assert doc["status_note"].startswith("本筆資料，依據法務部矯正署")


async def test_mol_and_mohw_unmarked_rows_are_in_force():
    mol = """<ul id="cph_content_ulLawCate"><li><span class="badge">2</span></li><li><span class="badge">0</span></li>
      <li><span class="badge">0</span></li></ul><div class="fint-list">
      <div class="row"><span class="fei">廢</span><a id="x_hlkTitle">勞檢一字第 0930031687 號公告</a>
        <div class="col-td">a</div><div class="col-td">93.03.01</div></div>
      <div class="row"><a id="y_hlkTitle">勞動條 3字第 1150147957 號函</a>
        <div class="col-td">a</div><div class="col-td">115.04.01</div></div></div>"""
    mohw = """共 1 筆<table id="dat02"><tr><td></td><td>發文字號：</td><td><a href="#"><span class="fei">廢</span>衛授疾 字第
      1142100020 號</a></td></tr><tr><td></td><td>發文日期：</td><td>民國 114 年 05 月 21 日</td></tr></table>"""
    seen = []

    def handler(request):
        seen.append(request.url)
        return httpx.Response(200, text=mol if "laws.mol" in str(request.url) else mohw)

    async with _client(handler) as http:
        groups = await ai._mol_search(http, ai.Query(keyword="資遣費"))
        (health,) = await ai._mohw_search(http, ai.Query(keyword="健康檢查"))
    assert "N1" not in seen[0].params  # 送空的字別，解釋令頁籤會變 0 筆
    assert [i["status"] for i in groups[0]["items"]] == ["停止適用", "適用中"]
    assert groups[1]["items"] == []  # 解釋令頁籤 0 筆時，站方列的是別的頁籤
    assert health["items"][0]["id"] == "mohw:衛授疾:1142100020" and health["items"][0]["status"] == "停止適用"


async def test_mohw_detail_line_survives_marker_removal():
    detail = """<pre>發文單位：衛生福利部</pre><pre>發文字號：<span class="fei">廢</span>衛授疾字第 1142100020 號</pre>
      <pre>發文日期：民國 114 年 05 月 21 日</pre><pre>
主    旨：公告修正。</pre>
      共 1 筆"""
    async with _client(lambda r: httpx.Response(200, text=detail)) as http:
        doc = await ai._mohw_get(http, "衛授疾:1142100020")
    assert doc["doc_number"] == "衛授疾字第 1142100020 號" and doc["status"] == "停止適用"


async def test_pcc_marks_ids_from_the_stopped_category_and_detail_note(monkeypatch):
    listing = """<table class="tb_01"><tr><td>1</td><td>政府採購法第30條</td><td>押標金</td><td>88-06-10 工程企字第8811396號</td>
      <td></td><td></td><a onclick="readExplainLetter(60044041)"></a></tr>
      <tr><td>2</td><td>政府採購法第30條</td><td>押標金</td><td>115-01-05 工程企字第1150100055號</td>
      <td></td><td></td><a onclick="readExplainLetter(75004960)"></a></tr></table>共有 2 筆"""
    detail = """<div class="title_1s">行政院公共工程委員會 函 <font color="red">備註：註：本解釋函即日起停止適用。</font></div>
      <div id="printExplain"><table><tr><td>發文字號：(88)工程企字第8811396號</td></tr></table><table><tr><td>主旨</td></tr></table></div>"""
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(dict(httpx.QueryParams(request.content.decode())).get("type"))
            if posts[-1] == "3":
                return httpx.Response(200, text='<a onclick="readExplainLetter(60044041)"></a>')
            return httpx.Response(200, text=listing)
        if "ContentDetail" in request.url.path:
            return httpx.Response(200, text=detail)
        return httpx.Response(200, text='<input name="_csrf" value="t">')

    monkeypatch.setattr(ai, "_session", lambda: _client(handler))  # 工程會要 CSRF，每次查詢另開 session
    async with _client(handler) as http:
        (group,) = await ai._pcc_search(http, ai.Query(keyword="押標金"))
        doc = await ai._pcc_get(http, "60044041")
    assert [i.get("status") for i in group["items"]] == ["停止適用", None]
    assert (doc["agency"], doc["doc_type"]) == ("行政院公共工程委員會", "函")  # 紅字備註不再混進機關與文別
    assert doc["status_note"] == "本解釋函即日起停止適用。"


async def test_mof_compilation_in_force_and_new_rulings_checked_against_dropped_list():
    def handler(request):
        form = dict(httpx.QueryParams(request.content.decode()))
        fid = form["FunctionID"]
        if fid == "FF20001":
            return httpx.Response(200, json={"Data": {"Table": [{"DocNum": "11104615470", "DocTitle": "台財稅字",
                                                                  "Subject": "x", "PosterDate": "111/08/10"}]}})
        if fid == "FB30001":  # 號碼是部分比對：要核對全號
            return httpx.Response(200, json={"Data": {"Table": [
                {"Number": "111046154701", "LawName": "x", "Content": "不是這件"},
                {"Number": "11104615470", "LawName": "所得稅(一一四年版)", "Content": "經財政部令廢止，爰予免列。"}]}})
        return httpx.Response(200, json={"Data": {"Table": [{"TaxSN": 143310, "TaxAct": "所得稅法", "Part": "114年版",
                                                              "Title": "1", "Content": "x", "TotalCount": 1}]}})

    async with _client(handler) as http:
        ff = await ai._mof_get(http, "ff:202208100001")
        fb = await ai._mof_get(http, "fb:143310")
    assert ff["status"] == "停止適用" and ff["status_note"] == "所得稅(一一四年版)免列：經財政部令廢止，爰予免列。"
    assert fb["status"] == "適用中" and "114年版" in fb["status_note"]


async def test_gcis_flag_two_is_stopped_and_zero_is_unknown():
    rows = [{"consCd": 5810, "pmgDate": "2001-12-12", "consSmy": "a", "aboFlg": "2"},
            {"consCd": 9984, "pmgDate": "2020-01-01", "consSmy": "b", "aboFlg": "0"}]
    async with _client(lambda r: httpx.Response(200, json=rows)) as http:
        (group,) = await ai._gcis_search(http, ai.Query(keyword="公司"))
    by_id = {i["id"]: i.get("status") for i in group["items"]}
    assert by_id == {"gcis:9984": None, "gcis:5810": "停止適用"}
